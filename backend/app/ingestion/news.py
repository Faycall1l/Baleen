"""Keyless news monitoring: RSS/Atom headlines into the same raw record table.

No API key and no model. Each entry becomes a ``RawTweet``-shaped record with
``raw_json["source_kind"] == "news"``, so the existing ingestion pipeline
stores it idempotently and ``GET /api/news`` can read it back. The article stays
visible even when the extractor finds no trade event in it — which is the point
of monitoring the media rather than only the filings.

Feeds are polled on their own clock (``NEWS_POLL_SECONDS``, default 15 min)
instead of every scheduler tick: news sites update far slower than filings, and
re-fetching four feeds every five minutes is wasted bandwidth on a small host.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from app.ingestion.news_parse import parse_date, parse_feed
from app.ingestion.source import FetchResult, RawTweet, TweetSource

logger = logging.getLogger(__name__)


#: Deliberately small and keyless. Mix of regulators (SEC, Fed) and political /
#: market media, which is the coverage a disclosure-monitoring desk needs.
@dataclass(frozen=True)
class Feed:
    key: str
    name: str
    url: str


DEFAULT_FEEDS = (
    Feed("sec", "SEC", "https://www.sec.gov/news/pressreleases.rss"),
    Feed("federal-reserve", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    Feed("cnbc", "CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    Feed("politico", "Politico", "https://rss.politico.com/politics-news.xml"),
)

#: Contact string sent to every outlet; SEC in particular requires one.
DEFAULT_USER_AGENT = "Baleen/1.0 (public-record media monitor; contact: admin@example.com)"


class NewsSource(TweetSource):
    """Poll a handful of RSS/Atom feeds for new articles."""

    def __init__(
        self,
        feeds: tuple[Feed, ...] = DEFAULT_FEEDS,
        *,
        timeout_seconds: float = 20.0,
        window_seconds: int = 48 * 3600,
        poll_seconds: int = 15 * 60,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.feeds = feeds
        self.timeout_seconds = timeout_seconds
        self.window_seconds = window_seconds
        self.poll_seconds = poll_seconds
        self.user_agent = user_agent
        self._last_fetch: datetime | None = None

    def _due(self, now: datetime) -> bool:
        if self._last_fetch is None:
            return True
        return (now - self._last_fetch).total_seconds() >= self.poll_seconds

    def fetch_recent(self, handles: list[str], since_seconds: int) -> FetchResult:
        now = datetime.now(UTC)
        if not self._due(now):
            return FetchResult()

        cutoff = now - timedelta(seconds=max(since_seconds, self.window_seconds))
        tweets: list[RawTweet] = []
        errors: list[str] = []

        for feed in self.feeds:
            try:
                response = httpx.get(
                    feed.url,
                    headers={"User-Agent": self.user_agent},
                    timeout=self.timeout_seconds,
                    follow_redirects=True,
                )
                response.raise_for_status()
                articles = parse_feed(response.content)
            except Exception as exc:  # noqa: BLE001 - one feed must not stop the rest
                errors.append(f"{feed.name}: {exc}")
                continue

            for article in articles:
                published = parse_date(article["published"]) or now
                if published < cutoff:
                    continue
                identifier = article["guid"] or article["url"]
                if not identifier:
                    continue
                title = article["title"]
                summary = article["summary"]
                record_id = f"news:{feed.key}:{identifier}"
                if len(record_id) > 240:
                    digest = hashlib.sha1(identifier.encode()).hexdigest()
                    record_id = f"news:{feed.key}:{digest}"
                tweets.append(
                    RawTweet(
                        tweet_id=record_id,
                        author_handle=feed.key,
                        author_id=feed.key,
                        text=f"{title}. {summary}".strip()[:4000],
                        created_at=published,
                        url=article["url"] or identifier,
                        raw_json={
                            "source_kind": "news",
                            "outlet": feed.name,
                            "title": title,
                            "summary": summary,
                        },
                    )
                )

        # Deduplicate within the batch (feeds occasionally repeat an entry).
        unique: dict[str, RawTweet] = {}
        for tweet in tweets:
            unique.setdefault(tweet.tweet_id, tweet)

        # Remember the poll only when at least one feed answered, so a total
        # outage retries on the next tick instead of waiting out the interval.
        if len(errors) < len(self.feeds):
            self._last_fetch = now
        if errors:
            logger.warning("news feeds failed: %s", errors)

        return FetchResult(tweets=list(unique.values()), errors=errors)
