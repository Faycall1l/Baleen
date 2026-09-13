"""Computed signals: pressure and inferred links, derived only from stored facts.

Nothing here predicts future returns — each signal aggregates what filings
and flow already say (buying pressure, insider clusters, options sentiment,
suspicious timing, uncovered sector bets, similar traders). All inputs are
graph properties or filing metadata; no LLM, deterministic, recomputed live.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore

logger = logging.getLogger(__name__)

WINDOW_DAYS = 90
CLUSTER_MIN_INSIDERS = 2
SUSPICIOUS_EXCESS_PCT = 15.0

MOMENTUM_QUERY = """
MATCH (e:Event)
WHERE e.type IN ['congress_trade', 'insider_trade']
  AND e.transaction_date IS NOT NULL
  AND e.transaction_date >= date($since)
OPTIONAL MATCH (e)-[:INVOLVES_TICKER]->(t:Ticker)
OPTIONAL MATCH (e)-[:INVOLVES_POLITICIAN]->(p:Politician)
OPTIONAL MATCH (e)-[:INVOLVES_INSIDER]->(i:Insider)
RETURN t.ticker AS ticker,
       sum(CASE WHEN e.transaction_type = 'buy' THEN 1 ELSE 0 END) AS buys,
       sum(CASE WHEN e.transaction_type = 'sell' THEN 1 ELSE 0 END) AS sells,
       count(DISTINCT p.id) + count(DISTINCT i.id) AS actors,
       collect(DISTINCT coalesce(p.name, i.name))[0..5] AS names
ORDER BY buys DESC, actors DESC
LIMIT 20
"""

CLUSTER_QUERY = """
MATCH (e:Event {type: 'insider_trade'})-[:INVOLVES_INSIDER]->(i:Insider),
      (e)-[:INVOLVES_TICKER]->(t:Ticker)
WHERE e.transaction_type = 'buy'
  AND e.transaction_date IS NOT NULL
  AND e.transaction_date >= date($since)
WITH t.ticker AS ticker, collect(DISTINCT i.name) AS insiders, count(*) AS buys
WHERE size(insiders) >= $min_insiders
RETURN ticker, insiders, buys
ORDER BY size(insiders) DESC, buys DESC
LIMIT 10
"""

OPTIONS_QUERY = """
MATCH (e:Event {type: 'options_flow'})-[:INVOLVES_TICKER]->(t:Ticker)
WHERE e.first_seen_at IS NOT NULL
  AND e.first_seen_at >= datetime($since_ts)
  AND e.volume IS NOT NULL
RETURN t.ticker AS ticker,
       sum(CASE WHEN e.contract_type = 'call' THEN e.volume ELSE 0 END) AS call_vol,
       sum(CASE WHEN e.contract_type = 'put' THEN e.volume ELSE 0 END) AS put_vol,
       count(*) AS prints
ORDER BY call_vol + put_vol DESC
LIMIT 15
"""

SIMILAR_QUERY = """
MATCH (p:Politician {id: $pid})<-[:INVOLVES_POLITICIAN]-(e:Event)-[:INVOLVES_TICKER]->(t:Ticker),
      (t)<-[:INVOLVES_TICKER]-(e2:Event)-[:INVOLVES_POLITICIAN]->(other:Politician)
WHERE other.id <> $pid
RETURN other.id AS id, other.name AS name, count(DISTINCT t.ticker) AS shared,
       collect(DISTINCT t.ticker)[0..8] AS tickers
ORDER BY shared DESC
LIMIT 5
"""

UNCOVERED_QUERY = """
MATCH (p:Politician)<-[:INVOLVES_POLITICIAN]-(e:Event)-[:INVOLVES_TICKER]->(t:Ticker)-[:IN_SECTOR]->(s:Sector)
WHERE NOT EXISTS {
    MATCH (p)-[:MEMBER_OF]->(:Committee)-[:OVERSEES]->(s)
}
RETURN p.name AS politician, s.name AS sector, count(DISTINCT t.ticker) AS tickers,
       collect(DISTINCT t.ticker)[0..8] AS symbols
ORDER BY tickers DESC
LIMIT 10
"""


class SignalsEngine:
    """Read-only signal computation over the graph (+ filing returns in PG)."""

    def __init__(self, neo4j: Neo4jStore, store: PostgresStore) -> None:
        self.neo4j = neo4j
        self.store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def bundle(self, window_days: int = WINDOW_DAYS) -> dict:
        """All signals in one payload for the UI panel."""
        since = (date.today() - timedelta(days=window_days)).isoformat()
        return {
            "window_days": window_days,
            "momentum": self.momentum(since),
            "clusters": self.clusters(since),
            "options_meter": self.options_meter(),
            "uncovered": self.uncovered(),
            "suspicious": self.suspicious_timing(),
        }

    def momentum(self, since: str) -> list[dict]:
        try:
            return self.neo4j.run_read(MOMENTUM_QUERY, since=since)
        except Exception:
            logger.exception("momentum query failed")
            return []

    def clusters(self, since: str) -> list[dict]:
        try:
            return self.neo4j.run_read(CLUSTER_QUERY, since=since, min_insiders=CLUSTER_MIN_INSIDERS)
        except Exception:
            logger.exception("clusters query failed")
            return []

    def options_meter(self) -> list[dict]:
        try:
            since_ts = (
                datetime.now(timezone.utc).date() - timedelta(days=30)
            ).isoformat() + "T00:00:00Z"
            return self.neo4j.run_read(OPTIONS_QUERY, since_ts=since_ts)
        except Exception:
            logger.exception("options meter query failed")
            return []

    def uncovered(self) -> list[dict]:
        try:
            return self.neo4j.run_read(UNCOVERED_QUERY)
        except Exception:
            logger.exception("uncovered query failed")
            return []

    def similar(self, politician_id: str) -> list[dict]:
        try:
            return self.neo4j.run_read(SIMILAR_QUERY, pid=politician_id)
        except Exception:
            logger.exception("similar query failed")
            return []

    def suspicious_timing(self, min_excess_pct: float = SUSPICIOUS_EXCESS_PCT) -> list[dict]:
        """Filings whose trade already beat the market hard — worth a look,
        not a conclusion. Returns data lives on the raw filing rows."""
        from sqlalchemy import text

        try:
            with self.store.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT raw_json->>'filer_name' AS who,
                               raw_json->>'ticker' AS ticker,
                               raw_json->>'transaction_date' AS date,
                               (raw_json->>'excess_since')::float AS excess_pct,
                               url AS source_url
                        FROM raw_tweets
                        WHERE (tweet_id LIKE 'ptr-%%' OR tweet_id LIKE 'edgar-%%')
                          AND raw_json->>'excess_since' ~ '^-?[0-9]+(\.[0-9]+)?$'
                          AND (raw_json->>'excess_since')::float >= :min_excess
                        ORDER BY (raw_json->>'excess_since')::float DESC
                        LIMIT 10
                        """
                    ),
                    {"min_excess": min_excess_pct},
                ).mappings()
                return [dict(row) for row in rows]
        except Exception:
            logger.exception("suspicious timing query failed")
            return []
