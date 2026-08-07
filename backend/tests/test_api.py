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
from app.models.events import CongressTradeEvent, OptionsFlowEvent, UnparsedEvent


def test_json_safe_serializes_native_temporal_props():
    """The writer stores temporal props natively; /api/graph must hand the JSON
    encoder plain ISO strings, including for the Neo4j driver's own temporal
    classes (which do NOT subclass stdlib datetime/date)."""
    from datetime import date, datetime, timezone

    from neo4j.time import Date as Neo4jDate
    from neo4j.time import DateTime as Neo4jDateTime

    from app.api.routes import _json_safe

    assert _json_safe(Neo4jDateTime(2026, 1, 16, 12, 30, 0)) == "2026-01-16T12:30:00.000000000"
    assert _json_safe(Neo4jDate(2026, 1, 15)) == "2026-01-15"
    assert _json_safe(datetime(2026, 1, 16, tzinfo=timezone.utc)) == "2026-01-16T00:00:00+00:00"
    assert _json_safe(date(2026, 1, 15)) == "2026-01-15"
    assert _json_safe("NVDA") == "NVDA"
    assert _json_safe(42) == 42


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


def test_health_reports_llm_down_when_configured(monkeypatch):
    if not _services_available():
        pytest.skip("postgres/neo4j not available")

    import app.main as main
    from app.config import Settings

    class DownLLM:
        base_url = "http://llm/v1"

        def health(self):
            return False

    # A real model name is configured, so /health must probe the LLM.
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: Settings(_env_file=None, llm_model_name="Meta-Llama-3-8B"),
    )
    monkeypatch.setattr(main.routes, "_get_llm", lambda: DownLLM())

    with _client() as c:
        res = c.get("/health")
        body = res.json()
        assert res.status_code == 503
        assert body["checks"]["llm"]["status"] == "down"
        assert body["checks"]["postgres"]["status"] == "ok"


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


def test_graph_limit_prefers_typed_events_over_unparsed_without_event_time(neo4j_store):
    """UnparsedEvent has no ``event_time``; the limit query falls back to a
    1900 sentinel so those events always sort as the oldest and never claim the
    recent-view slots over genuinely recent typed events."""
    from app.graph.writer import GraphWriter

    writer = GraphWriter(neo4j_store)
    unparsed_id = writer.write_event(
        UnparsedEvent(
            tweet_id="g-unparsed-1",
            tweet_url="https://x.com/unusual_whales/status/g-unparsed-1",
            confidence=0.3,
            raw_text="some unparsable tweet",
            reason="low confidence",
        )
    )
    writer.write_event(
        OptionsFlowEvent(
            tweet_id="g-typed-recent",
            tweet_url="https://x.com/unusual_whales/status/g-typed-recent",
            confidence=0.9,
            ticker="NVDA",
            contract_type="call",
            event_time=datetime(2031, 6, 1, tzinfo=timezone.utc),
        )
    )

    with _client() as c:
        body = c.get("/api/graph?limit=1").json()
        titles = {n["title"] for n in body["nodes"]}
        assert "NVDA" in titles  # newest typed event keeps its slot
        assert unparsed_id not in titles  # sentinel fallback must sort as oldest


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


def test_app_lifecycle_does_not_leak_extra_postgres_engine(monkeypatch):
    """Startup must reuse the shared store (routes._get_store) so each app
    lifecycle disposes exactly the engines it created."""
    if not _services_available():
        pytest.skip("postgres/neo4j not available")

    import app.api.routes as routes
    import app.db.postgres as postgres_module

    created = {"n": 0}
    original = postgres_module.PostgresStore

    class CountingStore(original):
        def __init__(self, dsn):
            created["n"] += 1
            super().__init__(dsn)

    monkeypatch.setattr(postgres_module, "PostgresStore", CountingStore)
    monkeypatch.setattr(routes, "PostgresStore", CountingStore)
    monkeypatch.setattr(routes, "_store", None)  # force lazy creation this cycle

    with _client() as c:
        assert c.get("/health").status_code == 200

    assert created["n"] == 1
