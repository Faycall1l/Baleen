"""Congressional disclosures: the Clerk mirror feed, plus grounding.

Polls the House Clerk's periodic-transaction mirror and emits one record per
trade, then grounds the extracted event in the filed row. The XML/JSON parsing
itself lives in :mod:`app.ingestion.disclosures_parse` (L5); this module owns
the network source and the deterministic enrichment.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from app.ingestion.disclosures_parse import (
    MIRROR_TTL_SECONDS,
    MIRROR_URL,
    ROSTER,
    SOURCE_KIND,
    _normalize_name,
    _parse_day,
    _record,
    _side_verb,
    _slug,
    _stable_id,
)
from app.ingestion.source import FetchResult, RawTweet, TweetSource
from app.models.events import CongressTradeEvent, Event

logger = logging.getLogger(__name__)

__all__ = [
    "SOURCE_KIND",
    "CongressDisclosureSource",
    "enrich_congress_trade",
    "maybe_enrich_event",
]


class CongressDisclosureSource(TweetSource):
    """Polls recent congressional PTR filings and emits one record per trade.

    Emission policy: every poll returns congress-branch rows filed within the
    last ``lookback_days`` (capped at ``max_rows``, newest first). Re-polls
    re-emit recent rows and downstream dedup by ``tweet_id`` drops them — this
    trades a cheap bulk upsert for never missing filings that appear in the
    mirror days after their filing date.
    """

    def __init__(
        self,
        mirror_url: str = MIRROR_URL,
        lookback_days: int = 7,
        max_rows: int = 500,
        timeout_seconds: float = 30.0,
        fetcher: Callable[[], list[dict]] | None = None,
    ) -> None:
        self.mirror_url = mirror_url
        self.lookback_days = lookback_days
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self._fetcher = fetcher or self._fetch_mirror
        self._cache_rows: list[dict] | None = None
        self._cache_at: float = 0.0

    # ------------------------------------------------------------------
    # TweetSource interface
    # ------------------------------------------------------------------

    def fetch_recent(self, handles: list[str], since_seconds: int) -> FetchResult:
        """``handles`` is ignored. The mirror feeds the Senate branch only —
        the Clerk PDF lane owns House filings, so each trade appears once."""
        try:
            rows = self._cached_rows()
        except Exception as exc:
            return FetchResult(tweets=[], errors=[f"disclosure mirror fetch failed: {exc}"])
        now = datetime.now(timezone.utc)
        window = max(since_seconds, self.lookback_days * 86400)
        cutoff = now.date().toordinal() - window // 86400 - 1
        fresh = [
            r
            for r in rows
            if (r.get("branch") or "") == "congress" and r.get("ticker")
            and (r.get("chamber") or "").strip().lower() == "senate"
            and (day := _parse_day(r.get("filing_date"))) is not None
            and day.toordinal() >= cutoff
        ]
        fresh.sort(key=lambda r: str(r.get("filing_date") or ""), reverse=True)
        tweets = []
        for row in fresh[: self.max_rows]:
            try:
                record = _record(row, now)
            except Exception as exc:  # one bad row must not kill the poll
                logger.warning("skipping malformed disclosure row: %s", exc)
                continue
            if record is not None:
                tweets.append(record)
        logger.info("disclosure source: %d filing(s) in window", len(tweets))
        return FetchResult(tweets=tweets)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cached_rows(self) -> list[dict]:
        if self._cache_rows is not None and time.monotonic() - self._cache_at < MIRROR_TTL_SECONDS:
            return self._cache_rows
        rows = self._fetcher()
        if not isinstance(rows, list):
            raise ValueError(f"mirror returned {type(rows).__name__}, expected a list")
        self._cache_rows = rows
        self._cache_at = time.monotonic()
        return rows

    def _fetch_mirror(self) -> list[dict]:
        response = httpx.get(self.mirror_url, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()


# ---------------------------------------------------------------------------
# Deterministic enrichment: filing facts overwrite model guesses
# ---------------------------------------------------------------------------


def enrich_congress_trade(tweet: RawTweet, event: CongressTradeEvent) -> CongressTradeEvent:
    """Ground a congress-trade event in the filing it came from.

    The feed — not the LLM — is authoritative for ticker, dates, amounts,
    party, chamber, and name. The roster is authoritative for the government
    identifier and committee memberships (unknown filers get a stable slug id
    and *no* committees, so the alert rule can only fire on verified
    memberships). Confidence and classification stay the model's.
    """
    meta = tweet.raw_json or {}
    updates: dict[str, Any] = {}

    ticker = str(meta.get("ticker") or "").strip().upper()
    if 1 <= len(ticker) <= 10:
        updates["ticker"] = ticker
    txn_day = _parse_day(meta.get("transaction_date"))
    if txn_day is not None:
        updates["transaction_date"] = txn_day
    amount = str(meta.get("amount_range_label") or "").strip()
    if amount:
        updates["amount_range"] = amount
    filer = str(meta.get("filer_name") or "").strip()
    if filer:
        updates["politician_name"] = filer
    party = str(meta.get("party") or "").strip().upper()
    if party in ("D", "R", "I"):
        updates["party"] = party
    chamber = str(meta.get("chamber") or "").strip().lower()
    if chamber in ("house", "senate"):
        updates["chamber"] = chamber

    name = updates.get("politician_name", event.politician_name)
    match = ROSTER.get(_normalize_name(str(name)))
    if match is None and filer:
        match = ROSTER.get(_normalize_name(filer))
    if match is not None:
        updates["politician_id"] = match["id"]
        updates["committee_ids"] = list(match.get("committees", []))
    else:
        updates["politician_id"] = f"slug-{_slug(str(name))}"
        updates["committee_ids"] = []

    if event.event_time is None:
        updates["event_time"] = tweet.created_at

    return event.model_copy(update=updates)


def maybe_enrich_event(tweet: RawTweet, event: Event) -> Event:
    """Apply disclosure enrichment when the record came from the filing feed.

    No-op for every other source. Never raises — enrichment must not break
    processing even on malformed feed rows.
    """
    try:
        meta = tweet.raw_json or {}
        if (
            isinstance(event, CongressTradeEvent)
            and isinstance(meta, dict)
            and meta.get("source_kind") == SOURCE_KIND
        ):
            return enrich_congress_trade(tweet, event)
    except Exception:
        logger.exception("disclosure enrichment failed for %s; keeping model output", tweet.tweet_id)
    return event
