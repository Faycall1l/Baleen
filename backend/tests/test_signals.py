"""Signals engine tests: pressure aggregates and inferred links."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.graph.writer import GraphWriter
from app.ingestion.source import RawTweet
from app.models.events import CongressTradeEvent, OptionsFlowEvent
from app.signals.engine import SignalsEngine


def _engine(neo4j_store, postgres_store):
    from app.db.postgres import PostgresStore as _PS

    assert isinstance(postgres_store, _PS)
    return SignalsEngine(neo4j_store, postgres_store)


def _trade(tweet_id: str, pid: str, name: str, ticker: str, side: str) -> CongressTradeEvent:
    return CongressTradeEvent(
        tweet_id=tweet_id,
        tweet_url=f"https://x.com/congress_trades/status/{tweet_id}",
        confidence=0.95,
        politician_name=name,
        politician_id=pid,
        chamber="house" if pid == "W000187" else "senate",
        party="D",
        committee_ids=["HSBA"] if pid == "W000187" else ["SSBK"],
        ticker=ticker,
        transaction_type=side,
        amount_range="$1,001 - $15,000",
        transaction_date=date(2026, 9, 1),
        event_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )


def _option(
    tweet_id: str,
    ticker: str,
    side: str,
    volume: int,
    open_interest: int,
) -> OptionsFlowEvent:
    # Fixed event_time: deterministic_id hashes it, so `now()` here would
    # make every call a different event (and the writer MERGE a duplicate).
    return OptionsFlowEvent(
        tweet_id=tweet_id,
        tweet_url=f"https://finance.yahoo.com/quote/{ticker}",
        confidence=0.95,
        ticker=ticker,
        contract_type=side,
        strike=130.0,
        expiration=date(2026, 10, 17),
        premium=2.5,
        volume=volume,
        open_interest=open_interest,
        sentiment="bullish" if side == "call" else "bearish",
        event_time=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )


def _cleanup(neo4j_store, *tweet_ids: str) -> None:
    neo4j_store.run(
        "MATCH (e:Event) WHERE e.tweet_id IN $ids DETACH DELETE e",
        ids=list(tweet_ids),
    )


def test_bundle_returns_all_sections(neo4j_store, postgres_store):
    bundle = _engine(neo4j_store, postgres_store).bundle(window_days=365)

    assert bundle["window_days"] == 365
    for key in ("momentum", "clusters", "options_meter", "uncovered", "suspicious"):
        assert isinstance(bundle[key], list)


def test_momentum_counts_buys_and_sells(neo4j_store, postgres_store):
    writer = GraphWriter(neo4j_store)
    writer.write_event(_trade("sig-mo-1", "W000187", "Maxine Waters", "NVDA", "buy"))
    writer.write_event(_trade("sig-mo-2", "W000817", "Elizabeth Warren", "NVDA", "sell"))

    rows = {r["ticker"]: r for r in _engine(neo4j_store, postgres_store).momentum("2026-01-01")}
    assert rows["NVDA"]["buys"] >= 1
    assert rows["NVDA"]["sells"] >= 1

    _cleanup(neo4j_store, "sig-mo-1", "sig-mo-2")


def test_momentum_excludes_untagged_events(neo4j_store, postgres_store):
    """Events without a ticker link must not surface as null-ticker rows."""
    _cleanup(neo4j_store, "sig-mo-untagged")
    neo4j_store.run(
        "CREATE (e:Event {tweet_id: 'sig-mo-untagged', type: 'congress_trade', "
        "transaction_date: date('2026-09-01'), transaction_type: 'buy'})"
    )
    try:
        rows = _engine(neo4j_store, postgres_store).momentum("2026-01-01")
        assert all(r["ticker"] for r in rows)
    finally:
        _cleanup(neo4j_store, "sig-mo-untagged")


def test_flow_returns_prints_ranked_by_volume_ratio(neo4j_store, postgres_store):
    # Defensive pre-cleanup: an earlier aborted run may have left stale
    # twins behind (cleanup below only runs on success).
    _cleanup(neo4j_store, "sig-flow-high", "sig-flow-low")
    writer = GraphWriter(neo4j_store)
    writer.write_event(_option("sig-flow-high", "NVDA", "call", 5000, 1000))
    writer.write_event(_option("sig-flow-low", "NVDA", "call", 1200, 1000))

    rows = _engine(neo4j_store, postgres_store).flow(min_ratio=2.0, limit=10)
    assert [r["event_id"] for r in rows if r["ticker"] == "NVDA"][0].endswith(
        _option("sig-flow-high", "NVDA", "call", 5000, 1000).deterministic_id
    )
    high = next(r for r in rows if r["ticker"] == "NVDA" and r["volume"] == 5000)
    assert high["side"] == "call"
    assert high["ratio"] == 5.0
    assert high["strike"] == 130.0
    assert high["sentiment"] == "bullish"
    assert all(r["ratio"] >= 2.0 for r in rows)

    _cleanup(neo4j_store, "sig-flow-high", "sig-flow-low")


def test_flow_filters_by_side_and_ticker(neo4j_store, postgres_store):
    _cleanup(neo4j_store, "sig-flow-call", "sig-flow-put")
    writer = GraphWriter(neo4j_store)
    writer.write_event(_option("sig-flow-call", "NVDA", "call", 5000, 1000))
    writer.write_event(_option("sig-flow-put", "NVDA", "put", 6000, 1000))

    engine = _engine(neo4j_store, postgres_store)
    puts = [r for r in engine.flow(side="put", min_ratio=2.0) if r["ticker"] == "NVDA"]
    assert {r["side"] for r in puts} == {"put"}
    assert all(
        r["ticker"] == "NVDA"
        for r in engine.flow(tickers=["NVDA"], min_ratio=2.0, limit=10)
    )
    assert engine.flow(tickers=["ZZZ"], min_ratio=0, limit=10) == []

    _cleanup(neo4j_store, "sig-flow-call", "sig-flow-put")


def test_similar_finds_shared_ticker_traders(neo4j_store, postgres_store):
    writer = GraphWriter(neo4j_store)
    writer.write_event(_trade("sig-sim-1", "W000187", "Maxine Waters", "NVDA", "buy"))
    writer.write_event(_trade("sig-sim-2", "W000817", "Elizabeth Warren", "NVDA", "buy"))

    similar = _engine(neo4j_store, postgres_store).similar("W000187")
    ids = [r["id"] for r in similar]
    assert "W000817" in ids

    _cleanup(neo4j_store, "sig-sim-1", "sig-sim-2")


def test_suspicious_timing_ranks_by_excess_return(postgres_store):
    def _ptr(tid: str, filer: str, excess: float | None) -> RawTweet:
        meta: dict = {
            "source_kind": "congress_disclosure", "filer_name": filer,
            "ticker": "NVDA", "transaction_type": "Purchase",
            "transaction_date": "2026-08-01", "amount_range_label": "$1 - $2",
        }
        if excess is not None:
            meta["excess_since"] = excess
        return RawTweet(
            tweet_id=tid, author_handle="congress_house", author_id="congress_house",
            text="x", created_at=datetime.now(timezone.utc),
            url="https://x.com/y", raw_json=meta,
        )

    postgres_store.insert_raw_tweets(
        [_ptr("ptr-sig-1", "Low Guy", 3.0), _ptr("ptr-sig-2", "High Gal", 44.0)]
    )

    ranked = SignalsEngine(None, postgres_store).suspicious_timing(min_excess_pct=1.0)
    assert [r["who"] for r in ranked[:2]] == ["High Gal", "Low Guy"]


def test_suspicious_timing_survives_junk_excess_values_issues_10(postgres_store):
    """#10: malformed excess_since strings must not silently kill the signal."""
    junk = RawTweet(
        tweet_id="ptr-sig-junk", author_handle="congress_house", author_id="congress_house",
        text="x", created_at=datetime.now(timezone.utc),
        url="https://x.com/y",
        raw_json={
            "source_kind": "congress_disclosure", "filer_name": "Junk Gal",
            "ticker": "NVDA", "transaction_type": "Purchase",
            "transaction_date": "2026-08-01", "amount_range_label": "$1 - $2",
            "excess_since": ".",
        },
    )
    postgres_store.insert_raw_tweets([junk])

    ranked = SignalsEngine(None, postgres_store).suspicious_timing(min_excess_pct=1.0)

    assert all(r["who"] != "Junk Gal" for r in ranked)


