"""BaleenScheduler failure handling: exponential backoff, then recovery."""

from __future__ import annotations

from unittest.mock import Mock

from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.scheduler import BaleenScheduler


class _FakeProcessing:
    store = object()


def _live_settings():
    return Settings(_env_file=None, x_api_bearer_token="real-bearer-token")


def _started_scheduler(settings: Settings, interval: int = 60) -> BaleenScheduler:
    scheduler = BaleenScheduler(settings, _FakeProcessing(), interval_seconds=interval)
    scheduler.scheduler.add_job(
        lambda: None,
        id=scheduler._job_id,
        trigger=IntervalTrigger(seconds=interval),
    )
    return scheduler


def _interval_of(scheduler: BaleenScheduler) -> float:
    trigger = scheduler.scheduler.get_job(scheduler._job_id).trigger
    assert isinstance(trigger, IntervalTrigger)
    return trigger.interval.total_seconds()


def test_backs_off_then_recovers():
    scheduler = _started_scheduler(_live_settings(), interval=60)
    scheduler.ingestion.run_once = Mock(
        return_value={"inserted": 0, "duplicates_skipped": 0, "errors": ["X API fetch failed"]}
    )
    scheduler.processing.process_new = Mock(return_value={"processed": 0})

    scheduler._tick()  # failure 1 -> base interval
    assert scheduler._consecutive_failures == 1
    assert _interval_of(scheduler) == 60

    scheduler._tick()  # failure 2 -> 120s
    assert scheduler._consecutive_failures == 2
    assert _interval_of(scheduler) == 120

    scheduler._tick()  # failure 3 -> 240s
    assert scheduler._consecutive_failures == 3
    assert _interval_of(scheduler) == 240

    scheduler.ingestion.run_once.return_value = {
        "inserted": 1,
        "duplicates_skipped": 0,
        "errors": [],
    }
    scheduler._tick()  # success -> back to base
    assert scheduler._consecutive_failures == 0
    assert _interval_of(scheduler) == 60


def test_backoff_is_capped():
    scheduler = _started_scheduler(_live_settings(), interval=60)
    scheduler.ingestion.run_once = Mock(
        return_value={"inserted": 0, "duplicates_skipped": 0, "errors": ["X API fetch failed"]}
    )
    scheduler.processing.process_new = Mock(return_value={"processed": 0})

    scheduler._tick()
    scheduler._tick()
    scheduler._tick()
    scheduler._tick()
    scheduler._tick()
    scheduler._tick()
    scheduler._tick()
    scheduler._tick()
    assert scheduler._consecutive_failures == 8
    assert _interval_of(scheduler) == 600  # max_backoff_seconds


def test_exception_in_tick_also_triggers_backoff():
    scheduler = _started_scheduler(_live_settings(), interval=60)
    scheduler.ingestion.run_once = Mock(side_effect=RuntimeError("boom"))

    scheduler._tick()
    assert scheduler._consecutive_failures == 1
    assert _interval_of(scheduler) == 60

    scheduler._tick()
    assert scheduler._consecutive_failures == 2
    assert _interval_of(scheduler) == 120


def test_llm_transport_error_in_tick_triggers_backoff():
    """A fully-down LLM raises LLMTransportError from process_new, which must
    surface to the scheduler as a failure so it backs off instead of hammering
    the dead endpoint every tick."""
    from app.extraction.client import LLMTransportError

    scheduler = _started_scheduler(_live_settings(), interval=60)
    scheduler.ingestion.run_once = Mock(return_value={"inserted": 1, "duplicates_skipped": 0, "errors": []})
    scheduler.processing.process_new = Mock(side_effect=LLMTransportError("connection refused"))

    scheduler._tick()
    assert scheduler._consecutive_failures == 1
    assert _interval_of(scheduler) == 60

    scheduler._tick()
    assert scheduler._consecutive_failures == 2
    assert _interval_of(scheduler) == 120


def test_tick_uses_configured_process_batch_size():
    settings = _live_settings()
    scheduler = _started_scheduler(settings, interval=60)
    scheduler.ingestion.run_once = Mock(return_value={"inserted": 0, "duplicates_skipped": 0, "errors": []})
    scheduler.processing.process_new = Mock(return_value={"processed": 0})

    scheduler._tick()
    scheduler.processing.process_new.assert_called_once_with(limit=settings.process_batch_size)


