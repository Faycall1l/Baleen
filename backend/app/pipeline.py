"""End-to-end processing: new raw tweets -> events -> graph -> alerts.

Drives the pipeline stages in order for every tweet with ``status='new'``:

    extract (LLM, Pydantic-validated)
      -> write graph (MERGE, traceable)
      -> evaluate alerts (MVP rule)

``UnparsedEvent`` results are still written to the graph (as ``Event`` nodes
with ``type: 'unparsed'``) so nothing is silently dropped, but they never
trigger alerts.
"""

from __future__ import annotations

import logging

from app.alerts.engine import AlertEngine
from app.db.postgres import PostgresStore
from app.extraction.extractor import EventExtractor
from app.graph.writer import GraphWriter

logger = logging.getLogger(__name__)


class ProcessingPipeline:
    def __init__(
        self,
        extractor: EventExtractor,
        writer: GraphWriter,
        alert_engine: AlertEngine,
        store: PostgresStore,
    ) -> None:
        self.extractor = extractor
        self.writer = writer
        self.alert_engine = alert_engine
        self.store = store

    def process_new(self, limit: int = 50) -> dict:
        """Process tweets with status ``new``; returns per-type counts.

        Each tweet is isolated: a failure in one tweet (extraction, graph write,
        or alert evaluation) is logged and skipped, never aborting the batch.
        Only fully-successful tweets are marked ``extracted``, so a failed tweet
        is retried on the next run (all stages are idempotent).
        """
        tweets = self.store.get_new_tweets(limit=limit)
        summary = {"processed": 0, "unparsed": 0, "alerts": 0, "failed": 0}
        done_ids: list[str] = []

        for tweet in tweets:
            try:
                event = self.extractor.extract(tweet)
                event_id = self.writer.write_event(event)

                if event.event_type == "unparsed":
                    summary["unparsed"] += 1
                else:
                    created = self.alert_engine.evaluate_event(event)
                    summary["alerts"] += len(created)

                done_ids.append(tweet.tweet_id)
                summary["processed"] += 1
                logger.info(
                    "processed tweet %s -> %s (%s)",
                    tweet.tweet_id,
                    event.event_type,
                    event_id,
                )
            except Exception:
                summary["failed"] += 1
                logger.exception(
                    "processing failed for tweet %s; will retry next run",
                    tweet.tweet_id,
                )

        if done_ids:
            self.store.mark_extracted(done_ids)

        logger.info("processing complete: %s", summary)
        return summary
