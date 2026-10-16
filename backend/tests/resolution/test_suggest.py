"""Fuzzy near-duplicate suggestions and the confidence scorer.

Part of the resolution suite split out of the former
``tests/test_resolution.py``. Everything runs against the real
``baleen_test`` Postgres and the shared Neo4j dev instance (conftest
fixtures) -- no mocks, in keeping with the research project's rigor bar.
"""

from __future__ import annotations

from app.graph.resolution import (
    AUTO_MERGE_THRESHOLD,
    REVIEW_MIN_THRESHOLD,
)
from tests.resolution.conftest import (
    WARREN_ID,
    WARREN_NAME,
    make_confidence,
    make_resolver,
)


class TestSuggestMerges:
    def _seed_distinct_politicians(self, postgres_store, names: list[tuple[str, str]]):
        for cid, name in names:
            postgres_store.ensure_alias("Politician", name, cid, name, source="seed")

    def test_gray_band_pairs_are_queued_for_review(self, postgres_store):
        # 0.9167 — inside the [0.85, 0.95) gray band where a human decides.
        self._seed_distinct_politicians(postgres_store, [("X001", "John Johnson"), ("X002", "John Johnsen")])
        assert REVIEW_MIN_THRESHOLD <= make_confidence("John Johnson", "John Johnsen") < AUTO_MERGE_THRESHOLD

        resolver = make_resolver(postgres_store)
        assert resolver.suggest_merges("Politician") == 1

        pending = resolver.pending_reviews()
        assert len(pending) == 1
        item = pending[0]
        assert item["reason"] == "fuzzy_match"
        assert item["candidate_id"] in ("X001", "X002") and item["canonical_id"] in ("X001", "X002")
        assert REVIEW_MIN_THRESHOLD <= item["confidence"] < AUTO_MERGE_THRESHOLD

    def test_auto_band_across_different_ids_is_flagged_ambiguous(self, postgres_store):
        # Distinct roster ids whose names score >= 0.95: a dangerous,
        # human-decidable alias collision — must be queued, never auto-merged.
        self._seed_distinct_politicians(postgres_store, [("X010", "Kevin McCarthy"), ("X011", "Kevin McCarty")])
        assert make_confidence("Kevin McCarthy", "Kevin McCarty") >= AUTO_MERGE_THRESHOLD

        resolver = make_resolver(postgres_store)
        resolver.suggest_merges("Politician")
        pending = resolver.pending_reviews()
        assert len(pending) == 1
        assert pending[0]["confidence"] >= AUTO_MERGE_THRESHOLD
        assert {pending[0]["candidate_id"], pending[0]["canonical_id"]} == {"X010", "X011"}

    def test_aliases_within_one_id_are_folded_not_queued(self, postgres_store):
        postgres_store.ensure_alias("Politician", "Elizabeth Warren", WARREN_ID, WARREN_NAME, source="seed")
        postgres_store.ensure_alias("Politician", "Senator Elizabeth Warren", WARREN_ID, WARREN_NAME)
        resolver = make_resolver(postgres_store)
        assert resolver.suggest_merges("Politician") == 0
        assert resolver.pending_reviews() == []

    def test_unrelated_names_never_queued(self, postgres_store):
        self._seed_distinct_politicians(postgres_store, [("X020", "Joe Biden"), ("X021", "Donald Trump")])
        assert make_resolver(postgres_store).suggest_merges("Politician") == 0
        assert make_resolver(postgres_store).pending_reviews() == []

    def test_suggest_merges_is_idempotent(self, postgres_store):
        self._seed_distinct_politicians(postgres_store, [("X030", "John Johnson"), ("X031", "John Johnsen")])
        resolver = make_resolver(postgres_store)
        assert resolver.suggest_merges("Politician") == 1
        assert resolver.suggest_merges("Politician") == 0
        assert len(resolver.pending_reviews()) == 1

    def test_blocking_scales_without_cross_block_pairs(self, postgres_store):
        # Two blocks ('jo' vs 'do') never compare against each other.
        self._seed_distinct_politicians(
            postgres_store,
            [("X040", "John Johnson"), ("X041", "Donald Trump")],
        )
        assert make_resolver(postgres_store).suggest_merges("Politician") == 0

    def test_below_threshold_stored_confidence_matches_scorer(self, postgres_store):
        self._seed_distinct_politicians(postgres_store, [("X050", "John Johnson"), ("X051", "John Johnsen")])
        make_resolver(postgres_store).suggest_merges("Politician")
        item = make_resolver(postgres_store).pending_reviews()[0]
        assert item["confidence"] == round(make_confidence(item["candidate_name"], item["canonical_name"]), 3)
