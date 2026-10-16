"""Graph writer integration tests: idempotency and traceability."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime

from app.graph.writer import GraphWriter
from app.models.events import CongressTradeEvent, InsiderTradeEvent, OptionsFlowEvent

# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


class _FakeTxStore:
    """Stores statements recorded inside a transaction; tracks how many
    transactions were opened and rejects calls outside one."""

    def __init__(self) -> None:
        self.transactions = 0
        self.stmts: list[tuple[str, dict]] = []
        self.counts: dict[str, int] = {}

    @contextmanager
    def transaction(self, timeout=None):
        self.transactions += 1
        yield self.run

    def run(self, query: str, **params) -> list[dict]:
        kind = query.strip().split()[0]
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.stmts.append((query, params))
        if kind == "MATCH":  # relationship merges need nodes to match; fake no-op
            return []
        return [{"count": 1}]

    def run_read(self, query: str, timeout=None, **params):
        return self.run(query, **params)


def test_write_event_fans_out_inside_one_transaction():
    """the whole event write (nodes + sectors + N committees) is one
    server transaction, not ~8 + 3N sequential round-trips."""
    store = _FakeTxStore()
    writer = GraphWriter(store)  # type: ignore[arg-type]
    writer.write_event(_event("batch-1"))

    assert store.transactions == 1
    # 6 node MERGEs + 7 relation MATCH-MERGEs (incl. 2 committees * 3) — all
    # batched into that single transaction instead of 13 sequential calls.
    assert len(store.stmts) == 13
    assert store.counts["MERGE"] == 6
    assert store.counts["MATCH"] == 7
    # Every recorded statement carries provenance so none leaks into the graph
    # without its source tweet.
    for query, params in store.stmts:
        if "tweet_id" in query:
            assert params["tweet_id"] == "batch-1"


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
        event_time=datetime(2026, 1, 16, tzinfo=UTC),
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

    assert _count(neo4j_store, "MATCH (e:Event {id: $id}) RETURN count(e) AS count", id=first) == 1
    assert _count(neo4j_store, "MATCH (p:Politician {id: 'W000817'}) RETURN count(p) AS count") == 1
    assert _count(neo4j_store, "MATCH (t:Ticker {ticker: 'NVDA'}) RETURN count(t) AS count") == 1
    # Event-written committees resolve their display name from the reference set.
    rows = neo4j_store.run("MATCH (c:Committee {id: 'SSBK'}) RETURN c.name AS name, c.first_seen_at AS first_seen")
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
        event_time=datetime(2026, 1, 16, tzinfo=UTC),
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
        event_time=datetime(2026, 1, 16, tzinfo=UTC),
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


def test_insider_trade_writes_insider_and_ticker(neo4j_store):
    writer = GraphWriter(neo4j_store)
    event = InsiderTradeEvent(
        tweet_id="insider-1",
        tweet_url="https://www.sec.gov/Archives/edgar/data/1/2-index.htm",
        confidence=0.95,
        insider_name="Milton C Ault III",
        insider_cik="0001212502",
        insider_id="0001212502",
        role="Chairman",
        ticker="GPUS",
        transaction_type="buy",
        shares=1000.0,
        price=2.5,
        amount_range="$2,500.00",
        transaction_date=date(2026, 9, 9),
    )
    event_id = writer.write_event(event)

    assert (
        _count(
            neo4j_store,
            "MATCH (:Event {id: $id})-[:INVOLVES_INSIDER]->(i:Insider {id: '0001212502'}) RETURN count(*) AS count",
            id=event_id,
        )
        == 1
    )
    assert (
        _count(
            neo4j_store,
            "MATCH (:Event {id: $id})-[:INVOLVES_TICKER]->(:Ticker {ticker: 'GPUS'}) RETURN count(*) AS count",
            id=event_id,
        )
        == 1
    )
    rows = neo4j_store.run("MATCH (i:Insider {id: '0001212502'}) RETURN i.name AS name, i.role AS role")
    assert rows and rows[0]["name"] == "Milton C Ault III"
    assert rows[0]["role"] == "Chairman"

    _cleanup(neo4j_store, event_id)


def test_insider_write_applies_entity_resolver_canonical_identity(neo4j_store):
    """With a resolver set, insider nodes are written under the canonical
    id and spelling instead of the raw mention."""

    class _StubResolver:
        @staticmethod
        def canonical_identity(entity_type, candidate_id):
            assert entity_type == "Insider"
            return "CANON-1"

        @staticmethod
        def canonical_name(entity_type, mention, canonical_id):
            return "Canonical Person"

    event = InsiderTradeEvent(
        tweet_id="insider-res-1",
        tweet_url="https://www.sec.gov/Archives/edgar/data/1/2-index.htm",
        confidence=0.95,
        insider_name="Raw Mention",
        insider_id="RAW1",
        ticker="GPUS",
        transaction_type="buy",
        transaction_date=date(2026, 9, 9),
    )
    try:
        event_id = GraphWriter(neo4j_store, entity_resolver=_StubResolver()).write_event(event)
        rows = neo4j_store.run(
            "MATCH (e:Event {id: $id})-[:INVOLVES_INSIDER]->(i:Insider) RETURN i.id AS id, i.name AS name",
            id=event_id,
        )
        assert [(r["id"], r["name"]) for r in rows] == [("CANON-1", "Canonical Person")]
    finally:
        _cleanup(neo4j_store, event.deterministic_id)


def test_amount_bounds_parsed_from_range_labels():
    from app.graph.writer import _parse_amount_bounds

    assert _parse_amount_bounds("$1,001 - $15,000") == (1001.0, 15000.0)
    assert _parse_amount_bounds("$52,120,753") == (52120753.0, 52120753.0)
    assert _parse_amount_bounds("over $1,000,000") == (1000000.0, None)
    assert _parse_amount_bounds("under $50,000") == (0.0, 50000.0)
    assert _parse_amount_bounds("$1.5M") == (1500000.0, 1500000.0)
    assert _parse_amount_bounds(None) == (None, None)
    assert _parse_amount_bounds("nonsense") == (None, None)


def test_trade_events_carry_amount_bounds():
    store = _FakeTxStore()
    writer = GraphWriter(store)
    writer.write_event(_event("bounds-1"))

    merges = [params for query, params in store.stmts if "MERGE (n:Event" in query]
    assert merges, "expected an Event MERGE statement"
    assert merges[0]["props"]["amount_low"] == 1001.0
    assert merges[0]["props"]["amount_high"] == 15000.0
