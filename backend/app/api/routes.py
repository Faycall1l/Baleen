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

from app.alerts.engine import AlertEngine
from app.config import Settings, get_settings
from app.query.agent import QueryAgent
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.extraction.client import LLMClient
from app.api.feed_routes import router as feed_router
from app.api.graph_routes import router as graph_router
from app.api.query_routes import router as query_router
from app.api.signals_routes import router as signals_router

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
# Handlers live in themed sub-modules; they resolve shared clients from this
# module at call time so tests keep one monkeypatch surface.
router.include_router(feed_router)
router.include_router(graph_router)
router.include_router(query_router)
router.include_router(signals_router)

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


from app.api.graph_payload import (
    build_graph_payload,
    json_safe as _json_safe,
    node_key as _node_key,
)


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
# Live stream (Server-Sent Events) lives in app.api.stream (B3).
# ---------------------------------------------------------------------------


def get_signals_engine() -> "SignalsEngine":
    from app.signals.engine import SignalsEngine

    return SignalsEngine(neo4j=_get_neo4j(), store=_get_store())


def get_query_agent() -> QueryAgent:
    return QueryAgent(llm=_get_llm(), store=_get_neo4j())


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
