"""REST routes for the three UI surfaces (spec §6):

- ``GET  /api/graph``  — the whole knowledge graph for force-directed rendering
- ``POST /api/query``  — natural-language question through the query agent
- ``GET  /api/alerts`` — recent alerts
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.alerts.engine import AlertEngine
from app.config import Settings, get_settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.extraction.client import LLMClient
from app.query.agent import QueryAgent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# Lazily-built shared clients (service objects are cheap to keep around).
# A lock guards the first build so concurrent startup requests cannot create
# duplicate drivers/engines (#18).
_init_lock = threading.Lock()
_store: PostgresStore | None = None
_neo4j: Neo4jStore | None = None
_llm: LLMClient | None = None

# Short-TTL cache for the /api/graph payload (keyed by ``limit``). The
# force-directed UI polls the endpoint, and rebuilding the subgraph costs 4
# Cypher round-trips; serving a copy refreshed every ``graph_cache_ttl_seconds``
# keeps the endpoint snappy. 0 TTL disables caching.
_graph_cache: dict[int, tuple[float, dict]] = {}
_graph_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Auth + rate limiting (#18)
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Sliding-window limiter keyed by IP, with periodic eviction.

    Config: ``api_rate_window_seconds`` (0 = unlimited) and
    ``api_rate_max_requests`` per window. In-memory only: fine for guarding a
    single-process uvicorn deployment before any public exposure.

    Thread-safe: a lock serializes the per-key read-modify-write, so concurrent
    requests for the same IP cannot slip past ``max_requests``. Eviction is
    time-gated — the full-dict sweep runs at most once per window — so traffic
    across many distinct IPs never pays an O(active keys) rebuild on every
    request.
    """

    def __init__(self, window_seconds: int, max_requests: int) -> None:
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._hits: dict[str, list[float]] = {}
        self._last_sweep = 0.0
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.window_seconds <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if t > cutoff]
            if len(hits) >= self.max_requests:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            if (
                len(self._hits) > 4096
                and now - self._last_sweep >= self.window_seconds
            ):
                # Once per window: evict every key whose newest hit is stale.
                self._hits = {k: v for k, v in self._hits.items() if v[-1] > cutoff}
                self._last_sweep = now
        return True


_rate_limiter: _RateLimiter | None = None


def _get_rate_limiter() -> _RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        s = _get_settings()
        _rate_limiter = _RateLimiter(s.api_rate_window_seconds, s.api_rate_max_requests)
    return _rate_limiter


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Only honor a plausible IP from the proxy chain; drop spoofed
        # junk (hostnames, garbage) by falling back to the direct peer so
        # the rate limiter keys cannot be arbitrarily rotated by a client.
        first = forwarded.split(",")[0].strip()
        try:
            ipaddress.ip_address(first)
        except ValueError:
            pass
        else:
            return first
    return request.client.host if request.client else "unknown"


def require_api_key(request: Request) -> None:
    """No-op when ``api_key`` is empty (local dev); otherwise verify the
    ``Authorization: Bearer`` header and apply the per-IP rate limit."""
    settings = _get_settings()
    if settings.api_key:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or token.strip() != settings.api_key:
            raise HTTPException(status_code=401, detail="missing or invalid API key")
    if not _get_rate_limiter().allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")


def _get_settings() -> Settings:
    return get_settings()


def _get_store() -> PostgresStore:
    global _store
    if _store is None:
        with _init_lock:
            if _store is None:
                _store = PostgresStore(_get_settings().postgres_dsn)
                _store.init_schema()
    return _store


def _get_neo4j() -> Neo4jStore:
    global _neo4j
    if _neo4j is None:
        with _init_lock:
            if _neo4j is None:
                _neo4j = Neo4jStore(_get_settings())
    return _neo4j


def _get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        with _init_lock:
            if _llm is None:
                s = _get_settings()
                _llm = LLMClient(
                    base_url=s.llm_base_url,
                    api_key=s.llm_api_key,
            model_name=s.llm_model_name,
            tool_mode=s.llm_tool_mode,
            max_retries=s.llm_max_retries,
            timeout_seconds=s.llm_timeout_seconds,
        )
    return _llm


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)


