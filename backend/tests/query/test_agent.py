"""QueryAgent: generation, validation, grounding, retries, and failure paths.

Part of the query suite split out of the former ``tests/test_query_agent.py`` (M3).
"""

from __future__ import annotations


from app.extraction.client import LLMResponse, ToolCall
from app.query.agent import NO_DATA_ANSWER, QueryAgent
from tests.query.conftest import (
    AlwaysErrorStore,
    AnswerDownLLM,
    DownLLM,
    EmptyStore,
    ExecutionErrorStore,
    SequencedStubLLM,
    stub_llm,
    tool_text,
)

def test_agent_says_no_data_on_empty_result():
    agent = QueryAgent(llm=stub_llm(), store=EmptyStore())
    result = agent.ask("Which tickers did Maxine Waters trade?")

    assert result.allowed
    assert result.rows == []
    assert result.answer == NO_DATA_ANSWER


def test_agent_refuses_disallowed_generated_cypher():
    agent = QueryAgent(llm=stub_llm(cypher="MATCH (u:User) RETURN u"), store=EmptyStore())
    result = agent.ask("Who follows whom?")

    assert result.allowed is False
    assert result.cypher == "MATCH (u:User) RETURN u"
    assert "disallowed" in result.answer


def test_agent_recovers_on_second_attempt_after_rejected_first():
    """A valid follow-up attempt must win over an earlier rejection. Regresses
    the bug where `allowed` stayed False after attempt 1, discarding a good
    attempt 2."""
    store = EmptyStore()

    class OneRowStore(EmptyStore):
        def run_read(self, cypher, **params):
            return [{"ticker": "NVDA"}]

    agent = QueryAgent(
        llm=SequencedStubLLM(
            ["MATCH (u:User) RETURN u", "MATCH (t:Ticker) RETURN t.ticker AS ticker LIMIT 1"]
        ),
        store=OneRowStore(),
    )
    result = agent.ask("What tickers exist?")

    assert result.allowed is True
    assert result.rows == [{"ticker": "NVDA"}]
    assert result.answer == "Based on the data, yes."


def test_agent_llm_down_during_generation_returns_failure():
    agent = QueryAgent(llm=DownLLM(), store=EmptyStore())
    result = agent.ask("Which tickers exist?")

    assert result.answer.startswith("I couldn't answer this question confidently.")
    assert "valid query" in result.answer


def test_agent_llm_down_during_answer_generation_uses_fallback():
    """Rows are present but the answer-generation LLM call fails: fall back to
    a plain message with source URLs pulled from the rows themselves."""

    class OneRowStore(EmptyStore):
        def run_read(self, cypher, **params):
            return [{"tweet_url": "https://x.com/unusual_whales/status/1"}]

    agent = QueryAgent(llm=AnswerDownLLM(), store=OneRowStore())
    result = agent.ask("What tickers exist?")

    assert result.has_data
    assert "couldn't summarize" in result.answer
    assert "https://x.com/unusual_whales/status/1" in result.sources


def test_agent_accepts_nameless_answer_arguments():
    """Some models emit bare {"answer", "sources"} without the tool name;
    the agent must still use them."""

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
    agent = QueryAgent(llm=stub_llm(), store=EmptyStore())
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


def test_agent_retries_with_feedback_after_cypher_execution_error():
    agent = QueryAgent(
        llm=SequencedStubLLM(
            [
                "MATCH (e:Event) RETURN foo(e) AS x",
                "MATCH (t:Ticker) RETURN t.ticker AS ticker LIMIT 1",
            ]
        ),
        store=ExecutionErrorStore(),
    )
    result = agent.ask("List tickers.")

    assert result.allowed is True
    assert result.rows == [{"ticker": "NVDA"}]
    assert result.answer


def test_agent_reports_execution_error_instead_of_no_data():
    """When the final attempt's Cypher passes the allowlist but fails to run,
    the agent must say the query failed — not fall through to the empty-rows
    'I don't have enough data' answer."""
    agent = QueryAgent(
        llm=stub_llm(cypher="MATCH (e:Event) RETURN foo(e) AS x"),
        store=AlwaysErrorStore(),
    )
    result = agent.ask("List events.")

    assert result.allowed is True
    assert result.rows == []
    assert result.answer.startswith("I couldn't answer this question confidently.")
    assert "failed to run" in result.answer
    assert "Unknown function 'foo'" in result.answer
    assert result.answer != NO_DATA_ANSWER


