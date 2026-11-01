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

#: Canonical directed edges (from_label, rel_type, to_label). Every schema
#: relationship flows one way; a labeled edge pointing anywhere else joins
#: nonsense and silently returns empty — the live failure that motivated
#: this check (``(p:Politician)-[:INVOLVES_POLITICIAN]->(e:Event)``).
DIRECTED_EDGES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("Ticker", "IN_SECTOR", "Sector"),
        ("Politician", "MEMBER_OF", "Committee"),
        ("Committee", "OVERSEES", "Sector"),
        ("Event", "INVOLVES_TICKER", "Ticker"),
        ("Event", "INVOLVES_POLITICIAN", "Politician"),
        ("Event", "INVOLVES_INSIDER", "Insider"),
        ("Event", "IN_COMMITTEE", "Committee"),
    }
)

#: Labeled node with optional variable and optional inline properties:
#: ``(t:Ticker)``, ``(t:Ticker {ticker: 'NVDA'})``. Captures the label.
_LABELED_NODE = r"\(\s*(?:[A-Za-z_]\w*\s*:)?\s*([A-Za-z_]\w*)\b[^()]*\)"
#: ``-[r:TYPE]->`` with optional variable and the mandatory colon.
_REL_RIGHT = r"-\[\s*:?\s*(?:[A-Za-z_]\w*\s*:)?\s*([A-Za-z_]\w*)\s*\]->"
_EDGE_RIGHT_RE = re.compile(_LABELED_NODE + _REL_RIGHT + _LABELED_NODE)
_EDGE_LEFT_RE = re.compile(_LABELED_NODE + r"<-\[\s*:?\s*(?:[A-Za-z_]\w*\s*:)?\s*([A-Za-z_]\w*)\s*\]-" + _LABELED_NODE)


def validate_cypher(query: str) -> list[str]:
    """Return a list of allowlist violations, or ``[]`` if the query is safe.

    Only whitespace/case differences are normalized; we deliberately avoid a
    full Cypher parser to keep the gate small and predictable.
    """
    violations: list[str] = []

    if FORBIDDEN_CLAUSES.search(query):
        violations.append(
            "statement contains a forbidden clause (CREATE/MERGE/SET/DELETE/DETACH/REMOVE/DROP/ALTER/CALL/LOAD/USE/SHOW/EXPLAIN/PROFILE)"
        )
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
    violations.extend(_directed_edge_violations(query))

    return violations


def _directed_edge_violations(query: str) -> list[str]:
    """Flag labeled edges running against the schema direction.

    Only judges edges where both endpoint labels are present and the
    relationship type is known — unlabeled or undirected patterns stay
    permissive so working queries never break on this check.
    """
    violations: list[str] = []
    for from_label, rel, to_label in _EDGE_RIGHT_RE.findall(query):
        violations.extend(_check_edge(from_label, rel, to_label, "->"))
    for to_label, rel, from_label in _EDGE_LEFT_RE.findall(query):
        violations.extend(_check_edge(from_label, rel, to_label, "->"))
    return violations


def _check_edge(from_label: str, rel: str, to_label: str, arrow: str) -> list[str]:
    if rel not in ALLOWED_RELATIONSHIPS:
        return []
    if from_label not in ALLOWED_LABELS or to_label not in ALLOWED_LABELS:
        return []
    if (from_label, rel, to_label) in DIRECTED_EDGES:
        return []
    return [f"edge `({from_label})-[:{rel}]{arrow}({to_label})` runs against the schema direction"]


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


_NODE_RE = re.compile(r"\(([^()]*)\)")
_NODE_LABEL_RE = re.compile(r"^\s*(?:[A-Za-z_]\w*)?\s*:\s*([A-Za-z_]\w*)")
_REL_SEGMENT_RE = re.compile(r"^(\s*)(<-|-)(\[\s*(?:[A-Za-z_]\w*)?\s*:\s*([A-Za-z_]\w*)[^\]]*\])(->|-)(\s*)$")


def repair_edge_directions(query: str) -> tuple[str, list[str]]:
    """Flip labeled edges written against the schema direction.

    Every relationship type joins a fixed pair of labels in one direction, so
    ``(t:Ticker)-[:INVOLVES_TICKER]->(e:Event)`` can only mean the reverse
    edge. Smaller models make this slip and then repeat it after feedback;
    repairing it deterministically is safer than rejecting the question.
    Only edges whose two labels and type are known, and whose reverse is a
    schema edge, are touched. Returns the query and a note per repair.
    """
    nodes = list(_NODE_RE.finditer(query))
    pieces: list[str] = []
    notes: list[str] = []
    cursor = 0
    for left, right in zip(nodes, nodes[1:], strict=False):
        between = query[left.end() : right.start()]
        seg = _REL_SEGMENT_RE.match(between)
        left_label = _NODE_LABEL_RE.match(left.group(1))
        right_label = _NODE_LABEL_RE.match(right.group(1))
        if not (seg and left_label and right_label):
            continue
        lead, head, body, rel, tail, trail = seg.groups()
        if head == "<-" and tail == "-":
            src, dst = right_label.group(1), left_label.group(1)
            flipped = f"{lead}-{body}->{trail}"
        elif head == "-" and tail == "->":
            src, dst = left_label.group(1), right_label.group(1)
            flipped = f"{lead}<-{body}-{trail}"
        else:
            continue
        if (src, rel, dst) in DIRECTED_EDGES or (dst, rel, src) not in DIRECTED_EDGES:
            continue
        pieces.append(query[cursor : left.end()])
        pieces.append(flipped)
        cursor = right.start()
        notes.append(f"flipped ({src})-[:{rel}]->({dst})")
    pieces.append(query[cursor:])
    return "".join(pieces), notes
