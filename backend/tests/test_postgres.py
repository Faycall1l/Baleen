"""Postgres store tests: leaderboard ranking over filing returns."""

from __future__ import annotations

from datetime import datetime, timezone

from app.ingestion.source import RawTweet


def test_mark_extracted_with_no_ids_is_a_noop(postgres_store):
    """Marking zero tweets extracted must not touch the DB (guard arm of the
    UPDATE helper)."""
    postgres_store.insert_raw_tweet(_ptr("ptr-1", "Some Filer", "AAPL", 0.5))
    postgres_store.mark_extracted([])

    pending = postgres_store.get_new_tweets(limit=10)
    assert [t.tweet_id for t in pending] == ["ptr-1"]


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


def test_leaderboard_ignores_non_numeric_excess_since_issues_10(postgres_store):
    """#10: a junk excess_since must not abort the whole leaderboard query."""
    meta = {
        "source_kind": "congress_disclosure",
        "filer_name": "Bad Row",
        "ticker": "CCC",
        "transaction_type": "Purchase",
        "transaction_date": "2026-08-01",
        "amount_range_label": "$1,001 - $15,000",
        "ret_since": "9.9",
        "excess_since": "N/A",
    }
    row = RawTweet(
        tweet_id="ptr-lb-junk", author_handle="congress_house",
        author_id="congress_house", text="x",
        created_at=datetime.now(timezone.utc),
        url="https://example.com/x.pdf", raw_json=meta,
    )
    postgres_store.insert_raw_tweets([row])

    leaders = postgres_store.get_leaderboard(limit=10)

    assert all(r["politician"] != "Bad Row" for r in leaders)


def test_leaderboard_accepts_scientific_notation_issues_25(postgres_store):
    """#25: a JSONB number in scientific notation must rank, not be dropped."""
    postgres_store.insert_raw_tweets(
        [_ptr("ptr-sci-1", "Sci Gal", "DDD", 1.2e-5)]
    )

    leaders = postgres_store.get_leaderboard(limit=10)

    assert [r["politician"] for r in leaders] == ["Sci Gal"]
    assert leaders[0]["ret_pct"] == 1.2e-5


def test_since_cursors_return_only_newer_rows(postgres_store):
    first = postgres_store.insert_report(
        kind="brief", event_id="e-1", title="t1", body="b1"
    )
    second = postgres_store.insert_report(
        kind="brief", event_id="e-2", title="t2", body="b2"
    )

    assert [r["event_id"] for r in postgres_store.get_reports_since(0)] == ["e-1", "e-2"]
    assert [r["event_id"] for r in postgres_store.get_reports_since(first)] == ["e-2"]
    assert postgres_store.get_reports_since(second) == []

    postgres_store.ensure_rule("test-rule", "desc", {})
    rule_id = next(r["id"] for r in postgres_store.get_active_rules() if r["name"] == "test-rule")
    postgres_store.insert_alert(
        rule_id=rule_id, event_id="ev-a", tweet_id="t", tweet_url="u",
        title="ta", summary="sa",
    )
    assert [a["event_id"] for a in postgres_store.get_alerts_since(0)] == ["ev-a"]


def test_get_reports_filters_by_kind_and_digest_returns_latest(postgres_store):
    postgres_store.insert_report(kind="brief", event_id="e-1", title="t1", body="b1")
    postgres_store.insert_report(kind="digest", period="2026-09-10", title="d1", body="b2")
    postgres_store.insert_report(kind="digest", period="2026-09-11", title="d2", body="b3")

    all_rows = postgres_store.get_reports()
    assert len(all_rows) == 3

    briefs = postgres_store.get_reports(kind="brief")
    assert [r["event_id"] for r in briefs] == ["e-1"]
    assert all(not r["event_id"] for r in postgres_store.get_reports(kind="digest"))

    assert postgres_store.get_digest("2026-09-10")["body"] == "b2"
    assert postgres_store.get_digest("2026-09-11")["title"] == "d2"
    assert postgres_store.get_digest("2025-01-01") is None
