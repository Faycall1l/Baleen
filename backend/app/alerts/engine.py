"""Alert engine (spec §5.5): evaluate active rules on new events and persist
drafted alerts.

Flow for each newly-written event:

1. Load active rules from ``alert_rules`` (Postgres).
2. For each rule, evaluate it against the event + graph.
3. On trigger, ask the LLM for a short factual summary, then store the alert
   in the ``alerts`` table.

Every evaluation is logged (rule, event, triggered yes/no) so nothing is
silently swallowed.
"""

from __future__ import annotations

import logging

from app.alerts.rules import AlertRules
from app.config import Settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.extraction.client import LLMClient, LLMError
from app.models.events import Event

logger = logging.getLogger(__name__)

DEFAULT_RULE = "politician_trades_in_oversight_sector"


class AlertEngine:
    def __init__(
        self,
        store: PostgresStore,
        neo4j: Neo4jStore,
        llm: LLMClient,
        settings: Settings,
    ) -> None:
        self.store = store
        self.neo4j = neo4j
        self.llm = llm
        self.settings = settings
        self.rules = AlertRules(neo4j)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_default_rules(self) -> None:
        for name, definition in self.rules.definitions().items():
            self.store.ensure_rule(
                name,
                definition["description"],
                definition.get("config"),
            )
        logger.info("default alert rules ensured")

    def evaluate_event(self, event: Event) -> list[dict]:
        """Evaluate all active rules against ``event``. Returns stored alerts.

        Assumes the event has already been written to the graph (the alert rule
        reads the graph path). Never raises — failures are logged.
        """
        alerts_created: list[dict] = []
        active_rules = self.store.get_active_rules()
        if not active_rules:
            # Self-heal: a fresh database has no rules until the app lifespan
            # runs; ensure them so every entry point behaves the same.
            self.ensure_default_rules()
            active_rules = self.store.get_active_rules()
        if not active_rules:
            logger.info("no active alert rules; skipping evaluation")
            return alerts_created

        rule_ids = {r["name"]: r["id"] for r in active_rules}

        for name in rule_ids:
            try:
                trigger = self.rules.evaluate(name, event)
            except Exception as exc:
                logger.exception("rule %s evaluation failed for event %s", name, event.deterministic_id)
                continue

            if not trigger.triggered:
                logger.info("rule %s did not trigger for event %s", name, event.deterministic_id)
                continue

            logger.info(
                "rule %s triggered for event %s with %d fact(s)",
                name,
                event.deterministic_id,
                len(trigger.facts),
            )
            alert = self._persist(name, rule_ids[name], event, trigger.facts)
            if alert:
                alerts_created.append(alert)

        return alerts_created

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _persist(self, rule_name: str, rule_id: int, event: Event, facts: list[dict]) -> dict | None:
        first = facts[0]
        title = (
            f"{first.get('politician', event.politician_name if hasattr(event, 'politician_name') else 'Politician')} "
            f"traded {first.get('ticker', getattr(event, 'ticker', ''))} in "
            f"{first.get('sector', '')}, a sector their committee oversees"
        )
        summary = self._draft_summary(event, facts)

        refs = self._graph_refs(facts, event)
        try:
            alert_id = self.store.insert_alert(
                rule_id=rule_id,
                event_id=event.deterministic_id,
                tweet_id=event.tweet_id,
                tweet_url=event.tweet_url,
                title=title,
                summary=summary,
                graph_refs=refs,
            )
        except Exception as exc:
            logger.exception("failed to persist alert for event %s", event.deterministic_id)
            return None

        if alert_id is None:
            logger.info(
                "alert already exists for (rule=%s, event=%s); skipping",
                rule_name,
                event.deterministic_id,
            )
            return None

        alert = {
            "id": alert_id,
            "rule_name": rule_name,
            "event_id": event.deterministic_id,
            "tweet_url": event.tweet_url,
            "title": title,
            "summary": summary,
            "graph_refs": refs,
        }
        logger.info("alert persisted (id=%d, rule=%s, event=%s)", alert_id, rule_name, event.deterministic_id)
        return alert

    def _draft_summary(self, event: Event, facts: list[dict]) -> str:
        prompt = (
            "You are Baleen's alert writer. Write a 2-3 sentence factual alert "
            "summary strictly from the facts provided. Include the source tweet "
            "URL. Do not invent figures or names.\n\n"
            f"Facts: {facts}"
        )
        try:
            response = self.llm.chat(
                [
                    {"role": "system", "content": "You write short, factual, non-alarmist alerts."},
                    {"role": "user", "content": prompt},
                ]
            )
            summary = response.content.strip()
            if summary:
                return summary
        except LLMError as exc:
            logger.warning("alert summary LLM call failed: %s", exc)
        return self._template_summary(facts)

    def _template_summary(self, facts: list[dict]) -> str:
        first = facts[0]
        return (
            f"{first.get('politician', 'A politician')} traded {first.get('ticker', '')} "
            f"({first.get('transaction_type', '')}) in the {first.get('sector', '')} sector, "
            f"which their committee {first.get('committee', '')} oversees. "
            f"Source: {first.get('tweet_url', '')}"
        )

    def _graph_refs(self, facts: list[dict], event: Event) -> list[str]:
        refs = {event.deterministic_id}
        for fact in facts:
            if fact.get("ticker"):
                refs.add(fact["ticker"])
            if fact.get("sector"):
                refs.add(fact["sector"])
            if fact.get("politician"):
                refs.add(fact["politician"])
        return sorted(refs)
