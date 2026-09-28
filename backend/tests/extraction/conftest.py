"""Shared helpers for the extraction suite.

Split out of the former ``tests/test_extraction.py`` (M2), which now has one
module per production module: extractor, tool-call parsing, and transport.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.ingestion.source import RawTweet


def _tweet(tweet_id: str = "1", text: str = "hello") -> RawTweet:
    return RawTweet(
        tweet_id=tweet_id,
        author_handle="unusual_whales",
        author_id="9",
        text=text,
        created_at=datetime.now(timezone.utc),
        url=f"https://x.com/unusual_whales/status/{tweet_id}",
    )
