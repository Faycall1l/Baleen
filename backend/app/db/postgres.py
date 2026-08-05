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

CREATE TABLE IF NOT EXISTS alert_rules (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    config      JSONB NOT NULL DEFAULT '{}'::jsonb,
    active      BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS alerts (
    id         SERIAL PRIMARY KEY,
    rule_id    INTEGER NOT NULL REFERENCES alert_rules(id),
    event_id   TEXT NOT NULL,
    tweet_id   TEXT NOT NULL,
    tweet_url  TEXT NOT NULL,
    title      TEXT NOT NULL,
    summary    TEXT NOT NULL,
    graph_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts (created_at DESC);
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

    # ------------------------------------------------------------------
    # alert_rules
    # ------------------------------------------------------------------

    def ensure_rule(self, name: str, description: str, config: dict | None = None) -> None:
        """Idempotently register a rule (no-op if it already exists)."""
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO alert_rules (name, description, config)
                    VALUES (:name, :description, :config)
                    ON CONFLICT (name) DO NOTHING
                    """
                ),
                {"name": name, "description": description, "config": json.dumps(config or {})},
            )

    def get_active_rules(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, name, description, config FROM alert_rules WHERE active = true"
                )
            ).mappings()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # alerts
    # ------------------------------------------------------------------

    def insert_alert(
        self,
        rule_id: int,
        event_id: str,
        tweet_id: str,
        tweet_url: str,
        title: str,
        summary: str,
        graph_refs: list[str] | None = None,
    ) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO alerts (rule_id, event_id, tweet_id, tweet_url, title, summary, graph_refs)
                    VALUES (:rule_id, :event_id, :tweet_id, :tweet_url, :title, :summary, :graph_refs)
                    RETURNING id
                    """
                ),
                {
                    "rule_id": rule_id,
                    "event_id": event_id,
                    "tweet_id": tweet_id,
                    "tweet_url": tweet_url,
                    "title": title,
                    "summary": summary,
                    "graph_refs": json.dumps(graph_refs or []),
                },
            )
            return result.scalar_one()

    def get_alerts(self, limit: int = 20) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT a.id, a.event_id, a.tweet_id, a.tweet_url, a.title, a.summary,
                           a.graph_refs, a.created_at, r.name AS rule_name
                    FROM alerts a
                    JOIN alert_rules r ON r.id = a.rule_id
                    ORDER BY a.created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings()
            return [dict(row) for row in rows]