from app.api.graph_payload import (
    build_graph_payload,
    json_safe as _json_safe,
    node_key as _node_key,
)


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


@router.get("/graph", dependencies=[Depends(require_api_key)])
def graph(
    limit: int = Query(default=500, ge=0, le=5000),
    since: str | None = Query(default=None),
) -> dict:
    """Serialize a subgraph for the force-directed UI.

    With ``limit`` > 0, only the ``limit`` most-recent events and their direct
    neighbors (tickers, politicians, committees, sectors) are returned, so a
    large live graph stays responsive. ``limit=0`` returns everything (demo /
    small graphs). The response carries ``truncated``/``event_count`` so the UI
    can tell a partial view from a full one. ``limit`` is capped at 5000
    (#16) so a single request cannot force an unbounded materialization.

    With ``since`` (ISO timestamp), only events at/after that time (plus
    their neighborhoods) are returned, flagged with ``delta: true`` — the
    incremental-sync pattern for polling: the UI merges deltas into its
    snapshot and refetches full every Nth poll as the resync. An unparseable
    ``since`` falls back to a full render. Deltas skip the total-events
    count and the cache (both pointless for a moving window).

    Deltas are deliberately NOT ``limit``-bounded (E4, see C2 numbers): a
    capped delta could silently drop events and break merge correctness, and
    ``truncated`` cannot signal it (``event_count`` is None for deltas). A
    wide catch-up window can therefore exceed a capped full render (measured:
    7d window -> 1390 nodes vs 822 for limit=500 full). This is the rare path
    (reconnect after downtime); steady-state polls use small windows and stay
    ~0.1s. Do NOT add a LIMIT to the delta keep-pass without also wiring a
    truncation signal the UI resyncs on.

    The payload is cached for ``graph_cache_ttl_seconds`` to keep UI polling
    cheap (``event_count``/``truncated`` tolerate a few seconds of staleness).
    """
    from datetime import datetime, timezone

    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            since_dt = None

    ttl = get_settings().graph_cache_ttl_seconds
    if ttl > 0 and since_dt is None:
        with _graph_cache_lock:
            hit = _graph_cache.get(limit)
        if hit and hit[0] > time.monotonic():
            return hit[1]

    payload = _render_graph(limit, since_dt)

    if ttl > 0 and since_dt is None:
        with _graph_cache_lock:
            _graph_cache[limit] = (time.monotonic() + ttl, payload)
    return payload


