"""Alert engine tests: the MVP rule fires on a matching event and not otherwise."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.alerts.engine import AlertEngine
from app.alerts.rules import AlertRules
from app.graph.writer import GraphWriter
from app.models.events import CongressTradeEvent, InsiderTradeEvent, OptionsFlowEvent
from scripts.seed_reference import seed

from conftest import StubLLM


def _congress_event(
    tweet_id: str,
    politician_id: str,
    politician_name: str,
    ticker: str,
    committees: list[str],
) -> CongressTradeEvent:
    return CongressTradeEvent(
        tweet_id=tweet_id,
        tweet_url=f"https://x.com/congress_trades/status/{tweet_id}",
        confidence=0.95,
        politician_name=politician_name,
        politician_id=politician_id,
        chamber="senate",
        party="D",
        committee_ids=committees,
        ticker=ticker,
        transaction_type="buy",
        amount_range="$1,001 - $15,000",
        transaction_date=date(2026, 1, 15),
        event_time=datetime(2026, 1, 16, tzinfo=timezone.utc),
    )


def _engine(postgres_store, neo4j_store) -> AlertEngine:
    engine = AlertEngine(
        store=postgres_store,
        neo4j=neo4j_store,
        llm=StubLLM(summary="Warren bought JPM; the Senate Banking committee oversees Financial Services."),
    )
    engine.ensure_default_rules()
    return engine


def test_rule_triggers_when_politician_trades_in_oversight_sector(
    postgres_store, neo4j_store
):
    seed(neo4j_store)
    engine = _engine(postgres_store, neo4j_store)

    # Elizabeth Warren (SSBK -> Financial Services) buys JPM (Financial Services).
    event = _congress_event(
        tweet_id="alert-trigger-1",
        politician_id="W000817",
        politician_name="Elizabeth Warren",
        ticker="JPM",
        committees=["SSBK", "SSHR"],
    )
    GraphWriter(neo4j_store).write_event(event)

    alerts = engine.evaluate_event(event)

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["rule_name"] == "politician_trades_in_oversight_sector"
    assert "JPM" in alert["title"]
    assert "Financial Services" in alert["title"]
    assert alert["tweet_url"].endswith("/alert-trigger-1")
    assert "Warren" in alert["summary"]


def test_rule_does_not_trigger_for_non_oversight_sector(
    postgres_store, neo4j_store
):
    seed(neo4j_store)
    engine = _engine(postgres_store, neo4j_store)

    # Warren's committees (SSBK, SSHR) oversee Financial Services + Healthcare;
    # NVDA is Technology -> no overlap, no alert.
    event = _congress_event(
        tweet_id="alert-nontrigger-1",
        politician_id="W000817",
        politician_name="Elizabeth Warren",
        ticker="NVDA",
        committees=["SSBK", "SSHR"],
    )
    GraphWriter(neo4j_store).write_event(event)

    alerts = engine.evaluate_event(event)

    assert alerts == []


def test_rule_ignores_options_flow_events(postgres_store, neo4j_store):
    seed(neo4j_store)
    engine = _engine(postgres_store, neo4j_store)

    options = OptionsFlowEvent(
        tweet_id="alert-flow-1",
        tweet_url="https://x.com/unusual_whales/status/alert-flow-1",
        confidence=0.9,
        ticker="JPM",
        contract_type="call",
        sentiment="bullish",
        event_time=datetime(2026, 1, 16, tzinfo=timezone.utc),
    )
    GraphWriter(neo4j_store).write_event(options)

    assert engine.evaluate_event(options) == []


def test_stored_alert_is_retrievable(postgres_store, neo4j_store):
    seed(neo4j_store)
    engine = _engine(postgres_store, neo4j_store)
    event = _congress_event(
        tweet_id="alert-list-1",
        politician_id="W000817",
        politician_name="Elizabeth Warren",
        ticker="JPM",
        committees=["SSBK"],
    )
    GraphWriter(neo4j_store).write_event(event)
    engine.evaluate_event(event)

    alerts = postgres_store.get_alerts(limit=5)
    assert len(alerts) == 1
    assert alerts[0]["rule_name"] == "politician_trades_in_oversight_sector"
    assert "JPM" in alerts[0]["title"]


def test_re_evaluating_the_same_event_creates_no_duplicate_alert(
    postgres_store, neo4j_store
):
    seed(neo4j_store)
    engine = _engine(postgres_store, neo4j_store)
    event = _congress_event(
        tweet_id="alert-dedup-1",
        politician_id="W000817",
        politician_name="Elizabeth Warren",
        ticker="JPM",
        committees=["SSBK"],
    )
    GraphWriter(neo4j_store).write_event(event)

    first = engine.evaluate_event(event)
    second = engine.evaluate_event(event)

    assert len(first) == 1
    assert second == []  # already-alerted event produces nothing new
    assert len(postgres_store.get_alerts(limit=10)) == 1


class _DownLLM:
    """LLM goes down after extraction succeeded (mid-batch failure): the alert
    must still be drafted from a template and persisted, not dropped."""

    def chat(self, messages, **kwargs):
        from app.extraction.client import LLMTransportError

        raise LLMTransportError("connection refused")


def test_llm_down_during_summary_drafting_uses_template_fallback(
    postgres_store, neo4j_store
):
    seed(neo4j_store)
    engine = AlertEngine(
        store=postgres_store,
        neo4j=neo4j_store,
        llm=_DownLLM(),
    )
    engine.ensure_default_rules()

    event = _congress_event(
        tweet_id="alert-llmdown-1",
        politician_id="W000817",
        politician_name="Elizabeth Warren",
        ticker="JPM",
        committees=["SSBK"],
    )
    GraphWriter(neo4j_store).write_event(event)

    alerts = engine.evaluate_event(event)

    assert len(alerts) == 1
    alert = alerts[0]
    # Template summary mentions the key facts even though the LLM was down.
    assert "Warren" in alert["summary"]
    assert "JPM" in alert["summary"]
    assert "Financial Services" in alert["summary"]
    assert "https://x.com/congress_trades/status/alert-llmdown-1" in alert["summary"]

    stored = postgres_store.get_alerts(limit=10)
    assert len(stored) == 1
    assert stored[0]["tweet_id"] == "alert-llmdown-1"


# ---------------------------------------------------------------------------
# Degradation: a hostile/missing backend must not raise out of evaluate_event.
# ---------------------------------------------------------------------------


class _EmptyBackend:
    def __init__(self):
        self.ensure_calls = 0
        self.persisted = []

    def get_active_rules(self):
        return []  # nothing is ever active

    def ensure_rule(self, *a, **k):
        self.ensure_calls += 1

    def insert_alert(self, **k):
        raise RuntimeError("postgres exploded")


def test_evaluate_event_self_heals_and_returns_empty_when_no_rules():
    """A database with zero active rules triggers the ensure_default_rules
    self-heal; if registration still cannot restore them, evaluate_event
    returns [] instead of raising."""
    store = _EmptyBackend()
    engine = AlertEngine(store=store, neo4j=None, llm=None)  # type: ignore[arg-type]
    event = _congress_event("alert-empty-1", "W000817", "Elizabeth Warren", "JPM", ["SSBK"])

    assert engine.evaluate_event(event) == []
    assert store.ensure_calls == len(AlertRules.definitions())


def test_evaluate_unknown_rule_never_triggers():
    """Unregistered rule names evaluate to an empty trigger, never raise."""
    rules = AlertRules(neo4j=None)  # type: ignore[arg-type]

    trigger = rules.evaluate("no_such_rule", None)  # type: ignore[arg-type]

    assert trigger.rule_name == "no_such_rule"
    assert trigger.facts == []
    assert trigger.triggered is False


def _insider_event(tweet_id: str, side: str, shares: float | None, price: float | None) -> InsiderTradeEvent:
    return InsiderTradeEvent(
        tweet_id=tweet_id,
        tweet_url=f"https://x.com/edgar_form4/status/{tweet_id}",
        confidence=0.95,
        insider_name="Milton C Ault III",
        insider_id="0001212502",
        ticker="DPW",
        transaction_type=side,  # type: ignore[arg-type]
        shares=shares,
        price=price,
        transaction_date=date(2026, 9, 9),
        event_time=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )


def _options_event(tweet_id: str, volume: int, open_interest: int) -> OptionsFlowEvent:
    return OptionsFlowEvent(
        tweet_id=tweet_id,
        tweet_url=f"https://x.com/unusual_whales/status/{tweet_id}",
        confidence=0.9,
        ticker="NVDA",
        contract_type="call",
        strike=250.0,
        sentiment="bullish",
        volume=volume,
        open_interest=open_interest,
        event_time=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )


def test_insider_large_buy_triggers_above_threshold():
    rules = AlertRules(neo4j=None)  # type: ignore[arg-type]

    trigger = rules.evaluate("insider_large_buy", _insider_event("a-ins-1", "buy", 5000.0, 250.0))

    assert trigger.triggered is True
    assert "Milton C Ault III" in (trigger.title or "")
    assert "DPW" in (trigger.title or "")


def test_insider_large_buy_ignores_small_sells_and_missing_figures():
    rules = AlertRules(neo4j=None)  # type: ignore[arg-type]

    assert rules.evaluate("insider_large_buy", _insider_event("a-ins-2", "sell", 5000.0, 250.0)).triggered is False
    assert rules.evaluate("insider_large_buy", _insider_event("a-ins-3", "buy", 100.0, 2.5)).triggered is False
    assert rules.evaluate("insider_large_buy", _insider_event("a-ins-4", "buy", None, 250.0)).triggered is False
    assert rules.evaluate("insider_large_buy", _insider_event("a-ins-5", "buy", 5000.0, None)).triggered is False


def test_unusual_options_sweep_triggers_at_5x_not_at_radar_bar():
    rules = AlertRules(neo4j=None)  # type: ignore[arg-type]

    hot = rules.evaluate("unusual_options_sweep", _options_event("a-opt-1", 10_000, 1_000))
    assert hot.triggered is True
    assert "NVDA" in (hot.title or "")

    warm = rules.evaluate("unusual_options_sweep", _options_event("a-opt-2", 3_000, 1_000))
    assert warm.triggered is False


def test_new_rules_ship_as_active_defaults(postgres_store, neo4j_store):
    engine = _engine(postgres_store, neo4j_store)

    alerts = engine.evaluate_event(_insider_event("a-ins-6", "buy", 5000.0, 250.0))

    assert [a["rule_name"] for a in alerts] == ["insider_large_buy"]
    assert "Milton C Ault III" in alerts[0]["title"]


def test_evaluate_event_skips_rule_when_evaluation_raises(monkeypatch, postgres_store, neo4j_store):
    """One rule blowing up (e.g. a graph query error) must not stop the loop
    or raise; the alert simply doesn't get created for that rule."""
    engine = _engine(postgres_store, neo4j_store)

    def boom(name, event):
        raise RuntimeError("graph exploded")

    monkeypatch.setattr(engine.rules, "evaluate", boom)

    event = _congress_event(
        tweet_id="alert-boom-1",
        politician_id="W000817",
        politician_name="Elizabeth Warren",
        ticker="JPM",
        committees=["SSBK"],
    )
    GraphWriter(neo4j_store).write_event(event)

    assert engine.evaluate_event(event) == []


