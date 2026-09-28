"""Guardrailed natural-language -> Cypher query agent (spec 5.4).

Pipeline for one question:

1. The fixed graph schema (``app.graph.schema``) is handed to the LLM as
   context — the schema is never inferred.
2. The LLM proposes read-only Cypher via tool-calling (or constrained JSON).
3. :func:`app.query.validate.validate_cypher` enforces the label/relationship
   allowlist and rejects write clauses.
4. The Cypher runs against the **read-only** Neo4j driver, bounded by
   :func:`app.query.tools.bounded_query`.
5. The LLM writes a grounded answer citing source tweet URLs pulled from the
   rows themselves.

If generation/validation fails repeatedly, or the query returns zero rows, the
agent says so — it never fabricates an answer that isn't grounded in graph
results. Tool schemas and limits live in :mod:`app.query.tools` (L6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.db.neo4j import Neo4jStore
from app.extraction.client import LLMClient, LLMError
from app.graph.schema import schema_context
from app.query.tools import (
    ANSWER_TOOL,
    GENERATE_TOOL,
    MAX_GENERATION_ATTEMPTS,
    NO_DATA_ANSWER,
    bounded_query,
)
from app.query.validate import validate_cypher

logger = logging.getLogger(__name__)

__all__ = [
    "ANSWER_TOOL",
    "GENERATE_TOOL",
    "MAX_GENERATION_ATTEMPTS",
    "NO_DATA_ANSWER",
    "QueryAgent",
    "QueryResult",
]


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
            # Each attempt stands on its own: a rejection in an earlier attempt
            # must not poison a later, valid one.
            result.allowed = True
            result.violations = []
            exec_error: str | None = None

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
                bounded = bounded_query(cypher)
                result.cypher = bounded
                result.rows = self.store.run_read(bounded)
            except Exception as exc:  # syntax / runtime errors from the graph
                logger.warning("Cypher execution failed: %s", exc)
                exec_error = str(exc)
                messages.append(self._feedback([f"execution error: {exc}"]))
                continue

            break

        if not result.allowed:
            result.answer = self._failure(
                "The generated query used disallowed labels or clauses, so it was not run."
            )
            return result

        if exec_error:
            result.answer = self._failure(f"The generated query failed to run: {exec_error}")
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
            "Inline literal user values directly (quote strings); never use "
            "$parameters — the executor does not bind them. Never use CREATE, "
            "MERGE, SET, DELETE, or CALL. If the question cannot be answered "
            "from this schema, call `generate_cypher` with a query that returns "
            "nothing."
        )

    def _generate_cypher(self, messages: list[dict]) -> str:
        try:
            response = self.llm.chat_with_tools(messages, [GENERATE_TOOL])
        except LLMError as exc:
            logger.warning("Cypher generation LLM call failed: %s", exc)
            return ""
        for call in response.tool_calls:
            # Accept an explicit generate_cypher call, or a nameless reply
            # carrying the cypher field (some models emit bare arguments).
            if call.name == "generate_cypher" or (
                not call.name and "cypher" in call.arguments
            ):
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
                    f"{rows}\n\n"
                    "Reply by calling the `answer_question` tool with `answer` "
                    "(plain English, grounded strictly in these rows) and "
                    "`sources` (the source URLs appearing in these rows)."
                ),
            },
        ]
        try:
            response = self.llm.chat_with_tools(messages, [ANSWER_TOOL])
        except LLMError as exc:
            logger.warning("answer generation LLM call failed: %s", exc)
            return "I found data but couldn't summarize it.", self._sources_from_rows(rows)

        for call in response.tool_calls:
            # Explicit answer_question call, or a nameless reply carrying the
            # answer field (some models emit bare arguments).
            if call.name == "answer_question" or (
                not call.name and "answer" in call.arguments
            ):
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
                # Any source URL counts — filings (Clerk/Senate PDFs), quote
                # pages, and tweets alike. The x.com-only check predates the
                # keyless feeds and would drop every real source link.
                if isinstance(value, str) and value.startswith(("https://", "http://")):
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
