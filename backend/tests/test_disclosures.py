"""Disclosure feed tests: mapping, filtering, and deterministic enrichment."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.ingestion.disclosures import (
    SOURCE_KIND,
    CongressDisclosureSource,
    enrich_congress_trade,
    maybe_enrich_event,
)
from app.ingestion.source import RawTweet
from app.models.events import CongressTradeEvent, OptionsFlowEvent


def _row(**overrides) -> dict:
    base = {
        "id": "senate_20035392_g5",
        "source_id": "house_clerk",
        "transaction_date": "2026-08-26",
        "filing_date": "2026-09-05",
        "ticker": "VRTX",
        "asset_name": "Vertex Pharmaceuticals Incorporated - Common Stock",
        "transaction_type": "Sale (Full)",
        "amount_range_label": "$1,001 - $15,000",
        "filer_name": "Maxine Waters",
        "branch": "congress",
        "chamber": "senate",
        "party": "D",
        "state": "CA",
        "office": "U.S. Senator · CA",
        "doc_url": "https://efdsearch.senate.gov/2026/09/05/1.pdf",
        "filing_type": "PTR",
    }
    base.update(overrides)
    return base


def _source(rows, **kwargs):
    return CongressDisclosureSource(fetcher=lambda: rows, **kwargs)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------
# Source mapping + filtering
# ---------------------------------------------------------------------------


def test_maps_row_to_traceable_record():
    src = _source([_row(filing_date=_today())])
    result = src.fetch_recent([], 3600)

    assert result.errors == []
    assert len(result.tweets) == 1
    tweet = result.tweets[0]
    assert tweet.tweet_id == "ptr-senate_20035392_g5"
    assert "Maxine Waters" in tweet.text
    assert "$VRTX" in tweet.text
    assert tweet.url == "https://efdsearch.senate.gov/2026/09/05/1.pdf"
    assert tweet.raw_json["source_kind"] == SOURCE_KIND


def test_filters_out_executive_branch_and_stale_filings():
    src = _source(
        [
            _row(id="exec-1", branch="executive", filing_date=_today()),
            _row(id="old-1", filing_date="2020-01-01"),
            _row(id="new-1", filing_date=_today()),
        ]
    )
    result = src.fetch_recent([], 3600)

    assert [t.tweet_id for t in result.tweets] == ["ptr-new-1"]


def test_coerces_mirror_return_fields_to_numbers_issues_25():
    """#25: numeric-looking mirror values become JSON numbers; junk sentinels
    are dropped at ingest so no query ever parses text defensively."""
    src = _source(
        [
            _row(id="ok-1", filing_date=_today(), ret_since="12.5", excess_since="1.2e-5"),
            _row(id="junk-1", filing_date=_today(), ret_since="N/A", excess_since="."),
            _row(id="nan-1", filing_date=_today(), ret_since="nan", excess_since=None),
        ]
    )
    result = src.fetch_recent([], 3600)

    assert result.errors == []
    by_id = {t.tweet_id: t.raw_json for t in result.tweets}
    assert by_id["ptr-ok-1"]["ret_since"] == 12.5
    assert by_id["ptr-ok-1"]["excess_since"] == 1.2e-05
    assert "ret_since" not in by_id["ptr-junk-1"]
    assert "excess_since" not in by_id["ptr-junk-1"]
    assert "ret_since" not in by_id["ptr-nan-1"]
    assert "excess_since" not in by_id["ptr-nan-1"]


def test_mirror_emits_senate_only_issues_9():
    """#9: the mirror covers Senate; Clerk owns House, so House rows are
    dropped here to avoid double events from the two lanes."""
    src = _source(
        [
            _row(id="sen-1", chamber="senate", filing_date=_today()),
            _row(id="rep-1", chamber="house", filing_date=_today()),
            _row(id="mixed", chamber=" House ", filing_date=_today()),
            _row(id="blank", chamber="", filing_date=_today()),
        ]
    )
    result = src.fetch_recent([], 3600)

    assert [t.tweet_id for t in result.tweets] == ["ptr-sen-1"]


def test_skips_rows_missing_ticker_or_filer():
    src = _source(
        [
            _row(id="no-ticker", ticker="", filing_date=_today()),
            _row(id="no-filer", filer_name="", filing_date=_today()),
        ]
    )
    assert src.fetch_recent([], 3600).tweets == []


def test_fetch_failure_returns_errors_not_raises():
    def boom():
        raise RuntimeError("mirror down")

    result = CongressDisclosureSource(fetcher=boom).fetch_recent([], 3600)
    assert result.tweets == []
    assert len(result.errors) == 1


def test_caps_at_max_rows_newest_first():
    rows = [_row(id=f"r-{i}", filing_date=_today()) for i in range(10)]
    src = _source(rows, max_rows=3)
    result = src.fetch_recent([], 3600)
    assert len(result.tweets) == 3


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


def _tweet(filer="Maxine Waters", **meta_overrides) -> RawTweet:
    meta = {"source_kind": SOURCE_KIND, "filer_name": filer, "party": "D",
            "chamber": "house", "ticker": "JPM",
            "transaction_date": "2026-08-01", "amount_range_label": "$1,001 - $15,000"}
    meta.update(meta_overrides)
    return RawTweet(
        tweet_id="ptr-x", author_handle="congress_house", author_id="congress_house",
        text="REPORTED: Rep. Maxine Waters (D-CA, house) sold $JPM shares worth $1,001 - $15,000.",
        created_at=datetime.now(timezone.utc),
        url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/1.pdf",
        raw_json=meta,
    )


def _event() -> CongressTradeEvent:
    return CongressTradeEvent(
        tweet_id="ptr-x", tweet_url="https://x/y", confidence=0.9,
        politician_name="Maxine Waters", politician_id="WRONG",
        chamber="senate", party="R", committee_ids=["MADEUP"],
        ticker="WRONG", transaction_type="sell",
        amount_range="wrong", transaction_date=date(2000, 1, 1),
    )


def test_enrichment_grounds_facts_and_roster_identity():
    enriched = enrich_congress_trade(_tweet(), _event())

    assert enriched.politician_id == "W000187"  # roster Bioguide id, not the model guess
    assert enriched.committee_ids == ["HSBA"]
    assert enriched.ticker == "JPM"
    assert enriched.transaction_date == date(2026, 8, 1)
    assert enriched.amount_range == "$1,001 - $15,000"
    assert enriched.party == "D"
    assert enriched.chamber == "house"
    assert enriched.confidence == 0.9  # the model's own score is untouched


def test_enrichment_unknown_filer_gets_slug_and_no_committees():
    tweet = _tweet(filer="Somebody Unknown", party="I", chamber="senate")
    event = _event()
    enriched = enrich_congress_trade(tweet, event)

    assert enriched.politician_id == "slug-somebody-unknown"
    assert enriched.committee_ids == []  # no verified membership -> rule cannot fire
    assert enriched.politician_name == "Somebody Unknown"
    assert enriched.party == "I"


def test_enrichment_resolves_new_roster_traders():
    """High-activity filers added to the roster must resolve to BioGuide ids
    with committees — previously they fell through to slugs (live split-brain:
    roster 'Ro Khanna' vs filing 'Rohit Khanna')."""
    cases = [
        ("Michael Rulli", "R000619", ["HSIF"]),
        ("Cleo Fields", "F000110", ["HSBA"]),
        ("Gilbert Cisneros", "C001123", ["HSAS"]),
        ("Josh Gottheimer", "G000583", ["HSBA"]),
        ("April McClain Delaney", "M001232", ["HSAG"]),
        ("Cory A Booker", "B001288", ["SSAF"]),
        ("Rohit Khanna", "K000375", ["HSAS"]),
    ]
    for name, pid, committees in cases:
        enriched = enrich_congress_trade(_tweet(filer=name), _event())
        assert enriched.politician_id == pid, name
        assert enriched.committee_ids == committees, name


def test_maybe_enrich_is_noop_without_marker():
    tweet = _tweet()
    tweet.raw_json = {}
    event = _event()
    assert maybe_enrich_event(tweet, event) is event


def test_maybe_enrich_is_noop_for_non_congress_events():
    tweet = _tweet()
    options = OptionsFlowEvent(
        tweet_id="ptr-x", tweet_url="https://x/y", confidence=0.9,
        ticker="JPM", contract_type="call",
    )
    assert maybe_enrich_event(tweet, options) is options


def test_maybe_enrich_never_raises():
    tweet = _tweet()
    tweet.raw_json = {"source_kind": SOURCE_KIND}  # minimal meta
    event = _event()
    assert maybe_enrich_event(tweet, event) is not None


# ---------------------------------------------------------------------------
# Deterministic row helpers
# ---------------------------------------------------------------------------


def test_stable_id_falls_back_to_hash_without_row_id():
    from app.ingestion.disclosures_parse import _stable_id

    row = _row().copy()
    row.pop("id")
    sid = _stable_id(row)
    assert sid.startswith("ptr-")
    assert sid == _stable_id(row)  # deterministic per (filer, ticker, dates)
    assert sid != _stable_id({**row, "ticker": "NVDA"})


def test_side_verb_covers_buy_sell_exchange_and_unknown():
    from app.ingestion.disclosures_parse import _side_verb

    assert _side_verb("Purchase") == "purchased"
    assert _side_verb("buy") == "purchased"
    assert _side_verb("Sale (Full)") == "sold"
    assert _side_verb("Sell") == "sold"
    assert _side_verb("Exchange") == "exchanged"
    assert _side_verb("transfer") == "traded"


def test_parse_day_rejects_garbage():
    from app.ingestion.disclosures_parse import _parse_day

    assert _parse_day("not-a-date") is None
    assert _parse_day("2026-13-40") is None
    assert _parse_day("2026-09-05") == date(2026, 9, 5)


def test_non_list_fetcher_surfaces_as_source_error():
    """A mirror endpoint returning a non-list (e.g. an error envelope) must
    surface through FetchResult.errors, not crash the poll."""
    src = _source({"error": "oops"})
    result = src.fetch_recent([], 3600)

    assert result.tweets == []
    assert len(result.errors) == 1
    assert "expected a list" in result.errors[0]


# ---------------------------------------------------------------------------
# Feed resilience + download path
# ---------------------------------------------------------------------------


def test_malformed_row_is_skipped_not_crash(monkeypatch):
    """A row that explodes inside the record builder is skipped; the rest of
    the poll still emits its records (and the failure is not an error)."""
    import app.ingestion.disclosures as disclosures
    import app.ingestion.disclosures_parse as disclosures_parse

    good = _row(transaction_date="2026-08-26")
    broken = _row(transaction_date="2026-08-26", id="broken-row")

    real = disclosures_parse._record

    def flaky(row, now):
        if row.get("id") == "broken-row":
            raise AttributeError("unexpected field type")
        return real(row, now)

    # The source resolves `_record` in its own module (L5), so patch there.
    monkeypatch.setattr(disclosures, "_record", flaky)
    src = _source([broken, good], lookback_days=30)
    result = src.fetch_recent([], 3600)

    assert len(result.tweets) == 1
    assert result.tweets[0].tweet_id == "ptr-senate_20035392_g5"
    assert result.errors == []


def test_cached_rows_reused_within_ttl():
    from app.ingestion.disclosures import CongressDisclosureSource

    calls = {"n": 0}

    def fetcher():
        calls["n"] += 1
        return [_row(transaction_date="2026-08-26")]

    src = CongressDisclosureSource(fetcher=fetcher, lookback_days=30)
    src.fetch_recent([], 3600)
    src.fetch_recent([], 3600)

    assert calls["n"] == 1  # second poll served from the TTL'd cache


def test_fetch_mirror_uses_httpx(monkeypatch):
    import httpx

    from app.ingestion.disclosures import CongressDisclosureSource

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"id": "senate_1"}]

    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    src = CongressDisclosureSource(
        mirror_url="https://example.com/trades.json", timeout_seconds=5
    )

    assert src._fetch_mirror() == [{"id": "senate_1"}]
    assert "trades.json" in seen["url"]


def test_maybe_enrich_event_never_raises_on_enrichment_error(monkeypatch):
    """Even if grounding itself crashes, maybe_enrich_event keeps the model's
    output and never raises."""
    def boom(tweet, event):
        raise RuntimeError("grounding failed")

    monkeypatch.setattr("app.ingestion.disclosures.enrich_congress_trade", boom)
    tweet = _tweet()
    event = _event()

    assert maybe_enrich_event(tweet, event) is event
