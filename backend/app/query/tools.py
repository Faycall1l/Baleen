"""Tool schemas and the read-only Cypher guardrails, split out of
:mod:`app.query.agent`.

The two tool definitions handed to the LLM, the shared limits, and the
statement bounding (``LIMIT`` injection, aggregation short-circuit) live here so
they can be asserted without standing up a ``QueryAgent``.
"""

from __future__ import annotations

import re

MAX_GENERATION_ATTEMPTS = 2

#: Result cap enforced on generated Cypher. A model that forgets LIMIT must not
#: dump the whole graph into the answer prompt / API response.
MAX_RESULT_ROWS = 200

_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_AGGREGATION_RE = re.compile(r"\b(?:count|collect|sum|avg|min|max)\s*\(", re.IGNORECASE)

GENERATE_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_cypher",
        "description": (
            "Write a read-only Cypher query that answers the user's question "
            "against the provided schema. Inline literal user values directly "
            "in the query (quote strings). Do NOT use $parameters — the executor "
            "does not bind them. Only MATCH/OPTIONAL MATCH/WHERE/RETURN/ORDER "
            "BY/LIMIT/WITH are allowed."
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


def bounded_query(query: str) -> str:
    """Cap result size when the generated query has no LIMIT.

    Aggregations return a handful of rows and keep their shape, so they are
    left untouched. Applied only at execution time — rejected queries are
    surfaced to the user exactly as generated.
    """
    cleaned = query.strip().rstrip(";").strip()
    if not cleaned:
        return cleaned
    if _LIMIT_RE.search(cleaned) or _AGGREGATION_RE.search(cleaned):
        return cleaned
    return f"{cleaned}\nLIMIT {MAX_RESULT_ROWS}"