def test_suspicious_timing_skips_null_ticker_or_filer(postgres_store):
    """Rows missing ticker/filer must not leave the backend (the UI used to
    filter them; now they never ship)."""
    base: dict = {
        "source_kind": "congress_disclosure",
        "transaction_type": "Purchase",
        "transaction_date": "2026-08-01",
        "amount_range_label": "$1 - $2",
        "excess_since": 99.0,
    }
    postgres_store.insert_raw_tweets(
        [
            RawTweet(
                tweet_id="ptr-sig-noticker", author_handle="h", author_id="h",
                text="x", created_at=datetime.now(timezone.utc),
                url="https://x.com/y", raw_json={**base, "filer_name": "No Ticker"},
            ),
            RawTweet(
                tweet_id="ptr-sig-nofiler", author_handle="h", author_id="h",
                text="x", created_at=datetime.now(timezone.utc),
                url="https://x.com/y", raw_json={**base, "ticker": "NVDA"},
            ),
        ]
    )

    ranked = SignalsEngine(None, postgres_store).suspicious_timing(min_excess_pct=1.0)

    assert all(r["ticker"] for r in ranked)
    assert all(r["who"] for r in ranked)


# ---------------------------------------------------------------------------
# Degradation: every signal degrades to [] when its store is down, and the
# bundle shape stays intact so the UI never gets a 500 from a query hiccup.
# ---------------------------------------------------------------------------


