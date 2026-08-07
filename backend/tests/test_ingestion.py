"""Ingestion tests: deduplication, retry/backoff, and pipeline metrics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests
from pydantic import ValidationError
from sqlalchemy import text

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


def test_bulk_insert_counts_new_and_skipped(postgres_store):
    postgres_store.insert_raw_tweet(_tweet("500"))  # pre-existing

    tweets = [_tweet("500"), _tweet("501"), _tweet("502")]
    inserted, skipped = postgres_store.insert_raw_tweets(tweets)

    assert inserted == 2
    assert skipped == 1
    assert sorted(t.tweet_id for t in postgres_store.get_new_tweets(limit=10)) == [
        "500",
        "501",
        "502",
    ]


def test_bulk_insert_empty_batch_is_noop(postgres_store):
    assert postgres_store.insert_raw_tweets([]) == (0, 0)


def test_schema_is_idempotent_and_serves_new_tweet_query(postgres_store):
    postgres_store.init_schema()  # second run must not fail
    with postgres_store.engine.connect() as conn:
        indexes = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'raw_tweets'"
            )
        ).scalars().all()
    assert "idx_raw_tweets_status_created" in indexes


def test_reset_status_requeues_tweets_for_processing(postgres_store):
    tweets = [_tweet("300"), _tweet("301")]
    for t in tweets:
        postgres_store.insert_raw_tweet(t)

    postgres_store.mark_extracted(["300", "301"])
    assert postgres_store.get_new_tweets() == []

    assert postgres_store.reset_status(["300", "301"]) == 2
    assert sorted(t.tweet_id for t in postgres_store.get_new_tweets()) == ["300", "301"]

    assert postgres_store.reset_status([], "new") == 0


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


def _rate_limited() -> "tweepy.errors.TooManyRequests":
    import tweepy

    fake_response = SimpleNamespace(status_code=429, reason="Too Many Requests")
    return tweepy.errors.TooManyRequests(fake_response, response_json={"errors": []})


def test_retry_backoff_on_transient_error(monkeypatch):
    source = XApiSource(bearer_token="test", max_retries=2, base_delay_seconds=0.01)

    class FlakyClient:
        def __init__(self, bearer_token=None):
            self.attempts = 0

        def search_recent_tweets(self, **kwargs):
            self.attempts += 1
            if self.attempts < 3:
                raise _rate_limited()
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
            raise _rate_limited()

    monkeypatch.setattr(source, "_client", AlwaysFail())
    result = source.fetch_recent(["somehandle"], 60)

    assert not result.ok
    assert result.errors
    assert "X API fetch failed" in result.errors[0]


def _unauthorized() -> "tweepy.errors.Unauthorized":
    import tweepy

    fake_response = SimpleNamespace(status_code=401, reason="Unauthorized")
    return tweepy.errors.Unauthorized(fake_response, response_json={"errors": []})


def test_unauthorized_reports_token_problem(monkeypatch):
    """A wrong/expired bearer token (the most common real-X failure) must be
    reported clearly, not retried or swallowed."""
    source = XApiSource(bearer_token="bad-token", max_retries=3, base_delay_seconds=0.01)

    class AuthErrorClient:
        def __init__(self, bearer_token=None):
            self.calls = 0

        def search_recent_tweets(self, **kwargs):
            self.calls += 1
            raise _unauthorized()

    client = AuthErrorClient()
    monkeypatch.setattr(source, "_client", client)
    result = source.fetch_recent(["somehandle"], 60)

    assert not result.ok
    assert result.errors == ["X API unauthorized — check X_API_BEARER_TOKEN"]
    assert client.calls == 1  # Unauthorized is never retried


def test_x_api_source_rejects_non_string_token():
    with pytest.raises(TypeError):
        XApiSource(bearer_token=None)
    with pytest.raises(TypeError):
        XApiSource(bearer_token="")


def test_x_api_source_applies_request_timeout():
    """A hung X endpoint must not block the scheduler tick forever: every HTTP
    call goes through a session carrying Baleen's timeout."""
    from app.ingestion.x_api import _TimeoutSession

    source = XApiSource(bearer_token="test", timeout_seconds=17)
    assert isinstance(source._client.session, _TimeoutSession)
    assert source._client.session._baleen_timeout == 17


