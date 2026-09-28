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


def test_chain_failure_records_errors_and_keeps_sweeping():
    def boom(ticker: str):
        raise RuntimeError("429 throttled")

    src = OptionsChainSource(watchlist=["NVDA", "AMD"], fetcher=boom)
    result = src.fetch_recent([], 3600)

    assert result.tweets == []
    assert len(result.errors) == 2  # issue #13: sweep continues, both tried
    assert "NVDA" in result.errors[0] and "AMD" in result.errors[1]


def test_dead_ticker_does_not_starve_following_tickers_issues_13():
    def fetch(ticker: str):
        if ticker == "ZZZZ":
            raise RuntimeError("delisted 404")
        return {"data": {"options": [
            {"option": "NVDA260909C00110000", "volume": 5000, "open_interest": 500,
             "last_trade_price": 10.0, "last_trade_time": "2026-09-09T15:12:25"}
        ]}}

    src = OptionsChainSource(watchlist=["ZZZZ", "NVDA"], fetcher=fetch)
    result = src.fetch_recent([], 3600)

    assert len(result.errors) == 1 and "ZZZZ" in result.errors[0]
    assert {t.tweet_id.split("-")[1] for t in result.tweets} == {"NVDA"}


def test_recent_failure_is_not_re_downloaded_immediately_issues_13():
    calls = {"n": 0}

    def flaky(ticker: str):
        calls["n"] += 1
        raise RuntimeError("down")

    src = OptionsChainSource(watchlist=["NVDA", "AMD"], fetcher=flaky)
    src.fetch_recent([], 3600)
    src.fetch_recent([], 3600)

    assert calls["n"] == 2  # one hit per ticker; cached failures not re-hit


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


# ---------------------------------------------------------------------------
# Helper + cache defensive branches
# ---------------------------------------------------------------------------


def test_parse_occ_rejects_impossible_expiry():
    assert parse_occ("NVDA999999C00110000") is None


def test_anomaly_rule_skips_low_ratio_and_unparsable_contracts():
    from app.ingestion.options import _anomalies

    day = "2026-09-09"
    contracts = [
        _contract("BADSYM", volume=9000, oi=2_000_000),   # ratio 0.0045 < min
        _contract("bogus", volume=9000, oi=100),          # unparsable symbol
    ]
    assert _anomalies(contracts, day) == []

    ratio_ok = _contract("NVDA260909C00110000", volume=9000, oi=100)
    assert _anomalies([ratio_ok], day)[0]["ticker"] == "NVDA"


def test_cached_chain_hit_skips_fetcher():
    payload = _payload([_contract("NVDA260909C00110000", volume=5000, oi=500)])
    calls = {"n": 0}

    def fetch(ticker: str):
        calls["n"] += 1
        return payload

    src = OptionsChainSource(watchlist=["NVDA"], fetcher=fetch)
    src.fetch_recent([], 3600)
    src.fetch_recent([], 3600)

    assert calls["n"] == 1  # first poll cached; second reused TTL'd chain


def test_non_dict_chain_payload_is_a_fetch_error():
    def bad(ticker: str):
        return ["not", "a", "payload"]

    src = OptionsChainSource(watchlist=["NVDA"], fetcher=bad)
    result = src.fetch_recent([], 3600)

    assert result.tweets == []
    assert len(result.errors) == 1 and "NVDA" in result.errors[0]
    # the failure is held in the negative cache, so the immediate re-poll
    # reports the hold-off instead of re-downloading
    assert "holding off" in src.fetch_recent([], 3600).errors[0]


def test_live_fetch_chain_uses_httpx(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"options": []}}

    src = OptionsChainSource(watchlist=["NVDA"], timeout_seconds=5)

    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **k: _Resp())
    assert src._fetch_chain("NVDA") == {"data": {"options": []}}
