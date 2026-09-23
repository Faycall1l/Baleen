"""Pure helpers shaping the ``GET /api/graph`` payload (spec §6).

Split out of :mod:`app.api.routes` (B1) so the shaping logic is unit-testable
without a live Neo4j: every function here is a pure function of its inputs —
no DB, no FastAPI, no settings. The route owns Cypher + cache; this module
owns keys, JSON-safety, node/link shaping, and envelope flags.
"""

from __future__ import annotations

from typing import Any

from app.graph.schema import LABEL_KEYS


def node_key(label: str, props: dict) -> str:
    """Stable ``Label:key`` suffix for a node (MERGE-key aware).

    Falls back to ``id``, then ``repr(props)`` when the label's key is absent
    — same fallback the route always used, so untagged nodes never collapse.
    """
    key = LABEL_KEYS.get(label, "id")
    value = props.get(key)
    if value is None:
        value = props.get("id") or str(props)
    return str(value)


def json_safe(value: Any) -> Any:
    """Serialize Neo4j temporal values to ISO strings for JSON.

    Node props and query rows carry native temporal values after the writer
    stores them with ``mode="python"``. Duck-types on ``isoformat`` because
    the Neo4j driver returns its own temporal classes (``neo4j.time.DateTime``/
    ``Date``) that are *not* subclasses of stdlib ``datetime``/``date``.
    Recurses into lists/dicts (``collect(...)`` projections nest values).
    """
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    return value


def build_nodes(rows: list[dict]) -> tuple[list[dict], dict[tuple[str, str], str]]:
    """Shape node dicts from combined node/link rows; dedupe by (label, key).

    Returns ``(nodes, ids_by_key)`` where ``ids_by_key`` maps
    ``(label, key) -> node_id`` for link resolution.
    """
    nodes: list[dict] = []
    ids_by_key: dict[tuple[str, str], str] = {}
    for row in rows:
        props = dict(row["node"].items())
        label = row["labels"][0]
        key = node_key(label, props)
        if (label, key) in ids_by_key:
            continue
        node_id = f"{label}:{key}"
        ids_by_key[(label, key)] = node_id
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "key": key,
                "title": props.get("name") or props.get("ticker") or props.get("id") or node_id,
                "props": {k: json_safe(v) for k, v in props.items()},
                "tweet_url": props.get("tweet_url"),
            }
        )
    return nodes, ids_by_key


def build_links(rows: list[dict], ids_by_key: dict[tuple[str, str], str]) -> list[dict]:
    """Shape link dicts, dropping edges touching nodes outside the kept set."""
    links: list[dict] = []
    for row in rows:
        if row["rel"] is None or row["target"] is None:
            continue
        a_label, b_label = row["labels"][0], row["target_labels"][0]
        a_key = node_key(a_label, dict(row["node"].items()))
        b_key = node_key(b_label, dict(row["target"].items()))
        source = ids_by_key.get((a_label, a_key))
        target = ids_by_key.get((b_label, b_key))
        if source and target:
            links.append(
                {
                    "source": source,
                    "target": target,
                    "type": row["rel"],
                    "tweet_url": (row["node"] or {}).get("tweet_url"),
                }
            )
    return links


def build_graph_payload(
    rows: list[dict],
    total: int | None,
    limit: int,
    delta: bool,
    keep_ids: list[str] | None,
) -> dict:
    """Assemble the full ``/api/graph`` envelope from rows + query context."""
    nodes, ids_by_key = build_nodes(rows)
    links = build_links(rows, ids_by_key)
    return {
        "nodes": nodes,
        "links": links,
        "node_count": len(nodes),
        "link_count": len(links),
        "event_count": total,
        "truncated": keep_ids is not None and total is not None and total > limit,
        "delta": delta,
    }