def test_timeout_session_injects_timeout_into_every_request():
    from app.ingestion.x_api import _TimeoutSession

    session = _TimeoutSession(5)
    captured = {}

    def spying_send(request, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        raise requests.exceptions.ConnectionError("boom")

    session.send = spying_send  # type: ignore[method-assign]
    with pytest.raises(requests.exceptions.ConnectionError):
        session.request("GET", "https://api.twitter.com/2/tweets/search/recent")
    assert captured["timeout"] == 5


def test_timeout_error_is_retried_then_recorded(monkeypatch):
    """A transport timeout is transient: retried with backoff, and when it
    exhausts retries it is recorded as a poll error, never raised (fetch_recent
    must never crash the scheduler tick)."""
    import requests

    source = XApiSource(
        bearer_token="test",
        max_retries=1,
        base_delay_seconds=0.01,
        timeout_seconds=2,
    )

    class TimingOutClient:
        def __init__(self, bearer_token=None):
            self.calls = 0

        def search_recent_tweets(self, **kwargs):
            self.calls += 1
            raise requests.exceptions.ReadTimeout("timed out")

    monkeypatch.setattr(source, "_client", TimingOutClient())
    result = source.fetch_recent(["somehandle"], 60)

    assert source._client.calls == 2  # 1 attempt + 1 retry
    assert not result.ok
    assert "X API fetch failed" in result.errors[0]


def test_x_api_source_normalizes_naive_created_at(monkeypatch):
    source = XApiSource(bearer_token="test")

    class Response:
        data = [
            SimpleNamespace(
                id="42",
                author_id="7",
                text="hello",
                created_at=datetime(2026, 1, 1, 12, 0, 0),  # naive
                data={},
            )
        ]
        includes = {"users": [SimpleNamespace(id="7", username="unusual_whales")]}

    class OkClient:
        def __init__(self, bearer_token=None):
            pass

        def search_recent_tweets(self, **kwargs):
            return Response()

    monkeypatch.setattr(source, "_client", OkClient())
    result = source.fetch_recent(["somehandle"], 60)

    assert len(result.tweets) == 1
    assert result.tweets[0].created_at.tzinfo is not None
    assert result.tweets[0].created_at.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# Pagination (next_token)
# ---------------------------------------------------------------------------


def _page(tweets, next_token=None):
    return SimpleNamespace(
        data=tweets,
        includes={"users": [SimpleNamespace(id="7", username="unusual_whales")]},
        meta={"next_token": next_token},
    )


def _page_tweet(tweet_id):
    return SimpleNamespace(
        id=tweet_id,
        author_id="7",
        text=f"tweet {tweet_id}",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        data={},
    )


def test_pagination_follows_next_token(monkeypatch):
    source = XApiSource(bearer_token="test", max_pages=3)
    pages = [
        _page([_page_tweet("1"), _page_tweet("2")], next_token="tok"),
        _page([_page_tweet("2"), _page_tweet("3")], next_token=None),  # overlap dedup
    ]

    class PagedClient:
        def __init__(self, bearer_token=None):
            self.calls = 0
            self.tokens = []

        def search_recent_tweets(self, **kwargs):
            self.calls += 1
            self.tokens.append(kwargs.get("next_token"))
            return pages.pop(0)

    client = PagedClient()
    monkeypatch.setattr(source, "_client", client)
    result = source.fetch_recent(["somehandle"], 60)

    assert client.calls == 2
    assert client.tokens == [None, "tok"]
    assert [t.tweet_id for t in result.tweets] == ["1", "2", "3"]


def test_pagination_stops_without_next_token(monkeypatch):
    source = XApiSource(bearer_token="test", max_pages=3)
    pages = [_page([_page_tweet("1")], next_token=None)]

    class SinglePageClient:
        def __init__(self, bearer_token=None):
            self.calls = 0

        def search_recent_tweets(self, **kwargs):
            self.calls += 1
            return pages.pop(0)

    client = SinglePageClient()
    monkeypatch.setattr(source, "_client", client)
    result = source.fetch_recent(["somehandle"], 60)

    assert client.calls == 1
    assert len(result.tweets) == 1


def test_pagination_respects_max_pages(monkeypatch):
    source = XApiSource(bearer_token="test", max_pages=2)

    class InfiniteClient:
        def __init__(self, bearer_token=None):
            self.calls = 0

        def search_recent_tweets(self, **kwargs):
            self.calls += 1
            return _page([_page_tweet(str(self.calls))], next_token="more")

    client = InfiniteClient()
    monkeypatch.setattr(source, "_client", client)
    result = source.fetch_recent(["somehandle"], 60)

    assert client.calls == 2
    assert [t.tweet_id for t in result.tweets] == ["1", "2"]


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
