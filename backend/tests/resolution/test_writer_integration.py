"""GraphWriter integration: raw vs canonical writes and aliases.

Part of the resolution suite split out of the former
``tests/test_resolution.py``. Everything runs against the real
``baleen_test`` Postgres and the shared Neo4j dev instance (conftest
fixtures) -- no mocks, in keeping with the research project's rigor bar.
"""

from __future__ import annotations

from app.graph.writer import GraphWriter
from scripts.seed_reference import seed
from tests.resolution.conftest import (
    WARREN_ID,
    WARREN_NAME,
    make_congress_event,
    make_resolver,
)


class TestWriterIntegration:
    def test_writer_without_resolver_keeps_raw_mention(self, postgres_store, neo4j_store):
        seed(neo4j_store, postgres_store)
        writer = GraphWriter(neo4j_store)
        writer.write_event(make_congress_event("res-no-resolver-1", WARREN_ID, "Sen. Elizabeth Warren"))

        rows = neo4j_store.run("MATCH (p:Politician {id: $id}) RETURN p.name AS name", id=WARREN_ID)
        assert rows[0]["name"] == "Sen. Elizabeth Warren"

    def test_writer_with_resolver_writes_canonical_name(self, postgres_store, neo4j_store):
        seed(neo4j_store, postgres_store)
        resolver = make_resolver(postgres_store)
        writer = GraphWriter(neo4j_store, entity_resolver=resolver)
        writer.write_event(make_congress_event("res-resolver-1", WARREN_ID, "Sen. Elizabeth Warren"))
        writer.write_event(make_congress_event("res-resolver-2", WARREN_ID, "elizabeth.warren"))

        rows = neo4j_store.run("MATCH (p:Politician {id: $id}) RETURN p.name AS name", id=WARREN_ID)
        names = {row["name"] for row in rows}
        # Every event spelled the node differently, but the graph stayed put.
        assert names == {WARREN_NAME}

    def test_writer_registers_llm_aliases_alongside_canonical(self, postgres_store, neo4j_store):
        seed(neo4j_store, postgres_store)
        writer = GraphWriter(neo4j_store, entity_resolver=make_resolver(postgres_store))
        writer.write_event(make_congress_event("res-resolver-3", WARREN_ID, "Sen. Elizabeth Warren"))

        alias = postgres_store.get_canonical("Politician", "Sen. Elizabeth Warren")
        assert alias == {"canonical_id": WARREN_ID, "canonical_name": WARREN_NAME}
        seeded = postgres_store.list_aliases("Politician", WARREN_ID)
        assert {a["source"] for a in seeded} == {"seed", "llm"}
