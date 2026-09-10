"""Published event reports: grounded briefs + period digests.

Every typed event gets a short human-readable **brief** (headline + a few
factual sentences + source links) written at processing time; a rolling
**digest** summarizes the last 24h of activity. Both are drafted by the LLM
*strictly from graph facts* — the model never invents tickers, people, or
figures — with a deterministic template fallback so reports survive LLM
outages. Stored in the ``reports`` table (idempotent) and served to the
Reports panel via ``/api/reports``.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.extraction.client import LLMClient, LLMError
from app.models.events import CongressTradeEvent, Event, OptionsFlowEvent

logger = logging.getLogger(__name__)

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
    return f"Event recorded. Source: {event.tweet_url}"


def _brief_refs(event: Event, facts: dict) -> list[str]:
    refs = {event.deterministic_id}
    for key in ("ticker", "politician", "graph_ticker", "graph_politician"):
        value = facts.get(key)
        if value:
            refs.add(str(value))
    for key in ("graph_committees", "graph_memberships"):
        for value in facts.get(key) or []:
            refs.add(str(value))
    return sorted(refs)


class ReportEngine:
    def __init__(self, store: PostgresStore, neo4j: Neo4jStore, llm: LLMClient) -> None:
        self.store = store
        self.neo4j = neo4j
        self.llm = llm

    # ------------------------------------------------------------------
    # Briefs (one per typed event)
    # ------------------------------------------------------------------

    def write_brief(self, event: Event) -> dict | None:
        """Draft + store the brief for ``event``. Idempotent; never raises."""
        if event.event_type not in ("options_flow", "congress_trade"):
            return None
        try:
            neighborhood = self._neighborhood(event.deterministic_id)
            facts = brief_facts(event, neighborhood)
            body = self._draft_brief(event, facts)
            report_id = self.store.insert_report(
                kind="brief",
                event_id=event.deterministic_id,
                title=brief_title(event),
                body=body,
                graph_refs=_brief_refs(event, facts),
            )
        except Exception:
            logger.exception("report brief failed for event %s", event.deterministic_id)
            return None
        if report_id is None:
            logger.info("brief already exists for event %s", event.deterministic_id)
            return None
        logger.info("brief persisted (id=%d, event=%s)", report_id, event.deterministic_id)
        return {"id": report_id, "event_id": event.deterministic_id}

    def _neighborhood(self, event_id: str) -> dict:
        try:
            rows = self.neo4j.run_read(BRIEF_QUERY, id=event_id)
        except Exception as exc:
            logger.warning("brief neighborhood query failed: %s", exc)
            return {}
        return rows[0] if rows else {}

    def _draft_brief(self, event: Event, facts: dict) -> str:
        prompt = (
            "You are Baleen's markets reporter. Write a 2-4 sentence factual "
            "brief strictly from the facts provided. Name the key figures "
            "(who, what, amounts, dates) and end with the source URL. Do not "
            "invent figures or names, do not editorialize.\n\n"
            f"Facts: {facts}"
        )
        try:
            response = self.llm.chat(
                [
                    {"role": "system", "content": "You write short, factual, non-alarmist market briefs."},
                    {"role": "user", "content": prompt},
                ]
            )
            body = response.content.strip()
            if body:
                return body
        except LLMError as exc:
            logger.warning("brief LLM call failed: %s", exc)
        return template_brief(event, facts)

    # ------------------------------------------------------------------
    # Digests (one per UTC day)
    # ------------------------------------------------------------------

    def ensure_digest(self, period: str | None = None) -> dict:
        """Return today's digest, generating it on first request. Never raises."""
        day = period or datetime.now(timezone.utc).date().isoformat()
        try:
            existing = self.store.get_digest(day)
            if existing is not None:
                return existing
            facts = self._digest_facts(day)
            body = self._draft_digest(day, facts)
            report_id = self.store.insert_report(
                kind="digest",
                period=day,
                title=f"Market digest — {day}",
                body=body,
                graph_refs=facts.get("tickers", [])[:10],
            )
            if report_id is None:  # raced another writer; serve the winner
                existing = self.store.get_digest(day)
                if existing is not None:
                    return existing
            return {
                "id": report_id, "kind": "digest", "period": day,
                "title": f"Market digest — {day}", "body": body,
            }
        except Exception:
            logger.exception("digest generation failed for %s", day)
            return {"kind": "digest", "period": day, "title": f"Market digest — {day}",
                    "body": "Digest unavailable (backend error)."}

    def _digest_facts(self, day: str) -> dict:
        since = f"{day}T00:00:00Z"
        try:
            rows = self.neo4j.run_read(DIGEST_QUERY, since=since)
        except Exception as exc:
            logger.warning("digest aggregate query failed: %s", exc)
            rows = []
        types = Counter(r.get("type") for r in rows)
        tickers = Counter(r.get("ticker") for r in rows if r.get("ticker"))
        sectors = Counter(r.get("sector") for r in rows if r.get("sector"))
        politicians = sorted({r.get("politician") for r in rows if r.get("politician")})
        try:
            alerts = self.store.get_alerts(limit=50)
            alert_titles = [a["title"] for a in alerts if str(a.get("created_at", "")) >= day]
        except Exception as exc:
            logger.warning("digest alerts fetch failed: %s", exc)
            alert_titles = []
        return {
            "period": day,
            "events": len(rows),
            "by_type": dict(types),
            "tickers": [t for t, _ in tickers.most_common(10)],
            "ticker_counts": dict(tickers.most_common(10)),
            "sectors": [s for s, _ in sectors.most_common(10)],
            "politicians": politicians[:10],
            "alerts": alert_titles,
        }

    def _draft_digest(self, day: str, facts: dict) -> str:
        if not facts.get("events"):
            return f"No events recorded on {day}."
        prompt = (
            "You are Baleen's markets reporter. Write a 4-6 sentence daily "
            "digest strictly from the aggregates provided: how many events of "
            "each type, the most active tickers/sectors, notable politicians, "
            "and any alerts. Do not invent figures or names.\n\n"
            f"Aggregates: {facts}"
        )
        try:
            response = self.llm.chat(
                [
                    {"role": "system", "content": "You write short, factual, non-alarmist market digests."},
                    {"role": "user", "content": prompt},
                ]
            )
            body = response.content.strip()
            if body:
                return body
        except LLMError as exc:
            logger.warning("digest LLM call failed: %s", exc)
        return template_digest(day, facts)


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
