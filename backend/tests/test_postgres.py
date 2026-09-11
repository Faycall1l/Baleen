"""Postgres store tests: leaderboard ranking over filing returns."""

from __future__ import annotations

from datetime import datetime, timezone

from app.ingestion.source import RawTweet


def _ptr(tweet_id: str, filer: str, ticker: str, ret: float | None) -> RawTweet:
    meta: dict = {
        "source_kind": "congress_disclosure",
        "filer_name": filer,
        "ticker": ticker,
        "transaction_type": "Purchase",
        "transaction_date": "2026-08-01",
        "amount_range_label": "$1,001 - $15,000",
    }
    if ret is not None:
        meta["ret_since"] = ret
        meta["excess_since"] = round(ret - 1.0, 2)
    return RawTweet(
        tweet_id=tweet_id,
        author_handle="congress_house",
        author_id="congress_house",
        text=f"{filer} bought {ticker}",
        created_at=datetime.now(timezone.utc),
        url="https://disclosures-clerk.house.gov/x.pdf",
        raw_json=meta,
    )


def test_leaderboard_orders_by_return_since(postgres_store):
    postgres_store.insert_raw_tweets(
        [
            _ptr("ptr-lb-1", "Zed Zed", "AAA", 5.0),
            _ptr("ptr-lb-2", "Amy Amy", "BBB", 42.5),
            _ptr("ptr-lb-3", "No Data", "CCC", None),
        ]
    )

    leaders = postgres_store.get_leaderboard(limit=10)

    assert [r["politician"] for r in leaders] == ["Amy Amy", "Zed Zed"]
    assert leaders[0]["ticker"] == "BBB"
    assert leaders[0]["ret_pct"] == 42.5
    assert leaders[0]["source_url"].endswith("x.pdf")
