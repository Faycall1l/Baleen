"""Neo4j store: server-enforced transaction timeouts."""

from __future__ import annotations

import pytest

from app.config import Settings


def _settings():
    return Settings(
        _env_file=None,
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="baleen_dev_pass",
    )


def _available(store) -> bool:
    try:
        store.run("RETURN 1")
        return True
    except Exception:
        return False


def test_query_timeout_kills_long_running_statement():
    from app.db.neo4j import Neo4jStore

    store = Neo4jStore(_settings())
    if not _available(store):
        pytest.skip("neo4j not available")
    try:
        with pytest.raises(Exception) as excinfo:
            store.run(
                "UNWIND range(1,10000000) AS x WITH sum(x) AS s RETURN s",
                timeout=0.2,
            )
        assert "TransactionTimedOut" in str(excinfo.value)
    finally:
        store.close()


def test_configured_cap_applies_to_default_calls():
    """The NEO4J_QUERY_TIMEOUT_SECONDS setting must cap plain run() calls,
    not just explicit per-call timeouts."""
    from app.db.neo4j import Neo4jStore

    store = Neo4jStore(Settings(_env_file=None, neo4j_query_timeout_seconds=1))
    if not _available(store):
        pytest.skip("neo4j not available")
    try:
        # 60M rows takes ~3.3s uncapped: far past the 1s cap regardless of
        # machine load, so the timeout is not flaky.
        with pytest.raises(Exception) as excinfo:
            store.run("UNWIND range(1,60000000) AS x WITH sum(x) AS s RETURN s")
        assert "TransactionTimedOut" in str(excinfo.value)
    finally:
        store.close()


def test_default_timeout_from_settings():
    from app.db.neo4j import Neo4jStore

    settings = Settings(_env_file=None, neo4j_query_timeout_seconds=15)
    store = Neo4jStore(settings)
    assert store._timeout == 15
    store.close()

    uncapped = Neo4jStore(Settings(_env_file=None, neo4j_query_timeout_seconds=0))
    assert uncapped._timeout is None
    uncapped.close()


def test_query_timeout_config_bounds():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, neo4j_query_timeout_seconds=-1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, neo4j_query_timeout_seconds=100000)
