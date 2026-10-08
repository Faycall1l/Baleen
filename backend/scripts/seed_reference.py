"""Load the reference dataset into Neo4j and the alias registry (idempotent).

Usage:
    python -m scripts.seed_reference

Creates Sector, Committee, Politician, and Ticker nodes plus the
OVERSEES / MEMBER_OF / IN_SECTOR relationships from ``app.graph.seed_data``,
and adds uniqueness constraints on every natural identifier. Safe to run
repeatedly — everything is ``MERGE``.

When a PostgresStore is supplied, the canonical spelling for every Politician
in the reference roster is also registered in ``entity_aliases``. The resolver
reads these entries on pipeline startup so that downstream tweets always
resolve to the seeded canonical name.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.graph.schema import LABEL_KEYS
from app.graph.seed_data import (
    COMMITTEES,
    COMMITTEE_SECTORS,
    POLITICIANS,
    SECTORS,
    TICKER_SECTORS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SYNTHETIC_SOURCE = {"tweet_id": "seed", "tweet_url": "https://x.com/seed"}

CONSTRAINTS = [
    f"CREATE CONSTRAINT baleen_{label.lower()}_key IF NOT EXISTS "
    f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
    for label, key in LABEL_KEYS.items()
]


def seed_aliases(entity_db: PostgresStore) -> int:
    """Register the reference roster's canonical spellings in the alias table.

    Postgres-only (no Neo4j round-trips), idempotent, and safe to run on every
    app startup: seed-source entries always win over later LLM mentions, so a
    dynamic alias never drifts a roster politician's node name.
    """
    for politician in POLITICIANS:
        entity_db.ensure_alias(
            "Politician",
            alias=politician["name"],
            canonical_id=politician["id"],
            canonical_name=politician["name"],
            source="seed",
        )
    return len(POLITICIANS)


def seed(store: Neo4jStore, entity_db: PostgresStore | None = None) -> dict:
    for constraint in CONSTRAINTS:
        store.run(constraint)

    for sector in SECTORS:
        store.run(
            "MERGE (s:Sector {name: $name}) "
            "SET s.first_seen_at = COALESCE(s.first_seen_at, datetime()), "
            "s.tweet_id = $tweet_id, s.tweet_url = $tweet_url",
            name=sector, **SYNTHETIC_SOURCE,
        )
    for committee_id, name in COMMITTEES.items():
        store.run(
            "MERGE (c:Committee {id: $id}) "
            "SET c.first_seen_at = COALESCE(c.first_seen_at, datetime()), "
            "c.name = $name, c.tweet_id = $tweet_id, c.tweet_url = $tweet_url",
            id=committee_id, name=name, **SYNTHETIC_SOURCE,
        )
    for politician in POLITICIANS:
        store.run(
            """
            MERGE (p:Politician {id: $id})
            SET p.first_seen_at = COALESCE(p.first_seen_at, datetime()),
                p.name = $name, p.party = $party, p.chamber = $chamber,
                p.tweet_id = $tweet_id, p.tweet_url = $tweet_url
            """,
            **politician, **SYNTHETIC_SOURCE,
        )

    for committee_id, sectors in COMMITTEE_SECTORS.items():
        for sector in sectors:
            store.run(
                """
                MATCH (c:Committee {id: $cid}), (s:Sector {name: $sector})
                MERGE (c)-[r:OVERSEES]->(s)
                SET r.tweet_id = $tweet_id, r.tweet_url = $tweet_url
                """,
                cid=committee_id, sector=sector, **SYNTHETIC_SOURCE,
            )
    for politician in POLITICIANS:
        for committee_id in politician["committees"]:
            store.run(
                """
                MATCH (p:Politician {id: $pid}), (c:Committee {id: $cid})
                MERGE (p)-[r:MEMBER_OF]->(c)
                SET r.tweet_id = $tweet_id, r.tweet_url = $tweet_url
                """,
                pid=politician["id"], cid=committee_id, **SYNTHETIC_SOURCE,
            )
    for ticker, sector in TICKER_SECTORS.items():
        store.run(
            """
            MERGE (t:Ticker {ticker: $ticker})
            SET t.first_seen_at = COALESCE(t.first_seen_at, datetime()),
                t.tweet_id = $tweet_id, t.tweet_url = $tweet_url
            WITH t
            MATCH (s:Sector {name: $sector})
            MERGE (t)-[r:IN_SECTOR]->(s)
            SET r.tweet_id = $tweet_id, r.tweet_url = $tweet_url
            """,
            ticker=ticker, sector=sector, **SYNTHETIC_SOURCE,
        )

    alias_count = 0
    if entity_db is not None:
        alias_count = seed_aliases(entity_db)

    summary = {
        "sectors": len(SECTORS),
        "committees": len(COMMITTEES),
        "politicians": len(POLITICIANS),
        "tickers": len(TICKER_SECTORS),
        "aliases_seeded": alias_count,
    }
    logger.info("reference data seeded: %s", summary)
    return summary


if __name__ == "__main__":
    settings = get_settings()
    store = Neo4jStore(settings)
    entity_db = PostgresStore(settings.postgres_dsn)
    try:
        seed(store, entity_db)
    finally:
        store.close()
        entity_db.close()
