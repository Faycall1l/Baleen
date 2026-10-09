"""Report engine tests: titles, grounding, fallbacks, idempotency hooks."""

from __future__ import annotations

from datetime import date

from app.models.events import CongressTradeEvent, OptionsFlowEvent, UnparsedEvent
from app.reports.engine import ReportEngine
from app.reports.format import (
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


def test_digest_alert_window_is_datetime_aware_issues_11():
    """#11: string-prefix compare pulled in *future* alerts for past periods."""
    future = {"title": "tomorrow", "created_at": "2026-09-19 08:00:00.123456+00:00"}
    today = {"title": "today", "created_at": "2026-09-10 08:00:00.123456+00:00"}
    broken = {"title": "broken", "created_at": "garbage"}  # must not crash

    class _StoreWithAlerts(_FakeStore):
        def get_alerts(self, limit=50):
            return [future, today, broken]

    engine = _engine(store=_StoreWithAlerts())
    facts = engine._digest_facts("2026-09-10")

    assert "today" in facts["alerts"]
    assert "tomorrow" not in facts["alerts"]


def test_insider_brief_title_and_template():
    from app.models.events import InsiderTradeEvent

    event = InsiderTradeEvent(
        tweet_id="ed-1", tweet_url="https://www.sec.gov/x", confidence=0.9,
        insider_name="Milton C Ault III", insider_cik="0001212502",
        insider_id="0001212502", role="Chairman",
        ticker="GPUS", transaction_type="buy",
        shares=1000.0, price=2.5, amount_range="$2,500.00",
        transaction_date=date(2026, 9, 9),
    )
    assert brief_title(event) == "Milton C Ault III bought GPUS ($2,500.00)"
    body = template_brief(event, brief_facts(event, {}))
    assert "Milton C Ault III" in body and "GPUS" in body


def test_unparsed_events_get_fallback_title_and_template():
    unparsed = UnparsedEvent(tweet_id="u-1", tweet_url="https://x/u", confidence=0.0,
                             raw_text="noise", reason="x")
    assert brief_title(unparsed) == f"Event {unparsed.deterministic_id}"
    facts = brief_facts(unparsed, {})
    assert template_brief(unparsed, facts) == "Event recorded. Source: https://x/u"


def test_brief_survives_neighborhood_failure():
    """A neo4j outage must not block brief generation — template fallback."""
    from app.extraction.client import LLMTransportError

    class BoomNeo4j(_FakeNeo4j):
        def run_read(self, *a, **k):
            raise RuntimeError("neo4j down")

    class DownLLM(_StubLLM):
        def chat(self, messages, **kwargs):
            raise LLMTransportError("down")

    store = _FakeStore()
    engine = _engine(store=store, rows=[], llm=DownLLM())
    engine.neo4j = BoomNeo4j([])
    event = _congress()

    assert engine._neighborhood(event.deterministic_id) == {}
    report = engine.write_brief(event)
    assert report is not None
    assert "Maxine Waters" in store.reports[0]["body"]


def test_ensure_digest_serves_race_winner():
    """If insert_report loses a race but a winner already exists, serve it."""
    class RaceStore(_FakeStore):
        def insert_report(self, *a, **k):
            self.reports.append({"id": 1, "kind": "digest", "period": "2026-09-10",
                                 "title": "Market digest — 2026-09-10", "body": "winner"})
            return None

    store = RaceStore()
    engine = _engine(store=store, rows=[], llm=_StubLLM())
    result = engine.ensure_digest("2026-09-10")
    assert result["body"] == "winner"
    assert result["id"] == 1


def test_ensure_digest_degrades_gracefully_on_backend_error():
    class BoomStore(_FakeStore):
        def insert_report(self, *a, **k):
            raise RuntimeError("db down")

    engine = _engine(store=BoomStore(), rows=[], llm=_StubLLM())
    result = engine.ensure_digest("2026-09-10")
    assert result["kind"] == "digest" and result["period"] == "2026-09-10"
    assert "unavailable" in result["body"]


def test_digest_facts_survive_graph_and_alert_failures():
    class BoomNeo4j(_FakeNeo4j):
        def run_read(self, *a, **k):
            raise RuntimeError("neo4j down")

    class BoomStore(_FakeStore):
        def get_alerts(self, limit=50):
            raise RuntimeError("pg down")

    engine = _engine(store=BoomStore(), rows=[], llm=_StubLLM())
    engine.neo4j = BoomNeo4j([])
    facts = engine._digest_facts("2026-09-10")
    assert facts["events"] == 0 and facts["tickers"] == [] and facts["alerts"] == []


def test_write_brief_without_llm_uses_template_and_never_calls_model():
    """REPORTS_LLM_BRIEFS=false: same facts, zero quota. The stub raises on
    any call so a model touch fails the test outright."""

    class _BoomLLM:
        def chat(self, *args, **kwargs):
            raise AssertionError("model must not be called")

    from app.reports.engine import ReportEngine

    store = _FakeStore()
    engine = ReportEngine(store=store, neo4j=_FakeNeo4j(None), llm=_BoomLLM(), llm_briefs=False)
    report = engine.write_brief(_congress())

    assert report is not None
    assert "Maxine Waters" in store.reports[0]["body"]
    assert "JPM" in store.reports[0]["body"]
    assert "https://" in store.reports[0]["body"]