def _render_graph(limit: int, since_dt=None) -> dict:
    from datetime import datetime

    neo4j = _get_neo4j()
    delta = since_dt is not None
    try:
        # The total-events count is a full label scan; deltas skip it (the UI
        # keeps the last full total) so steady-state polls stay index-bound.
        total = (
            None
            if delta
            else neo4j.run("MATCH (e:Event) RETURN count(e) AS n")[0]["n"]
        )
        keep_ids: list[str] | None = None
        if limit > 0 or delta:
            # Two-pass approach: the COALESCE wrapper in the original query
            # prevented Neo4j from using the range index on ``event_time``,
            # forcing a full label scan + sort.  Splitting into two index-
            # friendly paths (non-null event_time → ORDER BY + LIMIT; null
            # event_time → LIMIT only) lets the planner use the B-tree index
            # for the sorted path. Deltas filter the same indexed path by the
            # cursor instead of sorting the whole graph.
            if delta:
                assert since_dt is not None
                rows = neo4j.run(
                    """
                    MATCH (e:Event)
                    WHERE e.event_time IS NOT NULL
                      AND e.event_time >= datetime($since)
                    OPTIONAL MATCH (e)--(n)
                    WITH collect(DISTINCT e) + collect(DISTINCT n) AS all_keep
                    UNWIND [x IN all_keep WHERE x IS NOT NULL] AS kept
                    RETURN DISTINCT elementId(kept) AS internal_id
                    """,
                    since=since_dt.isoformat(),
                )
            else:
                rows = neo4j.run(
                    """
                    MATCH (e:Event)
                    WHERE e.event_time IS NOT NULL
                    WITH e ORDER BY e.event_time DESC
                    LIMIT $limit
                    OPTIONAL MATCH (e)--(n)
                    WITH collect(DISTINCT e) + collect(DISTINCT n) AS all_keep
                    UNWIND [x IN all_keep WHERE x IS NOT NULL] AS kept
                    RETURN DISTINCT elementId(kept) AS internal_id
                    """,
                    limit=limit,
                )
            keep_ids: list[str] = [row["internal_id"] for row in rows]
            # Pad with events that have no event_time (old / unparsed) if the
            # indexed path returned fewer than ``limit`` events. Full renders
            # only — deltas are strictly "what's new", never backfill.
            remaining = limit - len(keep_ids)
            if remaining > 0 and not delta:
                old_rows = neo4j.run(
                    """
                    MATCH (e:Event)
                    WHERE e.event_time IS NULL
                    WITH e LIMIT $remaining
                    OPTIONAL MATCH (e)--(n)
                    WITH collect(DISTINCT e) + collect(DISTINCT n) AS all_keep
                    UNWIND [x IN all_keep WHERE x IS NOT NULL] AS kept
                    RETURN DISTINCT elementId(kept) AS internal_id
                    """,
                    remaining=remaining,
                )
                keep_ids.extend(row["internal_id"] for row in old_rows)

        node_conditions = [
            *([f"elementId(n) IN $keep_ids"] if keep_ids is not None else []),
            # Tombstoned identity nodes (merged_into set, see IdentityEngine)
            # are hidden from the UI: their SAME_AS edge is provenance, not a
            # user-facing connection.
            "(NOT n:Politician OR n.merged_into IS NULL)",
            "(NOT n:Insider OR n.merged_into IS NULL)",
        ]
        # One round trip for nodes + links (previously two sequential scans):
        # every kept node's outgoing edges ride along, and links touching a
        # node outside the kept set (or a tombstoned node) are dropped in
        # Python exactly as before.
        rows = neo4j.run(
            f"""
            MATCH (n) WHERE {' AND '.join(node_conditions)}
            OPTIONAL MATCH (n)-[r]->(m)
            RETURN n AS node, labels(n) AS labels,
                   type(r) AS rel, m AS target, labels(m) AS target_labels
            """,
            keep_ids=keep_ids or [],
        )
    except Exception as exc:
        logger.exception("graph export failed")
        raise HTTPException(status_code=500, detail=f"graph export failed: {exc}")

    return build_graph_payload(rows, total, limit, delta, keep_ids)


# ---------------------------------------------------------------------------
# Query agent
# ---------------------------------------------------------------------------


