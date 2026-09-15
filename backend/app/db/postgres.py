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


def _strip_nulls(value: str) -> str:
    """Postgres text cannot hold NUL bytes (``\\x00``); strip them so a
    poisoned row from a hostile source cannot abort an entire ingest batch."""
    if "\x00" in value:
        return value.replace("\x00", "")
    return value


def _scrub_nulls(value):
    """Recursively strip actual NUL bytes (``\\x00``) from JSON payloads.

    Applied *before* ``json.dumps``: Postgres can neither store a NUL byte nor
    decode one from a ``\\u0000`` escape in a JSON document, and a single
    poisoned row would otherwise raise ``DataError`` and roll back the whole
    batch. Doing this at the value layer (instead of on the encoded string)
    leaves text that *literally* reads ``\\u0000`` untouched, since that is
    valid JSON text and not a control character.
    """
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, list):
        return [_scrub_nulls(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_nulls(v) for k, v in value.items()}
    return value


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
CREATE INDEX IF NOT EXISTS idx_raw_tweets_status_created
    ON raw_tweets (status, created_at);

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
-- Idempotency: one alert per (rule, event). Re-evaluating the same event must
-- never create a duplicate (CREATE UNIQUE INDEX IF NOT EXISTS is PostgreSQL's
-- idempotent form of "add unique constraint").
CREATE UNIQUE INDEX IF NOT EXISTS uq_alerts_rule_event ON alerts (rule_id, event_id);

CREATE TABLE IF NOT EXISTS reports (
    id         SERIAL PRIMARY KEY,
    kind       TEXT NOT NULL,               -- 'brief' (one event) | 'digest' (one period)
    event_id   TEXT,                        -- briefs only
    period     TEXT,                        -- digests only (YYYY-MM-DD, UTC)
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    graph_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Idempotency: one brief per event, one digest per period. NULLs never
-- collide in Postgres unique indexes, so the two lanes share the table.
CREATE UNIQUE INDEX IF NOT EXISTS uq_reports_brief ON reports (kind, event_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_reports_digest ON reports (kind, period);
CREATE INDEX IF NOT EXISTS idx_reports_created_at ON reports (created_at DESC);
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
    ) -> int | None:
        """Insert an alert. Returns its id, or ``None`` if the same (rule, event)
        already has an alert (idempotency)."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO alerts (rule_id, event_id, tweet_id, tweet_url, title, summary, graph_refs)
                    VALUES (:rule_id, :event_id, :tweet_id, :tweet_url, :title, :summary, :graph_refs)
                    ON CONFLICT (rule_id, event_id) DO NOTHING
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
            return result.scalar_one_or_none()

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

    # ------------------------------------------------------------------
    # reports
    # ------------------------------------------------------------------

    def insert_report(
        self,
        kind: str,
        title: str,
        body: str,
        event_id: str | None = None,
        period: str | None = None,
        graph_refs: list[str] | None = None,
    ) -> int | None:
        """Insert a report. Returns its id, or ``None`` when the same
        (kind, event) brief or (kind, period) digest already exists."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO reports (kind, event_id, period, title, body, graph_refs)
                    VALUES (:kind, :event_id, :period, :title, :body, :graph_refs)
                    ON CONFLICT (kind, event_id) DO NOTHING
                    RETURNING id
                    """
                    if event_id is not None
                    else """
                    INSERT INTO reports (kind, period, title, body, graph_refs)
                    VALUES (:kind, :period, :title, :body, :graph_refs)
                    ON CONFLICT (kind, period) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "kind": kind,
                    "event_id": event_id,
                    "period": period,
                    "title": title,
                    "body": body,
                    "graph_refs": json.dumps(graph_refs or []),
                },
            )
            return result.scalar_one_or_none()

    def get_reports(self, limit: int = 20, kind: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, kind, event_id, period, title, body, graph_refs, created_at
                    FROM reports
                    WHERE (CAST(:kind AS TEXT) IS NULL OR kind = :kind)
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit, "kind": kind},
            ).mappings()
            return [dict(row) for row in rows]

    def get_digest(self, period: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, kind, event_id, period, title, body, graph_refs, created_at
                    FROM reports
                    WHERE kind = 'digest' AND period = :period
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"period": period},
            ).mappings().first()
            return dict(row) if row is not None else None

    def get_reports_since(self, last_id: int, limit: int = 50) -> list[dict]:
        """Reports with ``id`` above the cursor, oldest first (stream catch-up)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, kind, event_id, period, title, body, graph_refs, created_at
                    FROM reports
                    WHERE id > :last_id
                    ORDER BY id ASC
                    LIMIT :limit
                    """
                ),
                {"last_id": last_id, "limit": limit},
            ).mappings()
            return [dict(row) for row in rows]

    def get_alerts_since(self, last_id: int, limit: int = 50) -> list[dict]:
        """Alerts with ``id`` above the cursor, oldest first (stream catch-up)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT a.id, a.event_id, a.tweet_id, a.tweet_url, a.title, a.summary,
                           a.graph_refs, a.created_at, r.name AS rule_name
                    FROM alerts a
                    JOIN alert_rules r ON r.id = a.rule_id
                    WHERE a.id > :last_id
                    ORDER BY a.id ASC
                    LIMIT :limit
                    """
                ),
                {"last_id": last_id, "limit": limit},
            ).mappings()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # leaderboard
    # ------------------------------------------------------------------

    def get_leaderboard(self, limit: int = 20) -> list[dict]:
        """Best-performing congressional trades by return since transaction.

        Reads the mirror-computed ``ret_since`` (percent) stored in each
        disclosure record — no LLM, no graph walk, just the filings.
        """
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT raw_json->>'filer_name' AS politician,
                           raw_json->>'ticker' AS ticker,
                           raw_json->>'transaction_type' AS side,
                           raw_json->>'transaction_date' AS date,
                           raw_json->>'amount_range_label' AS amount,
                           (raw_json->>'ret_since')::float AS ret_pct,
                           (raw_json->>'excess_since')::float AS excess_pct,
                           url AS source_url
FROM raw_tweets
                   WHERE tweet_id LIKE 'ptr-%'
                     AND raw_json->>'ret_since' ~ '^-?[0-9]+(\.[0-9]+)?$'
                     AND raw_json->>'excess_since' ~ '^-?[0-9]+(\.[0-9]+)?$'
                   ORDER BY (raw_json->>'ret_since')::float DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings()
            return [dict(row) for row in rows]
