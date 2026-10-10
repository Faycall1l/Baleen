"""Computed signals: pressure and inferred links, derived only from stored facts.

Nothing here predicts future returns — each signal aggregates what filings
and flow already say (buying pressure, insider clusters, options sentiment,
suspicious timing, uncovered sector bets, similar traders). All inputs are
graph properties or filing metadata; no LLM, deterministic, recomputed live.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore

logger = logging.getLogger(__name__)

WINDOW_DAYS = 90
CLUSTER_MIN_INSIDERS = 2
SUSPICIOUS_EXCESS_PCT = 15.0

MOMENTUM_QUERY = """
MATCH (e:Event)-[:INVOLVES_TICKER]->(t:Ticker)
WHERE e.type IN ['congress_trade', 'insider_trade']
  AND e.transaction_date IS NOT NULL
  AND e.transaction_date >= date($since)
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

ACTIVITY_MOVERS_QUERY = """
MATCH (e:Event)-[:INVOLVES_TICKER]->(t:Ticker)
WHERE e.first_seen_at IS NOT NULL
  AND e.first_seen_at >= datetime($prior_since)
WITH t.ticker AS ticker,
     sum(CASE WHEN e.first_seen_at >= datetime($recent_since) THEN 1 ELSE 0 END) AS recent,
     count(*) AS total
WITH ticker, recent, (total - recent) AS prior, (2 * recent - total) AS delta
WHERE recent > 0
RETURN ticker, recent, prior, delta
ORDER BY delta DESC, recent DESC
LIMIT 10
"""

ACTIVITY_NEWCOMERS_QUERY = """
MATCH (e:Event)-[:INVOLVES_TICKER]->(t:Ticker)
WHERE e.first_seen_at IS NOT NULL
WITH t.ticker AS ticker, min(e.first_seen_at) AS first_seen, count(*) AS events
WHERE first_seen >= datetime($recent_since)
RETURN ticker, events
ORDER BY events DESC
LIMIT 8
"""

ACTIVITY_FRESH_QUERY = """
MATCH (e:Event)
WHERE e.first_seen_at IS NOT NULL
  AND e.first_seen_at >= datetime($recent_since)
OPTIONAL MATCH (e)-[:INVOLVES_TICKER]->(t:Ticker)
OPTIONAL MATCH (e)-[:INVOLVES_POLITICIAN]->(p:Politician)
OPTIONAL MATCH (e)-[:INVOLVES_INSIDER]->(i:Insider)
RETURN e.id AS event_id, e.type AS kind, t.ticker AS ticker,
       coalesce(p.name, i.name) AS who, e.first_seen_at AS seen_at
ORDER BY seen_at DESC
LIMIT 8
"""

FLOW_QUERY = """
MATCH (e:Event {type: 'options_flow'})-[:INVOLVES_TICKER]->(t:Ticker)
WHERE e.first_seen_at IS NOT NULL
  AND e.first_seen_at >= datetime($since_ts)
  AND e.volume IS NOT NULL
  AND ($side = 'all' OR e.contract_type = $side)
  AND (size($tickers) = 0 OR t.ticker IN $tickers)
WITH e, t,
     CASE WHEN coalesce(e.open_interest, 0) > 0
          THEN toFloat(e.volume) / toFloat(e.open_interest)
          ELSE toFloat(e.volume)
     END AS ratio
WHERE ratio >= $min_ratio
RETURN e.id AS event_id,
       t.ticker AS ticker,
       e.contract_type AS side,
       e.strike AS strike,
       e.expiration AS expiration,
       e.premium AS premium,
       e.volume AS volume,
       e.open_interest AS open_interest,
       ratio,
       e.sentiment AS sentiment,
       e.event_time AS event_time,
       e.first_seen_at AS first_seen_at,
       e.tweet_url AS source_url
ORDER BY ratio DESC, e.volume DESC
LIMIT $limit
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

PERSON_QUERY = """
MATCH (p:Politician {id: $pid})
OPTIONAL MATCH (p)-[:MEMBER_OF]->(c:Committee)
RETURN p.id AS id, p.name AS name, p.chamber AS chamber, p.party AS party,
       collect(DISTINCT {id: c.id, name: c.name}) AS committees
"""

PERSON_TRADES_QUERY = """
MATCH (e:Event)-[:INVOLVES_POLITICIAN]->(p:Politician {id: $pid})
WHERE e.type = 'congress_trade'
OPTIONAL MATCH (e)-[:INVOLVES_TICKER]->(t:Ticker)
WITH e, t.ticker AS ticker
ORDER BY e.transaction_date DESC
LIMIT 20
RETURN e.id AS event_id, ticker,
       e.transaction_type AS side, e.transaction_date AS date,
       e.amount_range AS amount, e.tweet_url AS source_url
