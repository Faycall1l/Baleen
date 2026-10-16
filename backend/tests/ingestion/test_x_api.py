"""X API source: token validation, timeouts, retry/backoff, error reporting.

Part of the ingestion suite split out of the former ``tests/test_ingestion.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import requests

from app.ingestion.x_api import XApiSource

if TYPE_CHECKING:
    import tweepy


def _rate_limited() -> tweepy.errors.TooManyRequests:
    import tweepy

    fake_response = SimpleNamespace(status_code=429, reason="Too Many Requests")
    return tweepy.errors.TooManyRequests(fake_response, response_json={"errors": []})


def _unauthorized() -> tweepy.errors.Unauthorized:
    import tweepy

    fake_response = SimpleNamespace(status_code=401, reason="Unauthorized")
    return tweepy.errors.Unauthorized(fake_response, response_json={"errors": []})


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


def test_x_api_source_returns_immediately_with_no_handles():
    """With an empty handle list there is nothing to query: fetch_recent short-
    circuits without touching tweepy/the network."""
    result = XApiSource(bearer_token="test").fetch_recent([], 3600)
    assert result.tweets == []
    assert result.errors == []


def test_sample_source_emits_five_uniquely_keyed_tweets():
    from app.ingestion.source import SampleSource

    result = SampleSource().fetch_recent([], 3600)
    ids = {t.tweet_id for t in result.tweets}
    assert len(result.tweets) == 5
    assert ids == {f"sample-100{i}" for i in range(1, 6)}
    assert result.errors == []


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
