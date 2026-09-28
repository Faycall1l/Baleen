"""Shared helpers for the ingestion suite.

Split out of the former ``tests/test_ingestion.py`` (M4), which now has one
module per ingestion concern: models, the Postgres store, the X source, and
pagination.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.ingestion.source import RawTweet


def make_tweet(tweet_id: str) -> RawTweet:
    return RawTweet(
        tweet_id=tweet_id,
        author_handle="unusual_whales",
        author_id="1",
        text=f"tweet {tweet_id}",
        created_at=datetime.now(timezone.utc),
        url=f"https://x.com/unusual_whales/status/{tweet_id}",
    )


def page_tweet(tweet_id):
    return SimpleNamespace(
        id=tweet_id,
        author_id="7",
        text=f"tweet {tweet_id}",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        data={},
    )


def page(tweets, next_token=None):
    return SimpleNamespace(
        data=tweets,
        includes={"users": [SimpleNamespace(id="7", username="unusual_whales")]},
        meta={"next_token": next_token},
    )


class FakeSource:
    def __init__(self, tweets):
        self.tweets = tweets

    def fetch_recent(self, handles, since_seconds):
        from app.ingestion.source import FetchResult

        result = FetchResult()
        result.tweets = list(self.tweets)
        return result
