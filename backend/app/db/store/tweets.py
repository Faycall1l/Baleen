"""``raw_tweets`` table: idempotent ingest and the new/claimed/extracted
status machine, split out of :mod:`app.db.postgres`.

Mixed into ``PostgresStore``, which owns the shared SQLAlchemy engine, so every
method here stays a thin, auditable ``text()`` call.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from app.db.store.support import _scrub_nulls, _strip_nulls
from app.ingestion.source import RawTweet


class TweetStoreMixin:
    def insert_raw_tweet(self, tweet: RawTweet) -> bool:
        """Insert a tweet. Returns ``True`` if newly inserted, ``False`` if a
        duplicate was skipped (idempotency)."""
        inserted, _ = self.insert_raw_tweets([tweet])
        return inserted == 1

    def insert_raw_tweets(self, tweets: list[RawTweet]) -> tuple[int, int]:
        """Bulk insert; returns ``(inserted, skipped)``.

        One batched ``INSERT ... ON CONFLICT DO NOTHING`` instead of N
        per-row statements. Counts come from the statement's own rowcount
        (Postgres reports only actually-inserted rows), so they stay exact
        under concurrent writers — no separate COUNT round trip.

        Text and JSON payloads are stripped of NUL bytes: Postgres text
        cannot store ``\\x00``, and a single poisoned row would otherwise
        raise ``DataError`` and roll back the whole batch.
        """
        if not tweets:
            return 0, 0

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
                [
                    {
                        "tweet_id": t.tweet_id,
                        "author_handle": t.author_handle,
                        "author_id": t.author_id,
                        "text": _strip_nulls(t.text),
                        "created_at": t.created_at,
                        "url": _strip_nulls(t.url),
                        "raw_json": json.dumps(_scrub_nulls(t.raw_json)),
                    }
                    for t in tweets
                ],
            )
            inserted = result.rowcount
        return inserted, len(tweets) - inserted

    def claim_new_tweets(self, limit: int = 100, lease_seconds: int = 600) -> list[RawTweet]:
        """Atomically claim a batch of tweets for processing.

        Unlike a plain read, this flips the claimed rows to
        ``status='processing'`` before returning them, so two concurrent
        ``process_new`` runs can never grab the same tweet: the ``FOR UPDATE
        SKIP LOCKED`` scan makes each tweet claimable by exactly one worker at
        a time (previously concurrent runs each read the same first ``limit``
        rows, starving the tail until the next tick — no duplicates, but
        delayed processing).

        Rows left ``processing`` past ``lease_seconds`` (worker crashed
        mid-batch) are considered abandoned and re-admitted to the claim pool.
        """
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    WITH candidates AS (
                        SELECT tweet_id
                        FROM raw_tweets
                        WHERE status = 'new'
                           OR (status = 'processing'
                               AND claimed_at < now() - make_interval(secs => :lease))
                        ORDER BY created_at ASC
                        LIMIT :limit
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE raw_tweets
                    SET status = 'processing',
                        claimed_at = now()
                    FROM candidates
                    WHERE raw_tweets.tweet_id = candidates.tweet_id
                    RETURNING raw_tweets.tweet_id, raw_tweets.author_handle,
                              raw_tweets.author_id, raw_tweets.text,
                              raw_tweets.created_at, raw_tweets.url,
                              raw_tweets.raw_json
                    """
                ),
                {"limit": limit, "lease": lease_seconds},
            ).mappings()
            return [RawTweet(**dict(row)) for row in rows]

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

    def reset_status(self, tweet_ids: list[str], status: str = "new") -> int:
        """Flip the processing status of specific tweets (e.g. re-queue
        unparsed ones for re-extraction). Returns the number updated."""
        if not tweet_ids:
            return 0
        with self.engine.begin() as conn:
            result = conn.execute(
                text("UPDATE raw_tweets SET status = :status WHERE tweet_id = ANY(:ids)"),
                {"status": status, "ids": tweet_ids},
            )
            return result.rowcount
