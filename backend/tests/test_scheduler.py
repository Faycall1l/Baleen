"""BaleenScheduler failure handling: exponential backoff, then recovery."""

from __future__ import annotations

from unittest.mock import Mock

from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.scheduler import BaleenScheduler


class _FakeProcessing:
    store = object()


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


def test_backs_off_then_recovers(settings):
    scheduler = _started_scheduler(settings, interval=60)
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


def test_backoff_is_capped(settings):
    scheduler = _started_scheduler(settings, interval=60)
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


def test_exception_in_tick_also_triggers_backoff(settings):
    scheduler = _started_scheduler(settings, interval=60)
    scheduler.ingestion.run_once = Mock(side_effect=RuntimeError("boom"))

    scheduler._tick()
    assert scheduler._consecutive_failures == 1
    assert _interval_of(scheduler) == 60

    scheduler._tick()
    assert scheduler._consecutive_failures == 2
    assert _interval_of(scheduler) == 120


def test_tick_uses_configured_process_batch_size(settings):
    scheduler = _started_scheduler(settings, interval=60)
    scheduler.ingestion.run_once = Mock(
        return_value={"inserted": 0, "duplicates_skipped": 0, "errors": []}
    )
    scheduler.processing.process_new = Mock(return_value={"processed": 0})

    scheduler._tick()
    scheduler.processing.process_new.assert_called_once_with(limit=settings.process_batch_size)


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
