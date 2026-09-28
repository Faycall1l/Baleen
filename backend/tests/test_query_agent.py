"""Query agent tests: allowlist, no-data behavior, and the 3 example queries."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.extraction.client import LLMResponse, ToolCall
from app.models.events import CongressTradeEvent, OptionsFlowEvent
from app.query.agent import NO_DATA_ANSWER, QueryAgent
from app.query.tools import bounded_query
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


def test_allowlist_rejects_admin_and_planning_clauses_issues_15():
    """SHOW/USE/EXPLAIN/PROFILE are read-ish but leak schema or execution
    plans; the model must not be able to reach them."""
    for statement in [
        "SHOW CONSTRAINTS",
        "SHOW INDEXES YIELD name RETURN name",
        "USE system SHOW DATABASES",
        "EXPLAIN MATCH (t:Ticker) RETURN t",
        "PROFILE MATCH (t:Ticker) RETURN t",
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


def test_allowlist_rejects_load_csv():
    """LOAD CSV reads the server's local filesystem; a crafted question could
    otherwise prompt the model to exfiltrate file contents."""
    assert validate_cypher(
        "LOAD CSV WITH HEADERS FROM 'file:///etc/passwd' AS row RETURN row"
    )


def test_allowlist_rejects_multi_statement():
    """`;` separates statements in Neo4j; only a single trailing one is the
    model's statement terminator, anything else is statement injection."""
    assert validate_cypher("MATCH (t:Ticker) RETURN t; MATCH (e:Event) RETURN e")


def test_allowlist_tolerates_single_trailing_semicolon():
    assert validate_cypher("MATCH (t:Ticker) RETURN t;") == []


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


class _SequencedStubLLM:
    """Returns one canned cypher per generate_cypher call, in order; always
    answers the final grounding question."""

    def __init__(self, cyphers, answer="Based on the data, yes.", sources=None):
        self.cyphers = list(cyphers)
        self.answer = answer
        self.sources = sources or ["https://x.com/unusual_whales/status/1"]

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        from app.extraction.client import LLMResponse

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
        cypher = self.cyphers.pop(0) if self.cyphers else "MATCH (t:Ticker) RETURN t"
        return LLMResponse(tool_calls=[ToolCall("generate_cypher", {"cypher": cypher})])


def test_agent_recovers_on_second_attempt_after_rejected_first():
    """A valid follow-up attempt must win over an earlier rejection. Regresses
    the bug where `allowed` stayed False after attempt 1, discarding a good
    attempt 2."""
    store = EmptyStore()

    class OneRowStore(EmptyStore):
        def run_read(self, cypher, **params):
            return [{"ticker": "NVDA"}]

    agent = QueryAgent(
        llm=_SequencedStubLLM(
            ["MATCH (u:User) RETURN u", "MATCH (t:Ticker) RETURN t.ticker AS ticker LIMIT 1"]
        ),
        store=OneRowStore(),
    )
    result = agent.ask("What tickers exist?")

    assert result.allowed is True
    assert result.rows == [{"ticker": "NVDA"}]
    assert result.answer == "Based on the data, yes."


class _DownLLM:
    """Simulates the LLM endpoint being unreachable (LLMTransportError)."""

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        from app.extraction.client import LLMTransportError

        raise LLMTransportError("connection refused")


def test_agent_llm_down_during_generation_returns_failure():
    agent = QueryAgent(llm=_DownLLM(), store=EmptyStore())
    result = agent.ask("Which tickers exist?")

    assert result.answer.startswith("I couldn't answer this question confidently.")
    assert "valid query" in result.answer


class _AnswerDownLLM:
    """Serves a valid generate_cypher call but the LLM dies on the grounding
    question (transport error during answer generation)."""

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        from app.extraction.client import LLMResponse, LLMTransportError

        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        if "Query results (ground truth" in user:
            raise LLMTransportError("connection refused")
        return LLMResponse(
            tool_calls=[
                ToolCall("generate_cypher", {"cypher": "MATCH (t:Ticker) RETURN t.tweet_url AS tweet_url LIMIT 1"})
            ]
        )


