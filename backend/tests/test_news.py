"""News monitor tests: feed parsing, source polling, mentions, store read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.ingestion import news as news_module
from app.ingestion.news import Feed, NewsSource
from app.ingestion.news_parse import extract_mentions, parse_date, parse_feed
from app.ingestion.source import RawTweet

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>SEC charges insider over $NVDA trades</title>
    <description>&lt;p&gt;Senator Elizabeth Warren commented.&lt;/p&gt;</description>
    <link>https://example.test/a</link>
    <pubDate>Wed, 30 Oct 2026 12:00:00 GMT</pubDate>
    <guid>a-1</guid>
  </item>
  <item>
    <title>No link and no date</title>
    <guid>a-2</guid>
  </item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Fed holds rates steady</title>
    <summary>Policymakers left the target range unchanged.</summary>
    <link rel="alternate" href="https://example.test/b"/>
    <updated>2026-10-30T12:00:00Z</updated>
    <id>b-1</id>
  </entry>
</feed>"""


def _response(payload: bytes):
    class _Resp:
        content = payload

        def raise_for_status(self) -> None:
            return None

    return _Resp()


def test_parse_rss_keeps_title_summary_link_and_date():
    articles = parse_feed(RSS)

    assert len(articles) == 2
    assert articles[0]["title"] == "SEC charges insider over $NVDA trades"
    assert articles[0]["summary"] == "Senator Elizabeth Warren commented."
    assert articles[0]["url"] == "https://example.test/a"
    assert articles[0]["guid"] == "a-1"
    assert parse_date(articles[0]["published"]) == datetime(2026, 10, 30, 12, tzinfo=UTC)


def test_parse_atom_uses_href_and_iso_date():
    articles = parse_feed(ATOM)

    assert len(articles) == 1
    assert articles[0]["url"] == "https://example.test/b"
    assert articles[0]["summary"].startswith("Policymakers")
    assert parse_date(articles[0]["published"]) == datetime(2026, 10, 30, 12, tzinfo=UTC)


def test_parse_date_rejects_garbage_and_assumes_utc_for_naive():
    assert parse_date("not a date") is None
    assert parse_date("") is None
    assert parse_date("2026-10-30T12:00:00") == datetime(2026, 10, 30, 12, tzinfo=UTC)


def test_mentions_are_deterministic_and_ignore_unknown_cashtags():
    mentions = extract_mentions(
        "Elizabeth Warren questions $NVDA and $AMD",
        "A $BOGUS tag is not a ticker. Maxine Waters agreed.",
    )

    ids = [m["id"] for m in mentions]
    assert ids == ["NVDA", "AMD", "W000187", "W000817"]
    assert mentions[0] == {"kind": "ticker", "id": "NVDA", "label": "NVDA"}


def test_source_marks_news_and_isolates_a_failing_feed(monkeypatch):
    def fake_get(url, **kwargs):
        if "broken" in url:
            raise RuntimeError("feed down")
        return _response(RSS)

    monkeypatch.setattr(news_module.httpx, "get", fake_get)
    source = NewsSource(
        feeds=(
            Feed("good", "Good", "https://example.test/good"),
            Feed("broken", "Broken", "https://example.test/broken"),
        ),
        poll_seconds=0,
    )

    result = source.fetch_recent([], 3600)

    assert len(result.errors) == 1 and "Broken" in result.errors[0]
    assert [t.tweet_id for t in result.tweets] == ["news:good:a-1", "news:good:a-2"]
    assert all(t.raw_json["source_kind"] == "news" for t in result.tweets)
    assert all(t.author_handle == "good" for t in result.tweets)


def test_source_skips_poll_until_its_interval(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _response(RSS)

    monkeypatch.setattr(news_module.httpx, "get", fake_get)
    source = NewsSource(feeds=(Feed("good", "Good", "https://example.test/good"),), poll_seconds=3600)

    assert len(source.fetch_recent([], 3600).tweets) == 2
    assert source.fetch_recent([], 3600).tweets == []
    assert len(calls) == 1


def test_source_retries_after_total_outage(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        raise RuntimeError("down")

    monkeypatch.setattr(news_module.httpx, "get", fake_get)
    source = NewsSource(feeds=(Feed("broken", "Broken", "https://example.test/x"),), poll_seconds=3600)

    assert source.fetch_recent([], 3600).errors
    assert source.fetch_recent([], 3600).errors
    assert len(calls) == 2


def test_source_drops_articles_older_than_the_window(monkeypatch):
    old = b"""<rss><channel><item><title>Old</title><link>https://example.test/old</link>
    <guid>old</guid><pubDate>Mon, 01 Jan 2001 00:00:00 GMT</pubDate></item></channel></rss>"""
    monkeypatch.setattr(news_module.httpx, "get", lambda *a, **k: _response(old))
    source = NewsSource(feeds=(Feed("good", "Good", "https://example.test/good"),), window_seconds=3600)

    assert source.fetch_recent([], 3600).tweets == []


def _article(tweet_id: str, created: datetime, outlet: str, title: str, summary: str) -> RawTweet:
    return RawTweet(
        tweet_id=tweet_id,
        author_handle=outlet.lower(),
        author_id=outlet.lower(),
        text=f"{title}. {summary}",
        created_at=created,
        url=f"https://example.test/{tweet_id}",
        raw_json={"source_kind": "news", "outlet": outlet, "title": title, "summary": summary},
    )


def test_get_news_is_newest_first_and_adds_mentions(postgres_store):
    now = datetime.now(UTC)
    postgres_store.insert_raw_tweet(_article("news:cnbc:old", now - timedelta(hours=2), "CNBC", "Old", "nothing"))
    postgres_store.insert_raw_tweet(_article("news:cnbc:new", now, "CNBC", "Elizabeth Warren on $NVDA", "Summary"))

    rows = postgres_store.get_news(limit=10)

    assert [r["id"] for r in rows] == ["news:cnbc:new", "news:cnbc:old"]
    assert rows[0]["outlet"] == "CNBC"
    assert rows[0]["mentions"][0]["id"] == "NVDA"
    assert rows[0]["published_at"].startswith(now.date().isoformat())


def test_get_news_filters_by_outlet(postgres_store):
    now = datetime.now(UTC)
    postgres_store.insert_raw_tweet(_article("news:cnbc:1", now, "CNBC", "One", ""))
    postgres_store.insert_raw_tweet(_article("news:sec:2", now, "SEC", "Two", ""))

    rows = postgres_store.get_news(limit=10, outlet="sec")

    assert [r["id"] for r in rows] == ["news:sec:2"]
