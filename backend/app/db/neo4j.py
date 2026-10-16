"""Neo4j driver wrapper.

Provides a single place that builds and closes the ``neo4j`` driver, plus
separate read-only credentials for the query agent (see docs/SETUP.md for the
role creation SQL). All graph access goes through :meth:`Neo4jStore.run`.

Statements run as *managed* transactions (``execute_read``/``execute_write``),
so the driver automatically retries transient failures (leader switches,
brief unavailability). Every Baleen write is ``MERGE``-based and therefore
idempotent, which is exactly what makes those retries safe.
"""

from __future__ import annotations

import contextlib
import logging

from neo4j import Driver, GraphDatabase, unit_of_work

from app.config import Settings
from app.graph.schema import LABEL_KEYS

logger = logging.getLogger(__name__)


def _work_fn(timeout: float | None):
    """Transaction function with a server-enforced timeout.

    ``timeout=None`` leaves the server default in place. Defined per call
    because ``unit_of_work`` takes a static timeout while ours varies.
    """

    @unit_of_work(timeout=timeout)
    def _run(tx, query: str, params: dict) -> list[dict]:
        # Results must be materialized (never return the lazy ``Result``)
        # because the driver may re-invoke this function on retry.
        result = tx.run(query, **params)
        return [record.data() for record in result]

    return _run


class Neo4jStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        common = {
            # Recycle connections before they go stale and liveness-check
            # any idle pool entry before reuse; without this the driver can
            # hand out defunct connections after long idle stretches (bit us
            # in back-to-back full-suite runs).
            "liveness_check_timeout": 30,
            "max_connection_lifetime": 1800,
            "max_connection_pool_size": 50,
        }
        self._driver: Driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            **common,
        )
        # A dedicated read-only role is optional defense-in-depth (see
        # docs/SETUP.md). Until NEO4J_READ_URI is configured we fall back to the
        # writer driver; the query agent's real guardrail is the Cypher allowlist.
        self._read_driver: Driver | None = None
        if settings.neo4j_read_uri:
            self._read_driver = GraphDatabase.driver(
                settings.neo4j_read_uri,
                auth=(settings.neo4j_read_user, settings.neo4j_read_password),
                **common,
            )
        # Transaction timeout in seconds; ``None`` (or 0) means "no cap".
        self._timeout: float | None = settings.neo4j_query_timeout_seconds or None

    def close(self) -> None:
        self._driver.close()
        if self._read_driver is not None:
            self._read_driver.close()

    def run(self, query: str, timeout: float | None = None, **params) -> list[dict]:
        """Execute a write-capable statement (graph writer / seeding).

        ``timeout`` overrides the store's configured query timeout for this
        single statement; ``0`` disables it for this statement. Without an
        override, the configured ``NEO4J_QUERY_TIMEOUT_SECONDS`` cap applies.
        """
        with self._driver.session() as session:
            return session.execute_write(
                _work_fn(self._resolve_timeout(timeout)),
                query,
                params,
            )

    def run_read(self, query: str, timeout: float | None = None, **params) -> list[dict]:
        """Execute a read-only statement via the read-only user (query agent).

        Uses the dedicated read-only driver when configured, otherwise the
        writer driver (fine for local dev; production should pin the role).
        """
        driver = self._read_driver or self._driver
        with driver.session() as session:
            return session.execute_read(
                _work_fn(self._resolve_timeout(timeout)),
                query,
                params,
            )

    def write_transaction(self, fn, timeout: float | None = None):
        """Run ``fn(run)`` atomically inside a single write transaction.

        ``run(query, **params)`` behaves like :meth:`run` (list-of-dict rows),
        but all statements commit together — either the whole multi-statement
        write lands or none of it does. The configured query
        cap applies to every statement via the same ``_resolve_timeout`` path.
        """
        cap = self._resolve_timeout(timeout)

        def _body(tx):
            def run(query: str, **params) -> list[dict]:
                return [record.data() for record in tx.run(query, timeout=cap, **params)]

            return fn(run)

        with self._driver.session() as session:
            return session.execute_write(_body)

    def _resolve_timeout(self, timeout: float | None) -> float | None:
        """Per-call override: ``None`` -> configured cap, ``0`` -> no cap."""
        if timeout is None:
            return self._timeout
        return None if timeout == 0 else timeout

    def ensure_indexes(self) -> None:
        """Create idempotent indexes used by the graph endpoint.

        ``CREATE RANGE INDEX ... IF NOT EXISTS`` is safe to call repeatedly;
        the schema-change statements are committed in their own transaction.

        Every node key in ``LABEL_KEYS`` also gets a uniqueness constraint so
        the writer's equality MERGEs resolve through an index, not a label scan.
        """
        stmts = [
            # Enables index-backed ``ORDER BY e.event_time DESC`` in the
            # graph endpoint instead of a full label scan + sort.
            "CREATE RANGE INDEX event_event_time IF NOT EXISTS FOR (e:Event) ON (e.event_time)",
            # Backs the ``e.transaction_date >= date($since)`` filters in the
            # momentum/cluster signals instead of a full label scan.
            "CREATE RANGE INDEX event_transaction_date IF NOT EXISTS FOR (e:Event) ON (e.transaction_date)",
            # Backs the ``e.first_seen_at >= datetime($since_ts)`` filter in
            # the options-meter signal.
            "CREATE RANGE INDEX event_first_seen_at IF NOT EXISTS FOR (e:Event) ON (e.first_seen_at)",
            # Covers the ``WHERE e.tweet_id = ...`` dedup path in the writer.
            "CREATE RANGE INDEX event_tweet_id IF NOT EXISTS FOR (e:Event) ON (e.tweet_id)",
        ]
        # Unique constraints double as the equality index the writer's MERGEs
        # anchor on; a separate range index on the same property would block
        # them. Names match scripts/seed_reference.py.
        stmts += [
            f"CREATE CONSTRAINT baleen_{label.lower()}_key IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
            for label, key in LABEL_KEYS.items()
        ]
        with self._driver.session() as session:
            for stmt in stmts:
                session.run(stmt).consume()

    @contextlib.contextmanager
    def transaction(self, timeout: float | None = None):
        """Run a group of statements in one server transaction.

        The graph writer fans out one event into several node/edge MERGEs
        (up to ~8 + 3x per committee); batching them cuts that from N
        round-trips to 1 and makes the whole event atomically visible.

        Yields a ``run(query, **params)`` callable (fire-and-forget within
        the transaction); any exception rolls the group back.
        """
        with self._driver.session() as session:
            with session.begin_transaction(timeout=self._resolve_timeout(timeout)) as tx:

                def run(query: str, **params) -> None:
                    tx.run(query, **params)

                yield run
