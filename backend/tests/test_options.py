"""Options radar tests: OCC parsing, anomaly rule, sweep behavior."""

from __future__ import annotations

from app.ingestion.options import (
    SOURCE_KIND,
    OptionsChainSource,
    parse_occ,
)


def _payload(contracts: list[dict]) -> dict:
    return {"data": {"options": contracts}}


def _contract(symbol: str, volume: float, oi: float, last: float = 10.0) -> dict:
    return {
        "option": symbol,
        "volume": volume,
        "open_interest": oi,
        "last_trade_price": last,
        "last_trade_time": "2026-09-09T15:12:25",
    }


def test_parse_occ_splits_symbol():
    assert parse_occ("NVDA260909C00110000") == ("NVDA", "2026-09-09", "C", 110.0)
    assert parse_occ("AMD261016P00180000") == ("AMD", "2026-10-16", "P", 180.0)
    assert parse_occ("BRK B260918C00450000") == ("BRK B", "2026-09-18", "C", 450.0)
    assert parse_occ("bogus") is None
    assert parse_occ("") is None


def test_flags_unusual_volume_not_quiet_contracts():
    payload = _payload(
        [
            _contract("NVDA260909C00110000", volume=7500, oi=1200),   # 6.2x -> flag
            _contract("NVDA260909P00110000", volume=100, oi=50000),   # quiet
            _contract("NVDA260909C00115000", volume=400, oi=10),      # below abs bar
        ]
    )
    src = OptionsChainSource(watchlist=["NVDA"], fetcher=lambda ticker: payload)
    result = src.fetch_recent([], 3600)

    assert result.errors == []
    assert len(result.tweets) == 1
    tweet = result.tweets[0]
    assert tweet.tweet_id == "opt-NVDA-2026-09-09-110-C-" + tweet.created_at.date().isoformat()
    assert "$NVDA" in tweet.text and "calls" in tweet.text
    assert "delayed" in tweet.text
    assert tweet.raw_json["source_kind"] == SOURCE_KIND
    assert tweet.raw_json["ratio"] == 6.2


def test_caps_at_best_anomalies_per_ticker():
    payload = _payload(
        [_contract(f"NVDA261016C{(100 + i) * 1000:08d}", volume=1000 + i * 100, oi=100) for i in range(10)]
    )
    src = OptionsChainSource(watchlist=["NVDA"], fetcher=lambda ticker: payload)
    assert len(src.fetch_recent([], 3600).tweets) == 3


def test_chain_failure_returns_errors_and_stops_sweep():
    def boom(ticker: str):
        raise RuntimeError("429 throttled")

    src = OptionsChainSource(watchlist=["NVDA", "AMD"], fetcher=boom)
    result = src.fetch_recent([], 3600)

    assert result.tweets == []
    assert len(result.errors) == 1
    assert "NVDA" in result.errors[0]


def test_sweep_across_watchlist_collects_each_ticker():
    payload = _payload([_contract("NVDA260909C00110000", volume=5000, oi=500)])
    seen = []

    def fetch(ticker: str):
        seen.append(ticker)
        return {"data": {"options": [
            dict(payload["data"]["options"][0], option=f"{ticker}260909C00110000")
        ]}}

    src = OptionsChainSource(watchlist=["NVDA", "AMD"], fetcher=fetch)
    result = src.fetch_recent([], 3600)

    assert seen == ["NVDA", "AMD"]
    assert {t.tweet_id.split("-")[1] for t in result.tweets} == {"NVDA", "AMD"}
