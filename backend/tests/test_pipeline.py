"""Processing pipeline tests: failure isolation and retry semantics."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.pipeline import ProcessingPipeline
from app.ingestion.source import RawTweet
from app.models.events import OptionsFlowEvent


class _RaisingExtractor:
    """Extracts a valid event for ``ok-*`` tweets, raises for everything else."""

    def extract(self, tweet: RawTweet):
        if not tweet.tweet_id.startswith("ok-"):
            raise RuntimeError(f"boom {tweet.tweet_id}")
        return OptionsFlowEvent(
            tweet_id=tweet.tweet_id,
            tweet_url=tweet.url,
            confidence=0.9,
            ticker="JPM",
            contract_type="call",
        )


class _TransportDownExtractor:
    """Simulates the LLM endpoint being unreachable (transient infrastructure)."""

    def extract(self, tweet: RawTweet):
        from app.extraction.client import LLMTransportError

        raise LLMTransportError("connection refused")


class _MixedExtractor:
    """Succeeds for ``ok-*`` tweets, transport-fails for everything else."""

    def extract(self, tweet: RawTweet):
        from app.extraction.client import LLMTransportError

        if tweet.tweet_id.startswith("ok-"):
            return OptionsFlowEvent(
                tweet_id=tweet.tweet_id,
                tweet_url=tweet.url,
                confidence=0.9,
                ticker="JPM",
                contract_type="call",
            )
        raise LLMTransportError("connection refused")


class _FakeWriter:
    def write_event(self, event) -> str:
        return f"evt_{event.tweet_id}"


class _NoopAlertEngine:
    def evaluate_event(self, event) -> list[dict]:
        return []


def _raw(tweet_id: str) -> RawTweet:
    return RawTweet(
        tweet_id=tweet_id,
        author_handle="unusual_whales",
        author_id="1",
        text=f"tweet {tweet_id}",
        created_at=datetime.now(timezone.utc),
        url=f"https://x.com/unusual_whales/status/{tweet_id}",
    )


def test_failed_tweet_does_not_abort_batch_or_get_marked_extracted(postgres_store):
    postgres_store.insert_raw_tweet(_raw("ok-good"))
    postgres_store.insert_raw_tweet(_raw("bad-broken"))
    postgres_store.insert_raw_tweet(_raw("ok-fine"))

    pipeline = ProcessingPipeline(
        extractor=_RaisingExtractor(),
        writer=_FakeWriter(),
        alert_engine=_NoopAlertEngine(),
        store=postgres_store,
    )

    summary = pipeline.process_new()

    assert summary["processed"] == 2
    assert summary["failed"] == 1
    assert summary["unparsed"] == 0

    statuses = postgres_store.get_new_tweets(limit=10)
    assert [t.tweet_id for t in statuses] == ["bad-broken"]

    # A second run retries only the failed tweet.
    retry = pipeline.process_new()
    assert retry["failed"] == 1


def test_transport_failure_leaves_tweet_pending_for_retry(postgres_store):
    from app.extraction.client import LLMTransportError

    postgres_store.insert_raw_tweet(_raw("t-1"))

    pipeline = ProcessingPipeline(
        extractor=_TransportDownExtractor(),
        writer=_FakeWriter(),
        alert_engine=_NoopAlertEngine(),
        store=postgres_store,
    )

    with pytest.raises(LLMTransportError):
        pipeline.process_new()

    # A down LLM must not mark the tweet extracted: it stays new and is retried
    # on the next run once the endpoint recovers.
    pending = postgres_store.get_new_tweets(limit=10)
    assert [t.tweet_id for t in pending] == ["t-1"]


def test_whole_batch_transport_failure_raises_for_backoff(postgres_store):
    """When the LLM is fully down, signal the scheduler to back off."""
    from app.extraction.client import LLMTransportError

    postgres_store.insert_raw_tweet(_raw("t-1"))
    postgres_store.insert_raw_tweet(_raw("t-2"))

    pipeline = ProcessingPipeline(
        extractor=_TransportDownExtractor(),
        writer=_FakeWriter(),
        alert_engine=_NoopAlertEngine(),
        store=postgres_store,
    )

    with pytest.raises(LLMTransportError):
        pipeline.process_new()

    pending = postgres_store.get_new_tweets(limit=10)
    assert [t.tweet_id for t in pending] == ["t-1", "t-2"]


def test_mixed_transport_and_success_does_not_raise(postgres_store):
    postgres_store.insert_raw_tweet(_raw("ok-good"))
    postgres_store.insert_raw_tweet(_raw("bad-down"))

    pipeline = ProcessingPipeline(
        extractor=_MixedExtractor(),
        writer=_FakeWriter(),
        alert_engine=_NoopAlertEngine(),
        store=postgres_store,
    )

    summary = pipeline.process_new()

    assert summary["processed"] == 1
    assert summary["failed"] == 1
    # Only the successfully-processed tweet is marked extracted.
    pending = postgres_store.get_new_tweets(limit=10)
    assert [t.tweet_id for t in pending] == ["bad-down"]


class _UnparsedExtractor:
    """Extracts a low-confidence UnparsedEvent for every tweet."""

    def extract(self, tweet: RawTweet):
        from app.models.events import UnparsedEvent

        return UnparsedEvent(
            tweet_id=tweet.tweet_id,
            tweet_url=tweet.url,
            confidence=0.3,
            raw_text=tweet.text,
            reason="low confidence",
        )


class _CountingAlerts:
    def __init__(self):
        self.count = 0

    def evaluate_event(self, event) -> list[dict]:
        self.count += 1
        return [{"id": self.count}]


class _BoomReports:
    def write_brief(self, event):
        raise RuntimeError("report engine blew")


def test_unparsed_tweets_count_without_alerting_or_reporting(postgres_store):
    """Unparsed rows still count as processed, but never fire alerts and their
    report engine (if any) is not consulted."""
    postgres_store.insert_raw_tweet(_raw("up-1"))
    postgres_store.insert_raw_tweet(_raw("up-2"))

    alerts = _CountingAlerts()
    pipeline = ProcessingPipeline(
        extractor=_UnparsedExtractor(),
        writer=_FakeWriter(),
        alert_engine=alerts,
        store=postgres_store,
        report_engine=_BoomReports(),  # raises when consulted; unparsed must not consult
    )

    summary = pipeline.process_new()

    assert summary["processed"] == 2
    assert summary["unparsed"] == 2
    assert alerts.count == 0
    assert summary["alerts"] == 0
    assert summary["reports"] == 0
    assert summary["failed"] == 0


def test_report_engine_failure_is_isolated_to_that_tweet(postgres_store):
    """A crash in brief writing must not abort the batch — the tweet is still
    processed and its alert survives."""
    postgres_store.insert_raw_tweet(_raw("ok-a"))

    pipeline = ProcessingPipeline(
        extractor=_RaisingExtractor(),
        writer=_FakeWriter(),
        alert_engine=_CountingAlerts(),
        store=postgres_store,
        report_engine=_BoomReports(),
    )

    summary = pipeline.process_new()

    assert summary["processed"] == 1
    assert summary["alerts"] == 1
    assert summary["reports"] == 0
    assert summary["failed"] == 0
