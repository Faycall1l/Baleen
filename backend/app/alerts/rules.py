"""Alert rules (spec §5.5).

Exactly one rule ships in MVP: ``politician_trades_in_oversight_sector``. A rule
is a name + description + a predicate over a freshly-written event and the
graph. When the predicate returns facts, the event *triggers* and an alert is
drafted and stored (see :mod:`app.alerts.engine`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db.neo4j import Neo4jStore
from app.models.events import CongressTradeEvent, Event

#: Matches when the ticker's sector is overseen by a committee the trading
#: politician sits on.
OVERSIGHT_PATH_CYPHER = """
MATCH (e:Event {id: $event_id})-[:INVOLVES_POLITICIAN]->(p:Politician)-[:MEMBER_OF]->(c:Committee)-[:OVERSEES]->(s:Sector)
MATCH (e)-[:INVOLVES_TICKER]->(t:Ticker)-[:IN_SECTOR]->(s)
RETURN p.name AS politician, t.ticker AS ticker, c.id AS committee, s.name AS sector,
       e.transaction_type AS transaction_type, e.amount_range AS amount_range,
       e.transaction_date AS transaction_date, e.tweet_url AS tweet_url
"""


@dataclass
class RuleTrigger:
    """Facts gathered when a rule fires on an event."""

    rule_name: str
    facts: list[dict] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return bool(self.facts)


class AlertRules:
    """Registry of rule name -> evaluator. Add new MVP rules here."""

    def __init__(self, neo4j: Neo4jStore) -> None:
        self.neo4j = neo4j

    def definitions(self) -> dict[str, dict]:
        return {
            "politician_trades_in_oversight_sector": {
                "description": (
                    "Fires when a politician trades a ticker whose sector is "
                    "overseen by one of their committees."
                ),
                "config": {},
            }
        }

    def evaluate(self, rule_name: str, event: Event) -> RuleTrigger:
        """Run ``rule_name`` against ``event``; returns a (possibly empty)
        :class:`RuleTrigger`. Unknown rules never trigger."""
        if rule_name == "politician_trades_in_oversight_sector":
            return self._politician_trades_in_oversight_sector(event)
        return RuleTrigger(rule_name=rule_name)

    # ------------------------------------------------------------------
    # MVP rule
    # ------------------------------------------------------------------

    def _politician_trades_in_oversight_sector(
        self, event: Event
    ) -> RuleTrigger:
        if not isinstance(event, CongressTradeEvent):
            return RuleTrigger(rule_name="politician_trades_in_oversight_sector")

        rows = self.neo4j.run_read(
            OVERSIGHT_PATH_CYPHER, event_id=event.deterministic_id
        )
        return RuleTrigger(
            rule_name="politician_trades_in_oversight_sector",
            facts=rows,
        )
