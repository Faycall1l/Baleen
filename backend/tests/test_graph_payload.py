"""Unit tests for the pure /api/graph payload helpers (B1).

No DB required: rows are faked as plain dicts (the route only uses
``row["node"].items()``, ``row["labels"]``, ``row["rel"]``,
``row["target"]``/``row["target_labels"]`` — all satisfied by dicts).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.api.graph_payload import (
    build_graph_payload,
    build_links,
    build_nodes,
    json_safe,
    node_key,
)


def _row(node_props, label, rel=None, target_props=None, target_label=None):
    return {
        "node": dict(node_props),
        "labels": [label],
        "rel": rel,
        "target": dict(target_props) if target_props is not None else None,
        "target_labels": [target_label] if target_label is not None else None,
    }


def test_node_key_uses_label_merge_key():
    assert node_key("Ticker", {"ticker": "NVDA"}) == "NVDA"
    assert node_key("Politician", {"id": "P1", "name": "X"}) == "P1"


def test_node_key_falls_back_when_label_key_missing():
    assert node_key("Ticker", {"id": "evt-1"}) == "evt-1"
    assert node_key("Ticker", {"anything": 1}).startswith("{")


def test_json_safe_temporal_and_passthrough():
    from neo4j.time import Date as Neo4jDate
    from neo4j.time import DateTime as Neo4jDateTime

    assert json_safe(Neo4jDateTime(2026, 1, 16, 12, 30, 0)) == "2026-01-16T12:30:00.000000000"
    assert json_safe(Neo4jDate(2026, 1, 15)) == "2026-01-15"
    assert json_safe(datetime(2026, 1, 16, tzinfo=timezone.utc)) == "2026-01-16T00:00:00+00:00"
    assert json_safe(date(2026, 1, 15)) == "2026-01-15"
    assert json_safe("NVDA") == "NVDA"
    assert json_safe(42) == 42


def test_json_safe_recurses():
    from neo4j.time import DateTime as Neo4jDateTime

    rows = [{"at": Neo4jDateTime(2026, 9, 10, 12, 0, 0), "tags": ["a", "b"]}]
    import json

    safe = json_safe(rows)
    json.dumps(safe)
    assert safe[0]["at"] == "2026-09-10T12:00:00.000000000"


def test_build_nodes_dedupes_and_falls_back_title():
    rows = [
        _row({"ticker": "NVDA"}, "Ticker"),
        _row({"ticker": "NVDA"}, "Ticker"),  # duplicate (label, key)
        _row({"id": "e1"}, "Event"),
    ]
    nodes, ids = build_nodes(rows)
    assert len(nodes) == 2
    assert ids[("Ticker", "NVDA")] == "Ticker:NVDA"
    by_id = {n["id"]: n for n in nodes}
    assert by_id["Ticker:NVDA"]["title"] == "NVDA"
    assert by_id["Event:e1"]["title"] == "e1"


def test_build_nodes_serializes_props_and_tweet_url():
    rows = [_row({"id": "e1", "at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                  "tweet_url": "https://x.com/1"}, "Event")]
    nodes, _ = build_nodes(rows)
    assert nodes[0]["props"]["at"] == "2026-01-01T00:00:00+00:00"
    assert nodes[0]["tweet_url"] == "https://x.com/1"


def test_build_links_drops_dangling_and_null_rel():
    rows = [
        _row({"ticker": "NVDA"}, "Ticker", rel="IN_SECTOR",
             target_props={"name": "Chips"}, target_label="Sector"),
        _row({"ticker": "NVDA"}, "Ticker"),  # rel None -> no link
        _row({"ticker": "ORPH"}, "Ticker", rel="IN_SECTOR",
             target_props={"name": "Ghost"}, target_label="Sector"),
        # Every kept node also appears as `n` in some row (the combined
        # query returns each node as the match head), so targets resolve.
        _row({"name": "Chips"}, "Sector"),
        _row({"name": "Ghost"}, "Sector"),
    ]
    # Nodes: NVDA, ORPH, Chips, Ghost (targets alone don't create nodes).
    nodes, ids = build_nodes(rows)
    assert len(nodes) == 4  # NVDA, Chips, ORPH, Ghost
    links = build_links(rows, ids)
    assert len(links) == 2
    assert links[0] == {
        "source": "Ticker:NVDA",
        "target": "Sector:Chips",
        "type": "IN_SECTOR",
        "tweet_url": None,
    }

    # Dangling: target outside the kept set is dropped.
    ids_kept = {k: v for k, v in ids.items() if k != ("Sector", "Ghost")}
    assert build_links(rows, ids_kept) == [links[0]]


def test_build_graph_payload_envelope_flags():
    rows = [_row({"ticker": "NVDA"}, "Ticker")]
    full = build_graph_payload(rows, total=10, limit=5, delta=False, keep_ids=["x"])
    assert full["node_count"] == 1 and full["link_count"] == 0
    assert full["event_count"] == 10
    assert full["truncated"] is True
    assert full["delta"] is False

    # total <= limit -> not truncated; delta carries None total.
    assert build_graph_payload(rows, 3, 5, False, ["x"])["truncated"] is False
    assert build_graph_payload(rows, None, 5, False, ["x"])["truncated"] is False
    assert build_graph_payload(rows, 10, 5, False, None)["truncated"] is False
    delta = build_graph_payload(rows, None, 5, True, ["x"])
    assert delta["delta"] is True and delta["event_count"] is None


def test_build_graph_payload_empty():
    body = build_graph_payload([], total=0, limit=10, delta=False, keep_ids=None)
    assert body["nodes"] == [] and body["links"] == []
    assert body["node_count"] == 0 and body["link_count"] == 0
    assert body["truncated"] is False
