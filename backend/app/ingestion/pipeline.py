"""Ingestion pipeline: poll a source, deduplicate into Postgres.

Runs on the scheduler (or via ``scripts.ingest_once``). Idempotency is provided
by the ``raw_tweets.tweet_id`` unique constraint — re-polling a batch of
already-seen tweets inserts nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.config import Settings
from app.db.postgres import PostgresStore
from app.ingestion.source import TweetSource

logger = logging.getLogger(__name__)


class IngestionPipeline:
    def __init__(self, source: TweetSource, store: PostgresStore, settings: Settings) -> None:
        self.source = source
        self.store = store
        self.settings = settings

    def run_once(self) -> dict:
        """Fetch recent tweets for configured handles and store new ones.

        Returns a metrics dict for logging/tests. Never raises for per-handle
        API failures — those are recorded in ``errors``.
        """
        started = datetime.now(UTC)
        result = self.source.fetch_recent(
            self.settings.x_handles,
            self.settings.x_search_window_seconds,
        )
        inserted, skipped = self.store.insert_raw_tweets(result.tweets)

        metrics = {
            "fetched": len(result.tweets),
            "inserted": inserted,
            "duplicates_skipped": skipped,
            "errors": result.errors,
            "duration_seconds": round((datetime.now(UTC) - started).total_seconds(), 3),
            "timestamp": started.isoformat(),
        }
        logger.info(
            "ingestion run complete: %s (inserted=%d, dupes=%d, errors=%d)",
            {k: v for k, v in metrics.items() if k != "errors"},
            inserted,
            skipped,
            len(result.errors),
        )
        if result.errors:
            logger.error("ingestion errors: %s", result.errors)
        return metrics
