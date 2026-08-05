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
        """Process tweets with status ``new``; returns per-type counts."""
        tweets = self.store.get_new_tweets(limit=limit)
        summary = {"processed": 0, "unparsed": 0, "alerts": 0}
        event_ids: list[str] = []

        for tweet in tweets:
            event = self.extractor.extract(tweet)
            event_id = self.writer.write_event(event)
            event_ids.append(event_id)

            if event.event_type == "unparsed":
                summary["unparsed"] += 1
            else:
                created = self.alert_engine.evaluate_event(event)
                summary["alerts"] += len(created)

            summary["processed"] += 1
            logger.info(
                "processed tweet %s -> %s (%s)",
                tweet.tweet_id,
                event.event_type,
                event_id,
            )

        if tweets:
            self.store.mark_extracted([t.tweet_id for t in tweets])

        logger.info("processing complete: %s", summary)
        return summary
