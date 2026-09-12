"""Fixed Neo4j schema for the Baleen knowledge graph (spec §5.3).

This module is the single source of truth shared by:

- the graph writer (:mod:`app.graph.writer`) — MERGE keys and property names;
- the query agent (:mod:`app.query`) — the schema context handed to the LLM and
  the allowlist used to validate generated Cypher.

Never let the LLM infer the schema; always pass :func:`schema_context`.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Node labels
# ---------------------------------------------------------------------------

ALLOWED_LABELS = frozenset(
    ["Ticker", "Sector", "Politician", "Committee", "Event", "Insider"]
)

# ---------------------------------------------------------------------------
# Relationship types
# ---------------------------------------------------------------------------

ALLOWED_RELATIONSHIPS = frozenset(
    [
        "IN_SECTOR",            # (Ticker)-[:IN_SECTOR]->(Sector)
        "MEMBER_OF",            # (Politician)-[:MEMBER_OF]->(Committee)
        "OVERSEES",             # (Committee)-[:OVERSEES]->(Sector)
        "INVOLVES_TICKER",      # (Event)-[:INVOLVES_TICKER]->(Ticker)
        "INVOLVES_POLITICIAN",  # (Event)-[:INVOLVES_POLITICIAN]->(Politician)
        "INVOLVES_INSIDER",     # (Event)-[:INVOLVES_INSIDER]->(Insider)
        "IN_COMMITTEE",         # (Event)-[:IN_COMMITTEE]->(Committee)
    ]
)

# ---------------------------------------------------------------------------
# Identifier keys used by MERGE
# ---------------------------------------------------------------------------

LABEL_KEYS = {
    "Ticker": "ticker",
    "Sector": "name",
    "Politician": "id",
    "Committee": "id",
    "Event": "id",
    "Insider": "id",
}

# ---------------------------------------------------------------------------
# Schema context given to the LLM query agent verbatim
# ---------------------------------------------------------------------------

SCHEMA_CONTEXT = """\
The graph stores financial events extracted from X (Twitter). Node labels:
- Ticker {ticker}          a stock symbol (e.g. NVDA), uppercase
- Sector {name}            canonical sector name (e.g. Semiconductors)
- Politician {id, name, chamber, party}  a US politician
- Insider {id, name, role}      a corporate insider (SEC Form 4 filer)
- Committee {id, name}     a congressional committee
- Event {id, type, tweet_id, tweet_url, event_time}  an extracted event

Event.type is one of: options_flow, congress_trade, insider_trade.
Options-flow events have extra properties: ticker, contract_type (call|put),
strike, expiration, premium, volume, open_interest, sentiment, event_time.
Congress-trade events have extra properties: politician_name, chamber, party,
committee_ids, ticker, transaction_type (buy|sell), amount_range,
transaction_date, event_time.
Insider-trade events have extra properties: insider_name, insider_cik, role,
ticker, transaction_type (buy|sell), shares, price, amount_range,
transaction_date, event_time.

Relationship types:
- (Ticker)-[:IN_SECTOR]->(Sector)
- (Politician)-[:MEMBER_OF]->(Committee)
- (Committee)-[:OVERSEES]->(Sector)
- (Event)-[:INVOLVES_TICKER]->(Ticker)
- (Event)-[:INVOLVES_POLITICIAN]->(Politician)
- (Event)-[:INVOLVES_INSIDER]->(Insider)
- (Event)-[:IN_COMMITTEE]->(Committee)

Rules for writing Cypher:
- Only these labels may appear: Ticker, Sector, Politician, Committee, Event, Insider.
- Only these relationship types may appear: IN_SECTOR, MEMBER_OF, OVERSEES,
  INVOLVES_TICKER, INVOLVES_POLITICIAN, INVOLVES_INSIDER, IN_COMMITTEE.
- Queries are READ-ONLY: only MATCH, OPTIONAL MATCH, WHERE, RETURN, ORDER BY,
  LIMIT, WITH, collect, count, DISTINCT. Never use CREATE, MERGE, SET, DELETE.
- Inline literal user-provided values directly in the query (quote strings).
  Do NOT use $parameters — the executor does not bind them.
- Use the exact property names shown above.
"""


def schema_context() -> str:
    """Markdown/plain-text schema description for the LLM query agent."""
    return SCHEMA_CONTEXT
