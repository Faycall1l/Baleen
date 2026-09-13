"""Allowlist validation for LLM-generated Cypher (spec §5.4).

The query agent never executes a statement directly: every Cypher string is
checked here first. Two layers:

1. **Clause guard** — reject any statement that could write or mutate:
   ``CREATE``, ``MERGE``, ``SET``, ``DELETE``, ``DETACH``, ``REMOVE``,
   ``DROP``, ``ALTER``, ``CREATE/DROP INDEX/CONSTRAINT``, and procedure calls
   (``CALL``).
2. **Label/relationship allowlist** — every ``(:Label)`` and ``[:TYPE]`` written
   in the query must be in the fixed schema (see ``app.graph.schema``).

In addition, the agent runs against a read-only Neo4j role, so even a bypassed
check could not mutate the graph.
"""

from __future__ import annotations

import re

from app.graph.schema import ALLOWED_LABELS, ALLOWED_RELATIONSHIPS

#: Words that open a statement we refuse to run. Word-boundary anchored so
#: identifiers like ``CREATE_UNIQUE`` don't false-positive (they can't occur
#: anyway, but the check is cheap). ``LOAD`` is blocked because ``LOAD CSV
#: FROM 'file://...'`` reads the server's local filesystem — a read-only
#: exfiltration vector a crafted prompt could trigger.
FORBIDDEN_CLAUSES = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|ALTER|CALL|LOAD|USE|SHOW|EXPLAIN|PROFILE)\b",
    re.IGNORECASE,
)

#: Property mutation, explicit index/constraint statements.
FORBIDDEN_PATTERNS = re.compile(
    r"\b(CREATE INDEX|DROP INDEX|CREATE CONSTRAINT|DROP CONSTRAINT)\b",
    re.IGNORECASE,
)

#: ``(:Label)`` — with optional chained labels ``(:A:B)``.
LABEL_RE = re.compile(r"\([^()]*?:([A-Za-z_][\w:]*)")
#: ``[:TYPE]``.
REL_RE = re.compile(r"\[[^[\]]*?:([A-Za-z_][\w:]*)")


def validate_cypher(query: str) -> list[str]:
    """Return a list of allowlist violations, or ``[]`` if the query is safe.

    Only whitespace/case differences are normalized; we deliberately avoid a
    full Cypher parser to keep the gate small and predictable.
    """
    violations: list[str] = []

    if FORBIDDEN_CLAUSES.search(query):
        violations.append("statement contains a forbidden clause (CREATE/MERGE/SET/DELETE/DETACH/REMOVE/DROP/ALTER/CALL/LOAD/USE/SHOW/EXPLAIN/PROFILE)")
    if FORBIDDEN_PATTERNS.search(query):
        violations.append("statement contains an index/constraint definition")
    if _has_embedded_statement_separator(query):
        violations.append("statement contains more than one Cypher statement")

    for label in _extract(LABEL_RE, query):
        if label not in ALLOWED_LABELS:
            violations.append(f"label `{label}` is not in the allowlist")
    for rel in _extract(REL_RE, query):
        if rel not in ALLOWED_RELATIONSHIPS:
            violations.append(f"relationship `{rel}` is not in the allowlist")

    return violations


def _has_embedded_statement_separator(query: str) -> bool:
    """Reject multi-statement queries (``a; b``). A single trailing ``;`` is
    tolerated because models habitually terminate statements with it; the agent
    strips it before execution."""
    stripped = query.rstrip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    return ";" in stripped


def _extract(pattern: re.Pattern, query: str) -> list[str]:
    found: list[str] = []
    for match in pattern.finditer(query):
        for part in match.group(1).split(":"):
            if part:
                found.append(part)
    return found
