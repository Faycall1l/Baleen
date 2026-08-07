"""Graph writer integration tests: idempotency and traceability."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.graph.writer import GraphWriter
from app.models.events import CongressTradeEvent, OptionsFlowEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(event_id: str = "t1") -> CongressTradeEvent:
    return CongressTradeEvent(
        tweet_id=event_id,
        tweet_url=f"https://x.com/congress_trades/status/{event_id}",
        confidence=0.95,
        politician_name="Elizabeth Warren",
        politician_id="W000817",
        chamber="senate",
        party="D",
        committee_ids=["SSBK", "SSHR"],
        ticker="NVDA",
        transaction_type="buy",
        amount_range="$1,001 - $15,000",
        transaction_date=date(2026, 1, 15),
        event_time=datetime(2026, 1, 16, tzinfo=timezone.utc),
    )


def _count(neo4j_store, cypher: str, **params) -> int:
    rows = neo4j_store.run(cypher, **params)
    return rows[0]["count"] if rows else 0


def _cleanup(neo4j_store, event_id: str) -> None:
    neo4j_store.run(
        """
        MATCH (e:Event {id: $id})
        DETACH DELETE e
        """,
        id=event_id,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_writing_event_twice_is_idempotent(neo4j_store):
    writer = GraphWriter(neo4j_store)
    event = _event("double-write-1")

    first = writer.write_event(event)
    second = writer.write_event(event)
    assert first == second == event.deterministic_id

    assert (
        _count(neo4j_store, "MATCH (e:Event {id: $id}) RETURN count(e) AS count", id=first)
        == 1
    )
    assert (
        _count(neo4j_store, "MATCH (p:Politician {id: 'W000817'}) RETURN count(p) AS count")
        == 1
    )
    assert (
        _count(neo4j_store, "MATCH (t:Ticker {ticker: 'NVDA'}) RETURN count(t) AS count")
        == 1
    )
    # Event-written committees resolve their display name from the reference set.
    rows = neo4j_store.run(
        "MATCH (c:Committee {id: 'SSBK'}) RETURN c.name AS name, c.first_seen_at AS first_seen"
    )
    assert rows and rows[0]["name"] == "Senate Banking, Housing and Urban Affairs Committee"
    assert rows[0]["first_seen"] is not None
    assert (
        _count(
            neo4j_store,
            "MATCH (:Event {id: $id})-[:INVOLVES_TICKER]->(:Ticker {ticker: 'NVDA'}) RETURN count(*) AS count",
            id=first,
        )
        == 1
    )
    assert (
        _count(
            neo4j_store,
            "MATCH (:Event {id: $id})-[:INVOLVES_POLITICIAN]->(:Politician) RETURN count(*) AS count",
            id=first,
        )
        == 1
    )

    _cleanup(neo4j_store, first)


def test_event_is_traceable_to_source_tweet(neo4j_store):
    writer = GraphWriter(neo4j_store)
    event = _event("traceable-1")
    event_id = writer.write_event(event)

    rows = neo4j_store.run(
        "MATCH (e:Event {id: $id}) RETURN e.tweet_id AS tid, e.tweet_url AS url, e.first_seen_at AS first_seen",
        id=event_id,
    )
    assert rows[0]["tid"] == "traceable-1"
    assert "status/traceable-1" in rows[0]["url"]
    assert rows[0]["first_seen"] is not None

    _cleanup(neo4j_store, event_id)


def test_event_time_is_stored_as_native_temporal(neo4j_store):
    """event_time must be queryable as a temporal type (e.g. the documented
    ``datetime() - duration()`` window filters), not an ISO string."""
    writer = GraphWriter(neo4j_store)
    event = OptionsFlowEvent(
        tweet_id="time-1",
        tweet_url="https://x.com/unusual_whales/status/time-1",
        confidence=0.9,
        ticker="NVDA",
        contract_type="call",
        event_time=datetime(2026, 1, 16, tzinfo=timezone.utc),
    )
    event_id = writer.write_event(event)

    rows = neo4j_store.run(
        "MATCH (e:Event {id: $id}) "
        "WHERE e.event_time > datetime('2026-01-15T00:00:00Z') "
        "RETURN e.event_time AS event_time",
        id=event_id,
    )
    assert rows and rows[0]["event_time"] is not None

    _cleanup(neo4j_store, event_id)


def test_options_flow_writes_ticker_and_sector(neo4j_store):
    writer = GraphWriter(neo4j_store)
    event = OptionsFlowEvent(
        tweet_id="flow-1",
        tweet_url="https://x.com/unusual_whales/status/flow-1",
        confidence=0.9,
        ticker="NVDA",
        contract_type="call",
        strike=190.0,
        sentiment="bullish",
        event_time=datetime(2026, 1, 16, tzinfo=timezone.utc),
    )
    event_id = writer.write_event(event)

    assert (
        _count(
            neo4j_store,
            "MATCH (e:Event {id: $id})-[:INVOLVES_TICKER]->(t:Ticker)-[:IN_SECTOR]->(s:Sector {name: 'Technology'}) RETURN count(*) AS count",
            id=event_id,
        )
        == 1
    )

    _cleanup(neo4j_store, event_id)
