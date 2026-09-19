"""Alert rules (spec §5.5).

A rule is a name + description + a predicate over a freshly-written event and
the graph. When the predicate returns facts, the event *triggers* and an alert
is drafted and stored (see :mod:`app.alerts.engine`).

Add a rule by writing one method and tagging it with :func:`rule` — the
registry picks it up for seeding (``definitions``) and evaluation
(``evaluate``) with no other edits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.db.neo4j import Neo4jStore
from app.models.events import CongressTradeEvent, Event, InsiderTradeEvent, OptionsFlowEvent

#: Matches when the ticker's sector is overseen by a committee the trading
#: politician sits on.
OVERSIGHT_PATH_CYPHER = """
MATCH (e:Event {id: $event_id})-[:INVOLVES_POLITICIAN]->(p:Politician)-[:MEMBER_OF]->(c:Committee)-[:OVERSEES]->(s:Sector)
MATCH (e)-[:INVOLVES_TICKER]->(t:Ticker)-[:IN_SECTOR]->(s)
RETURN p.name AS politician, t.ticker AS ticker, c.id AS committee, s.name AS sector,
       e.transaction_type AS transaction_type, e.amount_range AS amount_range,
       e.transaction_date AS transaction_date, e.tweet_url AS tweet_url
"""

#: Insider buys below this notional (shares × price) never alert — filters
#: routine small purchases.
INSIDER_NOTIONAL_THRESHOLD_USD = 1_000_000.0

#: Options alerts fire well above the radar's own unusual bar (2.0×) so the
#: alert panel isn't just a copy of the options feed.
OPTIONS_SWEEP_RATIO_THRESHOLD = 5.0


@dataclass
class RuleTrigger:
    """Facts gathered when a rule fires on an event."""

    rule_name: str
    facts: list[dict] = field(default_factory=list)
    #: Headline override; the engine falls back to its generic title.
    title: str | None = None
    #: Deterministic fallback summary when the LLM is down.
    blurb: str | None = None

    @property
    def triggered(self) -> bool:
        return bool(self.facts)


_RULES: dict[str, dict] = {}


def rule(name: str, description: str) -> Callable:
    """Register an :class:`AlertRules` evaluator method under ``name``."""

    def decorator(fn: Callable) -> Callable:
        _RULES[name] = {"description": description, "method": fn.__name__}
        return fn

    return decorator


class AlertRules:
    """Registry of rule name -> evaluator. Add new rules with ``@rule``."""

    def __init__(self, neo4j: Neo4jStore) -> None:
        self.neo4j = neo4j

    @classmethod
    def definitions(cls) -> dict[str, dict]:
        return {
            name: {"description": meta["description"], "config": {}}
            for name, meta in _RULES.items()
        }

    def evaluate(self, rule_name: str, event: Event) -> RuleTrigger:
        """Run ``rule_name`` against ``event``; returns a (possibly empty)
        :class:`RuleTrigger`. Unknown rules never trigger."""
        meta = _RULES.get(rule_name)
        if meta is None:
            return RuleTrigger(rule_name=rule_name)
        return getattr(self, meta["method"])(event)

    @rule(
        "politician_trades_in_oversight_sector",
        "Fires when a politician trades a ticker whose sector is overseen "
        "by one of their committees.",
    )
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

    @rule(
        "insider_large_buy",
        "Fires when a corporate insider buys $1M+ of their own stock "
        "in one disclosed trade.",
    )
    def _insider_large_buy(self, event: Event) -> RuleTrigger:
        if not isinstance(event, InsiderTradeEvent):
            return RuleTrigger(rule_name="insider_large_buy")
        if event.transaction_type != "buy":
            return RuleTrigger(rule_name="insider_large_buy")
        if event.shares is None or event.price is None:
            return RuleTrigger(rule_name="insider_large_buy")
        notional = event.shares * event.price
        if notional < INSIDER_NOTIONAL_THRESHOLD_USD:
            return RuleTrigger(rule_name="insider_large_buy")
        amount = f"~${notional:,.0f}"
        return RuleTrigger(
            rule_name="insider_large_buy",
            facts=[
                {
                    "politician": event.insider_name,
                    "ticker": event.ticker,
                    "transaction_type": "buy",
                    "amount": amount,
                    "tweet_url": event.tweet_url,
                }
            ],
            title=f"{event.insider_name} bought {amount} of {event.ticker}",
            blurb=(
                f"{event.insider_name} bought {amount} of {event.ticker} "
                f"in one disclosed trade. Source: {event.tweet_url}"
            ),
        )

    @rule(
        "unusual_options_sweep",
        "Fires when one contract's day volume is 5×+ its open interest.",
    )
    def _unusual_options_sweep(self, event: Event) -> RuleTrigger:
        if not isinstance(event, OptionsFlowEvent):
            return RuleTrigger(rule_name="unusual_options_sweep")
        volume = event.volume or 0
        ratio = volume / max(event.open_interest or 0, 1)
        if ratio < OPTIONS_SWEEP_RATIO_THRESHOLD:
            return RuleTrigger(rule_name="unusual_options_sweep")
        side = "calls" if event.contract_type == "call" else "puts"
        return RuleTrigger(
            rule_name="unusual_options_sweep",
            facts=[
                {
                    "ticker": event.ticker,
                    "transaction_type": side,
                    "amount": f"{volume:,} vs {event.open_interest or 0:,} open interest ({ratio:.1f}x)",
                    "tweet_url": event.tweet_url,
                }
            ],
            title=f"Unusual {event.ticker} {side} sweep ({ratio:.1f}x volume)",
            blurb=(
                f"Unusual {event.ticker} {side}: {volume:,} contracts traded "
                f"against {event.open_interest or 0:,} open interest "
                f"({ratio:.1f}x). Source: {event.tweet_url}"
            ),
        )
