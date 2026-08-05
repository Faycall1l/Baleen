"""Load the reference dataset into the Neo4j graph (idempotent).

Usage:
    python -m scripts.seed_reference

Creates Sector, Committee, Politician, and Ticker nodes plus the
OVERSEES / MEMBER_OF / IN_SECTOR relationships from ``app.graph.seed_data``,
and adds uniqueness constraints on every natural identifier. Safe to run
repeatedly — everything is ``MERGE``.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.db.neo4j import Neo4jStore
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


def seed(store: Neo4jStore) -> dict:
    for constraint in CONSTRAINTS:
        store.run(constraint)

    for sector in SECTORS:
        store.run(
            "MERGE (s:Sector {name: $name}) SET s.tweet_id = $tid, s.tweet_url = $url",
            name=sector, tid=SYNTHETIC_SOURCE["tweet_id"], url=SYNTHETIC_SOURCE["tweet_url"],
        )
    for committee_id, name in COMMITTEES.items():
        store.run(
            "MERGE (c:Committee {id: $id}) SET c.name = $name, c.tweet_id = $tid, c.tweet_url = $url",
            id=committee_id, name=name, **SYNTHETIC_SOURCE,
        )
    for politician in POLITICIANS:
        store.run(
            """
            MERGE (p:Politician {id: $id})
            SET p.name = $name, p.party = $party, p.chamber = $chamber,
                p.tweet_id = $tid, p.tweet_url = $url
            """,
            **politician, **SYNTHETIC_SOURCE,
        )

    for committee_id, sectors in COMMITTEE_SECTORS.items():
        for sector in sectors:
            store.run(
                """
                MATCH (c:Committee {id: $cid}), (s:Sector {name: $sector})
                MERGE (c)-[r:OVERSEES]->(s)
                SET r.tweet_id = $tid, r.tweet_url = $url
                """,
                cid=committee_id, sector=sector, **SYNTHETIC_SOURCE,
            )
    for politician in POLITICIANS:
        for committee_id in politician["committees"]:
            store.run(
                """
                MATCH (p:Politician {id: $pid}), (c:Committee {id: $cid})
                MERGE (p)-[r:MEMBER_OF]->(c)
                SET r.tweet_id = $tid, r.tweet_url = $url
                """,
                pid=politician["id"], cid=committee_id, **SYNTHETIC_SOURCE,
            )
    for ticker, sector in TICKER_SECTORS.items():
        store.run(
            """
            MERGE (t:Ticker {ticker: $ticker})
            SET t.tweet_id = $tid, t.tweet_url = $url
            WITH t
            MATCH (s:Sector {name: $sector})
            MERGE (t)-[r:IN_SECTOR]->(s)
            SET r.tweet_id = $tid, r.tweet_url = $url
            """,
            ticker=ticker, sector=sector, **SYNTHETIC_SOURCE,
        )

    summary = {
        "sectors": len(SECTORS),
        "committees": len(COMMITTEES),
        "politicians": len(POLITICIANS),
        "tickers": len(TICKER_SECTORS),
    }
    logger.info("reference data seeded: %s", summary)
    return summary


if __name__ == "__main__":
    store = Neo4jStore(get_settings())
    try:
        seed(store)
    finally:
        store.close()
