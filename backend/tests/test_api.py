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


@pytest.fixture(autouse=True)
def _clear_graph_cache():
    """The /api/graph payload cache is module-level and keyed by ``limit``;
    without clearing it, one test's payload (real or boom-mock) leaks into the
    next test's assertions."""
    from app.api import routes

    routes._graph_cache.clear()
    yield
    routes._graph_cache.clear()


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


def test_json_safe_recurses_into_rows():
    """collect(...) projections nest values; whole row lists must come out
    JSON-encodable (this is what /api/query returns)."""
    from datetime import date, datetime, timezone

    from neo4j.time import DateTime as Neo4jDateTime

    from app.api.routes import _json_safe

    rows = [
        {"ticker": "NVDA", "at": Neo4jDateTime(2026, 9, 10, 12, 0, 0)},
        {"sector": "Tech", "tickers": ["NVDA", "AMD"],
         "d": datetime(2026, 1, 1, tzinfo=timezone.utc), "day": date(2026, 1, 2)},
    ]
    import json

    safe = _json_safe(rows)
    json.dumps(safe)  # must not raise
    assert safe[0]["at"] == "2026-09-10T12:00:00.000000000"
    assert safe[1]["tickers"] == ["NVDA", "AMD"]
    assert safe[1]["day"] == "2026-01-02"


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
        checks = res.json()["checks"]
        assert {"postgres", "neo4j"} <= set(checks)
        assert checks["postgres"]["status"] == "ok"
        assert checks["neo4j"]["status"] == "ok"
        if "llm" in checks:  # present iff a real model name is configured
            assert checks["llm"]["status"] == "ok"


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

    # Dated fixtures (2020/2030) would otherwise skew ordering assertions on
    # subsequent runs — clean up after ourselves.
    neo4j_store.run(
        "MATCH (e:Event) WHERE e.tweet_id IN $ids DETACH DELETE e",
        ids=["g-limit-old", "g-limit-new"],
    )


def test_graph_excludes_tombstoned_politicians(postgres_store, neo4j_store):
    """IdentityEngine tombstones the merged-away node (merged_into set); the
    /api/graph view must not surface it — only the canonical remains."""
    from datetime import date

    from app.graph.identity import IdentityEngine
    from app.graph.resolution import EntityResolver
    from app.graph.writer import GraphWriter

    cand_id, canon_id = "X080", "X081"
    resolver = EntityResolver(postgres_store)
    postgres_store.ensure_alias("Politician", "John Johnson", cand_id, "John Johnson", source="seed")
    postgres_store.ensure_alias("Politician", "John Johnsen", canon_id, "John Johnsen", source="seed")
    resolver.suggest_merges("Politician")
    queue_id = resolver.pending_reviews()[0]["id"]
    assert resolver.approve(queue_id)

    writer = GraphWriter(neo4j_store)
    # Future event_time keeps the fixtures inside the "most recent N events"
    # window of the /api/graph?limit=N render regardless of live scheduler
    # ingesting newer events while this long integration test runs.
    evt_a = writer.write_event(
        CongressTradeEvent(
            tweet_id="gt-x080",
            tweet_url="https://x.com/congress_trades/status/gt-x080",
            confidence=0.95,
            politician_name="John Johnson",
            politician_id=cand_id,
            chamber="senate",
            party="D",
            committee_ids=["SSBK"],
            ticker="NVDA",
            transaction_type="buy",
            amount_range="$1,001 - $15,000",
            transaction_date=date(2026, 1, 15),
            event_time=datetime(2031, 1, 1, tzinfo=timezone.utc),
        )
    )
    evt_b = writer.write_event(
        CongressTradeEvent(
            tweet_id="gt-x081",
            tweet_url="https://x.com/congress_trades/status/gt-x081",
            confidence=0.95,
            politician_name="John Johnsen",
            politician_id=canon_id,
            chamber="senate",
            party="D",
            committee_ids=["SSHR"],
            ticker="AMD",
            transaction_type="buy",
            amount_range="$1,001 - $15,000",
            transaction_date=date(2026, 1, 16),
            event_time=datetime(2031, 1, 1, tzinfo=timezone.utc),
        )
    )
    try:
        IdentityEngine(resolver, neo4j_store).apply_merge(queue_id, "Politician")

        # Unique limit keeps the module-level graph cache out of the way.
        with _client() as c:
            body = c.get("/api/graph?limit=137").json()
        keys = {(n["label"], n["key"]) for n in body["nodes"]}
        assert ("Politician", canon_id) in keys
        assert ("Politician", cand_id) not in keys
    finally:
        neo4j_store.run(
            "MATCH (p:Politician) WHERE p.id IN $ids DETACH DELETE p",
            ids=[cand_id, canon_id],
        )
        for event_id in (evt_a, evt_b):
            neo4j_store.run("MATCH (e:Event {id: $id}) DETACH DELETE e", id=event_id)
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

    # This test's 2031-dated event would otherwise outrank every other test's
    # "newest" fixture on subsequent runs — clean up after ourselves.
    neo4j_store.run(
        "MATCH (e:Event) WHERE e.tweet_id IN $ids DETACH DELETE e",
        ids=["g-unparsed-1", "g-typed-recent"],
    )


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


