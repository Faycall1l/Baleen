"""PostgreSQL persistence for Baleen.

Handles the ``raw_tweets`` table (idempotent ingestion via a unique constraint
on ``tweet_id``) and, in later phases, the alert tables. Uses SQLAlchemy core
with raw ``text()`` statements so the schema stays explicit and auditable.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.ingestion.source import RawTweet

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS raw_tweets (
    id            BIGSERIAL PRIMARY KEY,
    tweet_id      TEXT NOT NULL UNIQUE,
    author_handle TEXT NOT NULL,
    author_id     TEXT NOT NULL,
    text          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    url           TEXT NOT NULL,
    raw_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_raw_tweets_status ON raw_tweets (status);
"""


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        self.engine: Engine = create_engine(dsn, pool_pre_ping=True)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_schema(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(SCHEMA_SQL))
        logger.info("postgres schema ready")

    # ------------------------------------------------------------------
    # raw_tweets
    # ------------------------------------------------------------------

    def insert_raw_tweet(self, tweet: RawTweet) -> bool:
        """Insert a tweet. Returns ``True`` if newly inserted, ``False`` if a
        duplicate was skipped (idempotency)."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO raw_tweets
                        (tweet_id, author_handle, author_id, text, created_at, url, raw_json)
                    VALUES (:tweet_id, :author_handle, :author_id, :text, :created_at, :url, :raw_json)
                    ON CONFLICT (tweet_id) DO NOTHING
                    """
                ),
                {
                    "tweet_id": tweet.tweet_id,
                    "author_handle": tweet.author_handle,
                    "author_id": tweet.author_id,
                    "text": tweet.text,
                    "created_at": tweet.created_at,
                    "url": tweet.url,
                    "raw_json": json.dumps(tweet.raw_json),
                },
            )
        return result.rowcount == 1

    def insert_raw_tweets(self, tweets: list[RawTweet]) -> tuple[int, int]:
        """Bulk insert; returns ``(inserted, skipped)``."""
        inserted = 0
        skipped = 0
        for tweet in tweets:
            if self.insert_raw_tweet(tweet):
                inserted += 1
            else:
                skipped += 1
        return inserted, skipped

    def get_new_tweets(self, limit: int = 100) -> list[RawTweet]:
        """Tweets not yet handed to the extraction stage."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT tweet_id, author_handle, author_id, text, created_at, url, raw_json
                    FROM raw_tweets
                    WHERE status = 'new'
                    ORDER BY created_at ASC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings()
            return [RawTweet(**dict(row)) for row in rows]

    def mark_extracted(self, tweet_ids: list[str]) -> None:
        if not tweet_ids:
            return
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE raw_tweets SET status = 'extracted' WHERE tweet_id = ANY(:ids)"),
                {"ids": tweet_ids},
            )
