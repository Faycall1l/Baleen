"""Entity-resolution tests: alias registry, canonical-name resolution, the
fuzzy near-duplicate pass, and the human review queue.

Everything runs against the real ``baleen_test`` Postgres and the shared Neo4j
dev instance (conftest fixtures) — no mocks, in keeping with the research
project's rigor bar.
"""

from __future__ import annotations

import difflib

import pytest

from app.graph.resolution import (
    AUTO_MERGE_THRESHOLD,
    REVIEW_MIN_THRESHOLD,
    EntityResolver,
    normalize_mention,
)
from app.graph.writer import GraphWriter
from app.models.events import CongressTradeEvent
from scripts.seed_reference import seed

# Elizabeth Warren is part of the reference roster (seed_data.POLITICIANS).
WARREN_ID = "W000817"
WARREN_NAME = "Elizabeth Warren"


def _resolver(postgres_store) -> EntityResolver:
    return EntityResolver(postgres_store)


def _congress_event(
    tweet_id: str,
    politician_id: str,
    politician_name: str,
) -> CongressTradeEvent:
    return CongressTradeEvent(
        tweet_id=tweet_id,
        tweet_url=f"https://x.com/congress_trades/status/{tweet_id}",
        confidence=0.95,
        politician_name=politician_name,
        politician_id=politician_id,
        chamber="senate",
        party="D",
        committee_ids=["SSBK"],
        ticker="JPM",
        transaction_type="buy",
        amount_range="$1,001 - $15,000",
    )


# ----------------------------------------------------------------------
# normalization
# ----------------------------------------------------------------------


class TestNormalizeMention:
    def test_folds_case_punctuation_and_whitespace(self):
        assert normalize_mention("  Rep. Nancy Pelosi!,  ") == "rep nancy pelosi"

    def test_dash_and_apostrophe_fold_to_space(self):
        assert normalize_mention("Jean-Luc D'Amore") == "jean luc d amore"

    def test_token_order_is_preserved(self):
        # Reversed order is a different name, not a spelling variant.
        assert normalize_mention("Nancy Pelosi") != normalize_mention("Pelosi Nancy")


# ----------------------------------------------------------------------
# canonical-name resolution
# ----------------------------------------------------------------------


