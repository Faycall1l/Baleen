"""REST routes for the three UI surfaces (spec §6):

- ``GET  /api/graph``  — the whole knowledge graph for force-directed rendering
- ``POST /api/query``  — natural-language question through the query agent
- ``GET  /api/alerts`` — recent alerts
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from app.pipeline import ProcessingPipeline
    from app.reports.engine import ReportEngine
    from app.signals.engine import SignalsEngine

from app.alerts.engine import AlertEngine
from app.api.auth import _RateLimiter, require_api_key  # noqa: F401  (re-exported)
from app.api.committee_routes import router as committee_router
from app.api.feed_routes import router as feed_router
from app.api.graph_payload import json_safe as _json_safe  # noqa: F401 (used by route modules)
from app.api.graph_routes import router as graph_router
from app.api.market_routes import router as market_router
from app.api.people_routes import router as people_router
from app.api.query_routes import router as query_router
from app.api.signals_routes import router as signals_router
from app.config import Settings, get_settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.extraction.client import LLMClient
from app.query.agent import QueryAgent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
# Handlers live in themed sub-modules; they resolve shared clients from this
# module at call time so tests keep one monkeypatch surface.
router.include_router(committee_router)
router.include_router(feed_router)
router.include_router(graph_router)
router.include_router(market_router)
router.include_router(people_router)
router.include_router(query_router)
router.include_router(signals_router)

# Lazily-built shared clients (service objects are cheap to keep around).
# A lock guards the first build so concurrent startup requests cannot create
# duplicate drivers/engines.
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

# Concurrency gate for Neo4j-heavy reads. The UI's 30s poll fans out
# graph + signals + flow requests; when the database slows, overlapping polls
# pile queries faster than they drain, which OOMs the 2GB Neo4j container and
# restarts it mid-recovery. Fail fast with 503 instead of queueing behind a
# dying backend — the UI already treats 503 as degraded, not offline.
# Six slots absorb one tab's fan-out plus headroom on a healthy database
# (~1s reads); stampedes still shed fast, and the UI quietly retries busy
# responses instead of parking an error for a full poll cycle.
_NEO4J_READ_GATE_SLOTS = 6
_neo4j_read_gate = threading.BoundedSemaphore(_NEO4J_READ_GATE_SLOTS)


# ---------------------------------------------------------------------------
# Auth + rate limiting — policy in app.api.auth; the limiter singleton
# and settings lookup stay here so tests patch one module.
# ---------------------------------------------------------------------------

_rate_limiter: _RateLimiter | None = None


def _get_rate_limiter() -> _RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        s = _get_settings()
        _rate_limiter = _RateLimiter(s.api_rate_window_seconds, s.api_rate_max_requests)
    return _rate_limiter


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
                backup = (
                    LLMClient(
                        base_url=s.groq_base_url,
                        api_key=s.groq_api_key,
                        model_name=s.groq_model_name,
                        # Native tools: Groq parses tool calls server-side,
                        # and errors when a model emits one undeclared.
                        # The primary stays on json mode (its endpoint has
                        # no tool parser); the backup can use real tools.
                        tool_mode="auto",
                        max_retries=s.llm_max_retries,
                        timeout_seconds=s.llm_timeout_seconds,
                        # Groq's hosted models flake on an explicit
                        # json_object constraint; the strict parser guards
                        # output instead.
                        use_response_format=False,
                    )
                    if s.groq_api_key
                    else None
                )
                _llm = LLMClient(
                    base_url=s.llm_base_url,
                    api_key=s.llm_api_key,
                    model_name=s.llm_model_name,
                    tool_mode=s.llm_tool_mode,
                    max_retries=s.llm_max_retries,
                    timeout_seconds=s.llm_timeout_seconds,
                    backup=backup,
                )
    return _llm


# Re-exported for the route modules and the SSE stream, which resolve their
# dependencies from here so tests keep a single monkeypatch surface.

# ---------------------------------------------------------------------------
# POST /api/query lives in app.api.query_routes; it is registered through
# the include_router above and owns the QueryRequest model.
# ---------------------------------------------------------------------------


def get_signals_engine() -> SignalsEngine:
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


def get_report_engine() -> ReportEngine:
    from app.reports.engine import ReportEngine

    return ReportEngine(
        store=_get_store(),
        neo4j=_get_neo4j(),
        llm=_get_llm(),
        llm_briefs=_get_settings().reports_llm_briefs,
    )


def _get_processing() -> ProcessingPipeline:
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
        report_engine=ReportEngine(
            store=_get_store(),
            neo4j=_get_neo4j(),
            llm=_get_llm(),
            llm_briefs=s.reports_llm_briefs,
        ),
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


@contextlib.contextmanager
def neo4j_read_slot():
    """Fail-fast slot for one Neo4j-heavy read.

    Yields when a gate slot is free, else raises 503 immediately. Callers
    must hold the slot only around the render — never around cache hits —
    so a saturated backend sheds load instead of queueing polls behind a
    dying database.
    """
    from fastapi import HTTPException

    if not _neo4j_read_gate.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="graph busy; retry shortly")
    try:
        yield
    finally:
        _neo4j_read_gate.release()
