"""Published event reports: grounded briefs + period digests.

Every typed event gets a short human-readable **brief** (headline + a few
factual sentences + source links) written at processing time; a rolling
**digest** summarizes the last 24h of activity. Both are drafted by the LLM
*strictly from graph facts* — the model never invents tickers, people, or
figures — with a deterministic template fallback (see
:mod:`app.reports.format`) so reports survive LLM outages. Stored in the
``reports`` table (idempotent) and served to the Reports panel via
``/api/reports``.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.extraction.client import LLMClient
from app.models.events import Event
from app.reports.format import (
    BRIEF_QUERY,
    DIGEST_QUERY,
    brief_facts,
    brief_refs,
    brief_title,
    template_brief,
    template_digest,
)

logger = logging.getLogger(__name__)


class ReportEngine:
    def __init__(self, store: PostgresStore, neo4j: Neo4jStore, llm: LLMClient, llm_briefs: bool = True) -> None:
        self.store = store
        self.neo4j = neo4j
        self.llm = llm
        # False skips the model call and uses the deterministic template
        # directly — same facts, no prose polish, zero quota. The switch for
        # free-tier schedulers (REPORTS_LLM_BRIEFS).
        self.llm_briefs = llm_briefs

    # ------------------------------------------------------------------
    # Briefs (one per typed event)
    # ------------------------------------------------------------------

    def write_brief(self, event: Event) -> dict | None:
        """Draft + store the brief for ``event``. Idempotent; never raises."""
        if event.event_type not in ("options_flow", "congress_trade", "insider_trade"):
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
                graph_refs=brief_refs(event, facts),
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
        from app.extraction.client import draft_text

        if not self.llm_briefs:
            return template_brief(event, facts)
        prompt = (
            "You are Baleen's markets reporter. Write a 2-4 sentence factual "
            "brief strictly from the facts provided. Name the key figures "
            "(who, what, amounts, dates) and end with the source URL. Do not "
            "invent figures or names, do not editorialize.\n\n"
            f"Facts: {facts}"
        )
        return draft_text(
            self.llm,
            system="You write short, factual, non-alarmist market briefs.",
            prompt=prompt,
            fallback=template_brief(event, facts),
        )

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
            day_start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)
            alert_titles = []
            for a in alerts:
                created = a.get("created_at")
                try:
                    created_at = datetime.fromisoformat(str(created))
                except ValueError:
                    continue
                if day_start <= created_at < day_end:
                    alert_titles.append(a["title"])
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
        from app.extraction.client import draft_text

        if not facts.get("events"):
            return f"No events recorded on {day}."
        prompt = (
            "You are Baleen's markets reporter. Write a 4-6 sentence daily "
            "digest strictly from the aggregates provided: how many events of "
            "each type, the most active tickers/sectors, notable politicians, "
            "and any alerts. Do not invent figures or names.\n\n"
            f"Aggregates: {facts}"
        )
        return draft_text(
            self.llm,
            system="You write short, factual, non-alarmist market digests.",
            prompt=prompt,
            fallback=template_digest(day, facts),
        )
