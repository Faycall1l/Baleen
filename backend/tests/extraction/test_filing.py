"""Deterministic filing events: structured rows skip the LLM."""

from __future__ import annotations

from datetime import UTC, datetime

from app.extraction.filing import event_from_filing_row
from app.ingestion.source import RawTweet


def _tweet(meta: dict, tweet_id: str = "t1") -> RawTweet:
    return RawTweet(
        tweet_id=tweet_id,
        author_handle="edgar_form4",
        author_id="edgar_form4",
        text="x",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        url="https://sec.gov/idx",
        raw_json=meta,
    )


def test_congress_row_builds_without_llm():
    event = event_from_filing_row(
        _tweet(
            {
                "source_kind": "congress_disclosure",
                "filer_name": "Michael Rulli",
                "ticker": "gs",
                "transaction_type": "Sale (Full)",
                "chamber": "house",
                "party": "R",
                "amount_range_label": "$1,001 - $15,000",
                "transaction_date": "2026-09-03",
            }
        )
    )
    assert event is not None
    assert event.event_type == "congress_trade"
    assert event.ticker == "GS"
    assert event.transaction_type == "sell"
    assert event.chamber == "house"
    assert event.confidence == 0.95


def test_insider_row_builds_without_llm():
    event = event_from_filing_row(
        _tweet(
            {
                "source_kind": "insider_filing",
                "filer_name": "Jane Doe",
                "insider_cik": "000123",
                "role": "CEO",
                "ticker": "amd",
                "transaction_type": "Purchase",
                "shares": 100,
                "price": 12.5,
                "transaction_date": "2026-09-02",
            }
        )
    )
    assert event is not None
    assert event.event_type == "insider_trade"
    assert event.insider_id == "123"
    assert event.shares == 100
    assert event.price == 12.5


def test_unknown_kinds_and_thin_rows_fall_back_to_none():
    assert event_from_filing_row(_tweet({"source_kind": "tweet"})) is None
    assert event_from_filing_row(_tweet({"source_kind": "congress_disclosure"})) is None
    assert (
        event_from_filing_row(
            _tweet(
                {
                    "source_kind": "insider_filing",
                    "filer_name": "Jane",
                    "ticker": "TOOLONGTICKERNAME",
                    "transaction_type": "buy",
                }
            )
        )
        is None
    )
    # Never raises, even on hostile-but-valid shapes.
    assert (
        event_from_filing_row(
            _tweet({"source_kind": "insider_filing", "filer_name": 42, "ticker": ["X"], "transaction_type": None})
        )
        is None
    )
