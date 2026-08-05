"""Neo4j driver wrapper.

Provides a single place that builds and closes the ``neo4j`` driver, plus
separate read-only credentials for the query agent (see docs/SETUP.md for the
role creation SQL). All graph access goes through :meth:`Neo4jStore.run`.
"""

from __future__ import annotations

import logging

from neo4j import GraphDatabase, Driver

from app.config import Settings

logger = logging.getLogger(__name__)


class Neo4jStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._driver: Driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        read_uri = settings.neo4j_read_uri or settings.neo4j_uri
        self._read_driver: Driver = GraphDatabase.driver(
            read_uri,
            auth=(settings.neo4j_read_user, settings.neo4j_read_password),
        )

    def close(self) -> None:
        self._driver.close()
        self._read_driver.close()

    def run(self, query: str, **params) -> list[dict]:
        """Execute a write-capable statement (graph writer / seeding)."""
        with self._driver.session() as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def run_read(self, query: str, **params) -> list[dict]:
        """Execute a read-only statement via the read-only user (query agent)."""
        with self._read_driver.session() as session:
            result = session.run(query, **params)
            return [record.data() for record in result]
