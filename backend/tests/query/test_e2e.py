"""The three documented example questions, run against live Neo4j.

Part of the query suite split out of the former ``tests/test_query_agent.py`` (M3).
Skipped when the Neo4j container is unreachable.
"""

from __future__ import annotations

from datetime import date, datetime, timezone


from app.models.events import CongressTradeEvent, OptionsFlowEvent
from app.query.agent import QueryAgent
from tests.query.conftest import stub_llm


def _seed_and_write_demo_events(neo4j_store):
    from app.graph.writer import GraphWriter
    from scripts.seed_reference import seed

    seed(neo4j_store)
    writer = GraphWriter(neo4j_store)

    congress = CongressTradeEvent(
        tweet_id="q-demo-1",
        tweet_url="https://x.com/congress_trades/status/q-demo-1",
        confidence=0.95,
        politician_name="Maxine Waters",
        politician_id="W000187",
        chamber="house",
        party="D",
        committee_ids=["HSBA"],
        ticker="NVDA",
        transaction_type="buy",
        amount_range="$1,001 - $15,000",
        transaction_date=date(2026, 1, 15),
        event_time=datetime(2026, 1, 16, tzinfo=timezone.utc),
    )
    options = OptionsFlowEvent(
        tweet_id="q-demo-2",
        tweet_url="https://x.com/unusual_whales/status/q-demo-2",
        confidence=0.9,
        ticker="AMD",
        contract_type="call",
        strike=250.0,
        sentiment="bullish",
        event_time=datetime.now(timezone.utc),
    )
    return writer.write_event(congress), writer.write_event(options)


def test_query1_committee_sector_tickers(neo4j_store):
    _seed_and_write_demo_events(neo4j_store)
    cypher = (
        "MATCH (c:Committee {id: 'HSBA'})-[:OVERSEES]->(s:Sector)<-[:IN_SECTOR]-(t:Ticker) "
        "RETURN s.name AS sector, collect(DISTINCT t.ticker) AS tickers"
    )
    agent = QueryAgent(llm=stub_llm(cypher=cypher), store=neo4j_store)
    result = agent.ask(
        "What sectors does the House Financial Services Committee oversee, "
        "and which of those sectors have tickers in the graph?"
    )

    assert result.allowed
    assert result.rows, "expected data for HSBA oversight sectors"
    sector_rows = {row["sector"]: row["tickers"] for row in result.rows}
    assert "Financial Services" in sector_rows
    assert "NVDA" not in sector_rows["Financial Services"]  # NVDA is Technology
    assert result.answer


def test_query2_politician_trades(neo4j_store):
    _seed_and_write_demo_events(neo4j_store)
    cypher = (
        "MATCH (p:Politician {name: 'Maxine Waters'})<-[:INVOLVES_POLITICIAN]-(e:Event) "
        "MATCH (e)-[:INVOLVES_TICKER]->(t:Ticker) "
        "RETURN e.transaction_date AS date, t.ticker AS ticker, e.transaction_type AS type "
        "ORDER BY e.transaction_date DESC LIMIT 20"
    )
    agent = QueryAgent(llm=stub_llm(cypher=cypher), store=neo4j_store)
    result = agent.ask("Which tickers has Maxine Waters traded recently, and when?")

    assert result.allowed
    assert result.rows
    assert any(row["ticker"] == "NVDA" for row in result.rows)
    assert result.answer


def test_query3_options_flow_by_sector(neo4j_store):
    _seed_and_write_demo_events(neo4j_store)
    cypher = (
        "MATCH (e:Event {type: 'options_flow'})-[:INVOLVES_TICKER]->(t:Ticker)-[:IN_SECTOR]->(s:Sector) "
        "RETURN s.name AS sector, count(e) AS flow_count, collect(DISTINCT t.ticker) AS tickers "
        "ORDER BY flow_count DESC"
    )
    agent = QueryAgent(llm=stub_llm(cypher=cypher), store=neo4j_store)
    result = agent.ask("Show unusual options flow in the last 24 hours by sector.")

    assert result.allowed
    assert result.rows
    tech = next(r for r in result.rows if r["sector"] == "Technology")
    assert "AMD" in tech["tickers"]
    assert result.answer
