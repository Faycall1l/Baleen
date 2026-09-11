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
from app.extraction.client import LLMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def seed_rules(store: PostgresStore) -> None:
    store.init_schema()
    engine = AlertEngine(
        store=store,
        neo4j=Neo4jStore(get_settings()),
        llm=LLMClient(
            base_url=get_settings().llm_base_url,
            api_key=get_settings().llm_api_key,
            model_name=get_settings().llm_model_name,
            max_retries=get_settings().llm_max_retries,
            timeout_seconds=get_settings().llm_timeout_seconds,
        ),
    )
    engine.ensure_default_rules()
    logging.getLogger(__name__).info("alert rules seeded (default: %s)", DEFAULT_RULE)


if __name__ == "__main__":
    store = PostgresStore(get_settings().postgres_dsn)
    try:
        seed_rules(store)
    finally:
        store.engine.dispose()
