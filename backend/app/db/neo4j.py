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
        # A dedicated read-only role is optional defense-in-depth (see
        # docs/SETUP.md). Until NEO4J_READ_URI is configured we fall back to the
        # writer driver; the query agent's real guardrail is the Cypher allowlist.
        self._read_driver: Driver | None = None
        if settings.neo4j_read_uri:
            self._read_driver = GraphDatabase.driver(
                settings.neo4j_read_uri,
                auth=(settings.neo4j_read_user, settings.neo4j_read_password),
            )
        # Transaction timeout in seconds; ``None`` (or 0) means "no cap".
        self._timeout: float | None = settings.neo4j_query_timeout_seconds or None

    def close(self) -> None:
        self._driver.close()
        if self._read_driver is not None:
            self._read_driver.close()

    def run(self, query: str, timeout: float | None = None, **params) -> list[dict]:
        """Execute a write-capable statement (graph writer / seeding).

        ``timeout`` overrides the store's configured query timeout (seconds)
        for this single statement; ``0``/``None`` means no cap.
        """
        with self._driver.session() as session:
            return self._execute(session, query, timeout, params)

    def run_read(self, query: str, timeout: float | None = None, **params) -> list[dict]:
        """Execute a read-only statement via the read-only user (query agent).

        Uses the dedicated read-only driver when configured, otherwise the
        writer driver (fine for local dev; production should pin the role).
        """
        driver = self._read_driver or self._driver
        with driver.session() as session:
            return self._execute(session, query, timeout, params)

    @staticmethod
    def _execute(session, query: str, timeout: float | None, params: dict) -> list[dict]:
        """Run a statement inside an explicit transaction with a server-enforced
        timeout. `timeout=None` leaves the server default in place."""
        tx = session.begin_transaction(timeout=timeout)
        try:
            result = tx.run(query, **params)
            rows = [record.data() for record in result]
            tx.commit()
            return rows
        except Exception:
            tx.rollback()
            raise
