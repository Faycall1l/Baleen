"""Smoke tests for the FastAPI app (the three UI surfaces).

Exercises startup (lifespan), health, and the graph/alerts routes. Requires the
Postgres and Neo4j containers (skipped when unreachable). The query route needs
a live LLM, so it is not exercised here.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.models.events import CongressTradeEvent, OptionsFlowEvent


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
        res = c.get("/health")
        assert res.json()["app"] == "baleen"
        if not _services_available():
            assert res.status_code == 503
            return
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
        assert set(res.json()["checks"]) == {"postgres", "neo4j"}
        assert res.json()["checks"]["postgres"]["status"] == "ok"
        assert res.json()["checks"]["neo4j"]["status"] == "ok"


def test_graph_export():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        body = c.get("/api/graph").json()
        assert "nodes" in body and "links" in body
        assert body["node_count"] == len(body["nodes"])
        assert body["event_count"] >= 0
        assert "truncated" in body


def test_graph_limit_truncates_to_recent_events(neo4j_store):
    from app.graph.writer import GraphWriter

    writer = GraphWriter(neo4j_store)
    old = CongressTradeEvent(
        tweet_id="g-limit-old",
        tweet_url="https://x.com/congress_trades/status/g-limit-old",
        confidence=0.95,
        politician_name="Old Person",
        politician_id="P1",
        chamber="house",
        party="D",
        committee_ids=["HSBA"],
        ticker="NVDA",
        transaction_type="buy",
        amount_range="$1,001 - $15,000",
        transaction_date=date(2020, 1, 1),
        event_time=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    recent = OptionsFlowEvent(
        tweet_id="g-limit-new",
        tweet_url="https://x.com/unusual_whales/status/g-limit-new",
        confidence=0.9,
        ticker="ZZZZ",
        contract_type="call",
        strike=250.0,
        sentiment="bullish",
        event_time=datetime(2030, 6, 1, tzinfo=timezone.utc),
    )
    writer.write_event(old)
    writer.write_event(recent)

    with _client() as c:
        full = c.get("/api/graph?limit=0").json()
        assert full["event_count"] >= 2

        partial = c.get("/api/graph?limit=1").json()
        assert partial["event_count"] == full["event_count"]
        assert partial["truncated"] is True
        assert len(partial["nodes"]) < len(full["nodes"])
        titles = {n["title"] for n in partial["nodes"]}
        assert "ZZZZ" in titles  # newest event + its neighborhood
        assert "Old Person" not in titles  # oldest event excluded


def test_alerts_list():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        body = c.get("/api/alerts").json()
        assert "alerts" in body and "count" in body
        assert body["count"] == len(body["alerts"])


def test_alerts_limit_is_bounded():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        assert c.get("/api/alerts?limit=0").status_code == 422
        assert c.get("/api/alerts?limit=1000").status_code == 422
        assert c.get("/api/alerts?limit=50").status_code == 200
