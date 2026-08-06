"""Prune orphaned Event nodes from the graph.

An Event is an orphan when its source ``tweet_id`` has no matching row in
``raw_tweets`` — i.e. it came from a demo/test run or a seed rather than the
ingestion pipeline. Reference nodes (Politician / Committee / Sector / Ticker)
are never touched; only ``Event`` nodes with unverifiable provenance are
detached and deleted.

Usage:
    python -m scripts.prune_orphan_events --dry-run   # preview
    python -m scripts.prune_orphan_events             # delete orphans
"""

from __future__ import annotations

import argparse
import logging

from app.config import get_settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def orphan_tweet_ids(neo4j: Neo4jStore, store: PostgresStore) -> dict[str, int]:
    """Map of orphaned tweet_id -> number of Event nodes carrying it."""
    events = [r["tweet_id"] for r in neo4j.run("MATCH (e:Event) RETURN e.tweet_id AS tweet_id")]
    if not events:
        return {}
    with store.engine.connect() as conn:
        known = {row[0] for row in conn.execute(text("SELECT tweet_id FROM raw_tweets"))}
    orphans: dict[str, int] = {}
    for tweet_id in events:
        if tweet_id not in known:
            orphans[tweet_id] = orphans.get(tweet_id, 0) + 1
    return orphans


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete Event nodes with unverifiable provenance")
    parser.add_argument("--dry-run", action="store_true", help="report orphans without deleting")
    args = parser.parse_args()

    settings = get_settings()
    neo4j = Neo4jStore(settings)
    store = PostgresStore(settings.postgres_dsn)
    store.init_schema()

    orphans = orphan_tweet_ids(neo4j, store)
    if not orphans:
        logger.info("no orphaned events; graph provenance is clean")
        return

    total = sum(orphans.values())
    logger.info("found %d orphaned Event node(s) across %d tweet_id(s)", total, len(orphans))
    for tweet_id, count in sorted(orphans.items()):
        logger.info("  %s (%d node(s))", tweet_id, count)

    if args.dry_run:
        return

    neo4j.run("MATCH (e:Event) WHERE e.tweet_id IN $ids DETACH DELETE e", ids=list(orphans))
    logger.info("deleted %d orphaned Event node(s)", total)


if __name__ == "__main__":
    main()