def test_pipeline_writer_is_wired_with_entity_resolver(postgres_store, monkeypatch):
    """The production pipeline's GraphWriter must resolve Politician/Insider
    names through the alias registry, sharing the store instance (no second
    engine pool). Selective: needs only real Postgres; the Neo4j driver is
    constructed lazily and never queried here."""
    import app.api.routes as routes
    from app.graph.resolution import EntityResolver

    monkeypatch.setattr(routes, "_get_store", lambda: postgres_store)
    monkeypatch.setattr(routes, "_store", None)
    monkeypatch.setattr(routes, "_neo4j", None)
    monkeypatch.setattr(routes, "_llm", None)

    pipeline = routes._get_processing()
    try:
        resolver = pipeline.writer.entity_resolver
        assert isinstance(resolver, EntityResolver)
        assert resolver._db is postgres_store
    finally:
        routes.close_services()


def test_stream_emits_new_rows_and_resumes_from_cursor(postgres_store):
    """Live SSE feed: catch-up rows first (oldest first), resumable cursors,
    keepalive when quiet. Drives the generator directly (HTTP streaming
    semantics belong to Starlette, not us)."""
    import asyncio
    import uuid

    from app.api.routes import stream_events

    marker = uuid.uuid4().hex[:8]
    postgres_store.insert_report(
        kind="brief", event_id=f"e-stream-{marker}", title="t", body="b"
    )

    async def collect(after_report=0, after_alert=0, max_polls=3):
        polls = {"n": 0}

        async def fake_disconnected():
            polls["n"] += 1
            return polls["n"] > max_polls

        return [
            chunk
            async for chunk in stream_events(
                postgres_store, fake_disconnected, after_report, after_alert,
                poll_seconds=0.01,
            )
        ]

    chunks = asyncio.run(collect())
    data = [c for c in chunks if "\ndata: " in c]
    assert len(data) == 1 and f"e-stream-{marker}" in data[0]
    assert data[0].startswith("id: r") and "\nevent: report\n" in data[0]
    assert any(c == ": keepalive\n\n" for c in chunks)

    # Resume past the row: only keepalives from here on.
    last_id = postgres_store.get_reports_since(0)[-1]["id"]
    resumed = asyncio.run(collect(after_report=last_id))
    assert resumed and all(c == ": keepalive\n\n" for c in resumed)


def test_signals_bundle_shape():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        body = c.get("/api/signals?window_days=365").json()
        for key in ("momentum", "clusters", "options_meter", "uncovered", "suspicious"):
            assert isinstance(body[key], list)
        assert body["window_days"] == 365


