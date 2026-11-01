"""Seed the MVP alert rule into Postgres (idempotent).

Usage:
    python -m scripts.seed_alerts

Registers ``politician_trades_in_oversight_sector`` in ``alert_rules`` unless it
already exists. Also initializes the Postgres schema.
"""

from __future__ import annotations

import logging

from app.alerts.engine import DEFAULT_RULE, AlertEngine
from app.config import get_settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.extraction.client import build_llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def seed_rules(store: PostgresStore) -> None:
    store.init_schema()
    engine = AlertEngine(
        store=store,
        neo4j=Neo4jStore(get_settings()),
        llm=build_llm(get_settings()),
    )
    engine.ensure_default_rules()
    logging.getLogger(__name__).info("alert rules seeded (default: %s)", DEFAULT_RULE)


if __name__ == "__main__":
    store = PostgresStore(get_settings().postgres_dsn)
    try:
        seed_rules(store)
    finally:
        store.engine.dispose()
