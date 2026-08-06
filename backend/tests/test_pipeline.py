"""Processing pipeline tests: failure isolation and retry semantics."""

from __future__ import annotations

from datetime import datetime, timezone

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
    postgres_store.insert_raw_tweet(_raw("t-1"))

    pipeline = ProcessingPipeline(
        extractor=_TransportDownExtractor(),
        writer=_FakeWriter(),
        alert_engine=_NoopAlertEngine(),
        store=postgres_store,
    )

    summary = pipeline.process_new()

    assert summary["failed"] == 1
    # A down LLM must not mark the tweet extracted: it stays new and is retried
    # on the next run once the endpoint recovers.
    pending = postgres_store.get_new_tweets(limit=10)
    assert [t.tweet_id for t in pending] == ["t-1"]