class TestCanonicalName:
    def test_first_encounter_registers_mention_as_seed(self, postgres_store):
        resolver = _resolver(postgres_store)
        canonical = resolver.canonical_name(
            "Politician", "Joe Smith", canonical_id="X000001"
        )
        assert canonical == "Joe Smith"
        assert postgres_store.get_canonical("Politician", "Joe Smith") == {
            "canonical_id": "X000001",
            "canonical_name": "Joe Smith",
        }

    def test_seeded_canonical_id_converges_and_registers_alias(self, postgres_store):
        resolver = _resolver(postgres_store)
        resolver.seed_canonical("Politician", WARREN_ID, WARREN_NAME)

        canonical = resolver.canonical_name(
            "Politician", "Sen. Elizabeth Warren", canonical_id=WARREN_ID
        )
        assert canonical == WARREN_NAME
        # Downstream mention registered as an alias of the seeded canonical.
        assert postgres_store.get_canonical(
            "Politician", "Sen. Elizabeth Warren"
        ) == {"canonical_id": WARREN_ID, "canonical_name": WARREN_NAME}

    def test_mention_alias_reuse_returns_same_canonical(self, postgres_store):
        resolver = _resolver(postgres_store)
        resolver.canonical_name("Politician", "Elizabeth Warren", WARREN_ID)
        alias = postgres_store.get_canonical("Politician", "Elizabeth Warren")

        # A differently-spelled mention that already resolves to the id keeps
        # that id's canonical, even when this event claims the same id.
        canonical = resolver.canonical_name("Politician", "elizabeth.warren", WARREN_ID)
        assert canonical == alias["canonical_name"]

    def test_conflicting_canonical_id_keeps_first_registered_and_queues_clash(
        self, postgres_store
    ):
        resolver = _resolver(postgres_store)
        # Someone else claimed this exact name first.
        resolver.canonical_name("Politician", "Ben Carson", "C0010721")

        # A later event claims the same spelling for a different id: we cannot
        # tell who is right from spelling alone, so the first-registered id
        # stays authoritative and the clash is queued for a human to review.
        canonical = resolver.canonical_name("Politician", "Ben Carson", "C9999999")
        assert canonical == "Ben Carson"
        assert postgres_store.get_canonical("Politician", "Ben Carson") == {
            "canonical_id": "C0010721",
            "canonical_name": "Ben Carson",
        }

        pending = resolver.pending_reviews()
        assert len(pending) == 1
        assert pending[0]["reason"] == "alias_clash"
        assert {pending[0]["candidate_id"], pending[0]["canonical_id"]} == {
            "C0010721", "C9999999",
        }
        assert pending[0]["confidence"] == 1.0

    def test_alias_clash_is_not_requeued_on_every_write(self, postgres_store):
        resolver = _resolver(postgres_store)
        resolver.canonical_name("Politician", "Ben Carson", "C0010721")
        resolver.canonical_name("Politician", "Ben Carson", "C9999999")
        resolver.canonical_name("Politician", "Ben Carson", "C9999999")
        assert len(resolver.pending_reviews()) == 1

    def test_llm_alias_never_clobbers_seeded_spelling(self, postgres_store):
        resolver = _resolver(postgres_store)
        resolver.seed_canonical("Politician", WARREN_ID, WARREN_NAME)

        resolver.canonical_name(
            "Politician", "Senator Elizabeth Warren", canonical_id=WARREN_ID
        )
        assert postgres_store.get_canonical_name("Politician", WARREN_ID) == WARREN_NAME

    def test_realtier_spelling_is_stable_across_mentions(self, postgres_store):
        resolver = _resolver(postgres_store)
        first = resolver.canonical_name("Politician", "MTG", "M001202")
        second = resolver.canonical_name("Politician", "Marjorie Taylor Greene", "M001202")
        assert first == second  # node name never drifts with the mention

    @pytest.mark.parametrize(
        "mention", ["Rep. Nancy Pelosi", '"Pelosi"', "nancy pelosi (D-CA)"],
    )
    def test_no_roster_id_uses_raw_mention(self, postgres_store, mention):
        resolver = _resolver(postgres_store)
        canonical = resolver.canonical_name("Politician", mention, "P000197")
        # Registering a non-roster id: the mention becomes the spelling.
        assert canonical == mention
        assert postgres_store.get_canonical("Politician", mention)["canonical_id"] == "P000197"


# ----------------------------------------------------------------------
# reference-roster seeding
# ----------------------------------------------------------------------


class TestRosterSeeding:
    def test_seed_reference_registers_roster_aliases(self, postgres_store, neo4j_store):
        summary = seed(neo4j_store, postgres_store)
        assert summary["aliases_seeded"] > 0

        entry = postgres_store.get_canonical("Politician", WARREN_NAME)
        assert entry == {"canonical_id": WARREN_ID, "canonical_name": WARREN_NAME}
        assert postgres_store.list_aliases("Politician", WARREN_ID)[0]["source"] == "seed"

    def test_seed_reference_is_idempotent(self, postgres_store, neo4j_store):
        seed(neo4j_store, postgres_store)
        before = postgres_store.list_aliases_by_type("Politician")
        seed(neo4j_store, postgres_store)
        assert postgres_store.list_aliases_by_type("Politician") == before


# ----------------------------------------------------------------------
# fuzzy near-duplicate pass
# ----------------------------------------------------------------------


def _confidence(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_mention(a), normalize_mention(b)).ratio()


