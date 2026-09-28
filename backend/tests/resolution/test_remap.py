"""Identity remap on approve: atomics, chains, cycles.

Part of the resolution suite split out of the former
``tests/test_resolution.py`` (M5). Everything runs against the real
``baleen_test`` Postgres and the shared Neo4j dev instance (conftest
fixtures) -- no mocks, in keeping with the research project's rigor bar.
"""

from __future__ import annotations

from tests.resolution.conftest import (
    resolver,
)


class TestIdentityRemap:
    def _confirmed_pair(self, postgres_store) -> int:
        postgres_store.ensure_alias(
            "Politician", "John Johnson", "X080", "John Johnson", source="seed"
        )
        postgres_store.ensure_alias(
            "Politician", "John Johnsen", "X081", "John Johnsen", source="seed"
        )
        resolver = resolver(postgres_store)
        resolver.suggest_merges("Politician")
        queue_id = resolver.pending_reviews()[0]["id"]
        assert resolver.approve(queue_id)
        return queue_id

    def test_approve_records_identity_remap_atomically(self, postgres_store):
        self._confirmed_pair(postgres_store)

        assert postgres_store.get_identity("Politician", "X080") == {
            "candidate_id": "X080",
            "canonical_id": "X081",
            "confidence": 0.917,
        }
        assert resolver(postgres_store).pending_reviews() == []

    def test_confirmed_pair_cannot_be_approved_again(self, postgres_store):
        queue_id = self._confirmed_pair(postgres_store)
        assert resolver(postgres_store).approve(queue_id) is False

    def test_canonical_identity_resolves_candidate_to_canonical(self, postgres_store):
        self._confirmed_pair(postgres_store)
        resolver = resolver(postgres_store)

        assert resolver.canonical_identity("Politician", "X080") == "X081"
        assert resolver.canonical_identity("Politician", "X081") == "X081"  # canonical stays put
        assert resolver.canonical_identity("Politician", "X999999") == "X999999"  # unknown

    def test_canonical_identity_follows_consolidation_chains(self, postgres_store):
        self._confirmed_pair(postgres_store)  # X080 -> X081
        # Later the human consolidates X081 into X082 (roster spelling).
        postgres_store.record_identity(
            "Politician", "X081", "X082", confidence=1.0, source="review"
        )
        resolver = resolver(postgres_store)
        assert resolver.canonical_identity("Politician", "X080") == "X082"

    def test_malformed_cycle_degrades_gracefully(self, postgres_store):
        postgres_store.record_identity("Politician", "A000", "B000", 1.0, "review")
        postgres_store.record_identity("Politician", "B000", "A000", 1.0, "review")
        resolver = resolver(postgres_store)
        # The loop degrades to the first repeated id instead of spinning.
        assert resolver.canonical_identity("Politician", "A000") == "A000"

    def test_approve_then_new_write_converges_identity(self, postgres_store):
        self._confirmed_pair(postgres_store)  # X080 -> X081
        resolver = resolver(postgres_store)
        # Writer composition: remap the id first, then canonicalize the name.
        identity_id = resolver.canonical_identity("Politician", "X080")
        canonical = resolver.canonical_name("Politician", "John Johnson", identity_id)
        # The canonical identity X081 owns the spelling "John Johnsen", and the
        # mention spelling "John Johnson" re-converges onto X081's alias row.
        assert canonical == "John Johnsen"
        assert postgres_store.get_canonical("Politician", "John Johnson")["canonical_id"] == "X081"