def test_evaluate_event_survives_alert_persist_failure(monkeypatch, postgres_store, neo4j_store):
    """If inserting the alert row itself fails, the engine logs and moves on
    rather than tearing down the whole evaluation pass."""
    from unittest.mock import Mock

    engine = _engine(postgres_store, neo4j_store)
    monkeypatch.setattr(postgres_store, "insert_alert", Mock(side_effect=RuntimeError("insert exploded")))

    event = _congress_event(
        tweet_id="alert-persist-1",
        politician_id="W000817",
        politician_name="Elizabeth Warren",
        ticker="JPM",
        committees=["SSBK"],
    )
    GraphWriter(neo4j_store).write_event(event)

    assert engine.evaluate_event(event) == []


def test_rules_ignore_wrong_event_types():
    """Each rule's type guard returns an untriggered result without touching
    Neo4j — wrong-typed events short-circuit before any query."""
    rules = AlertRules(neo4j=None)  # type: ignore[arg-type]

    assert rules.evaluate(
        "politician_trades_in_oversight_sector",
        _insider_event("a-x-1", "buy", 5000.0, 250.0),
    ).triggered is False
    assert rules.evaluate(
        "insider_large_buy", _options_event("a-x-2", 10_000, 1_000)
    ).triggered is False
    assert rules.evaluate(
        "unusual_options_sweep",
        _insider_event("a-x-3", "buy", 5000.0, 250.0),
    ).triggered is False


def test_options_sweep_ignores_below_threshold_volume():
    rules = AlertRules(neo4j=None)  # type: ignore[arg-type]

    cold = rules.evaluate("unusual_options_sweep", _options_event("a-opt-2", 1500, 1_000))
    assert cold.triggered is False
    assert cold.facts == []