@router.post("/query", dependencies=[Depends(require_api_key)])
def ask_question(request: QueryRequest) -> dict:
    agent = QueryAgent(llm=_get_llm(), store=_get_neo4j())
    try:
        result = agent.ask(request.question)
    except Exception as exc:
        logger.exception("query agent failed for question %r", request.question)
        raise HTTPException(status_code=500, detail=f"query agent failed: {exc}")
    payload = result.as_dict()
    payload["rows"] = _json_safe(payload["rows"])
    return payload


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@router.get("/alerts")
def alerts(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    try:
        rows = _get_store().get_alerts(limit=limit)
    except Exception as exc:
        logger.exception("alerts fetch failed")
        raise HTTPException(status_code=500, detail=f"alerts fetch failed: {exc}")
    return {"alerts": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@router.get("/reports/digest")
def reports_digest(period: str | None = Query(default=None)) -> dict:
    """Serve the daily digest, generating it on first request for the period."""
    try:
        report = get_report_engine().ensure_digest(period)
    except Exception as exc:
        logger.exception("digest generation failed")
        raise HTTPException(status_code=500, detail=f"digest failed: {exc}")
    return {"report": report}


@router.get("/reports")
def reports(
    limit: int = Query(default=20, ge=1, le=100),
    kind: str | None = Query(default=None),
) -> dict:
    try:
        rows = _get_store().get_reports(limit=limit, kind=kind)
    except Exception as exc:
        logger.exception("reports fetch failed")
        raise HTTPException(status_code=500, detail=f"reports fetch failed: {exc}")
    return {"reports": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


@router.get("/leaderboard")
def leaderboard(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """Best-performing congressional trades by return since transaction."""
    try:
        rows = _get_store().get_leaderboard(limit=limit)
    except Exception as exc:
        logger.exception("leaderboard fetch failed")
        raise HTTPException(status_code=500, detail=f"leaderboard fetch failed: {exc}")
    return {"leaders": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def get_signals_engine() -> "SignalsEngine":
    from app.signals.engine import SignalsEngine

    return SignalsEngine(neo4j=_get_neo4j(), store=_get_store())


@router.get("/signals")
def signals(window_days: int = Query(default=90, ge=1, le=365)) -> dict:
    """Computed pressure signals: momentum, insider clusters, options meter,
    uncovered sector bets, suspicious timing."""
    try:
        bundle = get_signals_engine().bundle(window_days=window_days)
    except Exception as exc:
        logger.exception("signals failed")
        raise HTTPException(status_code=500, detail=f"signals failed: {exc}")
    return _json_safe(bundle)


@router.get("/signals/similar")
def signals_similar(politician_id: str = Query(min_length=1)) -> dict:
    """Politicians trading the same tickers (shared-ticker similarity)."""
    try:
        rows = get_signals_engine().similar(politician_id)
    except Exception as exc:
        logger.exception("similar failed")
        raise HTTPException(status_code=500, detail=f"similar failed: {exc}")
    return {"similar": _json_safe(rows)}


# ---------------------------------------------------------------------------
# Live stream (Server-Sent Events) lives in app.api.stream (B3).
# ---------------------------------------------------------------------------


def get_alert_engine() -> AlertEngine:
    return AlertEngine(
        store=_get_store(),
        neo4j=_get_neo4j(),
        llm=_get_llm(),
    )


def get_report_engine() -> "ReportEngine":
    from app.reports.engine import ReportEngine

    return ReportEngine(store=_get_store(), neo4j=_get_neo4j(), llm=_get_llm())


def _get_processing() -> "ProcessingPipeline":
    from app.extraction.extractor import EventExtractor
    from app.graph.resolution import EntityResolver
    from app.graph.writer import GraphWriter
    from app.pipeline import ProcessingPipeline
    from app.reports.engine import ReportEngine

    s = _get_settings()
    return ProcessingPipeline(
        extractor=EventExtractor(client=_get_llm(), confidence_threshold=s.confidence_threshold),
        # Every Politician/Insider mention is written with its canonical
        # spelling; LLM mention spelling variants stop fragmenting node names.
        writer=GraphWriter(store=_get_neo4j(), entity_resolver=EntityResolver(_get_store())),
        alert_engine=get_alert_engine(),
        store=_get_store(),
        report_engine=ReportEngine(store=_get_store(), neo4j=_get_neo4j(), llm=_get_llm()),
    )


def close_services() -> None:
    """Close lazily-created clients (drivers, engines) for graceful shutdown."""
    global _store, _neo4j, _llm
    if _neo4j is not None:
        try:
            _neo4j.close()
        except Exception:
            logger.exception("neo4j close failed")
        _neo4j = None
    if _store is not None:
        try:
            _store.engine.dispose()
        except Exception:
            logger.exception("postgres dispose failed")
        _store = None
    if _llm is not None:
        try:
            close = getattr(_llm, "close", None)
            if callable(close):
                close()
        except Exception:
            logger.exception("llm close failed")
        _llm = None
    logger.info("shared service clients closed")