def test_agent_all_generation_attempts_rejected_returns_failure():
    agent = QueryAgent(llm=stub_llm(cypher="MATCH (u:User) RETURN u"), store=EmptyStore())
    result = agent.ask("Who follows whom?")

    assert result.allowed is False
    assert "disallowed" in result.answer
    assert result.violations


def test_agent_prompt_instructs_inlining_literals():
    agent = QueryAgent(llm=stub_llm(), store=EmptyStore())
    system = agent._system_prompt()
    # The executor never binds $parameters, so the model must inline literals.
    assert "Parameterize" not in system
    assert "Inline literal user values" in system
    assert "Do NOT use $parameters" in system
    assert "Use $parameters" not in tool_text()
    assert "Do NOT use $parameters" in tool_text()


def test_columns_describe_ragged_rows_with_types():
    from app.query.agent import columns_for_rows

    rows = [
        {"ticker": "NVDA", "buys": 3, "active": True},
        {"ticker": "AMD", "excess_pct": 44.5, "note": None, "tags": ["x"]},
    ]
    assert columns_for_rows(rows) == [
        {"key": "ticker", "label": "ticker", "kind": "string"},
        {"key": "buys", "label": "buys", "kind": "number"},
        {"key": "active", "label": "active", "kind": "boolean"},
        {"key": "excess_pct", "label": "excess_pct", "kind": "number"},
        {"key": "note", "label": "note", "kind": "value"},
        {"key": "tags", "label": "tags", "kind": "value"},
    ]
    assert columns_for_rows([]) == []


def test_followups_suggest_tickers_present_capped_and_deduped():
    from app.query.agent import followups_for_rows

    rows = [
        {"ticker": "NVDA"},
        {"ticker": "AMD"},
        {"ticker": "NVDA"},
        {"ticker": "MSFT"},
        {"ticker": "TSLA"},
        {"n": 1},
    ]
    assert followups_for_rows(rows) == [
        "Show more activity for NVDA",
        "Show more activity for AMD",
        "Show more activity for MSFT",
    ]
    assert followups_for_rows([{"n": 1}]) == []
    assert followups_for_rows([]) == []


def test_ask_returns_columns_and_followups_with_rows():
    class OneRowStore(EmptyStore):
        def run_read(self, cypher, **params):
            return [{"ticker": "NVDA", "buys": 2}]

    agent = QueryAgent(llm=stub_llm(), store=OneRowStore())
    result = agent.ask("Which tickers?")

    assert result.columns == [
        {"key": "ticker", "label": "ticker", "kind": "string"},
        {"key": "buys", "label": "buys", "kind": "number"},
    ]
    assert result.followups == ["Show more activity for NVDA"]
    assert result.as_dict()["columns"] == result.columns
    assert result.as_dict()["followups"] == result.followups


def test_columns_infer_kind_from_later_non_null_values():
    """A leading None must not freeze the column kind: the first non-null
    value decides."""
    from app.query.agent import columns_for_rows

    rows = [{"n": None, "v": None}, {"n": 7, "v": "x"}]
    assert columns_for_rows(rows) == [
        {"key": "n", "label": "n", "kind": "number"},
        {"key": "v", "label": "v", "kind": "string"},
    ]


def test_ask_fails_when_llm_never_produces_cypher():
    """An LLM that only answers (never calls generate_cypher) yields the
    generation-failure path, not an empty crash."""

    class _AnswerOnlyLLM:
        def chat_with_tools(self, messages, tools, tool_choice="auto"):
            return LLMResponse(content="forty-two")

    agent = QueryAgent(llm=_AnswerOnlyLLM(), store=EmptyStore())
    result = agent.ask("What is the answer?")

    assert result.rows == []
    assert result.answer.startswith("I couldn't answer this question confidently.")


def test_ask_fails_when_bounded_query_empties_degenerate_cypher():
    """A lone ``;`` passes the truthiness check and the allowlist (a single
    trailing separator is tolerated), but bounded_query reduces it to
    nothing: the agent must fail gracefully, not run empty Cypher."""

    class _SemicolonLLM:
        def chat_with_tools(self, messages, tools, tool_choice="auto"):
            return LLMResponse(
                tool_calls=[ToolCall("generate_cypher", {"cypher": ";"})]
            )

    class _LenientStore(EmptyStore):
        def run_read(self, cypher, **params):
            assert cypher == ""
            return []

    agent = QueryAgent(llm=_SemicolonLLM(), store=_LenientStore())
    result = agent.ask("Anything at all?")

    assert result.rows == []
    assert "couldn't generate a query" in result.answer