class _Neo4jDown:
    def run(self, *a, **k):
        raise RuntimeError("neo4j down")

    def run_read(self, *a, **k):
        raise RuntimeError("neo4j down")


class _PostgresDown:
    class engine:
        class connect:
            def __enter__(self):
                raise RuntimeError("postgres down")

            def __exit__(self, *a):
                pass

    def __init__(self, *a, **k):
        pass


def _down_engine():
    return SignalsEngine(_Neo4jDown(), _PostgresDown())


def test_signal_queries_degrade_to_empty_list_when_neo4j_down():
    engine = _down_engine()
    assert engine.momentum("2026-01-01") == []
    assert engine.clusters("2026-01-01") == []
    assert engine.options_meter() == []
    assert engine.uncovered() == []
    assert engine.similar("W000187") == []
    assert engine.flow() == []


def test_suspicious_timing_degrades_to_empty_list_when_postgres_down():
    assert _down_engine().suspicious_timing() == []


def test_bundle_stays_shaped_when_all_backends_down():
    bundle = _down_engine().bundle(window_days=90)
    assert bundle["window_days"] == 90
    for key in ("momentum", "clusters", "options_meter", "uncovered", "suspicious"):
        assert bundle[key] == []


def test_person_merges_identity_committees_and_trades():
    """The card joins identity, memberships, and latest trades in Python so
    the route stays a thin shape over two indexed reads."""

    class _FakeNeo4j:
        def run_read(self, query, **params):
            if "MEMBER_OF" in query:
                assert params == {"pid": "W000817"}
                return [
                    {
                        "id": "W000817",
                        "name": "Elizabeth Warren",
                        "chamber": "senate",
                        "party": "D",
                        "committees": [
                            {"id": "SSBK", "name": "Banking"},
                            {"id": None, "name": None},
                        ],
                    }
                ]
            return [
                {
                    "event_id": "evt_1",
                    "ticker": "NVDA",
                    "side": "sell",
                    "date": "2026-09-01",
                    "amount": "$1,001 - $15,000",
                    "source_url": "https://x.com/y",
                },
                {"event_id": None, "ticker": None},
            ]

    person = SignalsEngine(_FakeNeo4j(), None).person("W000817")
    assert person["name"] == "Elizabeth Warren"
    assert person["committees"] == [{"id": "SSBK", "name": "Banking"}]
    assert [t["ticker"] for t in person["trades"]] == ["NVDA"]


