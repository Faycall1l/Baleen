"""Alert engine tests: the MVP rule fires on a matching event and not otherwise."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.alerts.engine import AlertEngine
from app.graph.writer import GraphWriter
from app.models.events import CongressTradeEvent, OptionsFlowEvent
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
