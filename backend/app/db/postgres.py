"""PostgreSQL persistence for Baleen: schema plus the composed store.

``PostgresStore`` owns the SQLAlchemy engine and the DDL. Table accessors live
in per-domain mixins under ``app.db.store`` (tweets, alerts, reports, identity,
leaderboard) so each domain reads on its own. SQLAlchemy core with raw
``text()`` statements keeps the schema explicit and auditable.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db.store.alerts import AlertStoreMixin
from app.db.store.identity import IdentityStoreMixin
from app.db.store.leaderboard import LeaderboardStoreMixin
from app.db.store.reports import ReportStoreMixin
from app.db.store.tweets import TweetStoreMixin

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


class PostgresStore(
    TweetStoreMixin,
    AlertStoreMixin,
    ReportStoreMixin,
    IdentityStoreMixin,
    LeaderboardStoreMixin,
):
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

    # ------------------------------------------------------------------
    # alert_rules
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # alerts
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # reports
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # entity resolution (alias registry + review queue)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # entity resolution (identity remap registry)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # leaderboard
    # ------------------------------------------------------------------
