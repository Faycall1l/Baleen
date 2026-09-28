"""Reference-roster seeding into the alias registry.

Part of the resolution suite split out of the former
``tests/test_resolution.py`` (M5). Everything runs against the real
``baleen_test`` Postgres and the shared Neo4j dev instance (conftest
fixtures) -- no mocks, in keeping with the research project's rigor bar.
"""

from __future__ import annotations

from scripts.seed_reference import seed
from tests.resolution.conftest import (
    WARREN_ID,
    WARREN_NAME,
)


class TestRosterSeeding:
    def test_seed_aliases_registers_roster_without_neo4j(self, postgres_store):
        from scripts.seed_reference import seed_aliases

        n = seed_aliases(postgres_store)
        assert n > 0
        assert postgres_store.get_canonical("Politician", WARREN_NAME) == {
            "canonical_id": WARREN_ID,
            "canonical_name": WARREN_NAME,
        }
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