def test_signals_similar_returns_traders():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        body = c.get("/api/signals/similar?politician_id=W000187").json()
        assert isinstance(body["similar"], list)


# ---------------------------------------------------------------------------
# #16: /api/graph limit is bounded
# ---------------------------------------------------------------------------


def test_graph_limit_is_capped():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        assert c.get("/api/graph?limit=5001").status_code == 422
        assert c.get("/api/graph?limit=-1").status_code == 422
        assert c.get("/api/graph?limit=5000").status_code == 200


# ---------------------------------------------------------------------------
# #17: SSE polls run off the event loop
# ---------------------------------------------------------------------------


def test_stream_polls_run_off_the_event_loop():
    import asyncio

    from app.api.routes import stream_events

    class ThreadAwareStore:
        def __init__(self):
            self.saw_running_loop = False

        def get_reports_since(self, after):
            self._record()
            return []

        def get_alerts_since(self, after):
            self._record()
            return []

        def _record(self):
            try:
                asyncio.get_running_loop()
                self.saw_running_loop = True
            except RuntimeError:
                pass

    store = ThreadAwareStore()
    polls = {"n": 0}

    async def disconnected():
        polls["n"] += 1
        return polls["n"] >= 2

    async def collect():
        return [c async for c in stream_events(store, disconnected, 0, 0, poll_seconds=0.01)]

    chunks = asyncio.run(collect())
    assert any(c == ": keepalive\n\n" for c in chunks)
    assert not store.saw_running_loop  # blocking DB calls never ran on the loop


# ---------------------------------------------------------------------------
# #18: API key guard + rate limiting
# ---------------------------------------------------------------------------


def test_rate_limiter_sliding_window():
    from app.api.routes import _RateLimiter

    limiter = _RateLimiter(window_seconds=60, max_requests=3)
    key = "1.2.3.4"
    assert limiter.allow(key)
    assert limiter.allow(key)
    assert limiter.allow(key)
    assert not limiter.allow(key)  # window exhausted
    assert limiter.allow("other-ip")  # different key unaffected
    assert _RateLimiter(0, 3).allow(key)  # window 0 = unlimited


def test_api_key_guard_blocks_unauthenticated(monkeypatch):
    import app.api.routes as routes
    from app.config import get_settings

    real_settings = get_settings()

    def secure_settings():
        return real_settings.model_copy(update={"api_key": "secret-key"})

    monkeypatch.setattr(routes, "get_settings", secure_settings)

    with _client() as c:
        assert c.get("/api/graph").status_code == 401
        assert (
            c.get("/api/graph", headers={"Authorization": "Bearer wrong"}).status_code == 401
        )


def test_api_key_guard_accepts_valid_key(monkeypatch):
    if not _services_available():
        pytest.skip("postgres/neo4j not available")

    import app.api.routes as routes
    from app.config import get_settings

    real_settings = get_settings()

    def secure_settings():
        return real_settings.model_copy(update={"api_key": "secret-key"})

    monkeypatch.setattr(routes, "get_settings", secure_settings)

    with _client() as c:
        res = c.get("/api/graph", headers={"Authorization": "Bearer secret-key"})
        assert res.status_code == 200


def test_query_rate_limited_per_ip(monkeypatch):
    if not _services_available():
        pytest.skip("postgres/neo4j not available")

    import app.api.routes as routes
    from app.config import get_settings

    real_settings = get_settings()

    def throttled_settings():
        return real_settings.model_copy(
            update={"api_rate_window_seconds": 3600, "api_rate_max_requests": 2}
        )

    monkeypatch.setattr(routes, "get_settings", throttled_settings)
    monkeypatch.setattr(routes, "_rate_limiter", None)

    with _client() as c:
        assert c.get("/api/graph").status_code == 200
        assert c.get("/api/graph").status_code == 200
        assert c.get("/api/graph").status_code == 429


# ---------------------------------------------------------------------------
# Degradation: every surface returns a clean 500 with a stable body when its
# backend raises, so a transient DB/Neo4j/LLM failure never leaks a traceback.
# ---------------------------------------------------------------------------


