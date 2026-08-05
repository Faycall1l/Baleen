"""Tweet source abstraction.

``TweetSource`` is the interface the ingestion pipeline talks to. The concrete
X API v2 implementation lives in :mod:`app.ingestion.x_api`; tests provide an
in-memory fake implementing the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, Field


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
