"""Shared pytest fixtures for the Baleen backend.

Tests that require live services (Postgres, Neo4j) connect on demand and are
skipped when the service is unreachable, so unit tests always run.
"""

from __future__ import annotations

import os

import pytest

from app.config import Settings
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
    yield store
    store.engine.dispose()


class FakeTweetSource:
    """In-memory TweetSource used to drive the pipeline deterministically."""

    def __init__(self, tweets=None, errors=None):
        self.tweets = list(tweets or [])
        self.errors = list(errors or [])
        self.calls = 0

    def fetch_recent(self, handles, since_seconds):
        self.calls += 1
        return _fetch_result(self.tweets, self.errors)


def _fetch_result(tweets, errors):
    from app.ingestion.source import FetchResult

    result = FetchResult()
    result.tweets = list(tweets)
    result.errors = list(errors)
    return result
