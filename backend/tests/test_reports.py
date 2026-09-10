"""Report engine tests: titles, grounding, fallbacks, idempotency hooks."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.ingestion.source import RawTweet
from app.models.events import CongressTradeEvent, OptionsFlowEvent, UnparsedEvent
from app.reports.engine import (
    ReportEngine,
    brief_facts,
    brief_title,
    template_brief,
    template_digest,
)


def _congress() -> CongressTradeEvent:
    return CongressTradeEvent(
        tweet_id="ptr-1", tweet_url="https://clerk.house.gov/ptr/1", confidence=0.95,
        politician_name="Maxine Waters", politician_id="W000187",
        chamber="house", party="D", committee_ids=["HSBA"],
        ticker="JPM", transaction_type="buy",
        amount_range="$1,001 - $15,000", transaction_date=date(2026, 9, 5),
    )


def _options() -> OptionsFlowEvent:
    return OptionsFlowEvent(
        tweet_id="opt-1", tweet_url="https://finance.yahoo.com/quote/NVDA", confidence=0.9,
        ticker="NVDA", contract_type="call", strike=225.0,
        expiration=date(2026, 9, 9), premium=1.2, volume=1000, open_interest=200,
        sentiment="bullish",
    )


def test_brief_title_is_deterministic():
    assert brief_title(_congress()) == "Maxine Waters bought JPM ($1,001 - $15,000)"
    assert brief_title(_options()) == "Unusual calls flow: NVDA $225"


def test_brief_facts_flatten_event_and_neighborhood():
    facts = brief_facts(_congress(), {"ticker": "JPM", "sector": "Financial Services",
                                      "committees": ["House Financial Services Committee"]})
    assert facts["ticker"] == "JPM"
    assert facts["graph_sector"] == "Financial Services"
    assert facts["tweet_url"].endswith("/ptr/1")


def test_template_brief_needs_no_llm():
    body = template_brief(_congress(), brief_facts(_congress(), {}))
    assert "Maxine Waters" in body and "JPM" in body
    body = template_brief(_options(), brief_facts(_options(), {}))
    assert "NVDA" in body and "1000" in body


def test_template_digest_summarizes_aggregates():
    body = template_digest("2026-09-10", {"events": 5, "by_type": {"congress_trade": 4},
                                          "tickers": ["JPM"], "sectors": [], "alerts": ["x"]})
    assert "2026-09-10" in body and "JPM" in body


class _FakeStore:
    def __init__(self):
        self.reports = []

    def insert_report(self, kind, title, body, event_id=None, period=None, graph_refs=None):
        if any(r["kind"] == kind and r.get("event_id") == event_id and r.get("period") == period
               for r in self.reports):
            return None
        rid = len(self.reports) + 1
        self.reports.append({"id": rid, "kind": kind, "event_id": event_id, "period": period,
                             "title": title, "body": body})
        return rid

    def get_digest(self, period):
        return next((r for r in self.reports if r["kind"] == "digest" and r["period"] == period), None)

    def get_alerts(self, limit=50):
        return []


class _FakeNeo4j:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []

    def run_read(self, query, **params):
        return list(self.rows)


class _StubLLM:
    def __init__(self, summary="Drafted brief with source link."):
        self.summary = summary

    def chat(self, messages, **kwargs):
        from app.extraction.client import LLMResponse

        return LLMResponse(content=self.summary)


def _engine(store=None, rows=None, llm=None):
    return ReportEngine(store=store or _FakeStore(),
                        neo4j=_FakeNeo4j(rows),
                        llm=llm or _StubLLM())


def test_write_brief_persists_grounded_report():
    store = _FakeStore()
    engine = _engine(store=store, rows=[{"ticker": "JPM", "sector": "Financial Services",
                                         "politician": "Maxine Waters",
                                         "committees": ["HSBA"], "memberships": ["HSBA"]}])
    report = engine.write_brief(_congress())

    assert report and report["event_id"] == _congress().deterministic_id
    assert len(store.reports) == 1
    assert store.reports[0]["title"] == "Maxine Waters bought JPM ($1,001 - $15,000)"
    assert store.reports[0]["body"] == "Drafted brief with source link."


def test_write_brief_is_idempotent():
    store = _FakeStore()
    engine = _engine(store=store)
    assert engine.write_brief(_congress()) is not None
    assert engine.write_brief(_congress()) is None
    assert len(store.reports) == 1


def test_write_brief_skips_unparsed():
    engine = _engine()
    unparsed = UnparsedEvent(tweet_id="u", tweet_url="https://x/u", confidence=0.0,
                             raw_text="noise", reason="x")
    assert engine.write_brief(unparsed) is None


def test_write_brief_never_raises():
    class BoomStore(_FakeStore):
        def insert_report(self, *a, **k):
            raise RuntimeError("db down")

    assert _engine(store=BoomStore()).write_brief(_congress()) is None


def test_write_brief_falls_back_to_template_when_llm_down():
    from app.extraction.client import LLMTransportError

    class DownLLM:
        def chat(self, messages, **kwargs):
            raise LLMTransportError("down")

    store = _FakeStore()
    engine = _engine(store=store, llm=DownLLM())
    assert engine.write_brief(_congress()) is not None
    assert "Maxine Waters" in store.reports[0]["body"]


def test_ensure_digest_generates_once_then_serves_cached():
    store = _FakeStore()
    engine = _engine(store=store, rows=[
        {"type": "congress_trade", "ticker": "JPM", "sector": "Financial Services", "politician": "Maxine Waters"},
    ])
    first = engine.ensure_digest("2026-09-10")
    second = engine.ensure_digest("2026-09-10")

    assert first["period"] == "2026-09-10"
    assert len(store.reports) == 1
    assert second["id"] == first["id"]


def test_ensure_digest_empty_day_reports_no_events():
    engine = _engine()
    report = engine.ensure_digest("2026-01-01")
    assert "No events recorded" in report["body"]
