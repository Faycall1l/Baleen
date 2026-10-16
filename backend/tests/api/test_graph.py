"""Graph endpoint: payload shape, limits, tombstones, caching, and delta.

Part of the API suite split out of the former ``tests/test_api.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.models.events import CongressTradeEvent, OptionsFlowEvent, UnparsedEvent
from tests.api.conftest import (
    _boom_app,
    _client,
    _quiet_settings,
    _services_available,
)


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
        event_time=datetime(2020, 1, 1, tzinfo=UTC),
    )
    recent = OptionsFlowEvent(
        tweet_id="g-limit-new",
        tweet_url="https://x.com/unusual_whales/status/g-limit-new",
        confidence=0.9,
        ticker="ZZZZ",
        contract_type="call",
        strike=250.0,
        sentiment="bullish",
        event_time=datetime(2030, 6, 1, tzinfo=UTC),
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


def test_graph_excludes_tombstoned_politicians(postgres_store, neo4j_store, future_event_time):
    """IdentityEngine tombstones the merged-away node (merged_into set); the
    /api/graph view must not surface it — only the canonical remains."""

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
            event_time=future_event_time,
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
            event_time=future_event_time,
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
            event_time=datetime(2031, 6, 1, tzinfo=UTC),
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


def test_graph_limit_is_capped():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        assert c.get("/api/graph?limit=5001").status_code == 422
        assert c.get("/api/graph?limit=-1").status_code == 422
        assert c.get("/api/graph?limit=5000").status_code == 200


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
        # mock's keep_ids are empty) + combined node/link fetch = 4 Cypher
        # round-trips: limit=20 rebuilt once (2nd call cached), limit=500 once
        # = 8 runs total, not 12.
        assert neo.runs == 8


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
    assert neo.runs == 8  # two full rebuilds, nothing cached


def test_graph_delta_skips_count_cache_and_pad(monkeypatch):
    """A delta (?since=) must not run the total-events count, must bypass the
    cache, and must not pad with null-event_time events: 2 round-trips."""
    import app.api.routes as routes

    _quiet_settings(monkeypatch)
    neo = _CountingNeo4j()
    monkeypatch.setattr(routes, "_get_neo4j", lambda: neo)

    with monkeypatch.context() as m:
        m.setattr(routes, "_graph_cache", {})

        with _client() as c:
            first = c.get("/api/graph?limit=20&since=2026-01-01T00%3A00%3A00%2B00%3A00")
            assert first.status_code == 200
            body = first.json()
            assert body["delta"] is True
            assert body["event_count"] is None
            second = c.get("/api/graph?limit=20&since=2026-01-01T00%3A00%3A00%2B00%3A00")
            assert second.status_code == 200
        # keep-pass + combined fetch per delta, nothing cached, no count.
        assert neo.runs == 4


def test_graph_delta_garbage_since_falls_back_to_full(monkeypatch):
    """An unparseable ?since= must render full, not 422."""
    import app.api.routes as routes

    _quiet_settings(monkeypatch)
    neo = _CountingNeo4j()
    monkeypatch.setattr(routes, "_get_neo4j", lambda: neo)

    with _client() as c:
        res = c.get("/api/graph?limit=20&since=not-a-date")
        assert res.status_code == 200
        assert res.json().get("delta") is not True


def test_graph_delta_returns_only_newer_events(postgres_store, neo4j_store):
    """Live: events before the cursor stay out of the delta payload."""

    from app.graph.writer import GraphWriter

    def _evt(tweet_id: str, pid: str, ticker: str, when: datetime) -> CongressTradeEvent:
        return CongressTradeEvent(
            tweet_id=tweet_id,
            tweet_url=f"https://x.com/congress_trades/status/{tweet_id}",
            confidence=0.95,
            politician_name="Delta Dan",
            politician_id=pid,
            chamber="senate",
            party="D",
            committee_ids=[],
            ticker=ticker,
            transaction_type="buy",
            amount_range="$1 - $2",
            transaction_date=when.date(),
            event_time=when,
        )

    writer = GraphWriter(neo4j_store)
    old_id = writer.write_event(_evt("g-delta-old", "D900", "ZZD1", datetime(2026, 1, 10, tzinfo=UTC)))
    new_id = writer.write_event(_evt("g-delta-new", "D901", "ZZD2", datetime(2026, 3, 10, tzinfo=UTC)))
    try:
        with _client() as c:
            body = c.get("/api/graph?limit=100&since=2026-02-01T00%3A00%3A00%2B00%3A00").json()
        assert body["delta"] is True
        ids = {n["id"] for n in body["nodes"]}
        assert f"Event:{new_id}" in ids
        assert f"Event:{old_id}" not in ids
    finally:
        neo4j_store.run(
            "MATCH (e:Event) WHERE e.id IN $ids DETACH DELETE e",
            ids=[old_id, new_id],
        )
        neo4j_store.run(
            "MATCH (p:Politician) WHERE p.id IN $ids DETACH DELETE p",
            ids=["D900", "D901"],
        )
        neo4j_store.run(
            "MATCH (t:Ticker) WHERE t.ticker IN $ids DETACH DELETE t",
            ids=["ZZD1", "ZZD2"],
        )


def test_graph_export_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)
    with _client() as c:
        res = c.get("/api/graph")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("graph export failed")


def test_graph_path_rejects_unknown_labels():
    with _client() as c:
        res = c.get("/api/graph/path?from=Bogus%3AX&to=Ticker%3ANVDA")
        assert res.status_code == 422
        res = c.get("/api/graph/path?from=Ticker%3A&to=Ticker%3ANVDA")
        assert res.status_code == 422
        res = c.get("/api/graph/path?from=NoColonHere&to=Ticker%3ANVDA")
        assert res.status_code == 422
        assert "Label:key" in res.json()["detail"]


def test_graph_path_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)
    with _client() as c:
        res = c.get("/api/graph/path?from=Ticker%3ANVDA&to=Ticker%3AAMD")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("graph path failed")


def test_graph_path_shapes_a_found_path(monkeypatch):
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _PathNeo4j:
        def run_read(self, query, **k):
            assert "shortestPath" in query
            assert k == {"from_key": "NVDA", "to_key": "evt_1"}
            return [
                {
                    "nodes": [
                        {"labels": ["Ticker"], "props": {"ticker": "NVDA"}},
                        {
                            "labels": ["Event"],
                            "props": {"id": "evt_1", "type": "options_flow"},
                        },
                    ],
                    "rels": ["INVOLVES_TICKER"],
                }
            ]

        def run(self, *a, **k):
            raise AssertionError("path reads must use run_read")

    monkeypatch.setattr(routes, "_get_neo4j", lambda: _PathNeo4j())
    with _client() as c:
        body = c.get("/api/graph/path?from=Ticker%3ANVDA&to=Event%3Aevt_1").json()
    assert body["length"] == 1
    assert body["rels"] == ["INVOLVES_TICKER"]
    assert [n["id"] for n in body["nodes"]] == ["Ticker:NVDA", "Event:evt_1"]
    assert body["nodes"][0]["title"] == "NVDA"


def test_graph_path_empty_when_disconnected(monkeypatch):
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _EmptyNeo4j:
        def run_read(self, query, **k):
            return []

    monkeypatch.setattr(routes, "_get_neo4j", lambda: _EmptyNeo4j())
    with _client() as c:
        body = c.get("/api/graph/path?from=Ticker%3ANVDA&to=Ticker%3AAMD").json()
    assert body == {
        "from": "Ticker:NVDA",
        "to": "Ticker:AMD",
        "nodes": [],
        "rels": [],
        "length": 0,
    }


def test_graph_path_finds_live_connection(neo4j_store):
    """Live: a fresh event links its ticker in one hop."""
    from datetime import date

    from app.graph.writer import GraphWriter
    from app.models.events import CongressTradeEvent

    event = CongressTradeEvent(
        tweet_id="g-path-1",
        tweet_url="https://x.com/congress_trades/status/g-path-1",
        confidence=0.95,
        politician_name="Path Person",
        politician_id="PP1",
        chamber="house",
        party="D",
        committee_ids=[],
        ticker="ZZPQ",
        transaction_type="buy",
        amount_range="$1 - $2",
        transaction_date=date(2026, 1, 1),
        event_time=datetime(2026, 1, 2, tzinfo=UTC),
    )
    event_id = GraphWriter(neo4j_store).write_event(event)
    try:
        with _client() as c:
            body = c.get("/api/graph/path?from=Ticker%3AZZPQ&to=Event%3A" + event_id).json()
        assert body["length"] == 1
        assert body["rels"] == ["INVOLVES_TICKER"]
        assert body["nodes"][-1]["id"] == f"Event:{event_id}"
    finally:
        neo4j_store.run("MATCH (e:Event) WHERE e.id = $id DETACH DELETE e", id=event_id)
        neo4j_store.run("MATCH (t:Ticker) WHERE t.ticker = 'ZZPQ' DETACH DELETE t")


def test_graph_since_and_before_are_mutually_exclusive():
    with _client() as c:
        res = c.get("/api/graph?since=2026-01-01T00%3A00%3A00%2B00%3A00&before=2026-02-01T00%3A00%3A00%2B00%3A00")
        assert res.status_code == 400
        assert "mutually exclusive" in res.json()["detail"]


def test_graph_before_binds_upper_bound_and_echoes(monkeypatch):
    """The as-of bound reaches Cypher as a parameter (index-friendly text),
    bypasses the cache, and echoes back for the UI frame label."""
    import app.api.routes as routes

    _quiet_settings(monkeypatch)
    seen: list[tuple[str, dict]] = []

    class _RecordingNeo4j(_CountingNeo4j):
        def run(self, query: str, **k):
            seen.append((query, k))
            if "count(e)" in query:
                return [{"n": 0}]
            return super().run(query, **k)

    monkeypatch.setattr(routes, "_get_neo4j", lambda: _RecordingNeo4j())
    with _client() as c:
        first = c.get("/api/graph?limit=20&before=2026-02-01T00%3A00%3A00%2B00%3A00").json()
        second = c.get("/api/graph?limit=20&before=2026-02-01T00%3A00%3A00%2B00%3A00").json()

    assert first["delta"] is False
    assert first["before"] == "2026-02-01T00:00:00+00:00"
    assert first == second  # same content…
    keep_queries = [q for q, _ in seen if "ORDER BY e.event_time DESC" in q]
    # …but rebuilt, not cache-served: one keep-pass per request.
    assert len(keep_queries) == 2
    assert all("e.event_time <= datetime($before)" in q for q in keep_queries)
    params = [k for _, k in seen if "before" in k]
    assert params and all(p["before"] == "2026-02-01T00:00:00+00:00" for p in params)


def test_graph_before_returns_only_older_events(neo4j_store):
    """Live: an as-of render excludes events after the bound."""

    from app.graph.writer import GraphWriter

    def _evt(tweet_id: str, pid: str, ticker: str, when: datetime) -> CongressTradeEvent:
        return CongressTradeEvent(
            tweet_id=tweet_id,
            tweet_url=f"https://x.com/congress_trades/status/{tweet_id}",
            confidence=0.95,
            politician_name="Asa Archer",
            politician_id=pid,
            chamber="senate",
            party="D",
            committee_ids=[],
            ticker=ticker,
            transaction_type="buy",
            amount_range="$1 - $2",
            transaction_date=when.date(),
            event_time=when,
        )

    writer = GraphWriter(neo4j_store)
    old_id = writer.write_event(_evt("g-asof-old", "A900", "ZZA1", datetime(2026, 1, 10, tzinfo=UTC)))
    new_id = writer.write_event(_evt("g-asof-new", "A901", "ZZA2", datetime(2026, 3, 10, tzinfo=UTC)))
    try:
        with _client() as c:
            body = c.get("/api/graph?limit=100&before=2026-02-01T00%3A00%3A00%2B00%3A00").json()
        assert body["delta"] is False
        assert body["before"] == "2026-02-01T00:00:00+00:00"
        ids = {n["id"] for n in body["nodes"]}
        assert f"Event:{old_id}" in ids
        assert f"Event:{new_id}" not in ids
    finally:
        neo4j_store.run(
            "MATCH (e:Event) WHERE e.id IN $ids DETACH DELETE e",
            ids=[old_id, new_id],
        )
        neo4j_store.run(
            "MATCH (p:Politician) WHERE p.id IN $ids DETACH DELETE p",
            ids=["A900", "A901"],
        )
        neo4j_store.run(
            "MATCH (t:Ticker) WHERE t.ticker IN $ids DETACH DELETE t",
            ids=["ZZA1", "ZZA2"],
        )