class TestSuggestMerges:
    def _seed_distinct_politicians(self, postgres_store, names: list[tuple[str, str]]):
        for cid, name in names:
            postgres_store.ensure_alias(
                "Politician", name, cid, name, source="seed"
            )

    def test_gray_band_pairs_are_queued_for_review(self, postgres_store):
        # 0.9167 — inside the [0.85, 0.95) gray band where a human decides.
        self._seed_distinct_politicians(
            postgres_store, [("X001", "John Johnson"), ("X002", "John Johnsen")]
        )
        assert REVIEW_MIN_THRESHOLD <= _confidence("John Johnson", "John Johnsen") < AUTO_MERGE_THRESHOLD

        resolver = _resolver(postgres_store)
        assert resolver.suggest_merges("Politician") == 1

        pending = resolver.pending_reviews()
        assert len(pending) == 1
        item = pending[0]
        assert item["reason"] == "fuzzy_match"
        assert item["candidate_id"] in ("X001", "X002") and item["canonical_id"] in (
            "X001", "X002"
        )
        assert REVIEW_MIN_THRESHOLD <= item["confidence"] < AUTO_MERGE_THRESHOLD

    def test_auto_band_across_different_ids_is_flagged_ambiguous(self, postgres_store):
        # Distinct roster ids whose names score >= 0.95: a dangerous,
        # human-decidable alias collision — must be queued, never auto-merged.
        self._seed_distinct_politicians(
            postgres_store, [("X010", "Kevin McCarthy"), ("X011", "Kevin McCarty")]
        )
        assert _confidence("Kevin McCarthy", "Kevin McCarty") >= AUTO_MERGE_THRESHOLD

        resolver = _resolver(postgres_store)
        resolver.suggest_merges("Politician")
        pending = resolver.pending_reviews()
        assert len(pending) == 1
        assert pending[0]["confidence"] >= AUTO_MERGE_THRESHOLD
        assert {pending[0]["candidate_id"], pending[0]["canonical_id"]} == {"X010", "X011"}

    def test_aliases_within_one_id_are_folded_not_queued(self, postgres_store):
        postgres_store.ensure_alias(
            "Politician", "Elizabeth Warren", WARREN_ID, WARREN_NAME, source="seed"
        )
        postgres_store.ensure_alias(
            "Politician", "Senator Elizabeth Warren", WARREN_ID, WARREN_NAME
        )
        resolver = _resolver(postgres_store)
        assert resolver.suggest_merges("Politician") == 0
        assert resolver.pending_reviews() == []

    def test_unrelated_names_never_queued(self, postgres_store):
        self._seed_distinct_politicians(
            postgres_store, [("X020", "Joe Biden"), ("X021", "Donald Trump")]
        )
        assert _resolver(postgres_store).suggest_merges("Politician") == 0
        assert _resolver(postgres_store).pending_reviews() == []

    def test_suggest_merges_is_idempotent(self, postgres_store):
        self._seed_distinct_politicians(
            postgres_store, [("X030", "John Johnson"), ("X031", "John Johnsen")]
        )
        resolver = _resolver(postgres_store)
        assert resolver.suggest_merges("Politician") == 1
        assert resolver.suggest_merges("Politician") == 0
        assert len(resolver.pending_reviews()) == 1

    def test_blocking_scales_without_cross_block_pairs(self, postgres_store):
        # Two blocks ('jo' vs 'do') never compare against each other.
        self._seed_distinct_politicians(
            postgres_store,
            [("X040", "John Johnson"), ("X041", "Donald Trump")],
        )
        assert _resolver(postgres_store).suggest_merges("Politician") == 0

    def test_below_threshold_stored_confidence_matches_scorer(self, postgres_store):
        self._seed_distinct_politicians(
            postgres_store, [("X050", "John Johnson"), ("X051", "John Johnsen")]
        )
        _resolver(postgres_store).suggest_merges("Politician")
        item = _resolver(postgres_store).pending_reviews()[0]
        assert item["confidence"] == round(
            _confidence(item["candidate_name"], item["canonical_name"]), 3
        )