class _Neo4jBoom:
    def ensure_indexes(self):
        pass

    def run(self, *a, **k):
        raise RuntimeError("neo4j exploded")

    def run_read(self, *a, **k):
        raise RuntimeError("neo4j exploded")


class _StoreBoom:
    def __init__(self, *a, **k):
        pass

    def ensure_rule(self, *a, **k):
        pass

    def get_alerts(self, **k):
        raise RuntimeError("postgres exploded")

    def get_reports(self, **k):
        raise RuntimeError("postgres exploded")

    def get_leaderboard(self, **k):
        raise RuntimeError("postgres exploded")

    def get_reports_since(self, after):
        raise RuntimeError("postgres exploded")

    def get_alerts_since(self, after):
        raise RuntimeError("postgres exploded")

    class engine:
        def dispose(self):
            raise RuntimeError("dispose exploded")


def _quiet_settings(monkeypatch):
    """Point both main.lifespan and routes at settings with the scheduler and
    alert seeding switched off, so failure-path tests don't spin up live jobs."""
    from app.config import get_settings

    quiet = get_settings().model_copy(
        update={"scheduler_enabled": False, "seed_default_alerts": False}
    )
    monkeypatch.setattr("app.main.get_settings", lambda: quiet)
    monkeypatch.setattr("app.api.routes.get_settings", lambda: quiet)
    return quiet


def _boom_app(monkeypatch) -> None:
    import app.api.routes as routes

    _quiet_settings(monkeypatch)
    monkeypatch.setattr(routes, "_get_neo4j", lambda: _Neo4jBoom())
    monkeypatch.setattr(routes, "_get_store", lambda: _StoreBoom())


def test_graph_export_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)
    with _client() as c:
        res = c.get("/api/graph")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("graph export failed")


class _CountingNeo4j:
    def __init__(self):
        self.runs = 0

    def run(self, query: str, **k):
        self.runs += 1
        if query.startswith("MATCH (e:Event) RETURN count"):
            return [{"n": 0}]
        return []

    def ensure_indexes(self):
        pass


def test_graph_response_is_cached_within_ttl(monkeypatch):
    """Polling the force-directed endpoint must not replay the 4 Cypher
    round-trips on every request when caching is enabled."""
    import app.api.routes as routes

    _quiet_settings(monkeypatch)
    neo = _CountingNeo4j()
    monkeypatch.setattr(routes, "_get_neo4j", lambda: neo)

    with monkeypatch.context() as m:
        m.setattr(routes, "_graph_cache", {})

        with _client() as c:
            assert c.get("/api/graph?limit=20").status_code == 200
            assert c.get("/api/graph?limit=20").status_code == 200
            assert c.get("/api/graph?limit=500").status_code == 200
        # Each rebuild is count + keep_ids + (null-event_time pad, since the
        # mock's keep_ids are empty) + node fetch + link fetch = 5 Cypher
        # round-trips: limit=20 rebuilt once (2nd call cached), limit=500 once
        # = 10 runs total, not 15.
        assert neo.runs == 10


def test_graph_cache_disabled_when_ttl_zero(monkeypatch):
    """graph_cache_ttl_seconds=0 must bypass the cache entirely (the config
    kill-switch for anyone who wants live data on every poll)."""
    import app.api.routes as routes
    from app.config import get_settings

    _quiet_settings(monkeypatch)
    no_cache = get_settings().model_copy(update={"graph_cache_ttl_seconds": 0})
    monkeypatch.setattr("app.api.routes.get_settings", lambda: no_cache)
    neo = _CountingNeo4j()
    monkeypatch.setattr(routes, "_get_neo4j", lambda: neo)

    with _client() as c:
        assert c.get("/api/graph?limit=20").status_code == 200
        assert c.get("/api/graph?limit=20").status_code == 200
    assert neo.runs == 10  # two full rebuilds, nothing cached


