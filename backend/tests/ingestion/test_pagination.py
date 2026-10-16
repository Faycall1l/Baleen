"""X API pagination: next-token following and the max-pages stop.

Part of the ingestion suite split out of the former ``tests/test_ingestion.py``.
"""

from __future__ import annotations

from app.ingestion.x_api import XApiSource
from tests.ingestion.conftest import page, page_tweet


def test_pagination_follows_next_token(monkeypatch):
    source = XApiSource(bearer_token="test", max_pages=3)
    pages = [
        page([page_tweet("1"), page_tweet("2")], next_token="tok"),
        page([page_tweet("2"), page_tweet("3")], next_token=None),  # overlap dedup
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
    pages = [page([page_tweet("1")], next_token=None)]

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
            return page([page_tweet(str(self.calls))], next_token="more")

    client = InfiniteClient()
    monkeypatch.setattr(source, "_client", client)
    result = source.fetch_recent(["somehandle"], 60)

    assert client.calls == 2
    assert [t.tweet_id for t in result.tweets] == ["1", "2"]