# ----------------------------------------------------------------------
# review queue lifecycle
# ----------------------------------------------------------------------


class TestReviewQueue:
    def _queued_pair(self, postgres_store) -> int:
        postgres_store.ensure_alias(
            "Politician", "John Johnson", "X060", "John Johnson", source="seed"
        )
        postgres_store.ensure_alias(
            "Politician", "John Johnsen", "X061", "John Johnsen", source="seed"
        )
        resolver = _resolver(postgres_store)
        resolver.suggest_merges("Politician")
        return resolver.pending_reviews()[0]["id"]

    def test_approve_flips_status_and_is_idempotent(self, postgres_store):
        resolver = _resolver(postgres_store)
        queue_id = self._queued_pair(postgres_store)

        assert resolver.approve(queue_id)
        assert resolver.pending_reviews() == []
        # Already resolved (or the row no longer exists): no-op, never an error.
        assert resolver.approve(queue_id) is False

    def test_reject_keeps_pair_out_of_pending(self, postgres_store):
        resolver = _resolver(postgres_store)
        queue_id = self._queued_pair(postgres_store)
        assert resolver.reject(queue_id)
        assert resolver.pending_reviews() == []
        assert resolver.reject(queue_id) is False

    def test_insert_suggestion_is_deduped_per_pair(self, postgres_store):
        inserted = postgres_store.insert_resolution_suggestion(
            "Politician", "x", "X", "y", "Y", 0.9, "fuzzy_match"
        )
        assert inserted is not None
        dup = postgres_store.insert_resolution_suggestion(
            "Politician", "x", "X", "y", "Y", 0.9, "fuzzy_match"
        )
        assert dup is None


# ----------------------------------------------------------------------
# end-to-end: GraphWriter uses the resolver (real Neo4j)
# ----------------------------------------------------------------------


class TestWriterIntegration:
    def test_writer_without_resolver_keeps_raw_mention(self, postgres_store, neo4j_store):
        seed(neo4j_store, postgres_store)
        writer = GraphWriter(neo4j_store)
        writer.write_event(_congress_event("res-no-resolver-1", WARREN_ID, "Sen. Elizabeth Warren"))

        row = neo4j_store.run(
            "MATCH (p:Politician {id: $id}) RETURN p.name AS name", id=WARREN_ID
        ).single()
        assert row["name"] == "Sen. Elizabeth Warren"

    def test_writer_with_resolver_writes_canonical_name(self, postgres_store, neo4j_store):
        seed(neo4j_store, postgres_store)
        resolver = _resolver(postgres_store)
        writer = GraphWriter(neo4j_store, entity_resolver=resolver)
        writer.write_event(_congress_event("res-resolver-1", WARREN_ID, "Sen. Elizabeth Warren"))
        writer.write_event(_congress_event("res-resolver-2", WARREN_ID, "elizabeth.warren"))

        rows = neo4j_store.run(
            "MATCH (p:Politician {id: $id}) RETURN p.name AS name", id=WARREN_ID
        )
        names = {row["name"] for row in rows}
        # Every event spelled the node differently, but the graph stayed put.
        assert names == {WARREN_NAME}

    def test_writer_registers_llm_aliases_alongside_canonical(self, postgres_store, neo4j_store):
        seed(neo4j_store, postgres_store)
        writer = GraphWriter(neo4j_store, entity_resolver=_resolver(postgres_store))
        writer.write_event(_congress_event("res-resolver-3", WARREN_ID, "Sen. Elizabeth Warren"))

        alias = postgres_store.get_canonical("Politician", "Sen. Elizabeth Warren")
        assert alias == {"canonical_id": WARREN_ID, "canonical_name": WARREN_NAME}
        seeded = postgres_store.list_aliases("Politician", WARREN_ID)
        assert {a["source"] for a in seeded} == {"seed", "llm"}