def test_alerts_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)
    with _client() as c:
        res = c.get("/api/alerts")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("alerts fetch failed")


def test_reports_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)
    with _client() as c:
        res = c.get("/api/reports")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("reports fetch failed")


def test_leaderboard_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)
    with _client() as c:
        res = c.get("/api/leaderboard")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("leaderboard fetch failed")


def test_signals_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)

    class _SignalsBoom:
        def bundle(self, **k):
            raise RuntimeError("signals exploded")

        def similar(self, **k):
            raise RuntimeError("signals exploded")

    import app.api.routes as routes

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _SignalsBoom())
    with _client() as c:
        assert c.get("/api/signals").status_code == 500
        assert c.get("/api/signals/similar?politician_id=W000187").status_code == 500


def test_digest_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)

    class _DigestBoom:
        def ensure_digest(self, **k):
            raise RuntimeError("digest exploded")

    import app.api.routes as routes

    monkeypatch.setattr(routes, "get_report_engine", lambda: _DigestBoom())
    with _client() as c:
        res = c.get("/api/reports/digest")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("digest failed")


def test_query_route_returns_payload(monkeypatch):
    """/api/query happy path with a stubbed agent: no live LLM needed."""
    import app.api.routes as routes

    class FakeAgent:
        def __init__(self, llm, store):
            self.llm, self.store = llm, store

        def ask(self, question):
            return type(
                "Res",
                (),
                {"as_dict": lambda self: {"answer": "a", "rows": [{"ticker": "NVDA"}], "sources": [], "allowed": True, "has_data": True, "error": None}},
            )()

    monkeypatch.setattr(routes, "QueryAgent", FakeAgent)
    monkeypatch.setattr(routes, "_get_llm", lambda: object())
    monkeypatch.setattr(routes, "_get_neo4j", lambda: object())
    _quiet_settings(monkeypatch)

    with _client() as c:
        res = c.post("/api/query", json={"question": "What did Warren buy?"})
        assert res.status_code == 200
        assert res.json()["rows"] == [{"ticker": "NVDA"}]


def test_query_route_error_is_json_500(monkeypatch):
    import app.api.routes as routes

    class BoomAgent:
        def __init__(self, llm, store):
            pass

        def ask(self, question):
            raise RuntimeError("agent exploded")

    monkeypatch.setattr(routes, "QueryAgent", BoomAgent)
    monkeypatch.setattr(routes, "_get_llm", lambda: object())
    monkeypatch.setattr(routes, "_get_neo4j", lambda: object())
    _quiet_settings(monkeypatch)

    with _client() as c:
        res = c.post("/api/query", json={"question": "hi"})
        assert res.status_code == 500
        assert res.json()["detail"].startswith("query agent failed")


def test_stream_breaks_cleanly_when_store_raises():
    """A backend failure mid-stream terminates the SSE generator without
    throwing — the client just reconnects and resumes from its cursor."""
    import asyncio

    from app.api.routes import stream_events

    store = _StoreBoom()

    async def disconnected():
        return False

    async def collect():
        return [c async for c in stream_events(store, disconnected, 0, 0, poll_seconds=0.01)]

    assert asyncio.run(collect()) == []


def test_stream_emits_alert_and_report_rows():
    import asyncio

    from app.api.routes import stream_events

    class RowStore:
        def __init__(self):
            self.calls = 0

        def get_reports_since(self, after):
            return [{"id": 1, "kind": "brief", "event_id": "e1", "title": "t", "body": "b"}]

        def get_alerts_since(self, after):
            return [{"id": 2, "level": "high", "event_id": "e2", "summary": "s", "kind": "k"}]

    polls = {"n": 0}

    async def disconnected():
        polls["n"] += 1
        return polls["n"] >= 2

    async def collect():
        return [c async for c in stream_events(RowStore(), disconnected, 0, 0, poll_seconds=0.01)]

    chunks = asyncio.run(collect())
    reports = [c for c in chunks if "\nevent: report\n" in c]
    alerts = [c for c in chunks if "\nevent: alert\n" in c]
    assert reports and reports[0].startswith("id: r1")
    assert alerts and alerts[0].startswith("id: a2")