def test_agent_llm_down_during_answer_generation_uses_fallback():
    """Rows are present but the answer-generation LLM call fails: fall back to
    a plain message with source URLs pulled from the rows themselves."""

    class OneRowStore(EmptyStore):
        def run_read(self, cypher, **params):
            return [{"tweet_url": "https://x.com/unusual_whales/status/1"}]

    agent = QueryAgent(llm=_AnswerDownLLM(), store=OneRowStore())
    result = agent.ask("What tickers exist?")

    assert result.has_data
    assert "couldn't summarize" in result.answer
    assert "https://x.com/unusual_whales/status/1" in result.sources


def test_agent_accepts_nameless_answer_arguments():
    """Some models emit bare {"answer", "sources"} without the tool name;
    the agent must still use them."""
    from app.extraction.client import LLMResponse, ToolCall

    class NamelessAnswerLLM:
        def chat_with_tools(self, messages, tools, tool_choice="auto"):
            user = next((m["content"] for m in messages if m["role"] == "user"), "")
            if "Query results (ground truth" in user:
                return LLMResponse(
                    tool_calls=[ToolCall("", {"answer": "CHRW and MRNA.", "sources": []})]
                )
            return LLMResponse(
                tool_calls=[ToolCall("generate_cypher", {"cypher": "MATCH (t:Ticker) RETURN t.ticker AS ticker LIMIT 1"})]
            )

    class OneRowStore(EmptyStore):
        def run_read(self, cypher, **params):
            return [{"ticker": "CHRW", "u": "https://disclosures-clerk.house.gov/x.pdf"}]

    agent = QueryAgent(llm=NamelessAnswerLLM(), store=OneRowStore())
    result = agent.ask("Which tickers?")

    assert result.answer == "CHRW and MRNA."
    assert result.sources == ["https://disclosures-clerk.house.gov/x.pdf"]


def test_allowlist_rejects_index_constraint_statements():
    """Index/constraint DDL is caught by the dedicated pattern check (beyond
    the generic CREATE clause block)."""
    for query in (
        "CREATE INDEX idx FOR (n:Event) ON (n.id)",
        "CREATE CONSTRAINT c FOR (n:Event) REQUIRE n.id IS UNIQUE",
    ):
        violations = validate_cypher(query)
        assert any("index/constraint" in v for v in violations)


def test_query_result_as_dict_snapshot():
    from app.query.agent import QueryResult

    q = QueryResult(
        question="Which tickers?",
        cypher="MATCH (t:Ticker) RETURN t.ticker AS ticker LIMIT 5",
        allowed=True,
        rows=[{"ticker": "NVDA"}],
        answer="NVDA.",
        sources=["https://x.com/unusual_whales/status/1"],
    )
    snapshot = q.as_dict()
    assert snapshot["question"] == "Which tickers?"
    assert snapshot["allowed"] is True
    assert snapshot["has_data"] is True
    assert snapshot["rows"] == [{"ticker": "NVDA"}]


def test_bounded_rejects_empty_query():
    agent = QueryAgent(llm=object(), store=object())  # type: ignore[arg-type]
    assert bounded_query("  ;   ") == ""


def test_agent_falls_back_when_answer_contains_no_answer_call():
    """If the LLM replies to the grounding question without an answer tool
    call, the agent falls back to sources harvested from the rows."""

    class StubbornLLM:
        def chat_with_tools(self, messages, tools, tool_choice="auto"):
            return LLMResponse(
                tool_calls=[ToolCall("generate_cypher", {"cypher": "MATCH (t:Ticker) RETURN t.ticker AS ticker LIMIT 1"})]
            )

    class OneRowStore(EmptyStore):
        def run_read(self, cypher, **params):
            return [{"ticker": "NVDA", "u": "https://x.com/unusual_whales/status/1"}]

    agent = QueryAgent(llm=StubbornLLM(), store=OneRowStore())
    result = agent.ask("Which tickers?")

    assert "couldn't summarize" in result.answer
    assert result.sources == ["https://x.com/unusual_whales/status/1"]


