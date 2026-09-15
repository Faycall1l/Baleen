"""FastAPI entrypoint for the Baleen backend.

Serves the three UI surfaces under ``/api`` (graph, query, alerts) plus a
``/health`` probe. On startup it:

- ensures the Postgres schema and the default alert rules,
- lazily wires the shared clients,
- starts the background ingest->process scheduler.

Scheduler ticking is disabled in tests (see ``tests/conftest.py``).
"""

from __future__ import annotations

import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from sqlalchemy import text

from app.alerts.rules import AlertRules
from app.api import routes
from app.config import Settings, get_settings
from app.pipeline import ProcessingPipeline
from app.scheduler import BaleenScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    # Use the shared store so startup doesn't spin up a second engine pool that
    # shutdown never disposes (routes.close_services() owns the shared one).
    store = routes._get_store()

    if settings.seed_default_alerts:
        for name, definition in AlertRules.definitions().items():
            store.ensure_rule(name, definition["description"], definition.get("config"))
        logger.info("default alert rules ensured")

    try:
        routes._get_neo4j().ensure_indexes()
    except Exception:
        logger.warning("neo4j index ensure failed; graph reads fall back to slow path", exc_info=True)
    else:
        logger.info("neo4j indexes ensured")

    scheduler = None
    if settings.scheduler_enabled:
        scheduler = _build_scheduler(settings)
        scheduler.start()

    yield

    if scheduler is not None:
        scheduler.stop()
    routes.close_services()


def _build_scheduler(settings: Settings) -> BaleenScheduler:
    return BaleenScheduler(
        settings=settings,
        processing=routes._get_processing(),
        interval_seconds=settings.poll_interval_seconds,
    )


app = FastAPI(title="Baleen API", version="0.1.0", lifespan=lifespan)
app.include_router(routes.router)


@app.get("/health")
def health(response: Response) -> dict:
    """Liveness + component checks (Postgres, Neo4j, optional LLM).

    Returns 503 when a core dependency is down; the body names which one.
    The LLM is probed only when a real model name is configured (placeholders
    are valid in dev). X API is intentionally absent (a probe costs a rate
    limit).
    """
    settings = get_settings()
    checks = {}
    checks_spec = [
        ("postgres", _check_postgres),
        ("neo4j", _check_neo4j),
    ]
    if settings.llm_configured:
        checks_spec.append(("llm", _check_llm))

    for name, probe in checks_spec:
        try:
            probe()
            checks[name] = {"status": "ok"}
        except Exception as exc:
            checks[name] = {"status": "down", "detail": str(exc)[:200]}

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    status_code = 200 if overall == "ok" else 503
    response.status_code = status_code
    return {"status": overall, "app": "baleen", "checks": checks}


def _check_postgres() -> None:
    with routes._get_store().engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def _check_neo4j() -> None:
    routes._get_neo4j().run("RETURN 1")


def _check_llm() -> None:
    if not routes._get_llm().health():
        raise RuntimeError(
            f"LLM endpoint unreachable (probe GET {routes._get_llm().base_url}/models)"
        )
