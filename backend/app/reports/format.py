"""Deterministic brief/digest formatting (L4), split out of :mod:`app.reports.engine`.

Pure functions plus the graph queries they need: turning typed events into
headline, grounded facts, and a template body. No database or LLM handles are
involved, so the fallback rendering is unit-testable on its own and stays the
exact copy the model output is checked against.
"""

from __future__ import annotations

from app.models.events import CongressTradeEvent, Event, InsiderTradeEvent, OptionsFlowEvent


BRIEF_QUERY = """
MATCH (e:Event {id: $id})
OPTIONAL MATCH (e)-[:INVOLVES_TICKER]->(t:Ticker)-[:IN_SECTOR]->(s:Sector)
OPTIONAL MATCH (e)-[:INVOLVES_POLITICIAN]->(p:Politician)
OPTIONAL MATCH (e)-[:IN_COMMITTEE]->(c:Committee)
OPTIONAL MATCH (p)-[:MEMBER_OF]->(m:Committee)
RETURN e AS event,
       t.ticker AS ticker, s.name AS sector,
       p.name AS politician,
       collect(DISTINCT COALESCE(c.name, c.id)) AS committees,
       collect(DISTINCT COALESCE(m.name, m.id)) AS memberships
"""

DIGEST_QUERY = """
MATCH (e:Event)
// first_seen_at is when Baleen learned of the event (trade time can lag
// weeks behind); the digest covers newly-arrived events, not newly-made ones.
WHERE e.first_seen_at IS NOT NULL AND e.first_seen_at >= datetime($since)
OPTIONAL MATCH (e)-[:INVOLVES_TICKER]->(t:Ticker)-[:IN_SECTOR]->(s:Sector)
OPTIONAL MATCH (e)-[:INVOLVES_POLITICIAN]->(p:Politician)
RETURN e.type AS type, t.ticker AS ticker, s.name AS sector, p.name AS politician
"""

def brief_title(event: Event) -> str:
    """Deterministic headline — never model-generated."""
    if isinstance(event, CongressTradeEvent):
        verb = "bought" if event.transaction_type == "buy" else "sold"
        amount = f" ({event.amount_range})" if event.amount_range else ""
        return f"{event.politician_name} {verb} {event.ticker}{amount}"
    if isinstance(event, OptionsFlowEvent):
        side = "calls" if event.contract_type == "call" else "puts"
        strike = f" ${event.strike:g}" if event.strike is not None else ""
        return f"Unusual {side} flow: {event.ticker}{strike}"
    if isinstance(event, InsiderTradeEvent):
        verb = "bought" if event.transaction_type == "buy" else "sold"
        amount = f" ({event.amount_range})" if event.amount_range else ""
        return f"{event.insider_name} {verb} {event.ticker}{amount}"
    return f"Event {event.deterministic_id}"

def brief_facts(event: Event, neighborhood: dict) -> dict:
    """Flatten event + graph neighborhood into prompt facts."""
    facts: dict = {
        "event_type": event.event_type,
        "tweet_url": event.tweet_url,
        "confidence": event.confidence,
    }
    if isinstance(event, CongressTradeEvent):
        facts.update(
            {
                "politician": event.politician_name,
                "chamber": event.chamber,
                "party": event.party,
                "ticker": event.ticker,
                "side": event.transaction_type,
                "amount": event.amount_range,
                "transaction_date": str(event.transaction_date) if event.transaction_date else None,
            }
        )
    elif isinstance(event, OptionsFlowEvent):
        facts.update(
            {
                "ticker": event.ticker,
                "contract": event.contract_type,
                "strike": event.strike,
                "expiration": str(event.expiration) if event.expiration else None,
                "premium": event.premium,
                "volume": event.volume,
                "open_interest": event.open_interest,
                "sentiment": event.sentiment,
            }
        )
    elif isinstance(event, InsiderTradeEvent):
        facts.update(
            {
                "insider_name": event.insider_name,
                "role": event.role,
                "ticker": event.ticker,
                "side": event.transaction_type,
                "shares": event.shares,
                "price": event.price,
                "amount": event.amount_range,
                "transaction_date": str(event.transaction_date) if event.transaction_date else None,
            }
        )
    for key in ("ticker", "sector", "politician", "committees", "memberships"):
        value = neighborhood.get(key)
        if value:
            facts[f"graph_{key}"] = value
    return facts

def template_brief(event: Event, facts: dict) -> str:
    """Deterministic fallback when the LLM is unreachable."""
    if isinstance(event, CongressTradeEvent):
        committees = ", ".join(facts.get("graph_committees") or facts.get("graph_memberships") or [])
        tail = f" Sits on: {committees}." if committees else ""
        return (
            f"{facts.get('politician')} ({facts.get('chamber')}) "
            f"{'bought' if facts.get('side') == 'buy' else 'sold'} {facts.get('ticker')} "
            f"worth {facts.get('amount')} on {facts.get('transaction_date')}.{tail} "
            f"Source: {event.tweet_url}"
        )
    if isinstance(event, OptionsFlowEvent):
        return (
            f"Unusual {facts.get('contract')} flow in {facts.get('ticker')}: "
            f"volume {facts.get('volume')} vs open interest {facts.get('open_interest')}. "
            f"Source: {event.tweet_url}"
        )
    if isinstance(event, InsiderTradeEvent):
        role = f" ({facts.get('role')})" if facts.get("role") else ""
        return (
            f"{facts.get('insider_name')}{role} "
            f"{'bought' if facts.get('side') == 'buy' else 'sold'} {facts.get('ticker')} "
            f"worth {facts.get('amount')} on {facts.get('transaction_date')}. "
            f"Source: {event.tweet_url}"
        )
    return f"Event recorded. Source: {event.tweet_url}"

def brief_refs(event: Event, facts: dict) -> list[str]:
    refs = {event.deterministic_id}
    for key in ("ticker", "politician", "graph_ticker", "graph_politician"):
        value = facts.get(key)
        if value:
            refs.add(str(value))
    for key in ("graph_committees", "graph_memberships"):
        for value in facts.get(key) or []:
            refs.add(str(value))
    return sorted(refs)

def template_digest(day: str, facts: dict) -> str:
    """Deterministic digest fallback."""
    parts = [f"On {day}, Baleen recorded {facts.get('events', 0)} event(s)"]
    by_type = facts.get("by_type") or {}
    if by_type:
        parts.append(", ".join(f"{n} {t}" for t, n in sorted(by_type.items())))
    if facts.get("tickers"):
        parts.append(f"Most active tickers: {', '.join(facts['tickers'][:5])}")
    if facts.get("sectors"):
        parts.append(f"Sectors: {', '.join(facts['sectors'][:5])}")
    if facts.get("alerts"):
        parts.append(f"Alerts fired: {len(facts['alerts'])}")
    return ". ".join(parts) + "."
