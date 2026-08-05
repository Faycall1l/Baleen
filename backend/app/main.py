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
from app.db.postgres import PostgresStore
from app.pipeline import ProcessingPipeline
from app.scheduler import BaleenScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    store = PostgresStore(settings.postgres_dsn)
    store.init_schema()

    if settings.seed_default_alerts:
        for name, definition in AlertRules.definitions().items():
            store.ensure_rule(name, definition["description"], definition.get("config"))
        logger.info("default alert rules ensured")

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
    """Liveness + component checks (Postgres, Neo4j).

    Returns 503 when a core dependency is down; the body names which one.
    The LLM / X API are intentionally absent: placeholders are valid in dev.
    """
    checks = {}
    for name, probe in (
        ("postgres", _check_postgres),
        ("neo4j", _check_neo4j),
    ):
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