def test_sources_from_rows_accepts_any_source_url():
    agent = QueryAgent(llm=_stub_llm(), store=EmptyStore())
    rows = [
        {"u": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/1.pdf"},
        {"u": "https://finance.yahoo.com/quote/NVDA"},
        {"u": "https://x.com/unusual_whales/status/1"},
        {"n": 42},
    ]
    assert agent._sources_from_rows(rows) == [r["u"] for r in rows[:3]]


def test_generate_cypher_returns_empty_when_llm_never_calls_tool():
    """A response whose tool calls never mention generate_cypher (nor a
    nameless cypher) yields an empty string, so the agent answers 'no data'."""

    class _WrongToolLLM:
        def chat_with_tools(self, messages, tools, tool_choice="auto"):
            return LLMResponse(
                tool_calls=[
                    ToolCall("answer_question", {"answer": "42", "sources": []})
                ]
            )

    agent = QueryAgent(llm=_WrongToolLLM(), store=EmptyStore())
    assert (
        agent._generate_cypher([{"role": "user", "content": "How many tickers?"}])
        == ""
    )


class _ExecutionErrorStore:
    """Fails the first execution with a graph runtime error, then succeeds."""

    def __init__(self):
        self.calls = 0

    def run_read(self, cypher, **params):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Unknown function 'foo'")
        return [{"ticker": "NVDA"}]


def test_agent_retries_with_feedback_after_cypher_execution_error():
    agent = QueryAgent(
        llm=_SequencedStubLLM(
            [
                "MATCH (e:Event) RETURN foo(e) AS x",
                "MATCH (t:Ticker) RETURN t.ticker AS ticker LIMIT 1",
            ]
        ),
        store=_ExecutionErrorStore(),
    )
    result = agent.ask("List tickers.")

    assert result.allowed is True
    assert result.rows == [{"ticker": "NVDA"}]
    assert result.answer


class _AlwaysErrorStore:
    """Every execution raises a graph runtime error (e.g. bad function)."""

    def run_read(self, cypher, **params):
        raise RuntimeError("Unknown function 'foo'")


def test_agent_reports_execution_error_instead_of_no_data():
    """When the final attempt's Cypher passes the allowlist but fails to run,
    the agent must say the query failed — not fall through to the empty-rows
    'I don't have enough data' answer."""
    agent = QueryAgent(
        llm=_stub_llm(cypher="MATCH (e:Event) RETURN foo(e) AS x"),
        store=_AlwaysErrorStore(),
    )
    result = agent.ask("List events.")

    assert result.allowed is True
    assert result.rows == []
    assert result.answer.startswith("I couldn't answer this question confidently.")
    assert "failed to run" in result.answer
    assert "Unknown function 'foo'" in result.answer
    assert result.answer != NO_DATA_ANSWER


def test_agent_all_generation_attempts_rejected_returns_failure():
    agent = QueryAgent(llm=_stub_llm(cypher="MATCH (u:User) RETURN u"), store=EmptyStore())
    result = agent.ask("Who follows whom?")

    assert result.allowed is False
    assert "disallowed" in result.answer
    assert result.violations


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
    from app.query.tools import GENERATE_TOOL

    return GENERATE_TOOL["function"]["description"]


# ---------------------------------------------------------------------------
# Result-size cap (no unbounded RETURNs)
# ---------------------------------------------------------------------------


def test_bounded_appends_limit_when_missing():
    from app.query.agent import QueryAgent
    from app.query.tools import MAX_RESULT_ROWS

    agent = QueryAgent(llm=_stub_llm(), store=EmptyStore())
    bounded = bounded_query("MATCH (e:Event) RETURN e")
    assert bounded.endswith(f"LIMIT {MAX_RESULT_ROWS}")


def test_bounded_keeps_existing_limit():
    agent = QueryAgent(llm=_stub_llm(), store=EmptyStore())
    query = "MATCH (e:Event) RETURN e ORDER BY e.event_time DESC LIMIT 10"
    assert bounded_query(query) == query


def test_bounded_leaves_aggregations_alone():
    agent = QueryAgent(llm=_stub_llm(), store=EmptyStore())
    query = (
        "MATCH (e:Event)-[:INVOLVES_TICKER]->(t:Ticker) "
        "RETURN t.ticker AS ticker, count(e) AS n ORDER BY n DESC"
    )
    assert bounded_query(query) == query


def test_bounded_strips_trailing_semicolon():
    from app.query.tools import MAX_RESULT_ROWS

    agent = QueryAgent(llm=_stub_llm(), store=EmptyStore())
    bounded = bounded_query("MATCH (e:Event) RETURN e;")
    assert bounded.endswith(f"LIMIT {MAX_RESULT_ROWS}")


def test_agent_caps_unbounded_query_at_execution():
    from app.query.tools import MAX_RESULT_ROWS

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