"""

TICKER_QUERY = """
MATCH (t:Ticker {ticker: $sym})
OPTIONAL MATCH (t)-[:IN_SECTOR]->(s:Sector)
RETURN t.ticker AS ticker, s.name AS sector
"""

TICKER_TRADES_QUERY = """
MATCH (e:Event)-[:INVOLVES_TICKER]->(t:Ticker {ticker: $sym})
WHERE e.type IN ['congress_trade', 'insider_trade']
OPTIONAL MATCH (e)-[:INVOLVES_POLITICIAN]->(p:Politician)
OPTIONAL MATCH (e)-[:INVOLVES_INSIDER]->(i:Insider)
WITH e, coalesce(p.name, i.name) AS who
ORDER BY e.transaction_date DESC
LIMIT 20
RETURN e.id AS event_id, e.type AS kind, who,
       e.transaction_type AS side, e.transaction_date AS date,
       e.amount_range AS amount, e.tweet_url AS source_url
"""

TICKER_FLOW_QUERY = """
MATCH (e:Event {type: 'options_flow'})-[:INVOLVES_TICKER]->(t:Ticker {ticker: $sym})
WHERE e.first_seen_at IS NOT NULL
WITH e,
     CASE WHEN coalesce(e.open_interest, 0) > 0
          THEN toFloat(e.volume) / toFloat(e.open_interest)
          ELSE toFloat(e.volume)
     END AS ratio
ORDER BY e.first_seen_at DESC
LIMIT 10
RETURN e.id AS event_id, e.contract_type AS side, e.strike AS strike,
       e.expiration AS expiration, e.volume AS volume,
       e.open_interest AS open_interest, ratio, e.sentiment AS sentiment
"""

COMMITTEE_QUERY = """
MATCH (c:Committee {id: $cid})
OPTIONAL MATCH (c)-[:OVERSEES]->(s:Sector)
RETURN c.id AS id, c.name AS name,
       collect(DISTINCT {name: s.name}) AS sectors
"""

COMMITTEE_MEMBERS_QUERY = """
MATCH (p:Politician)-[:MEMBER_OF]->(c:Committee {id: $cid})
RETURN p.id AS id, p.name AS name, p.chamber AS chamber, p.party AS party
ORDER BY p.name
LIMIT 30
"""

OVERLAP_QUERY = """
MATCH (p:Politician {id: $pid})-[:MEMBER_OF]->(c:Committee)-[:OVERSEES]->(s:Sector)
OPTIONAL MATCH (e:Event)-[:INVOLVES_POLITICIAN]->(p),
      (e)-[:INVOLVES_TICKER]->(t:Ticker)-[:IN_SECTOR]->(s)
WHERE e IS NULL OR e.type = 'congress_trade'
RETURN s.name AS sector,
       collect(DISTINCT c.name) AS committees,
       collect(DISTINCT t.ticker) AS tickers,
       count(DISTINCT e) AS trades
