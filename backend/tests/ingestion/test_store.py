"""Postgres ingest store: dedup on tweet_id, status machine, rerun safety.

Part of the ingestion suite split out of the former ``tests/test_ingestion.py``.
"""

from __future__ import annotations

from sqlalchemy import text

from app.ingestion.pipeline import IngestionPipeline
from tests.ingestion.conftest import FakeSource, make_tweet


def test_insert_raw_tweet_dedups_on_tweet_id(postgres_store):
    tweet = make_tweet("100")

    assert postgres_store.insert_raw_tweet(tweet) is True
    assert postgres_store.insert_raw_tweet(tweet) is False  # duplicate skipped

    remaining = postgres_store.get_new_tweets(limit=10)
    assert [t.tweet_id for t in remaining] == ["100"]


def test_bulk_insert_counts_new_and_skipped(postgres_store):
    postgres_store.insert_raw_tweet(make_tweet("500"))  # pre-existing

    tweets = [make_tweet("500"), make_tweet("501"), make_tweet("502")]
    inserted, skipped = postgres_store.insert_raw_tweets(tweets)

    assert inserted == 2
    assert skipped == 1
    assert sorted(t.tweet_id for t in postgres_store.get_new_tweets(limit=10)) == [
        "500",
        "501",
        "502",
    ]


def test_bulk_insert_empty_batch_is_noop(postgres_store):
    assert postgres_store.insert_raw_tweets([]) == (0, 0)


def test_schema_is_idempotent_and_serves_new_tweet_query(postgres_store):
    postgres_store.init_schema()  # second run must not fail
    with postgres_store.engine.connect() as conn:
        indexes = conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'raw_tweets'")).scalars().all()
    assert "idx_raw_tweets_status_created" in indexes


def test_reset_status_requeues_tweets_for_processing(postgres_store):
    tweets = [make_tweet("300"), make_tweet("301")]
    for t in tweets:
        postgres_store.insert_raw_tweet(t)

    postgres_store.mark_extracted(["300", "301"])
    assert postgres_store.get_new_tweets() == []

    assert postgres_store.reset_status(["300", "301"]) == 2
    assert sorted(t.tweet_id for t in postgres_store.get_new_tweets()) == ["300", "301"]

    assert postgres_store.reset_status([], "new") == 0


def test_pipeline_rerun_is_idempotent(settings, postgres_store):
    tweets = [make_tweet("200"), make_tweet("201")]
    source = FakeSource(tweets)
    pipeline = IngestionPipeline(source, postgres_store, settings)

    first = pipeline.run_once()
    second = pipeline.run_once()  # same input again

    assert first["inserted"] == 2
    assert second["inserted"] == 0
    assert second["duplicates_skipped"] == 2
    assert len(postgres_store.get_new_tweets()) == 2


def test_pipeline_records_source_errors(settings, postgres_store):
    class ErrorSource:
        def fetch_recent(self, handles, since_seconds):
            from app.ingestion.source import FetchResult

            r = FetchResult()
            r.errors.append("boom")
            return r

    pipeline = IngestionPipeline(ErrorSource(), postgres_store, settings)
    metrics = pipeline.run_once()

    assert metrics["fetched"] == 0
    assert metrics["errors"] == ["boom"]


def test_multi_source_fans_out_and_namespaces_errors():
    from app.ingestion.source import FetchResult, MultiSource

    class ExplodingSource:
        def fetch_recent(self, handles, since_seconds):
            raise RuntimeError("down")

    good = FakeSource([make_tweet("a-1")])
    bad = FakeSource([])
    bad.fetch_recent = lambda handles, since_seconds: FetchResult(tweets=[], errors=["throttled"])

    result = MultiSource([good, bad, ExplodingSource()]).fetch_recent(["h"], 60)

    assert [t.tweet_id for t in result.tweets] == ["a-1"]
    assert len(result.errors) == 2
    assert any("throttled" in e for e in result.errors)
    assert any("ExplodingSource" in e for e in result.errors)
