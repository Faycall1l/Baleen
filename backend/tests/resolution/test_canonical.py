"""Canonical-name resolution: seeded ids, aliases, roster fallback.

Part of the resolution suite split out of the former
``tests/test_resolution.py`` (M5). Everything runs against the real
``baleen_test`` Postgres and the shared Neo4j dev instance (conftest
fixtures) -- no mocks, in keeping with the research project's rigor bar.
"""

from __future__ import annotations

import pytest

from tests.resolution.conftest import (
    WARREN_ID,
    WARREN_NAME,
    resolver,
)


class TestCanonicalName:
    def test_first_encounter_registers_mention_as_seed(self, postgres_store):
        resolver = resolver(postgres_store)
        canonical = resolver.canonical_name(
            "Politician", "Joe Smith", canonical_id="X000001"
        )
        assert canonical == "Joe Smith"
        assert postgres_store.get_canonical("Politician", "Joe Smith") == {
            "canonical_id": "X000001",
            "canonical_name": "Joe Smith",
        }

    def test_seeded_canonical_id_converges_and_registers_alias(self, postgres_store):
        resolver = resolver(postgres_store)
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
        resolver = resolver(postgres_store)
        resolver.canonical_name("Politician", "Elizabeth Warren", WARREN_ID)
        alias = postgres_store.get_canonical("Politician", "Elizabeth Warren")

        # A differently-spelled mention that already resolves to the id keeps
        # that id's canonical, even when this event claims the same id.
        canonical = resolver.canonical_name("Politician", "elizabeth.warren", WARREN_ID)
        assert canonical == alias["canonical_name"]

    def test_conflicting_canonical_id_keeps_first_registered_and_queues_clash(
        self, postgres_store
    ):
        resolver = resolver(postgres_store)
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
        resolver = resolver(postgres_store)
        resolver.canonical_name("Politician", "Ben Carson", "C0010721")
        resolver.canonical_name("Politician", "Ben Carson", "C9999999")
        resolver.canonical_name("Politician", "Ben Carson", "C9999999")
        assert len(resolver.pending_reviews()) == 1

    def test_llm_alias_never_clobbers_seeded_spelling(self, postgres_store):
        resolver = resolver(postgres_store)
        resolver.seed_canonical("Politician", WARREN_ID, WARREN_NAME)

        resolver.canonical_name(
            "Politician", "Senator Elizabeth Warren", canonical_id=WARREN_ID
        )
        assert postgres_store.get_canonical_name("Politician", WARREN_ID) == WARREN_NAME

    def test_realtier_spelling_is_stable_across_mentions(self, postgres_store):
        resolver = resolver(postgres_store)
        first = resolver.canonical_name("Politician", "MTG", "M001202")
        second = resolver.canonical_name("Politician", "Marjorie Taylor Greene", "M001202")
        assert first == second  # node name never drifts with the mention

    @pytest.mark.parametrize(
        "mention", ["Rep. Nancy Pelosi", '"Pelosi"', "nancy pelosi (D-CA)"],
    )
    def test_no_roster_id_uses_raw_mention(self, postgres_store, mention):
        resolver = resolver(postgres_store)
        canonical = resolver.canonical_name("Politician", mention, "P000197")
        # Registering a non-roster id: the mention becomes the spelling.
        assert canonical == mention
        assert postgres_store.get_canonical("Politician", mention)["canonical_id"] == "P000197"
