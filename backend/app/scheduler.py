"""Scheduled execution of the ingest -> process loop.

A thin wrapper around APScheduler. The loop is intentionally kept to two calls:

1. ``IngestionPipeline.run_once()`` — pull new tweets from X, persist raw.
2. ``ProcessingPipeline.process_new()`` — extract, graph, alert.

When X credentials are placeholders the ingestion stage no-ops (the mock /
demo path uses ``scripts/ingest_once.py`` to load sample tweets instead).
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
    ) -> None:
        self.settings = settings
        self.processing = processing
        self.interval_seconds = interval_seconds
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.source = XApiSource(settings)
        self.ingestion = IngestionPipeline(self.source, processing.store, self.settings)

    def _tick(self) -> None:
        try:
            ingest = self.ingestion.run_once()
            logger.info(
                "ingested %s tweets (%s skipped)",
                ingest["inserted"],
                ingest["duplicates_skipped"],
            )
            summary = self.processing.process_new()
            logger.info("processed %s", summary)
        except Exception:
            logger.exception("scheduled tick failed")

    def start(self) -> None:
        if self.settings.x_api_bearer_token == "placeholder-bearer-token":
            logger.warning("X API token is a placeholder; ingestion will no-op")
        self.scheduler.add_job(
            self._tick,
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
