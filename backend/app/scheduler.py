"""Scheduled execution of the ingest -> process loop.

A thin wrapper around APScheduler. The loop is intentionally kept to two calls:

1. ``IngestionPipeline.run_once()`` — pull new records from every live source
   (congressional disclosures, options radar, and X when configured),
   persist raw.
2. ``ProcessingPipeline.process_new()`` — extract, ground, graph, alert.

When X credentials are placeholders the X source is simply left out (the
disclosure + options sources need no keys). The demo path
(``scripts/ingest_once.py``) loads sample tweets instead.

On repeated failures (rate limits, transient API errors) the tick backs off
exponentially up to ``max_backoff_seconds`` instead of hammering the APIs, and
recovers to the base interval on the next successful run.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.ingestion.disclosures import CongressDisclosureSource
from app.ingestion.options import OptionsChainSource
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source import MultiSource, TweetSource
from app.ingestion.x_api import XApiSource
from app.pipeline import ProcessingPipeline

logger = logging.getLogger(__name__)


def build_sources(settings: Settings) -> list[TweetSource]:
    """Live sources for this run: disclosures + options always (keyless
    public feeds); X only when a real bearer token is configured."""
    sources: list[TweetSource] = [
        CongressDisclosureSource(),
        OptionsChainSource(),
    ]
    if settings.x_api_configured:
        sources.append(
            XApiSource(
                settings.x_api_bearer_token,
                max_pages=settings.x_max_pages,
                timeout_seconds=settings.x_api_timeout_seconds,
            )
        )
    else:
        logger.info("X API token not configured; polling disclosures + options radar only")
    return sources


class BaleenScheduler:
    def __init__(
        self,
        settings: Settings,
        processing: ProcessingPipeline,
        interval_seconds: int = 60,
        max_backoff_seconds: int = 600,
    ) -> None:
        self.settings = settings
        self.processing = processing
        self.interval_seconds = interval_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.sources = build_sources(settings)
        self.ingestion = IngestionPipeline(MultiSource(self.sources), processing.store, self.settings)
        self._consecutive_failures = 0
        self._job_id = "baleen-tick"

    def _tick(self) -> None:
        ok = False
        try:
            if self.sources:
                ingest = self.ingestion.run_once()
                ok = not ingest["errors"]
                logger.info(
                    "ingested %s records (%s skipped); processing",
                    ingest["inserted"],
                    ingest["duplicates_skipped"],
                )
            else:
                logger.debug("no ingestion sources configured; skipping ingestion")
                ok = True

            summary = self.processing.process_new(limit=self.settings.process_batch_size)
            logger.info("processed %s", summary)
        except Exception:
            ok = False
            logger.exception("scheduled tick failed")

        if ok:
            if self._consecutive_failures:
                logger.info("tick recovered after %d failure(s)", self._consecutive_failures)
            self._consecutive_failures = 0
            self._reschedule(self.interval_seconds)
        else:
            self._consecutive_failures += 1
            delay = min(
                self.interval_seconds * (2 ** (self._consecutive_failures - 1)),
                self.max_backoff_seconds,
            )
            logger.warning(
                "tick failed (%d consecutive); backing off to %ss",
                self._consecutive_failures,
                delay,
            )
            self._reschedule(delay)

    def _reschedule(self, interval_seconds: int) -> None:
        job = self.scheduler.get_job(self._job_id)
        if job is not None:
            job.reschedule(trigger=IntervalTrigger(seconds=interval_seconds))

    def start(self) -> None:
        if not self.settings.x_api_configured:
            logger.warning(
                "X API token is not configured; polling disclosures + options radar only"
            )
        self.scheduler.add_job(
            self._tick,
            id=self._job_id,
            trigger=IntervalTrigger(seconds=self.interval_seconds),
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        logger.info("scheduler started (tick every %ss)", self.interval_seconds)

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("scheduler stopped")
