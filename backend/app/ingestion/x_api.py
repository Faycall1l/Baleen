"""X (Twitter) API v2 implementation of :class:`TweetSource`.

Uses the official ``tweepy`` client against the ``search/recent`` endpoint —
no scraping, respects X's rate limits. All API calls go through a
retry/backoff wrapper; every poll is logged with count, timestamp, and errors.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import tweepy

from app.ingestion.source import FetchResult, RawTweet, TweetSource

logger = logging.getLogger(__name__)

RETRYABLE_ERRORS = (tweepy.errors.TooManyRequests, tweepy.errors.NotFound)


class XApiSource(TweetSource):
    """Poll the X API v2 ``search/recent`` endpoint for configured handles."""

    def __init__(
        self,
        bearer_token: str,
        max_retries: int = 3,
        base_delay_seconds: float = 2.0,
        backoff_factor: float = 2.0,
        max_results: int = 100,
    ) -> None:
        if not isinstance(bearer_token, str) or not bearer_token:
            raise TypeError("bearer_token must be a non-empty string")
        self._client = tweepy.Client(bearer_token=bearer_token)
        self._max_retries = max_retries
        self._base_delay = base_delay_seconds
        self._backoff_factor = backoff_factor
        self._max_results = max_results

    # ------------------------------------------------------------------
    # TweetSource interface
    # ------------------------------------------------------------------

    def fetch_recent(self, handles: list[str], since_seconds: int) -> FetchResult:
        result = FetchResult()
        if not handles:
            return result

        query = " OR ".join(f"from:{h}" for h in handles)
        start_time = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)

        logger.info("fetching recent tweets [query=%r start_time=%s]", query, start_time)
        try:
            response = self._with_retry(
                lambda: self._client.search_recent_tweets(
                    query=query,
                    start_time=start_time,
                    max_results=self._max_results,
                    tweet_fields=["id", "created_at", "author_id", "text"],
                    user_fields=["username", "name"],
                    expansions=["author_id"],
                )
            )
        except tweepy.errors.Unauthorized:
            message = "X API unauthorized — check X_API_BEARER_TOKEN"
            logger.error(message)
            result.errors.append(message)
            return result
        except tweepy.errors.TweepyException as exc:
            message = f"X API fetch failed: {exc}"
            logger.error(message)
            result.errors.append(message)
            return result

        if not response or not response.data:
            logger.info("no tweets returned")
            return result

        users = {u.id: u.username for u in (response.includes.get("users", []) or [])}
        for tweet in response.data:
            handle = users.get(tweet.author_id, "unknown")
            created_at = tweet.created_at
            if created_at.tzinfo is None:
                # X API timestamps are UTC; normalize naive datetimes so
                # Postgres TIMESTAMPTZ round-trips correctly.
                created_at = created_at.replace(tzinfo=timezone.utc)
            result.tweets.append(
                RawTweet(
                    tweet_id=str(tweet.id),
                    author_handle=handle,
                    author_id=str(tweet.author_id),
                    text=tweet.text,
                    created_at=created_at,
                    url=f"https://x.com/{handle}/status/{tweet.id}",
                    raw_json=tweet.data,
                )
            )

        logger.info(
            "fetched %d tweets [query=%r]",
            len(result.tweets),
            query,
        )
        return result

    # ------------------------------------------------------------------
    # Retry / backoff
    # ------------------------------------------------------------------

    def _with_retry(self, fn):
        """Run ``fn`` with exponential backoff on transient errors."""
        attempt = 0
        while True:
            try:
                return fn()
            except RETRYABLE_ERRORS as exc:
                attempt += 1
                if attempt > self._max_retries:
                    raise
                delay = self._base_delay * (self._backoff_factor ** (attempt - 1))
                logger.warning(
                    "transient X API error (%s), retry %d/%d in %.1fs",
                    type(exc).__name__,
                    attempt,
                    self._max_retries,
                    delay,
                )
                time.sleep(delay)
