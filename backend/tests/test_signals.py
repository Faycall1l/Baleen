"""Signals engine tests: pressure aggregates and inferred links."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.graph.writer import GraphWriter
from app.ingestion.source import RawTweet
from app.models.events import CongressTradeEvent
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


def test_suspicious_timing_degrades_to_empty_list_when_postgres_down():
    assert _down_engine().suspicious_timing() == []


def test_bundle_stays_shaped_when_all_backends_down():
    bundle = _down_engine().bundle(window_days=90)
    assert bundle["window_days"] == 90
    for key in ("momentum", "clusters", "options_meter", "uncovered", "suspicious"):
        assert bundle[key] == []
