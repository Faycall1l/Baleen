"""Scheduled execution of the ingest -> process loop.

A thin wrapper around APScheduler. The loop is intentionally kept to two calls:

1. ``IngestionPipeline.run_once()`` — pull new tweets from X, persist raw.
2. ``ProcessingPipeline.process_new()`` — extract, graph, alert.

When X credentials are placeholders the ingestion stage no-ops (the mock /
demo path uses ``scripts/ingest_once.py`` to load sample tweets instead).

On repeated failures (rate limits, transient API errors) the tick backs off
exponentially up to ``max_backoff_seconds`` instead of hammering the API, and
recovers to the base interval on the next successful run.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.x_api import XApiSource
from app.pipeline import ProcessingPipeline

logger = logging.getLogger(__name__)


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
        self.source = XApiSource(settings.x_api_bearer_token)
        self.ingestion = IngestionPipeline(self.source, processing.store, self.settings)
        self._consecutive_failures = 0
        self._job_id = "baleen-tick"

    def _tick(self) -> None:
        ok = False
        try:
            ingest = self.ingestion.run_once()
            summary = self.processing.process_new()
            ok = not ingest["errors"]
            logger.info(
                "ingested %s tweets (%s skipped); processed %s",
                ingest["inserted"],
                ingest["duplicates_skipped"],
                summary,
            )
        except Exception:
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
            logger.warning("X API token is not configured; ingestion will no-op until it is")
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
