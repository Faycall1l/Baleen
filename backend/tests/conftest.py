"""Shared pytest fixtures for the Baleen backend.

Tests that require live services (Postgres, Neo4j) connect on demand and are
skipped when the service is unreachable, so unit tests always run.
"""

from __future__ import annotations

import os

import pytest

# Keep the app-level background scheduler out of tests regardless of .env.
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SEED_DEFAULT_ALERTS"] = "true"

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

        conn.execute(text("TRUNCATE raw_tweets, alerts, alert_rules, reports"))
    yield store
    store.engine.dispose()


@pytest.fixture
def neo4j_store():
    """Neo4jStore for integration tests. Skipped when Neo4j is unreachable.

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
