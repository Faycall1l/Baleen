"""Tweet source abstraction.

``TweetSource`` is the interface the ingestion pipeline talks to. The concrete
X API v2 implementation lives in :mod:`app.ingestion.x_api`; tests provide an
in-memory fake implementing the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class BoundedSeen:
    """A membership set that evicts the oldest entry once it exceeds ``cap``.

    Used by the filing sources to skip filings a poll already handled this
    process run. The in-memory set is purely an optimization — downstream
    dedup by ``tweet_id`` is authoritative — so eviction only causes an
    occasional redundant download of an old filing, never a duplicate insert.
    """

    def __init__(self, cap: int) -> None:
        if cap < 1:
            raise ValueError("cap must be >= 1")
        self._cap = cap
        self._order: deque[str] = deque()
        self._seen: set[str] = set()

    def __contains__(self, key: str) -> bool:
        return key in self._seen

    def remember(self, key: str) -> None:
        if key in self._seen:
            return
        if len(self._seen) >= self._cap:
            self._seen.discard(self._order.popleft())
        self._seen.add(key)
        self._order.append(key)


class RawTweet(BaseModel):
    """A normalized tweet as stored in ``raw_tweets``.

    ``tweet_id`` is the natural identifier used for deduplication.
    """

    tweet_id: str
    author_handle: str
    author_id: str
    text: str
    created_at: datetime
    url: str
    raw_json: dict = Field(default_factory=dict)


@dataclass
class FetchResult:
    """Outcome of one polling call to a source.

    ``errors`` are collected (never raised) so a partial failure in one handle
    does not silently kill the whole poll — every error is logged by the caller.
    """

    tweets: list[RawTweet] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class TweetSource(ABC):
    """Interface for fetching tweets. Implementations must be safe to poll
    repeatedly; callers rely on downstream deduplication by ``tweet_id``."""

    @abstractmethod
    def fetch_recent(self, handles: list[str], since_seconds: int) -> FetchResult:
        """Fetch tweets for ``handles`` published within the last
        ``since_seconds``. Never raises; returns errors in ``FetchResult``."""


class MultiSource(TweetSource):
    """Fan-out over several sources (X + disclosure feeds + options radar).

    Lets the scheduler poll heterogeneous real-data sources through the one
    ``TweetSource`` interface without changing the ingestion pipeline. One
    failing child never aborts the others; every failure is namespaced into
    ``FetchResult.errors``.
    """

    def __init__(self, sources: list[TweetSource]) -> None:
        self.sources = list(sources)

    def fetch_recent(self, handles: list[str], since_seconds: int) -> FetchResult:
        tweets: list[RawTweet] = []
        errors: list[str] = []
        for source in self.sources:
            name = type(source).__name__
            try:
                result = source.fetch_recent(handles, since_seconds)
            except Exception as exc:  # never let one source kill the poll
                errors.append(f"{name} failed: {exc}")
                continue
            tweets.extend(result.tweets)
            errors.extend(f"{name}: {e}" for e in result.errors)
        return FetchResult(tweets=tweets, errors=errors)


# ---------------------------------------------------------------------------
# Sample source (local demos without X API credentials)
# ---------------------------------------------------------------------------


def _sample(  # noqa: PLR0913
    tweet_id: str,
    handle: str,
    author_id: str,
    text: str,
    when: datetime,
) -> RawTweet:
    return RawTweet(
        tweet_id=tweet_id,
        author_handle=handle,
        author_id=author_id,
        text=text,
        created_at=when,
        url=f"https://x.com/{handle}/status/{tweet_id}",
    )


class SampleSource(TweetSource):
    """A small bundled corpus of realistic financial tweets.

    Covers options-flow reports, a congressional trade, and noise, so the full
    stack can be exercised end-to-end without X API credentials. Deduplicated
    on re-runs by ``tweet_id``.
    """

    def fetch_recent(self, handles: list[str], since_seconds: int) -> FetchResult:
        now = datetime.now(UTC)
        tweets = [
            _sample(
                "sample-1001",
                "unusual_whales",
                "uwhales",
                "BIG ALERT: $NVDA 7,500 calls at $130 expiring 06/20 are filling right now. "
                "Notional ~$97M, unusual volume vs open interest.",
                now,
            ),
            _sample(
                "sample-1002",
                "unusual_whales",
                "uwhales",
                "$AMD sweep 1,200 puts at $180. Looking like a large buyer.",
                now,
            ),
            _sample(
                "sample-1003",
                "congress_trades",
                "ctrades",
                "REPORTED: Rep. Bill Cassidy sold $UNH shares worth $50,001 - $100,000 on 2026-05-14. "
                "Disclosure #D-2026-001.",
                now,
            ),
            _sample(
                "sample-1004",
                "unusual_whales",
                "uwhales",
                "Markets are green today, retail sentiment improving after the last CPI print.",
                now,
            ),
            _sample(
                "sample-1005",
                "unusual_whales",
                "uwhales",
                "$TSLA 2,000 puts at $320, 06/20 expiry printing now. High IV.",
                now,
            ),
        ]
        return FetchResult(tweets=tweets)