def test_client_ip_honors_forwarded_header():
    import app.api.routes as routes
    from fastapi import Request
    from starlette.datastructures import Headers

    req = Request(
        {
            "type": "http",
            "headers": Headers({"x-forwarded-for": "203.0.113.7, 10.0.0.1"}).raw,
            "client": ("10.0.0.1", 12345),
        }
    )
    assert routes._client_ip(req) == "203.0.113.7"


def test_client_ip_falls_back_to_peer_for_spoofed_forwarded_header():
    """Garbage in X-Forwarded-For must not become a rate-limit key: a client
    could otherwise rotate keys to defeat the limiter and its eviction sweep."""
    import app.api.routes as routes
    from fastapi import Request
    from starlette.datastructures import Headers

    def req(xff: str):
        return Request(
            {
                "type": "http",
                "headers": Headers({"x-forwarded-for": xff}).raw,
                "client": ("203.0.113.9", 12345),
            }
        )

    for spoofed in ["not-an-ip", "evil.example", "203.0.113.7 garbage", ":::" , ""]:
        assert routes._client_ip(req(spoofed)) == "203.0.113.9"
    # a valid IPv6 address is accepted as a bonafide proxy-derived peer
    assert routes._client_ip(req("2001:db8::1, 10.0.0.1")) == "2001:db8::1"


def test_rate_limiter_prunes_stale_keys_on_sweep(monkeypatch):
    """The sweep only runs past 4096 total keys, but then it evicts every
    entry older than the window — so a stale flood can never pin the map."""
    import app.api.routes as routes

    clock = {"now": 0.0}

    def fake_now():
        return clock["now"]

    monkeypatch.setattr(routes.time, "monotonic", fake_now)
    monkeypatch.setattr(routes, "_rate_limiter", None)

    limiter = routes._RateLimiter(window_seconds=60, max_requests=2)
    for i in range(5000):
        limiter.allow(f"stale-{i}")
    assert len(limiter._hits) == 5000  # stays resident inside the window

    clock["now"] += 120.0  # every entry is now older than the window
    for _ in range(3):  # hammer one hot key -> 3rd call is a denial -> sweep
        limiter.allow("hot")

    assert set(limiter._hits) == {"hot"}  # stale-* entries all evicted


def test_close_services_survives_close_failures(monkeypatch):
    import app.api.routes as routes

    class _BadNeo4j:
        def close(self):
            raise RuntimeError("close exploded")

    class _BadStore:
        class engine:
            def dispose(self):
                raise RuntimeError("dispose exploded")

    class _BadLLM:
        def close(self):
            raise RuntimeError("llm close exploded")

    monkeypatch.setattr(routes, "_neo4j", _BadNeo4j())
    monkeypatch.setattr(routes, "_store", _BadStore())
    monkeypatch.setattr(routes, "_llm", _BadLLM())

    routes.close_services()  # must not raise

    assert routes._neo4j is None and routes._store is None and routes._llm is None


# ---------------------------------------------------------------------------
# Remaining route branches + engine factories
# ---------------------------------------------------------------------------


def test_node_key_falls_back_when_label_key_missing():
    from app.api.routes import _node_key

    # Ticker's label key is `ticker`; absent props fall back to id, then repr.
    assert _node_key("Ticker", {"ticker": "NVDA"}) == "NVDA"
    assert _node_key("Ticker", {"id": "evt-1"}) == "evt-1"
    fallback = _node_key("Ticker", {"anything": 1})
    assert fallback.startswith("{")