def test_person_returns_none_when_unknown_or_down():
    class _EmptyNeo4j:
        def run_read(self, query, **params):
            return []

    assert SignalsEngine(_EmptyNeo4j(), None).person("NOPE") is None
    assert _down_engine().person("W000817") is None


def test_ticker_merges_sector_trades_and_flow():
    """The ticker card joins sector, latest trades, and latest prints."""

    class _FakeNeo4j:
        def run_read(self, query, **params):
            assert params == {"sym": "NVDA"}
            if "IN_SECTOR" in query:
                return [{"ticker": "NVDA", "sector": "Technology"}]
            if "congress_trade" in query:
                return [
                    {
                        "event_id": "evt_t",
                        "kind": "congress_trade",
                        "who": "Jane Doe",
                        "side": "buy",
                        "date": "2026-09-01",
                        "amount": "$1 - $2",
                        "source_url": "https://x.com/y",
                    },
                    {"event_id": None},
                ]
            return [
                {
                    "event_id": "evt_f",
                    "side": "call",
                    "strike": 130.0,
                    "expiration": "2026-10-17",
                    "volume": 5000,
                    "open_interest": 1000,
                    "ratio": 5.0,
                    "sentiment": "bullish",
                }
            ]

    card = SignalsEngine(_FakeNeo4j(), None).ticker("nvda")
    assert card["ticker"] == "NVDA"
    assert card["sector"] == "Technology"
    assert [t["event_id"] for t in card["trades"]] == ["evt_t"]
    assert [f["event_id"] for f in card["flow"]] == ["evt_f"]


def test_ticker_returns_none_when_unknown_or_blank_or_down():
    class _EmptyNeo4j:
        def run_read(self, query, **params):
            return []

    engine = SignalsEngine(_EmptyNeo4j(), None)
    assert engine.ticker("ZZZ") is None
    assert engine.ticker("  ") is None
    assert _down_engine().ticker("NVDA") is None


def test_committee_merges_sectors_members_and_trades():
    """The committee card joins overseen sectors, roster, and member trades."""

    class _FakeNeo4j:
        def run_read(self, query, **params):
            assert params == {"cid": "HSBA"}
            if "OVERSEES" in query:
                return [
                    {
                        "id": "HSBA",
                        "name": "Financial Services",
                        "sectors": [{"name": "Financials"}, {"name": None}],
                    }
                ]
            # NB: the trades query also mentions MEMBER_OF, so it matches first.
            if "congress_trade" in query:
                return [
                    {
                        "event_id": "evt_c",
                        "who": "Maxine Waters",
                        "politician_id": "W000187",
                        "ticker": "NVDA",
                        "side": "buy",
                        "date": "2026-09-01",
                        "amount": "$1 - $2",
                        "source_url": "https://x.com/y",
                    },
                    {"event_id": None},
                ]
            return [
                {"id": "W000187", "name": "Maxine Waters", "chamber": "house", "party": "D"},
                {"name": "Nameless"},
            ]

    card = SignalsEngine(_FakeNeo4j(), None).committee("hsba")
    assert card["name"] == "Financial Services"
    assert card["sectors"] == ["Financials"]
    assert [m["id"] for m in card["members"]] == ["W000187"]
    assert [t["event_id"] for t in card["trades"]] == ["evt_c"]


def test_committee_returns_none_when_unknown_or_blank_or_down():
    class _EmptyNeo4j:
        def run_read(self, query, **params):
            return []

    engine = SignalsEngine(_EmptyNeo4j(), None)
    assert engine.committee("ZZZ") is None
    assert engine.committee("  ") is None
    assert _down_engine().committee("HSBA") is None
