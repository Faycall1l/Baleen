"""Shared pytest fixtures for the Baleen backend.

Tests that require live services (Postgres, Neo4j) connect on demand and are
skipped when the service is unreachable, so unit tests always run.

Neo4j isolation (#24): the integration suite prefers the dedicated
``neo4j-test`` container (bolt://localhost:7690, see docker-compose.yml) so
tests never mutate the live graph the scheduler writes to. When that container
is not running, the suite falls back to the regular instance on 7687 so plain
``pytest`` keeps working out of the box.
"""

from __future__ import annotations

import os
import socket

import pytest

# Keep the app-level background scheduler out of tests regardless of .env.
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SEED_DEFAULT_ALERTS"] = "true"

# Promote the whole test process to the isolated test Neo4j when it is up
# (respecting an explicitly exported NEO4J_URI). Must run before pydantic
# Settings is first instantiated (get_settings is lru-cached).
_TEST_NEO4J_BOLT = "bolt://localhost:7690"


def _prefer_test_neo4j_if_available() -> None:
    try:
        with socket.create_connection(("127.0.0.1", 7690), timeout=1.0):
            os.environ.setdefault("NEO4J_URI", _TEST_NEO4J_BOLT)
            os.environ.setdefault("NEO4J_READ_URI", _TEST_NEO4J_BOLT)
            # Promoting the URI alone builds a read driver with the default
            # read-only-role credentials, which are not provisioned in dev —
            # every run_read then dies on auth. Point the read role at the
            # same credentials so reads keep working under pytest.
            os.environ.setdefault("NEO4J_READ_USER", os.getenv("NEO4J_USER", "neo4j"))
            os.environ.setdefault(
                "NEO4J_READ_PASSWORD", os.getenv("NEO4J_PASSWORD", "baleen_dev_pass")
            )
    except OSError:
        pass  # dev fallback: tests run against the regular 7687 instance


_prefer_test_neo4j_if_available()

from app.config import Settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://baleen:baleen@localhost:5432/baleen_test",
)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def postgres_store():
    """PostgresStore against a dedicated ``baleen_test`` database.

    Skipped when Postgres is unreachable (e.g. before ``docker-compose up``).
    """
    try:
        store = PostgresStore(TEST_DATABASE_URL)
        store.engine.connect()
    except Exception as exc:  # pragma: no cover - depends on environment
        pytest.skip(f"postgres not available: {exc}")

    store.init_schema()
    with store.engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(
            text(
                "TRUNCATE raw_tweets, alerts, alert_rules, reports, "
                "entity_aliases, entity_resolution_queue, entity_identities"
            )
        )
    yield store
    store.engine.dispose()


@pytest.fixture
def neo4j_store():
    """Neo4jStore for integration tests. Skipped when Neo4j is unreachable.

    Targets the isolated ``neo4j-test`` instance (7688) when it is up — the
    suite then never touches the live graph the scheduler writes to (#24) —
    and falls back to the regular instance otherwise.

    The read-only role (see docs/SETUP.md) is not provisioned in CI, so the
    test store points the read driver at the same admin credentials. The
    read-only guardrail itself is enforced by the Cypher allowlist.
    """
    from app.config import Settings

    settings = Settings(
        _env_file=None,
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", "baleen_dev_pass"),
        neo4j_read_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_read_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_read_password=os.getenv("NEO4J_PASSWORD", "baleen_dev_pass"),
    )
    try:
        store = Neo4jStore(settings)
        store.run("RETURN 1")
    except Exception as exc:  # pragma: no cover - depends on environment
        pytest.skip(f"neo4j not available: {exc}")

    yield store
    store.close()


@pytest.fixture
def future_event_time():
    """A fixed future timestamp for graph-visibility fixtures.

    The live scheduler ingests new events minute-by-minute into the shared
    graph (see #21), so fixtures that must appear inside the
    ``/api/graph?limit=N`` "most recent N events" window cannot rely on "now" —
    by the time a slow integration test renders the graph, newer events have
    pushed the fixtures out (#24). Events seeded with a far-future ``event_time``
    always sort newest; every such fixture must clean up after itself.
    """
    from datetime import datetime, timezone

    return datetime(2031, 1, 1, tzinfo=timezone.utc)


class StubLLM:
    """Deterministic LLM stub for extraction tests.

    ``rules`` maps a substring of the tweet text to an :class:`LLMResponse`
    (or a list of responses consumed in order). Anything unmatched returns an
    empty response (no tool call). ``chat`` (plain completion) returns a canned
    summary.
    """

    def __init__(self, rules=None, summary="Stub alert summary with source link."):
        self.rules = list(rules or [])
        self.summary = summary

    def chat(self, messages, **kwargs):
        from app.extraction.client import LLMResponse

        return LLMResponse(content=self.summary)

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        from app.extraction.client import LLMResponse

        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        for substring, response in self.rules:
            if substring in user:
                if isinstance(response, list):
                    if response:
                        return response.pop(0)
                    continue
                return response
        return LLMResponse(content="")


def stub_tool_call(name, arguments):
    from app.extraction.client import LLMResponse, ToolCall

    return LLMResponse(tool_calls=[ToolCall(name=name, arguments=arguments)])