def test_reports_digest_leaderboard_success_paths(monkeypatch):
    """The happy-path returns for /api/reports, /api/reports/digest and
    /api/leaderboard (the error 500s are already covered elsewhere)."""
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _Store:
        @staticmethod
        def get_reports(limit=20, kind=None):
            return ["brief-row"] if kind is None else [f"{kind}-row"]

        @staticmethod
        def get_leaderboard(limit=20):
            return [{"politician": "Zed Zed"}]

    monkeypatch.setattr(routes, "_get_store", lambda: _Store())

    class _Digest:
        @staticmethod
        def ensure_digest(period=None):
            return {"id": 8, "period": "2026-09-14"}

    monkeypatch.setattr(routes, "get_report_engine", lambda: _Digest())

    with _client() as c:
        all_reports = c.get("/api/reports").json()
        assert all_reports == {"reports": ["brief-row"], "count": 1}

        digests = c.get("/api/reports?kind=digest").json()
        assert digests["reports"] == ["digest-row"]

        digest = c.get("/api/reports/digest").json()
        assert digest["report"]["id"] == 8

        leaders = c.get("/api/leaderboard").json()
        assert leaders["count"] == 1 and leaders["leaders"][0]["politician"] == "Zed Zed"


def test_engine_factories_wire_real_dependencies(monkeypatch):
    """The lazy factories build production engines against whatever getters are
    wired — here stubbed out, so no DB clients are constructed."""
    import app.api.routes as routes

    _quiet_settings(monkeypatch)
    monkeypatch.setattr(routes, "_get_store", lambda: object())
    monkeypatch.setattr(routes, "_get_neo4j", lambda: object())
    monkeypatch.setattr(routes, "_get_llm", lambda: object())

    from app.alerts.engine import AlertEngine
    from app.pipeline import ProcessingPipeline
    from app.reports.engine import ReportEngine

    assert isinstance(routes.get_alert_engine(), AlertEngine)
    assert isinstance(routes.get_report_engine(), ReportEngine)
    assert isinstance(routes._get_processing(), ProcessingPipeline)


def test_stream_route_wraps_events_in_sse_response(monkeypatch):
    """The /api/stream route wires the events generator into an SSE streaming
    response. Called directly (not over TestClient: infinite streams deadlock
    its transport), with an immediately-disconnected request so the generator
    yields the keepalive chunk once and terminates."""
    import asyncio

    import app.api.routes as routes
    from starlette.responses import StreamingResponse

    from app.api.routes import stream

    _quiet_settings(monkeypatch)

    class _EmptyStore:
        @staticmethod
        def get_reports_since(last_id, limit=50):
            return []

        @staticmethod
        def get_alerts_since(last_id, limit=50):
            return []

    monkeypatch.setattr(routes, "_get_store", lambda: _EmptyStore())

    class _Request:
        def __init__(self):
            self._n = 0

        async def is_disconnected(self):
            self._n += 1
            return self._n > 1  # one quiet poll, then disconnect

    async def run():
        response = await stream(
            request=_Request(), after_report=0, after_alert=0, poll_seconds=1.0
        )
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"
        chunks = [c async for c in response.body_iterator]
        return response, chunks

    response, chunks = asyncio.run(run())
    assert any("keepalive" in c for c in chunks)


def test_lifespan_starts_and_stops_scheduler(monkeypatch):
    """With scheduler_enabled=True the lifespan boots and later stops a real
    (here stubbed) scheduler instance."""
    from app.config import Settings

    captured = {"started": False, "stopped": False}

    class _Recorder:
        def start(self):
            captured["started"] = True

        def stop(self):
            captured["stopped"] = True

    enabled = _quiet_settings(monkeypatch).model_copy(
        update={"scheduler_enabled": True}
    )
    monkeypatch.setattr("app.main.get_settings", lambda: enabled)
    monkeypatch.setattr("app.api.routes.get_settings", lambda: enabled)
    monkeypatch.setattr("app.main._build_scheduler", lambda s: _Recorder())

    with _client() as c:
        try:
            assert c.get("/health").status_code == 200
        finally:
            pass

    assert captured["started"] is True
    assert captured["stopped"] is True
