"""Re-queue tweets whose extraction only ever produced ``UnparsedEvent``.

When a better model (or a fixed extractor) is available, this resets those
tweets to ``status='new'`` so the next pipeline run asks the LLM again.
Idempotent; safe to run repeatedly.

Two kinds of unparsed nodes exist in the graph:

* **genuine** — the tweet has *no* typed event, so it deserves a retry. These
  are re-queued to ``new`` (their unparsed node stays as a historical record
  unless ``--purge`` is passed).
* **stale duplicates** — the tweet later produced a typed event, so the
  unparsed node is obsolete. These are always deleted to keep the graph clean.

Usage:
    python -m scripts.retry_unparsed              # reset genuine ones, purge stale ones
    python -m scripts.retry_unparsed --dry-run    # show what would happen
    python -m scripts.retry_unparsed --process    # reset, then run the pipeline

Then run ``python -m scripts.ingest_once`` (or let the scheduler tick) to
re-extract.
"""

from __future__ import annotations

import argparse
import logging

from app.config import get_settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GENUINE_QUERY = """
MATCH (e:Event {type: 'unparsed'})
WHERE NOT EXISTS {
    MATCH (other:Event)
    WHERE other.tweet_id = e.tweet_id AND other.type <> 'unparsed'
}
RETURN e.tweet_id AS tweet_id ORDER BY e.tweet_id
"""

STALE_QUERY = """
MATCH (e:Event {type: 'unparsed'})
WHERE EXISTS {
    MATCH (other:Event)
    WHERE other.tweet_id = e.tweet_id AND other.type <> 'unparsed'
}
RETURN e.tweet_id AS tweet_id ORDER BY e.tweet_id
"""


def _ids(neo4j: Neo4jStore, query: str) -> list[str]:
    return [str(row["tweet_id"]) for row in neo4j.run(query) if row.get("tweet_id")]


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-queue genuinely-unparsed tweets")
    parser.add_argument("--process", action="store_true", help="run the processing pipeline after resetting")
    parser.add_argument(
        "--purge", action="store_true", help="also delete genuine unparsed Event nodes (stale ones are always purged)"
    )
    parser.add_argument("--dry-run", action="store_true", help="only report what would change")
    args = parser.parse_args()

    settings = get_settings()
    neo4j = Neo4jStore(settings)
    store = PostgresStore(settings.postgres_dsn)
    store.init_schema()

    genuine = _ids(neo4j, GENUINE_QUERY)
    stale = _ids(neo4j, STALE_QUERY)

    logger.info("genuine (to retry):   %d", len(genuine))
    logger.info("stale (to purge):     %d", len(stale))

    if not genuine and not stale:
        return

    if args.dry_run:
        if genuine:
            logger.info("would reset to 'new': %s", ", ".join(genuine))
        if stale:
            logger.info("would purge stale unparsed nodes for: %s", ", ".join(stale))
        return

    if stale:
        neo4j.run(
            "MATCH (e:Event {type: 'unparsed'}) WHERE e.tweet_id IN $ids DETACH DELETE e",
            ids=stale,
        )
        logger.info("purged %d stale unparsed Event node(s)", len(stale))

    if genuine:
        if args.purge:
            neo4j.run(
                "MATCH (e:Event {type: 'unparsed'}) WHERE e.tweet_id IN $ids DETACH DELETE e",
                ids=genuine,
            )
            logger.info("purged %d genuine unparsed Event node(s)", len(genuine))
        store.reset_status(genuine)
        logger.info("reset %d tweet(s) to status='new': %s", len(genuine), ", ".join(genuine))

    if args.process:
        from scripts.ingest_once import build_pipeline

        summary = build_pipeline(settings).process_new()
        logger.info("processing summary: %s", summary)


if __name__ == "__main__":
    main()
