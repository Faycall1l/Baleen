"""Query agent tests: allowlist, no-data behavior, and the 3 example queries."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.extraction.client import LLMResponse, ToolCall
from app.models.events import CongressTradeEvent, OptionsFlowEvent
from app.query.agent import NO_DATA_ANSWER, QueryAgent
from app.query.validate import validate_cypher

# ---------------------------------------------------------------------------
# Allowlist (unit)
# ---------------------------------------------------------------------------


def test_allowlist_rejects_disallowed_label():
    violations = validate_cypher("MATCH (u:User) RETURN u")
    assert any("User" in v for v in violations)


def test_allowlist_rejects_disallowed_relationship():
    violations = validate_cypher(
        "MATCH (a:Ticker)-[:OWNED_BY]->(b:Politician) RETURN a"
    )
    assert any("OWNED_BY" in v for v in violations)


def test_allowlist_rejects_write_clauses():
    for statement in [
        "MERGE (t:Ticker {ticker: 'NVDA'})",
        "MATCH (t:Ticker {ticker: 'NVDA'}) SET t.x = 1",
        "MATCH (t:Ticker) DELETE t",
        "CREATE (e:Event {id: 'x'})",
    ]:
        assert validate_cypher(statement), f"expected rejection: {statement}"


def test_allowlist_accepts_read_only_query():
    query = (
        "MATCH (c:Committee {id: $id})-[:OVERSEES]->(s:Sector)<-[:IN_SECTOR]-(t:Ticker) "
        "RETURN s.name AS sector, collect(DISTINCT t.ticker) AS tickers"
    )
    assert validate_cypher(query) == []


def test_allowlist_rejects_procedure_calls():
    assert validate_cypher("CALL db.labels() YIELD label RETURN label")


# ---------------------------------------------------------------------------
# No-data behavior (unit, fake store)
# ---------------------------------------------------------------------------


class EmptyStore:
    def run_read(self, cypher, **params):
        return []


class QueryStubLLM:
    """Responds with canned cypher for the user question, then a grounded
    answer once query results are presented."""

    def __init__(self, cypher=None, answer="Based on the data, yes.", sources=None):
        self.cypher = cypher or "MATCH (t:Ticker) RETURN t.ticker AS ticker LIMIT 1"
        self.answer = answer
        self.sources = sources or ["https://x.com/unusual_whales/status/1"]

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        if "Query results (ground truth" in user:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        "answer_question",
                        {"answer": self.answer, "sources": self.sources},
                    )
                ]
            )
        return LLMResponse(
            tool_calls=[ToolCall("generate_cypher", {"cypher": self.cypher})]
        )


def _stub_llm(cypher=None, **kwargs):
    return QueryStubLLM(cypher=cypher, **kwargs)


def test_agent_says_no_data_on_empty_result():
    agent = QueryAgent(llm=_stub_llm(), store=EmptyStore())
    result = agent.ask("Which tickers did Maxine Waters trade?")

    assert result.allowed
    assert result.rows == []
    assert result.answer == NO_DATA_ANSWER


def test_agent_refuses_disallowed_generated_cypher():
    agent = QueryAgent(llm=_stub_llm(cypher="MATCH (u:User) RETURN u"), store=EmptyStore())
    result = agent.ask("Who follows whom?")

    assert result.allowed is False
    assert result.cypher == "MATCH (u:User) RETURN u"
    assert "disallowed" in result.answer


def test_agent_prompt_instructs_inlining_literals():
    agent = QueryAgent(llm=_stub_llm(), store=EmptyStore())
    system = agent._system_prompt()
    # The executor never binds $parameters, so the model must inline literals.
    assert "Parameterize" not in system
    assert "Inline literal user values" in system
    assert "Do NOT use $parameters" in system
    assert "Use $parameters" not in _tool_text()
    assert "Do NOT use $parameters" in _tool_text()


def _tool_text() -> str:
    from app.query.agent import GENERATE_TOOL

    return GENERATE_TOOL["function"]["description"]


# ---------------------------------------------------------------------------
# Result-size cap (no unbounded RETURNs)
# ---------------------------------------------------------------------------


def test_bounded_appends_limit_when_missing():
    from app.query.agent import MAX_RESULT_ROWS, QueryAgent

    agent = QueryAgent(llm=_stub_llm(), store=EmptyStore())
    bounded = agent._bounded("MATCH (e:Event) RETURN e")
    assert bounded.endswith(f"LIMIT {MAX_RESULT_ROWS}")


def test_bounded_keeps_existing_limit():
    agent = QueryAgent(llm=_stub_llm(), store=EmptyStore())
    query = "MATCH (e:Event) RETURN e ORDER BY e.event_time DESC LIMIT 10"
    assert agent._bounded(query) == query


def test_bounded_leaves_aggregations_alone():
    agent = QueryAgent(llm=_stub_llm(), store=EmptyStore())
    query = (
        "MATCH (e:Event)-[:INVOLVES_TICKER]->(t:Ticker) "
        "RETURN t.ticker AS ticker, count(e) AS n ORDER BY n DESC"
    )
    assert agent._bounded(query) == query


def test_bounded_strips_trailing_semicolon():
    from app.query.agent import MAX_RESULT_ROWS

    agent = QueryAgent(llm=_stub_llm(), store=EmptyStore())
    bounded = agent._bounded("MATCH (e:Event) RETURN e;")
    assert bounded.endswith(f"LIMIT {MAX_RESULT_ROWS}")


def test_agent_caps_unbounded_query_at_execution():
    from app.query.agent import MAX_RESULT_ROWS

    captured = {}

    class RecordingStore:
        def run_read(self, cypher, **params):
            captured["cypher"] = cypher
            return [{"e": "row"}]

    agent = QueryAgent(
        llm=_stub_llm(cypher="MATCH (e:Event) RETURN e"),
        store=RecordingStore(),
    )
    result = agent.ask("Show me all events.")

    assert captured["cypher"].endswith(f"LIMIT {MAX_RESULT_ROWS}")
    assert result.cypher == captured["cypher"]
    assert result.rows == [{"e": "row"}]
    assert result.answer


# ---------------------------------------------------------------------------
# Integration: the 3 example queries (spec §5.4)
# ---------------------------------------------------------------------------


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
    agent = QueryAgent(llm=_stub_llm(cypher=cypher), store=neo4j_store)
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
    agent = QueryAgent(llm=_stub_llm(cypher=cypher), store=neo4j_store)
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
    agent = QueryAgent(llm=_stub_llm(cypher=cypher), store=neo4j_store)
    result = agent.ask("Show unusual options flow in the last 24 hours by sector.")

    assert result.allowed
    assert result.rows
    tech = next(r for r in result.rows if r["sector"] == "Technology")
    assert "AMD" in tech["tickers"]
    assert result.answer
