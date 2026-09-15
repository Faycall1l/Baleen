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
from app.extraction.client import LLMTransportError
from app.extraction.extractor import EventExtractor
from app.graph.writer import GraphWriter
from app.ingestion.disclosures import maybe_enrich_event
from app.ingestion.edgar import maybe_enrich_insider
from app.reports.engine import ReportEngine

logger = logging.getLogger(__name__)


class ProcessingPipeline:
    def __init__(
        self,
        extractor: EventExtractor,
        writer: GraphWriter,
        alert_engine: AlertEngine,
        store: PostgresStore,
        report_engine: ReportEngine | None = None,
    ) -> None:
        self.extractor = extractor
        self.writer = writer
        self.alert_engine = alert_engine
        self.store = store
        self.report_engine = report_engine

    def process_new(self, limit: int = 50) -> dict:
        """Process tweets awaiting extraction; returns per-type counts.

        Tweets are *claimed* atomically (``status -> processing``) before work
        begins, so concurrent ``process_new`` runs partition the queue instead
        of re-reading the same head window. Successful tweets are marked
        ``extracted``; failed ones are released back to ``new`` so the next run
        retries them (all stages are idempotent). A crash mid-batch leaves rows
        ``processing``; they are reclaimed by the claim layer after its lease
        expires.

        Each tweet is isolated: a failure in one tweet (extraction, graph
        write, or alert evaluation) is logged and skipped, never aborting the
        batch.

        If the *entire* batch failed only because the LLM endpoint was
        unreachable, :class:`LLMTransportError` is raised so the scheduler can
        back off instead of hammering a dead endpoint every tick. Mixed
        failures never raise.
        """
        tweets = self.store.claim_new_tweets(limit=limit)
        summary = {"processed": 0, "unparsed": 0, "alerts": 0, "reports": 0, "failed": 0}
        done_ids: list[str] = []
        failed_ids: list[str] = []
        transport_failures = 0

        for tweet in tweets:
            try:
                event = self.extractor.extract(tweet)
                # Records from the filing feeds carry authoritative facts;
                # ground the model output in them before writing.
                event = maybe_enrich_event(tweet, event)
                event = maybe_enrich_insider(tweet, event)
                event_id = self.writer.write_event(event)

                if event.event_type == "unparsed":
                    summary["unparsed"] += 1
                else:
                    created = self.alert_engine.evaluate_event(event)
                    summary["alerts"] += len(created)
                    if self.report_engine is not None:
                        try:
                            if self.report_engine.write_brief(event):
                                summary["reports"] += 1
                        except Exception:
                            logger.exception(
                                "report brief failed for event %s; continuing",
                                event.deterministic_id,
                            )

                done_ids.append(tweet.tweet_id)
                summary["processed"] += 1
                logger.info(
                    "processed tweet %s -> %s (%s)",
                    tweet.tweet_id,
                    event.event_type,
                    event_id,
                )
            except LLMTransportError:
                transport_failures += 1
                failed_ids.append(tweet.tweet_id)
                summary["failed"] += 1
                logger.warning(
                    "LLM unreachable while processing tweet %s; leaving pending",
                    tweet.tweet_id,
                )
            except Exception:
                failed_ids.append(tweet.tweet_id)
                summary["failed"] += 1
                logger.exception(
                    "processing failed for tweet %s; will retry next run",
                    tweet.tweet_id,
                )

        if done_ids:
            self.store.mark_extracted(done_ids)
        if failed_ids:
            self.store.reset_status(failed_ids, "new")

        if (
            tweets
            and summary["failed"] == transport_failures
            and transport_failures > 0
            and summary["processed"] == 0
        ):
            raise LLMTransportError(
                f"LLM endpoint unreachable for all {transport_failures} tweet(s)"
            )

        logger.info("processing complete: %s", summary)
        return summary
