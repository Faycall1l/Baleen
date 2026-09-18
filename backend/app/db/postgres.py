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
    status        TEXT NOT NULL DEFAULT 'new',
    claimed_at    TIMESTAMPTZ
);
-- Idempotent upgrade for tables created before the claim column existed.
ALTER TABLE raw_tweets ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_raw_tweets_status ON raw_tweets (status);
CREATE INDEX IF NOT EXISTS idx_raw_tweets_status_created
    ON raw_tweets (status, created_at);
CREATE INDEX IF NOT EXISTS idx_raw_tweets_status_claimed
    ON raw_tweets (status, claimed_at);

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

-- Entity alias registry (entity resolution, naming layer).
-- One canonical name per (entity_type, canonical_id), many aliases per
-- canonical. ``alias`` is the normalized spelling seen in a tweet / filing;
-- ``canonical_name`` is the spelling written to the graph node. Resolution is
-- deterministic: given a mention and a roster id we always converge on the
-- registered canonical spelling, so "Rep. Nancy Pelosi" and "Nancy Pelosi"
-- stop fragmenting the Politician node's ``name`` property.
CREATE TABLE IF NOT EXISTS entity_aliases (
    id             BIGSERIAL PRIMARY KEY,
    entity_type    TEXT NOT NULL,               -- 'Politician' | 'Insider' | 'Ticker'
    alias          TEXT NOT NULL,               -- normalized spelling seen
    canonical_id   TEXT NOT NULL,               -- stable natural key (politician_id, insider_id, ticker)
    canonical_name TEXT NOT NULL,               -- canonical spelling written to the graph
    confidence     FLOAT NOT NULL DEFAULT 1.0,
    source         TEXT NOT NULL,               -- 'seed' | 'llm' | 'review'
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_alias
    ON entity_aliases (entity_type, alias);
CREATE INDEX IF NOT EXISTS idx_entity_canonical
    ON entity_aliases (entity_type, canonical_id);

-- Review queue for the 0.85-0.95 gray band: near-duplicate candidates that a
-- human should confirm or reject before they resolve into a shared canonical.
-- Nothing here mutates the graph automatically; approve() flips status and (in
-- the later identity/dedup layer) is what would drive a Neo4j merge.
CREATE TABLE IF NOT EXISTS entity_resolution_queue (
    id             SERIAL PRIMARY KEY,
    entity_type    TEXT NOT NULL,
    candidate_id   TEXT NOT NULL,               -- id that would merge into canonical
    candidate_name TEXT NOT NULL,
    canonical_id   TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    confidence     FLOAT NOT NULL,
    reason         TEXT NOT NULL,               -- 'fuzzy_match'
    status         TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'confirmed' | 'rejected'
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at    TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_resolution_pair
    ON entity_resolution_queue (entity_type, candidate_id, canonical_id);

-- Identity remap registry (entity resolution, identity layer). When a human
-- confirms a review, the candidate id is declared an alias of the canonical
-- id. Every future event claiming the candidate id MERGEs onto the canonical
-- node instead — ids converge at the KEY level, exactly like ``entity_aliases``
-- converged the NAME level. Chains are possible (consolidation); resolvers
-- follow them to the root.
CREATE TABLE IF NOT EXISTS entity_identities (
    id            BIGSERIAL PRIMARY KEY,
    entity_type   TEXT NOT NULL,
    candidate_id  TEXT NOT NULL,
    canonical_id  TEXT NOT NULL,
    confidence    FLOAT NOT NULL DEFAULT 1.0,
    source        TEXT NOT NULL,              -- 'review'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolution_id INTEGER REFERENCES entity_resolution_queue (id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_candidate
    ON entity_identities (entity_type, candidate_id);
CREATE INDEX IF NOT EXISTS idx_identity_canonical
    ON entity_identities (entity_type, canonical_id);
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

    def claim_new_tweets(
        self, limit: int = 100, lease_seconds: int = 600
    ) -> list[RawTweet]:
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
    # entity resolution (alias registry + review queue)
    # ------------------------------------------------------------------

    def ensure_alias(
        self,
        entity_type: str,
        alias: str,
        canonical_id: str,
        canonical_name: str,
        confidence: float = 1.0,
        source: str = "llm",
    ) -> None:
        """Register (or refresh) one alias for a canonical entity.

        Idempotent per (entity_type, alias). When an alias is re-seen, the
        canonical spelling is *converged*, never duplicated: the stored
        canonical_name is refreshed to the argument, and the confidence is
        decayed toward the newer observation only if it rises. ``source``
        ranks 'seed' > 'review' > 'llm' for tie-break precedence.
        """
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO entity_aliases (entity_type, alias, canonical_id, canonical_name, confidence, source)
                    VALUES (:entity_type, :alias, :canonical_id, :canonical_name, :confidence, :source)
                    ON CONFLICT (entity_type, alias) DO UPDATE SET
                        canonical_id   = EXCLUDED.canonical_id,
                        canonical_name = EXCLUDED.canonical_name,
                        confidence     = GREATEST(entity_aliases.confidence, EXCLUDED.confidence),
                        source         = CASE
                                             WHEN entity_aliases.source = 'seed' THEN 'seed'
                                             WHEN EXCLUDED.source = 'seed'     THEN 'seed'
                                             WHEN entity_aliases.source = 'review' THEN 'review'
                                             ELSE EXCLUDED.source
                                         END
                    """
                ),
                {
                    "entity_type": entity_type,
                    "alias": alias,
                    "canonical_id": canonical_id,
                    "canonical_name": canonical_name,
                    "confidence": confidence,
                    "source": source,
                },
            )

    def get_canonical(self, entity_type: str, alias: str) -> dict | None:
        """The canonical (id, name) a visited alias already resolves to."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT canonical_id, canonical_name
                    FROM entity_aliases
                    WHERE entity_type = :entity_type AND alias = :alias
                    """
                ),
                {"entity_type": entity_type, "alias": alias},
            ).mappings().first()
            return dict(row) if row is not None else None

    def get_canonical_name(self, entity_type: str, canonical_id: str) -> str | None:
        """The canonical spelling registered for a stable id, if any."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT canonical_name
                    FROM entity_aliases
                    WHERE entity_type = :entity_type AND canonical_id = :canonical_id
                    ORDER BY source = 'seed' DESC, confidence DESC, id ASC
                    LIMIT 1
                    """
                ),
                {"entity_type": entity_type, "canonical_id": canonical_id},
            ).scalar_one_or_none()
            return row

    def list_aliases(self, entity_type: str, canonical_id: str) -> list[dict]:
        """Every registered spelling for a canonical entity (for the UI / audit)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT alias, canonical_name, confidence, source, created_at
                    FROM entity_aliases
                    WHERE entity_type = :entity_type AND canonical_id = :canonical_id
                    ORDER BY confidence DESC, id ASC
                    """
                ),
                {"entity_type": entity_type, "canonical_id": canonical_id},
            ).mappings()
            return [dict(row) for row in rows]

    def list_aliases_by_type(self, entity_type: str) -> list[dict]:
        """All registered aliases for an entity type (drives the fuzzy pass)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT alias, canonical_id, canonical_name
                    FROM entity_aliases
                    WHERE entity_type = :entity_type
                    ORDER BY canonical_id, id
                    """
                ),
                {"entity_type": entity_type},
            ).mappings()
            return [dict(row) for row in rows]

    def insert_resolution_suggestion(
        self,
        entity_type: str,
        candidate_id: str,
        candidate_name: str,
        canonical_id: str,
        canonical_name: str,
        confidence: float,
        reason: str,
    ) -> int | None:
        """Queue a near-duplicate pair for human review. Returns its id, or
        ``None`` if the same (entity_type, candidate_id, canonical_id) pair is
        already queued (any status)."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO entity_resolution_queue
                        (entity_type, candidate_id, candidate_name, canonical_id,
                         canonical_name, confidence, reason)
                    VALUES (:entity_type, :candidate_id, :candidate_name, :canonical_id,
                            :canonical_name, :confidence, :reason)
                    ON CONFLICT (entity_type, candidate_id, canonical_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "entity_type": entity_type,
                    "candidate_id": candidate_id,
                    "candidate_name": candidate_name,
                    "canonical_id": canonical_id,
                    "canonical_name": canonical_name,
                    "confidence": confidence,
                    "reason": reason,
                },
            )
            return result.scalar_one_or_none()

    def pending_resolutions(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, entity_type, candidate_id, candidate_name,
                           canonical_id, canonical_name, confidence, reason, created_at
                    FROM entity_resolution_queue
                    WHERE status = 'pending'
                    ORDER BY confidence DESC, created_at ASC
                    """
                )
            ).mappings()
            return [dict(row) for row in rows]

    def mark_resolution(self, queue_id: int, status: str) -> bool:
        """Flip a queue entry to 'confirmed' or 'rejected'. Returns False when
        the id does not exist (idempotent caller behaviour)."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE entity_resolution_queue
                    SET status = :status, resolved_at = now()
                    WHERE id = :id AND status = 'pending'
                    """
                ),
                {"status": status, "id": queue_id},
            )
            return result.rowcount == 1

    def confirm_resolution(self, queue_id: int) -> bool:
        """Confirm a queue entry AND record its identity remap, atomically.

        The human's decision is two things at once: (a) this pair is resolved,
        and (b) from now on ``candidate_id`` is an alias of ``canonical_id`` at
        the id level. Both need to land or neither does, so they share one
        transaction. Returns False when the entry was not pending (already
        resolved or unknown — idempotent).
        """
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE entity_resolution_queue
                    SET status = 'confirmed', resolved_at = now()
                    WHERE id = :id AND status = 'pending'
                    RETURNING entity_type, candidate_id, canonical_id, confidence
                    """
                ),
                {"id": queue_id},
            ).mappings().first()
            if row is None:
                return False
            conn.execute(
                text(
                    """
                    INSERT INTO entity_identities
                        (entity_type, candidate_id, canonical_id, confidence, source, resolution_id)
                    VALUES (:entity_type, :candidate_id, :canonical_id, :confidence, 'review', :resolution_id)
                    ON CONFLICT (entity_type, candidate_id) DO UPDATE SET
                        canonical_id = EXCLUDED.canonical_id,
                        confidence   = GREATEST(entity_identities.confidence, EXCLUDED.confidence),
                        resolution_id = EXCLUDED.resolution_id
                    """
                ),
                {
                    "entity_type": row["entity_type"],
                    "candidate_id": row["candidate_id"],
                    "canonical_id": row["canonical_id"],
                    "confidence": row["confidence"],
                    "resolution_id": queue_id,
                },
            )
            return True

    def get_resolution(self, queue_id: int) -> dict | None:
        """A queue entry by id, regardless of status (for the apply/undo flows)."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, entity_type, candidate_id, candidate_name,
                           canonical_id, canonical_name, confidence, reason, status, created_at
                    FROM entity_resolution_queue
                    WHERE id = :id
                    """
                ),
                {"id": queue_id},
            ).mappings().first()
            return dict(row) if row is not None else None

    # ------------------------------------------------------------------
    # entity resolution (identity remap registry)
    # ------------------------------------------------------------------

    def get_identity(self, entity_type: str, candidate_id: str) -> dict | None:
        """The canonical an id has been remapped to, if any."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT candidate_id, canonical_id, confidence
                    FROM entity_identities
                    WHERE entity_type = :entity_type AND candidate_id = :candidate_id
                    """
                ),
                {"entity_type": entity_type, "candidate_id": candidate_id},
            ).mappings().first()
            return dict(row) if row is not None else None

    def record_identity(
        self,
        entity_type: str,
        candidate_id: str,
        canonical_id: str,
        confidence: float = 1.0,
        source: str = "review",
        resolution_id: int | None = None,
    ) -> None:
        """Declare ``candidate_id`` an alias of ``canonical_id`` at the id level.

        Idempotent per (entity_type, candidate_id); a later declaration simply
        retargets the candidate. No graph rewrite happens here — that is the
        caller's separate, explicit step.
        """
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO entity_identities
                        (entity_type, candidate_id, canonical_id, confidence, source, resolution_id)
                    VALUES (:entity_type, :candidate_id, :canonical_id, :confidence, :source, :resolution_id)
                    ON CONFLICT (entity_type, candidate_id) DO UPDATE SET
                        canonical_id   = EXCLUDED.canonical_id,
                        confidence     = GREATEST(entity_identities.confidence, EXCLUDED.confidence),
                        resolution_id  = EXCLUDED.resolution_id,
                        source         = EXCLUDED.source
                    """
                ),
                {
                    "entity_type": entity_type,
                    "candidate_id": candidate_id,
                    "canonical_id": canonical_id,
                    "confidence": confidence,
                    "source": source,
                    "resolution_id": resolution_id,
                },
            )

    def list_identity_remaps(self, entity_type: str | None = None) -> list[dict]:
        """Every remap row (candidate -> canonical), optionally per type."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, entity_type, candidate_id, canonical_id, confidence,
                           source, created_at, resolution_id
                    FROM entity_identities
                    WHERE (:entity_type IS NULL OR entity_type = :entity_type)
                    ORDER BY entity_type, candidate_id
                    """
                ),
                {"entity_type": entity_type},
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
                     AND jsonb_typeof(raw_json->'ret_since') = 'number'
                     AND jsonb_typeof(raw_json->'excess_since') = 'number'
                   ORDER BY (raw_json->>'ret_since')::float DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings()
            return [dict(row) for row in rows]
