"""Shared stubs for the query-agent suite.

Split out of the former ``tests/test_query_agent.py`` (M3): Cypher allowlist,
the agent loop, statement bounding, and the live-service example queries now
live in sibling modules.
"""

from __future__ import annotations



from app.extraction.client import LLMResponse, ToolCall


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


def stub_llm(cypher=None, **kwargs):
    return QueryStubLLM(cypher=cypher, **kwargs)


class SequencedStubLLM:
    """Returns one canned cypher per generate_cypher call, in order; always
    answers the final grounding question."""

    def __init__(self, cyphers, answer="Based on the data, yes.", sources=None):
        self.cyphers = list(cyphers)
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
        cypher = self.cyphers.pop(0) if self.cyphers else "MATCH (t:Ticker) RETURN t"
        return LLMResponse(tool_calls=[ToolCall("generate_cypher", {"cypher": cypher})])


class DownLLM:
    """Simulates the LLM endpoint being unreachable (LLMTransportError)."""

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        from app.extraction.client import LLMTransportError

        raise LLMTransportError("connection refused")


class AnswerDownLLM:
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


class ExecutionErrorStore:
    """Fails the first execution with a graph runtime error, then succeeds."""

    def __init__(self):
        self.calls = 0

    def run_read(self, cypher, **params):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Unknown function 'foo'")
        return [{"ticker": "NVDA"}]


class AlwaysErrorStore:
    """Every execution raises a graph runtime error (e.g. bad function)."""

    def run_read(self, cypher, **params):
        raise RuntimeError("Unknown function 'foo'")


def tool_text() -> str:
    from app.query.tools import GENERATE_TOOL

    return GENERATE_TOOL["function"]["description"]
