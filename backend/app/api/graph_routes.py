"""``GET /api/graph`` — the knowledge-graph payload for the Sigma UI.

Split out of :mod:`app.api.routes` so the Cypher/serialization path reads on
its own. Every service lookup goes through ``app.api.routes`` at call time, so
test monkeypatches on the shared clients still apply (the same pattern
:mod:`app.api.stream` uses).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.graph_payload import build_graph_payload, json_safe, node_key
from app.graph.schema import ALLOWED_LABELS, LABEL_KEYS

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_api_key(request: Request) -> None:
    """Thin indirection so the guard stays owned by :mod:`app.api.routes`
    without a module-load circular import."""
    from app.api.routes import require_api_key

    require_api_key(request)


def _parse_since(since: str | None) -> datetime | None:
    """Parse the poll cursor; an unparseable value falls back to a full render."""
    if not since:
        return None
    try:
        dt = datetime.fromisoformat(since)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@router.get("/graph", dependencies=[Depends(_require_api_key)])
def graph(
    limit: int = Query(default=500, ge=0, le=5000),
    since: str | None = Query(default=None),
    before: str | None = Query(default=None),
    without_events: bool = Query(default=False),
) -> dict:
    """Serialize a subgraph for the graph UI.

    With ``limit`` > 0, only the ``limit`` most-recent events and their direct
    neighbors (tickers, politicians, committees, sectors) are returned, so a
    large live graph stays responsive. ``limit=0`` returns everything (demo /
    small graphs). The response carries ``truncated``/``event_count`` so the UI
    can tell a partial view from a full one. ``limit`` is capped at 5000
    so a single request cannot force an unbounded materialization.

    With ``since`` (ISO timestamp), only events at/after that time (plus
    their neighborhoods) are returned, flagged with ``delta: true`` — the
    incremental-sync pattern for polling: the UI merges deltas into its
    snapshot and refetches full every Nth poll as the resync. An unparseable
    ``since`` falls back to a full render. Deltas skip the total-events count
    and the cache (both pointless for a moving window).

    Deltas are deliberately NOT ``limit``-bounded : a
    capped delta could silently drop events and break merge correctness, and
    ``truncated`` cannot signal it (``event_count`` is None for deltas). A
    wide catch-up window can therefore exceed a capped full render (measured:
    7d window -> 1390 nodes vs 822 for limit=500 full). This is the rare path
    (reconnect after downtime); steady-state polls use small windows and stay
    ~0.1s. Do NOT add a LIMIT to the delta keep-pass without also wiring a
    truncation signal the UI resyncs on.

    With ``before`` (ISO timestamp), the render is a snapshot as of that
    time: only events at/before it (plus their neighborhoods), with
    ``event_count``/``truncated`` computed against the as-of universe. This
    powers the time scrubber. ``since`` and ``before`` are mutually
    exclusive. As-of renders bypass the cache (a scrub drag fires many
    distinct values; caching each would just bloat memory) and echo the
    bound back as ``before`` so the UI can label the frozen frame.

    The payload is cached for ``graph_cache_ttl_seconds`` to keep UI polling
    cheap (``event_count``/``truncated`` tolerate a few seconds of staleness).
    """
    from app.api import routes

    since_dt = _parse_since(since)
    before_dt = _parse_since(before)
    if since_dt is not None and before_dt is not None:
        raise HTTPException(status_code=400, detail="since and before are mutually exclusive")
    ttl = routes.get_settings().graph_cache_ttl_seconds
    if ttl > 0 and since_dt is None and before_dt is None:
        with routes._graph_cache_lock:
            hit = routes._graph_cache.get(limit)
        if hit and hit[0] > time.monotonic():
            return hit[1]

    with routes.neo4j_read_slot():
        payload = _render_graph(limit, since_dt, before_dt, without_events)

    if ttl > 0 and since_dt is None and before_dt is None:
        with routes._graph_cache_lock:
            routes._graph_cache[limit] = (time.monotonic() + ttl, payload)
    if before_dt is not None:
        payload = {**payload, "before": before_dt.isoformat()}
    return payload


def _split_node_ref(ref: str) -> tuple[str, str] | None:
    """Split a ``Label:key`` node reference, validated against the schema.

    Labels interpolate into Cypher (parameters cannot), so anything outside
    the allowlist is rejected here — the same gating the writer relies on.
    """
    if ":" not in ref:
        return None
    label, _, key = ref.partition(":")
    if label not in ALLOWED_LABELS or not key:
        return None
    return label, key


@router.get("/graph/path", dependencies=[Depends(_require_api_key)])
def graph_path(
    from_node: str = Query(alias="from", min_length=1),
    to_node: str = Query(alias="to", min_length=1),
) -> dict:
    """Shortest relationship path between two nodes (R53a).

    Powers "how is A connected to B" — the relationship-noticing workflow
    the full-graph hairball cannot answer. Undirected, max 6 hops; no path
    answers 200 with empty lists (probing is normal use, not an error).
    """
    from app.api import routes

    parsed_from = _split_node_ref(from_node)
    parsed_to = _split_node_ref(to_node)
    if parsed_from is None or parsed_to is None:
        raise HTTPException(status_code=422, detail="from/to must be Label:key with a known label")
    (from_label, from_key), (to_label, to_key) = parsed_from, parsed_to

    try:
        with routes.neo4j_read_slot():
            rows = routes._get_neo4j().run_read(
                f"""
                MATCH (a:{from_label} {{{LABEL_KEYS[from_label]}: $from_key}}),
                      (b:{to_label} {{{LABEL_KEYS[to_label]}: $to_key}})
                MATCH p = shortestPath((a)-[*..6]-(b))
                RETURN [x IN nodes(p) | {{labels: labels(x), props: properties(x)}}] AS nodes,
                       [r IN relationships(p) | type(r)] AS rels
                """,
                from_key=from_key,
                to_key=to_key,
            )
    except Exception as exc:
        logger.exception("graph path failed")
        raise HTTPException(status_code=500, detail=f"graph path failed: {exc}") from None
    if not rows:
        return {"from": from_node, "to": to_node, "nodes": [], "rels": [], "length": 0}
    path = rows[0]
    nodes = []
    for entry in path["nodes"]:
        label = entry["labels"][0]
        props = json_safe(entry["props"])
        key = node_key(label, props)
        nodes.append(
            {
                "id": f"{label}:{key}",
                "label": label,
                "title": props.get("name") or props.get("ticker") or props.get("id") or key,
            }
        )
    return {
        "from": from_node,
        "to": to_node,
        "nodes": nodes,
        "rels": list(path["rels"]),
        "length": len(path["rels"]),
    }


def _render_graph(
    limit: int,
    since_dt: datetime | None = None,
    before_dt: datetime | None = None,
    without_events: bool = False,
) -> dict:
    from app.api import routes

    neo4j = routes._get_neo4j()
    delta = since_dt is not None
    try:
        # The total-events count is a full label scan; deltas skip it (the UI
        # keeps the last full total) so steady-state polls stay index-bound.
        # As-of renders count their own universe so truncation stays honest.
        if delta:
            total = None
        elif before_dt is not None:
            total = neo4j.run(
                "MATCH (e:Event) WHERE e.event_time <= datetime($before) RETURN count(e) AS n",
                before=before_dt.isoformat(),
            )[0]["n"]
        else:
            total = neo4j.run("MATCH (e:Event) RETURN count(e) AS n")[0]["n"]
        keep_ids = _keep_ids(neo4j, limit, since_dt, delta, before_dt) if limit > 0 or delta else None
        rows = _fetch_nodes_and_links(neo4j, keep_ids, without_events)
    except Exception as exc:
        logger.exception("graph export failed")
        raise HTTPException(status_code=500, detail=f"graph export failed: {exc}") from None

    return build_graph_payload(rows, total, limit, delta, keep_ids)


def _keep_ids(
    neo4j,
    limit: int,
    since_dt: datetime | None,
    delta: bool,
    before_dt: datetime | None = None,
) -> list[str]:
    """Element ids of the events to keep, plus their direct neighbors.

    Two-pass approach: the COALESCE wrapper in the original query prevented
    Neo4j from using the range index on ``event_time``, forcing a full label
    scan + sort. Splitting into two index-friendly paths (non-null event_time
    → ORDER BY + LIMIT; null event_time → LIMIT only) lets the planner use the
    B-tree index for the sorted path. Deltas filter the same indexed path by
    the cursor instead of sorting the whole graph. The as-of bound is baked
    into the query text (not a nullable parameter) so the no-bound plan —
    the hot polling path — is byte-identical to before.
    """
    if delta:
        assert since_dt is not None
        rows = neo4j.run(
            """
            MATCH (e:Event)
            WHERE e.event_time IS NOT NULL
              AND e.event_time >= datetime($since)
            OPTIONAL MATCH (e)--(n)
            WITH collect(DISTINCT e) + collect(DISTINCT n) AS all_keep
            UNWIND [x IN all_keep WHERE x IS NOT NULL] AS kept
            RETURN DISTINCT elementId(kept) AS internal_id
            """,
            since=since_dt.isoformat(),
        )
    elif before_dt is not None:
        rows = neo4j.run(
            """
            MATCH (e:Event)
            WHERE e.event_time IS NOT NULL
              AND e.event_time <= datetime($before)
            WITH e ORDER BY e.event_time DESC
            LIMIT $limit
            OPTIONAL MATCH (e)--(n)
            WITH collect(DISTINCT e) + collect(DISTINCT n) AS all_keep
            UNWIND [x IN all_keep WHERE x IS NOT NULL] AS kept
            RETURN DISTINCT elementId(kept) AS internal_id
            """,
            limit=limit,
            before=before_dt.isoformat(),
        )
    else:
        rows = neo4j.run(
            """
            MATCH (e:Event)
            WHERE e.event_time IS NOT NULL
            WITH e ORDER BY e.event_time DESC
            LIMIT $limit
            OPTIONAL MATCH (e)--(n)
            WITH collect(DISTINCT e) + collect(DISTINCT n) AS all_keep
            UNWIND [x IN all_keep WHERE x IS NOT NULL] AS kept
            RETURN DISTINCT elementId(kept) AS internal_id
            """,
            limit=limit,
        )
    keep_ids: list[str] = [row["internal_id"] for row in rows]
    # Pad with events that have no event_time (old / unparsed) if the indexed
    # path returned fewer than ``limit`` events. Full renders only — deltas are
    # strictly "what's new", never backfill.
    remaining = limit - len(keep_ids)
    if remaining > 0 and not delta:
        old_rows = neo4j.run(
            """
            MATCH (e:Event)
            WHERE e.event_time IS NULL
            WITH e LIMIT $remaining
            OPTIONAL MATCH (e)--(n)
            WITH collect(DISTINCT e) + collect(DISTINCT n) AS all_keep
            UNWIND [x IN all_keep WHERE x IS NOT NULL] AS kept
            RETURN DISTINCT elementId(kept) AS internal_id
            """,
            remaining=remaining,
        )
        keep_ids.extend(row["internal_id"] for row in old_rows)
    return keep_ids


def _fetch_nodes_and_links(neo4j, keep_ids: list[str] | None, without_events: bool = False) -> list[dict]:
    """One round trip for nodes + links (previously two sequential scans):
    every kept node's outgoing edges ride along, and links touching a node
    outside the kept set (or a tombstoned node) are dropped in Python exactly
    as before.
    """
    node_conditions = [
        *(["elementId(n) IN $keep_ids"] if keep_ids is not None else []),
        # Tombstoned identity nodes (merged_into set, see IdentityEngine)
        # are hidden from the UI: their SAME_AS edge is provenance, not a
        # user-facing connection.
        "(NOT n:Politician OR n.merged_into IS NULL)",
        "(NOT n:Insider OR n.merged_into IS NULL)",
    ]
    if without_events:
        node_conditions.append("(NOT n:Event)")
    return neo4j.run(
        f"""
        MATCH (n) WHERE {" AND ".join(node_conditions)}
        OPTIONAL MATCH (n)-[r]->(m)
        RETURN n AS node, labels(n) AS labels,
               type(r) AS rel, m AS target, labels(m) AS target_labels
        """,
        keep_ids=keep_ids or [],
    )
