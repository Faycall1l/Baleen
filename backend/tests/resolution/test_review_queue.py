"""Human review queue: approval, rejection, dedup.

Part of the resolution suite split out of the former
``tests/test_resolution.py``. Everything runs against the real
``baleen_test`` Postgres and the shared Neo4j dev instance (conftest
fixtures) -- no mocks, in keeping with the research project's rigor bar.
"""

from __future__ import annotations

from tests.resolution.conftest import (
    make_resolver,
)


class TestReviewQueue:
    def _queued_pair(self, postgres_store) -> int:
        postgres_store.ensure_alias("Politician", "John Johnson", "X060", "John Johnson", source="seed")
        postgres_store.ensure_alias("Politician", "John Johnsen", "X061", "John Johnsen", source="seed")
        resolver = make_resolver(postgres_store)
        resolver.suggest_merges("Politician")
        return resolver.pending_reviews()[0]["id"]

    def test_approve_flips_status_and_is_idempotent(self, postgres_store):
        resolver = make_resolver(postgres_store)
        queue_id = self._queued_pair(postgres_store)

        assert resolver.approve(queue_id)
        assert resolver.pending_reviews() == []
        # Already resolved (or the row no longer exists): no-op, never an error.
        assert resolver.approve(queue_id) is False

    def test_reject_keeps_pair_out_of_pending(self, postgres_store):
        resolver = make_resolver(postgres_store)
        queue_id = self._queued_pair(postgres_store)
        assert resolver.reject(queue_id)
        assert resolver.pending_reviews() == []
        assert resolver.reject(queue_id) is False

    def test_insert_suggestion_is_deduped_per_pair(self, postgres_store):
        inserted = postgres_store.insert_resolution_suggestion("Politician", "x", "X", "y", "Y", 0.9, "fuzzy_match")
        assert inserted is not None
        dup = postgres_store.insert_resolution_suggestion("Politician", "x", "X", "y", "Y", 0.9, "fuzzy_match")
        assert dup is None
