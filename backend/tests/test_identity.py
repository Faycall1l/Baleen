"""IdentityEngine integration tests against the real graph.

A confirmed resolution (from the naming layer) is applied to the graph: the
candidate's relationships transfer to the canonical node, the candidate is
tombstoned, and a provenance SAME_AS edge links them.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.graph.identity import IdentityEngine, _MERGEABLE_LABELS
from app.graph.resolution import EntityResolver
from app.graph.writer import GraphWriter
from app.models.events import CongressTradeEvent


def _congress(
    tweet_id: str,
    politician_id: str,
    politician_name: str,
    committees: list[str],
    ticker: str = "NVDA",
) -> CongressTradeEvent:
    return CongressTradeEvent(
        tweet_id=tweet_id,
        tweet_url=f"https://x.com/congress_trades/status/{tweet_id}",
        confidence=0.95,
        politician_name=politician_name,
        politician_id=politician_id,
        chamber="senate",
        party="D",
        committee_ids=committees,
        ticker=ticker,
        transaction_type="buy",
        amount_range="$1,001 - $15,000",
        transaction_date=date(2026, 1, 15),
    )


def _write(neo4j_store, event: CongressTradeEvent) -> str:
    writer = GraphWriter(neo4j_store)
    return writer.write_event(event)


def _confirmed_resolution(postgres_store, candidate_id: str, alias: str) -> int:
    """Approve a gray-band pair so candidate_id resolves into a canonical id."""
    resolver = EntityResolver(postgres_store)
    postgres_store.ensure_alias(
        "Politician", alias, candidate_id, alias, source="seed"
    )
    postgres_store.ensure_alias(
        "Politician", "John Johnsen", "P-CANON", "John Johnsen", source="seed"
    )
    resolver.suggest_merges("Politician")
    queue_id = resolver.pending_reviews()[0]["id"]
    assert resolver.approve(queue_id)
    return queue_id


def _cleanup(
    neo4j_store, politician_ids: list[str] | None = None, event_ids: list[str] | None = None
) -> None:
    if politician_ids:
        neo4j_store.run(
            """
            MATCH (p:Politician)
            WHERE p.id IN $ids
            DETACH DELETE p
            """,
            ids=politician_ids,
        )
    for event_id in event_ids or []:
        neo4j_store.run("MATCH (e:Event {id: $id}) DETACH DELETE e", id=event_id)


def test_apply_merge_transfers_relationships_and_tombstones(
    neo4j_store, postgres_store
):
    cand_id, canon_id = "P-CAND", "P-CANON"
    event_a = _write(neo4j_store, _congress("ident-ea", cand_id, "John Johnson", ["C1"]))
    event_b = _write(neo4j_store, _congress("ident-eb", canon_id, "John Johnsen", ["C2", "C1"]))
    queue_id = _confirmed_resolution(postgres_store, cand_id, "John Johnson")

    try:
        engine = IdentityEngine(EntityResolver(postgres_store), neo4j_store)
        summary = engine.apply_merge(queue_id, "Politician")

        # The candidate's unique MEMBER_OF C1 is skipped (canon already has it),
        # but the EventA inbound relationship is transferred to the canonical.
        assert summary == {
            "transferred": 1,
            "skipped_duplicates": 1,
            "same_as": 1,
            "already_merged": False,
            "resumed": False,
        }

        assert (
            neo4j_store.run(
                "MATCH (:Event {id: $id})-[:INVOLVES_POLITICIAN]->(:Politician {id: $pid}) "
                "RETURN count(*) AS count",
                id=event_a,
                pid=canon_id,
            )[0]["count"]
            == 1
        )
        # Canonical accumulated both committees.
        assert (
            neo4j_store.run(
                "MATCH (:Politician {id: $pid})-[:MEMBER_OF]->(c:Committee) "
                "RETURN count(c) AS count",
                pid=canon_id,
            )[0]["count"]
            == 2
        )
        # Candidate is tombstoned with provenance.
        assert (
            neo4j_store.run(
                "MATCH (p:Politician {id: $id}) RETURN p.merged_into AS m",
                id=cand_id,
            )[0]["m"]
            == canon_id
        )
        sa = neo4j_store.run(
            "MATCH (:Politician {id: $c})-[r:SAME_AS]->(:Politician {id: $o}) "
            "RETURN r.resolution_id AS rid, r.source AS source",
            c=cand_id,
            o=canon_id,
        )[0]
        assert sa["rid"] == queue_id
        assert sa["source"] == "review"
        # No stray relationship survives on the tombstone.
        assert (
            neo4j_store.run(
                "MATCH (p:Politician {id: $id})-[r]->() WHERE type(r) <> 'SAME_AS' "
                "RETURN count(r) AS count",
                id=cand_id,
            )[0]["count"]
            == 0
        )

        # Idempotent re-apply.
        again = engine.apply_merge(queue_id, "Politician")
        assert again["already_merged"] is True
        assert (
            neo4j_store.run(
                "MATCH (:Politician {id: $id})-[:MEMBER_OF]->(c:Committee) "
                "RETURN count(c) AS count",
                id=canon_id,
            )[0]["count"]
            == 2
        )
    finally:
        _cleanup(neo4j_store, [cand_id, canon_id], [event_a, event_b])


def test_apply_merge_refuses_unshared_labels(neo4j_store, postgres_store):
    engine = IdentityEngine(EntityResolver(postgres_store), neo4j_store)
    with pytest.raises(ValueError):
        engine.apply_merge(1, "Ticker")
    assert _MERGEABLE_LABELS == {"Politician", "Insider"}


def test_apply_merge_requires_confirmed_resolution(neo4j_store, postgres_store):
    resolver = EntityResolver(postgres_store)
    postgres_store.ensure_alias("Politician", "John Johnson", "A1", "John Johnson", source="seed")
    postgres_store.ensure_alias("Politician", "John Johnsen", "A2", "John Johnsen", source="seed")
    resolver.suggest_merges("Politician")
    queue_id = resolver.pending_reviews()[0]["id"]  # still pending -> refused

    engine = IdentityEngine(resolver, neo4j_store)
    with pytest.raises(ValueError):
        engine.apply_merge(queue_id, "Politician")


def test_apply_merge_raises_when_events_missing(neo4j_store, postgres_store):
    queue_id = _confirmed_resolution(postgres_store, "P-CAND", "John Johnson")
    engine = IdentityEngine(EntityResolver(postgres_store), neo4j_store)
    with pytest.raises(ValueError):
        # No nodes exist under these ids: nothing to merge.
        engine.apply_merge(queue_id, "Politician")


def test_apply_merge_resumes_pre_existing_tombstone_without_same_as(
    neo4j_store, postgres_store
):
    """A tombstone that lost its SAME_AS edge (aborted/manual op) is healed by
    re-applying the merge instead of reporting 'already merged' (#22)."""
    cand_id, canon_id = "P-CAND", "P-CANON"
    event_a = _write(neo4j_store, _congress("ident-ra", cand_id, "John Johnson", ["C1"]))
    _write(neo4j_store, _congress("ident-rb", canon_id, "John Johnsen", ["C2"]))
    queue_id = _confirmed_resolution(postgres_store, cand_id, "John Johnson")

    try:
        # Simulate the partial state: tombstoned into the canonical but with no
        # SAME_AS provenance edge and relationships still on the candidate.
        neo4j_store.run(
            "MATCH (p:Politician {id: $id}) SET p.merged_into = $canon",
            id=cand_id,
            canon=canon_id,
        )

        engine = IdentityEngine(EntityResolver(postgres_store), neo4j_store)
        summary = engine.apply_merge(queue_id, "Politician")
        assert summary["resumed"] is True
        assert summary["already_merged"] is False
        assert summary["same_as"] == 1

        # Transfer completed and provenance healed.
        assert (
            neo4j_store.run(
                "MATCH (:Event {id: $id})-[:INVOLVES_POLITICIAN]->"
                "(:Politician {id: $pid}) RETURN count(*) AS count",
                id=event_a,
                pid=canon_id,
            )[0]["count"]
            == 1
        )
        assert (
            neo4j_store.run(
                "MATCH (:Politician {id: $c})-[r:SAME_AS]->(:Politician {id: $o}) "
                "RETURN r.resolution_id AS rid",
                c=cand_id,
                o=canon_id,
            )[0]["rid"]
            == queue_id
        )

        # A subsequent normal re-run is the idempotent no-op.
        again = engine.apply_merge(queue_id, "Politician")
        assert again["already_merged"] is True
    finally:
        _cleanup(neo4j_store, [cand_id, canon_id], [event_a, "ident-rb"])


def test_writer_remaps_candidate_id_after_merge(neo4j_store, postgres_store):
    cand_id, canon_id = "P-CAND", "P-CANON"
    event_a = _write(neo4j_store, _congress("ident-wa", cand_id, "John Johnson", ["C1"]))
    event_b = _write(neo4j_store, _congress("ident-wb", canon_id, "John Johnsen", ["C2"]))
    queue_id = _confirmed_resolution(postgres_store, cand_id, "John Johnson")

    try:
        engine = IdentityEngine(EntityResolver(postgres_store), neo4j_store)
        engine.apply_merge(queue_id, "Politician")

        # A new mention of the *candidate* id must land on the canonical node,
        # not resurrect a second candidate node.
        writer = GraphWriter(
            neo4j_store, entity_resolver=EntityResolver(postgres_store)
        )
        event_c = writer.write_event(
            _congress("ident-wc", cand_id, "John Johnson", ["C3"])
        )

        assert (
            neo4j_store.run(
                "MATCH (p:Politician) WHERE p.id = $id RETURN count(p) AS count",
                id=cand_id,
            )[0]["count"]
            == 1  # only the tombstone remains
        )
        assert (
            neo4j_store.run(
                "MATCH (p:Politician {id: $id}) RETURN p.merged_into AS m",
                id=cand_id,
            )[0]["m"]
            == canon_id
        )
        # The canonical node now carries all three Events' INVOLVES_POLITICIAN
        # (event_a was transferred, event_b + event_c went straight there).
        assert (
            neo4j_store.run(
                "MATCH (:Event)-[:INVOLVES_POLITICIAN]->(:Politician {id: $id}) "
                "RETURN count(*) AS count",
                id=canon_id,
            )[0]["count"]
            == 3
        )
        # Candidate and canonical are the only Politician nodes for these ids.
        assert (
            neo4j_store.run(
                "MATCH (p:Politician) WHERE p.id IN $ids RETURN count(p) AS count",
                ids=[cand_id, canon_id],
            )[0]["count"]
            == 2
        )

        # The canonical node remembers the canonical roster spelling.
        assert (
            neo4j_store.run(
                "MATCH (p:Politician {id: $id}) RETURN p.name AS name",
                id=canon_id,
            )[0]["name"]
            == "John Johnsen"
        )
    finally:
        _cleanup(neo4j_store, [cand_id, canon_id], [event_a, event_b, event_c])