def test_placeholder_token_excludes_x_but_still_ingests_and_processes():
    """Without an X token the X source is left out, but the keyless feeds
    (disclosures + options radar) still poll and pending records still drain
    through processing."""
    from app.ingestion.x_api import XApiSource

    settings = Settings(_env_file=None)  # default token is a placeholder
    scheduler = _started_scheduler(settings, interval=60)
    assert not any(isinstance(s, XApiSource) for s in scheduler.sources)
    assert len(scheduler.sources) == 4  # disclosures + clerk-direct + EDGAR + options radar

    scheduler.ingestion.run_once = Mock(return_value={"inserted": 1, "duplicates_skipped": 0, "errors": []})
    scheduler.processing.process_new = Mock(return_value={"processed": 2})

    scheduler._tick()

    scheduler.processing.process_new.assert_called_once_with(limit=settings.process_batch_size)
    assert scheduler._consecutive_failures == 0  # not treated as a failure
    assert _interval_of(scheduler) == 60


def test_no_sources_skips_ingestion_but_still_processes():
    """A tick with zero configured sources must not be a failure: it skips
    ingestion (there is nothing to poll) yet still drains pending records."""
    settings = _live_settings()
    scheduler = _started_scheduler(settings, interval=60)
    scheduler.sources = []
    scheduler.ingestion.run_once = Mock()
    scheduler.processing.process_new = Mock(return_value={"processed": 1})

    scheduler._tick()

    scheduler.ingestion.run_once.assert_not_called()
    scheduler.processing.process_new.assert_called_once_with(limit=settings.process_batch_size)
    assert scheduler._consecutive_failures == 0
    assert _interval_of(scheduler) == 60


def test_start_and_stop_lifecycle() -> None:
    """start() registers the interval job on a running scheduler; stop()
    shuts the background thread down (and is safe to call twice)."""
    scheduler = BaleenScheduler(_live_settings(), _FakeProcessing(), interval_seconds=60)

    scheduler.start()
    try:
        assert scheduler.scheduler.running
        assert scheduler.scheduler.get_job(scheduler._job_id) is not None
        trigger = scheduler.scheduler.get_job(scheduler._job_id).trigger
        assert isinstance(trigger, IntervalTrigger)
        assert trigger.interval.total_seconds() == 60
    finally:
        scheduler.stop()
    assert not scheduler.scheduler.running

    scheduler.stop()  # idempotent: never raises after shutdown


def test_real_token_includes_x_source():
    from app.ingestion.x_api import XApiSource

    scheduler = _started_scheduler(_live_settings(), interval=60)
    assert any(isinstance(s, XApiSource) for s in scheduler.sources)
    assert len(scheduler.sources) == 5  # disclosures + clerk-direct + EDGAR + options radar + X


def test_start_warns_when_x_api_not_configured(caplog):
    import logging

    settings = Settings(_env_file=None)  # default bearer token is a placeholder
    scheduler = BaleenScheduler(settings, _FakeProcessing(), interval_seconds=60)

    with caplog.at_level(logging.WARNING, logger="app.scheduler"):
        scheduler.start()
    try:
        assert scheduler.scheduler.running
        assert "X API token is not configured" in caplog.text
        assert scheduler.scheduler.get_job(scheduler._job_id) is not None
    finally:
        scheduler.stop()


def test_build_scheduler_uses_poll_interval(monkeypatch):
    import app.main as main

    captured = {}

    class FakeScheduler:
        def __init__(self, settings, processing, interval_seconds=60):
            captured["interval_seconds"] = interval_seconds

    monkeypatch.setattr(main, "BaleenScheduler", FakeScheduler)
    monkeypatch.setattr(main.routes, "_get_processing", lambda: object())
    settings = Settings(_env_file=None, poll_interval_seconds=321)

    main._build_scheduler(settings)

    assert captured["interval_seconds"] == 321


def test_partial_source_failure_does_not_back_off():
    """one erroring feed must not back off the healthy ones — a
    tick that ingested records made progress."""
    scheduler = _started_scheduler(_live_settings(), interval=60)
    scheduler.ingestion.run_once = Mock(
        return_value={"inserted": 5, "duplicates_skipped": 0, "errors": ["OptionsChainSource: 429 throttled"]}
    )
    scheduler.processing.process_new = Mock(return_value={"processed": 0})

    scheduler._tick()

    assert scheduler._consecutive_failures == 0
    assert _interval_of(scheduler) == 60
