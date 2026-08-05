"""Smoke tests for the FastAPI app (the three UI surfaces).

Exercises startup (lifespan), health, and the graph/alerts routes. Requires the
Postgres and Neo4j containers (skipped when unreachable). The query route needs
a live LLM, so it is not exercised here.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore


def _client():
    from app.main import app

    return TestClient(app)


def _services_available() -> bool:
    from app.config import get_settings

    settings = get_settings()
    try:
        store = PostgresStore(settings.postgres_dsn)
        store.engine.connect()
        neo4j = Neo4jStore(settings)
        neo4j.run("RETURN 1")
        neo4j.close()
        store.engine.dispose()
        return True
    except Exception:
        return False


def test_health():
    with _client() as c:
        assert c.get("/health").json() == {"status": "ok", "app": "baleen"}


def test_graph_export():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        body = c.get("/api/graph").json()
        assert "nodes" in body and "links" in body
        assert body["node_count"] == len(body["nodes"])


def test_alerts_list():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        body = c.get("/api/alerts").json()
        assert "alerts" in body and "count" in body
        assert body["count"] == len(body["alerts"])
