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
        "id": "house_20035392_g5",
        "source_id": "house_clerk",
        "transaction_date": "2026-08-26",
        "filing_date": "2026-09-05",
        "ticker": "VRTX",
        "asset_name": "Vertex Pharmaceuticals Incorporated - Common Stock",
        "transaction_type": "Sale (Full)",
        "amount_range_label": "$1,001 - $15,000",
        "filer_name": "Maxine Waters",
        "branch": "congress",
        "chamber": "house",
        "party": "D",
        "state": "CA",
        "office": "U.S. Representative · CA-43",
        "doc_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035392.pdf",
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
    assert tweet.tweet_id == "ptr-house_20035392_g5"
    assert "Maxine Waters" in tweet.text
    assert "$VRTX" in tweet.text
    assert tweet.url == "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035392.pdf"
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
