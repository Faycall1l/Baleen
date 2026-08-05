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

from fastapi import FastAPI

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
        processing = routes._get_processing()
        scheduler = BaleenScheduler(settings=settings, processing=processing)
        scheduler.start()

    yield

    if scheduler is not None:
        scheduler.stop()


app = FastAPI(title="Baleen API", version="0.1.0", lifespan=lifespan)
app.include_router(routes.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": "baleen"}