ORDER BY trades DESC
LIMIT 20
"""

COMMITTEE_TRADES_QUERY = """
MATCH (e:Event)-[:INVOLVES_POLITICIAN]->(p:Politician)-[:MEMBER_OF]->(c:Committee {id: $cid})
WHERE e.type = 'congress_trade'
OPTIONAL MATCH (e)-[:INVOLVES_TICKER]->(t:Ticker)
WITH e, p, t.ticker AS ticker
ORDER BY e.transaction_date DESC
LIMIT 20
RETURN e.id AS event_id, p.name AS who, p.id AS politician_id, ticker,
       e.transaction_type AS side, e.transaction_date AS date,
       e.amount_range AS amount, e.tweet_url AS source_url
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
        """All signals in one payload for the UI panel.

        The five sections query independently, so they fan out across
        threads instead of stacking five sequential round-trips into one
        gate slot — sequential was ~8s wall time and piled polls into
        nginx 504s under load. Each section already degrades to [].
        """
        since = (date.today() - timedelta(days=window_days)).isoformat()
        with ThreadPoolExecutor(max_workers=5) as pool:
            momentum = pool.submit(self.momentum, since)
            clusters = pool.submit(self.clusters, since)
            meter = pool.submit(self.options_meter)
            uncovered = pool.submit(self.uncovered)
            suspicious = pool.submit(self.suspicious_timing)
            return {
                "window_days": window_days,
                "momentum": momentum.result(),
                "clusters": clusters.result(),
                "options_meter": meter.result(),
                "uncovered": uncovered.result(),
                "suspicious": suspicious.result(),
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

    def activity(self, recent_days: int = 7) -> dict:
        """What is moving right now: tickers accelerating vs the prior
        window, first-seen tickers, and the freshest events. Recency uses
        ``first_seen_at`` (set on every node at write time) so options flow
        and dated filings share one clock. Degrades to empty sections."""
        now = datetime.now(timezone.utc)
        recent_since = (now - timedelta(days=recent_days)).isoformat()
        prior_since = (now - timedelta(days=2 * recent_days)).isoformat()
        empty = {"window_days": recent_days, "movers": [], "newcomers": [], "fresh": []}
        try:
            movers = self.neo4j.run_read(
                ACTIVITY_MOVERS_QUERY, recent_since=recent_since, prior_since=prior_since
            )
            newcomers = self.neo4j.run_read(ACTIVITY_NEWCOMERS_QUERY, recent_since=recent_since)
            fresh = self.neo4j.run_read(ACTIVITY_FRESH_QUERY, recent_since=recent_since)
        except Exception:
            logger.exception("activity query failed")
            return empty
        return {"window_days": recent_days, "movers": movers, "newcomers": newcomers, "fresh": fresh}

    def flow(
        self,
        tickers: list[str] | None = None,
        side: str = "all",
        min_ratio: float = 2.0,
        window_days: int = 30,
        limit: int = 50,
    ) -> list[dict]:
        """Per-print options tape, ranked by volume/open-interest ratio.

        Unlike ``options_meter`` (per-ticker totals), the tape preserves each
        contract's strike, expiry, side, and sentiment so a terminal UI can
        filter and scan like a trading desk.
        """
        try:
            symbols = [t.strip().upper() for t in (tickers or []) if t.strip()]
            since_ts = (
                datetime.now(timezone.utc).date() - timedelta(days=window_days)
            ).isoformat() + "T00:00:00Z"
            return self.neo4j.run_read(
                FLOW_QUERY,
                tickers=symbols,
                side=side,
                min_ratio=float(min_ratio),
                since_ts=since_ts,
                limit=int(limit),
            )
        except Exception:
            logger.exception("options flow query failed")
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

    def person(self, politician_id: str) -> dict | None:
        """One politician's card: identity, committees, latest trades.

        Returns None when the id is unknown so the route answers 404
        instead of an empty shape. Trade rows carry ``event_id`` so the UI
        can join its alert list client-side without a second round-trip.
        """
        try:
            rows = self.neo4j.run_read(PERSON_QUERY, pid=politician_id)
            trades = self.neo4j.run_read(PERSON_TRADES_QUERY, pid=politician_id)
            overlap = self.neo4j.run_read(OVERLAP_QUERY, pid=politician_id)
        except Exception:
            logger.exception("person query failed")
            return None
        if not rows:
            return None
        person = dict(rows[0])
        person["committees"] = [c for c in person.get("committees", []) if c.get("id")]
        person["trades"] = [t for t in trades if t.get("event_id")]
        person["oversight"] = [
            {
                "sector": o.get("sector"),
                "committees": [c for c in o.get("committees", []) if c],
                "tickers": [t for t in o.get("tickers", []) if t],
                "trades": o.get("trades", 0),
            }
            for o in overlap
            if o.get("sector")
        ]
        return person

    def ticker(self, symbol: str) -> dict | None:
        """One ticker's card: sector, latest flow prints, latest trades.

        Returns None when the symbol is unknown so the route answers 404.
        Both lists carry ``event_id`` so the UI joins its alert list
        client-side without a second round-trip.
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return None
        try:
            rows = self.neo4j.run_read(TICKER_QUERY, sym=sym)
            trades = self.neo4j.run_read(TICKER_TRADES_QUERY, sym=sym)
            flow = self.neo4j.run_read(TICKER_FLOW_QUERY, sym=sym)
        except Exception:
            logger.exception("ticker query failed")
            return None
        if not rows:
            return None
        card = dict(rows[0])
        card["trades"] = [t for t in trades if t.get("event_id")]
        card["flow"] = [f for f in flow if f.get("event_id")]
        return card

    def committee(self, committee_id: str) -> dict | None:
        """One committee's card: overseen sectors, members, member trades.

        Returns None when the id is unknown so the route answers 404.
        Trades carry ``event_id`` (and member ``politician_id``) so the UI
        joins alerts and person cards client-side without round-trips.
        """
        cid = (committee_id or "").strip().upper()
        if not cid:
            return None
        try:
            rows = self.neo4j.run_read(COMMITTEE_QUERY, cid=cid)
            members = self.neo4j.run_read(COMMITTEE_MEMBERS_QUERY, cid=cid)
            trades = self.neo4j.run_read(COMMITTEE_TRADES_QUERY, cid=cid)
        except Exception:
            logger.exception("committee query failed")
            return None
        if not rows:
            return None
        card = dict(rows[0])
        card["sectors"] = [s.get("name") for s in card.get("sectors", []) if s.get("name")]
        card["members"] = [m for m in members if m.get("id")]
        card["trades"] = [t for t in trades if t.get("event_id")]
        return card

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
                          AND jsonb_typeof(raw_json->'excess_since') = 'number'
                          AND (raw_json->>'excess_since')::float >= :min_excess
                          AND raw_json->>'ticker' IS NOT NULL
                          AND raw_json->>'filer_name' IS NOT NULL
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
