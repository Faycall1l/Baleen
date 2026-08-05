"""Guardrailed natural-language -> Cypher query agent (spec §5.4).

Pipeline for one question:

1. The fixed graph schema (``app.graph.schema``) is handed to the LLM as
   context — the schema is never inferred.
2. The LLM proposes read-only Cypher via tool-calling (or constrained JSON).
3. :func:`app.query.validate.validate_cypher` enforces the label/relationship
   allowlist and rejects write clauses.
4. The Cypher runs against the **read-only** Neo4j driver.
5. The LLM writes a grounded answer citing source tweet URLs pulled from the
   rows themselves.

If generation/validation fails repeatedly, or the query returns zero rows, the
agent says so — it never fabricates an answer that isn't grounded in graph
results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.db.neo4j import Neo4jStore
from app.extraction.client import LLMClient, LLMError, ToolCall
from app.graph.schema import schema_context
from app.query.validate import validate_cypher

logger = logging.getLogger(__name__)

MAX_GENERATION_ATTEMPTS = 2

GENERATE_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_cypher",
        "description": (
            "Write a read-only Cypher query that answers the user's question "
            "against the provided schema. Use $parameters for user-supplied "
            "values. Only MATCH/OPTIONAL MATCH/WHERE/RETURN/ORDER BY/LIMIT/WITH "
            "are allowed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cypher": {
                    "type": "string",
                    "description": "The read-only Cypher statement.",
                }
            },
            "required": ["cypher"],
        },
    },
}

ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "answer_question",
        "description": (
            "Write a concise, factual answer to the user's question grounded "
            "strictly in the provided query results. Never invent data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "The grounded answer in plain English.",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Source tweet URLs supporting the answer.",
                },
            },
            "required": ["answer"],
        },
    },
}

NO_DATA_ANSWER = (
    "I don't have enough data in the graph to answer this question. "
    "Try asking about a ticker, sector, politician, or committee that has "
    "been ingested."
)


@dataclass
class QueryResult:
    question: str
    cypher: str = ""
    allowed: bool = True
    violations: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    answer: str = ""
    sources: list[str] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return bool(self.rows)

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "cypher": self.cypher,
            "allowed": self.allowed,
            "violations": self.violations,
            "rows": self.rows,
            "answer": self.answer,
            "sources": self.sources,
            "has_data": self.has_data,
        }


class QueryAgent:
    def __init__(self, llm: LLMClient, store: Neo4jStore, max_attempts: int = MAX_GENERATION_ATTEMPTS) -> None:
        self.llm = llm
        self.store = store
        self.max_attempts = max_attempts

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ask(self, question: str) -> QueryResult:
        result = QueryResult(question=question)
        messages = [{"role": "system", "content": self._system_prompt()}]
        messages.append({"role": "user", "content": f"Question: {question}"})

        for attempt in range(self.max_attempts):
            cypher = self._generate_cypher(messages)
            if not cypher:
                result.answer = self._failure("I couldn't produce a valid query for this question.")
                return result

            result.cypher = cypher
            violations = validate_cypher(cypher)
            if violations:
                result.allowed = False
                result.violations = violations
                logger.warning("generated Cypher rejected: %s", violations)
                messages.append(self._feedback(violations))
                continue

            try:
                result.rows = self.store.run_read(cypher)
            except Exception as exc:  # syntax / runtime errors from the graph
                logger.warning("Cypher execution failed: %s", exc)
                messages.append(self._feedback([f"execution error: {exc}"]))
                continue

            break

        if not result.allowed:
            result.answer = self._failure(
                "The generated query used disallowed labels or clauses, so it was not run."
            )
            return result

        if not result.cypher:
            result.answer = self._failure("I couldn't generate a query for this question.")
            return result

        if not result.rows:
            result.answer = NO_DATA_ANSWER
            return result

        result.answer, result.sources = self._grounded_answer(question, result.rows)
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _system_prompt(self) -> str:
        return (
            "You are Baleen's query agent: you translate natural-language "
            "questions about financial events into read-only Cypher, and then "
            "answer them factually from query results.\n\n"
            f"GRAPH SCHEMA (authoritative):\n{schema_context()}\n\n"
            "When generating Cypher, always call the `generate_cypher` tool. "
            "Parameterize literal user values (e.g. names, tickers) with "
            "$parameters. Never use CREATE, MERGE, SET, DELETE, or CALL. If the "
            "question cannot be answered from this schema, call `generate_cypher` "
            "with a query that returns nothing."
        )

    def _generate_cypher(self, messages: list[dict]) -> str:
        try:
            response = self.llm.chat_with_tools(messages, [GENERATE_TOOL])
        except LLMError as exc:
            logger.warning("Cypher generation LLM call failed: %s", exc)
            return ""
        for call in response.tool_calls:
            if call.name == "generate_cypher":
                return str(call.arguments.get("cypher", "")).strip()
        logger.warning("LLM returned no generate_cypher tool call")
        return ""

    def _grounded_answer(self, question: str, rows: list[dict]) -> tuple[str, list[str]]:
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\n"
                    "Query results (ground truth; answer ONLY from these):\n"
                    f"{rows}"
                ),
            },
        ]
        try:
            response = self.llm.chat_with_tools(messages, [ANSWER_TOOL])
        except LLMError as exc:
            logger.warning("answer generation LLM call failed: %s", exc)
            return "I found data but couldn't summarize it.", self._sources_from_rows(rows)

        for call in response.tool_calls:
            if call.name == "answer_question":
                sources = call.arguments.get("sources") or []
                return (
                    str(call.arguments.get("answer", "")).strip(),
                    [str(s) for s in sources] or self._sources_from_rows(rows),
                )
        return "I found data but couldn't summarize it.", self._sources_from_rows(rows)

    def _sources_from_rows(self, rows: list[dict]) -> list[str]:
        sources: list[str] = []
        for row in rows:
            for value in row.values():
                if isinstance(value, str) and value.startswith("https://x.com/"):
                    if value not in sources:
                        sources.append(value)
        return sources

    def _feedback(self, problems: list[str]) -> dict:
        return {
            "role": "user",
            "content": (
                "Your previous Cypher was rejected. Fix it and call "
                "`generate_cypher` again. Problems:\n- " + "\n- ".join(problems)
            ),
        }

    def _failure(self, message: str) -> str:
        return f"I couldn't answer this question confidently. {message}"
