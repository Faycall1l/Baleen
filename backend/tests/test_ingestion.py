"""Ingestion tests: deduplication, retry/backoff, and pipeline metrics."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source import RawTweet
from app.ingestion.x_api import XApiSource

# ---------------------------------------------------------------------------
# RawTweet model
# ---------------------------------------------------------------------------


def test_raw_tweet_requires_tweet_id():
    with pytest.raises(ValidationError):
        RawTweet(  # type: ignore[call-arg]
            author_handle="unusual_whales",
            author_id="1",
            text="hello",
            created_at=datetime.now(timezone.utc),
            url="https://x.com/x/status/1",
        )


# ---------------------------------------------------------------------------
# Deduplication (idempotency)
# ---------------------------------------------------------------------------


def _tweet(tweet_id: str) -> RawTweet:
    return RawTweet(
        tweet_id=tweet_id,
        author_handle="unusual_whales",
        author_id="1",
        text=f"tweet {tweet_id}",
        created_at=datetime.now(timezone.utc),
        url=f"https://x.com/unusual_whales/status/{tweet_id}",
    )


def test_insert_raw_tweet_dedups_on_tweet_id(postgres_store):
    tweet = _tweet("100")

    assert postgres_store.insert_raw_tweet(tweet) is True
    assert postgres_store.insert_raw_tweet(tweet) is False  # duplicate skipped

    remaining = postgres_store.get_new_tweets(limit=10)
    assert [t.tweet_id for t in remaining] == ["100"]


def test_pipeline_rerun_is_idempotent(settings, postgres_store):
    tweets = [_tweet("200"), _tweet("201")]
    source = _FakeSource(tweets)
    pipeline = IngestionPipeline(source, postgres_store, settings)

    first = pipeline.run_once()
    second = pipeline.run_once()  # same input again

    assert first["inserted"] == 2
    assert second["inserted"] == 0
    assert second["duplicates_skipped"] == 2
    assert len(postgres_store.get_new_tweets()) == 2


# ---------------------------------------------------------------------------
# Retry / backoff on the X API source
# ---------------------------------------------------------------------------


def test_retry_backoff_on_transient_error(monkeypatch):
    source = XApiSource(bearer_token="test", max_retries=2, base_delay_seconds=0.01)

    class FlakyClient:
        def __init__(self, bearer_token=None):
            self.attempts = 0

        def search_recent_tweets(self, **kwargs):
            self.attempts += 1
            if self.attempts < 3:
                import tweepy

                raise tweepy.errors.TooManyRequests("rate limited")
            return None

    monkeypatch.setattr(source, "_client", FlakyClient())
    result = source.fetch_recent(["somehandle"], 60)

    assert source._client.attempts == 3  # 2 retries after first failure
    assert result.ok


def test_gives_up_after_max_retries(monkeypatch):
    source = XApiSource(bearer_token="test", max_retries=2, base_delay_seconds=0.01)

    class AlwaysFail:
        def __init__(self, bearer_token=None):
            pass

        def search_recent_tweets(self, **kwargs):
            import tweepy

            raise tweepy.errors.TooManyRequests("rate limited")

    monkeypatch.setattr(source, "_client", AlwaysFail())
    result = source.fetch_recent(["somehandle"], 60)

    assert source._client  # sanity
    assert not result.ok
    assert result.errors
    assert "X API fetch failed" in result.errors[0]


def test_pipeline_records_source_errors(settings, postgres_store):
    class ErrorSource:
        def fetch_recent(self, handles, since_seconds):
            from app.ingestion.source import FetchResult

            r = FetchResult()
            r.errors.append("boom")
            return r

    pipeline = IngestionPipeline(ErrorSource(), postgres_store, settings)
    metrics = pipeline.run_once()

    assert metrics["fetched"] == 0
    assert metrics["errors"] == ["boom"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSource:
    def __init__(self, tweets):
        self.tweets = tweets

    def fetch_recent(self, handles, since_seconds):
        from app.ingestion.source import FetchResult

        result = FetchResult()
        result.tweets = list(self.tweets)
        return result
