"""Real insider-trade feed: SEC EDGAR Form 4 filings, keyless and live.

Polls the SEC's Atom feed of latest Form 4 submissions (accepted filings
appear within seconds), downloads each filing's ownership XML, and emits one
record per insider transaction. No API key — the SEC only asks for an
identifying User-Agent.

The XML/Atom parsing itself lives in :mod:`app.ingestion.edgar_parse` (L2);
this module owns the network source and the deterministic enrichment that
grounds extracted events in the filing they came from. Only open-market
transactions (codes P/S) become records; grants, gifts, and exercises are
skipped, and each record carries ``raw_json["source_kind"] == "insider_filing"``
so the pipeline can trust the filing's CIK over any model guess.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.ingestion.edgar_parse import (
    ATOM_URL,
    LOOKBACK_SECONDS,
    MAX_FILINGS_PER_POLL,
    OPEN_MARKET_CODES,
    SEEN_CAP,
    SOURCE_KIND,
    USER_AGENT,
    _record,
    _xml_href,
    parse_atom,
    parse_ownership,
)
from app.ingestion.source import BoundedSeen, FetchResult, RawTweet, TweetSource
from app.models.events import Event, InsiderTradeEvent

logger = logging.getLogger(__name__)

__all__ = [
    "OPEN_MARKET_CODES",
    "SOURCE_KIND",
    "EdgarForm4Source",
    "enrich_insider_trade",
    "maybe_enrich_insider",
    "parse_atom",
    "parse_ownership",
]


class EdgarForm4Source(TweetSource):
    """Polls recent SEC Form 4 filings and emits one record per insider trade."""

    def __init__(
        self,
        atom_url: str = ATOM_URL,
        user_agent: str = USER_AGENT,
        max_filings_per_poll: int = MAX_FILINGS_PER_POLL,
        timeout_seconds: float = 30.0,
        fetch_text: Callable[[str], str] | None = None,
    ) -> None:
        self.atom_url = atom_url
        self.user_agent = user_agent
        self.max_filings_per_poll = max_filings_per_poll
        self.timeout_seconds = timeout_seconds
        self._fetch_text = fetch_text or self._download
        self._seen_accessions = BoundedSeen(SEEN_CAP)

    # ------------------------------------------------------------------
    # TweetSource interface
    # ------------------------------------------------------------------

    def fetch_recent(self, handles: list[str], since_seconds: int) -> FetchResult:
        """``handles`` is ignored (the feed covers all filers)."""
        try:
            entries = parse_atom(self._fetch_text(self.atom_url))
        except Exception as exc:
            return FetchResult(tweets=[], errors=[f"EDGAR Atom fetch failed: {exc}"])
        now = datetime.now(timezone.utc)
        window = max(since_seconds, LOOKBACK_SECONDS)
        cutoff = now.timestamp() - window
        fresh = [e for e in entries if _parse_ts(e.get("updated")) >= cutoff]
        fresh.sort(key=lambda e: e.get("updated") or "", reverse=True)
        tweets: list[RawTweet] = []
        errors: list[str] = []
        for entry in fresh[: self.max_filings_per_poll]:
            if entry["accession"] in self._seen_accessions:
                continue
            try:
                tweets.extend(self._filing_records(entry, now))
            except Exception as exc:
                errors.append(f"Form 4 {entry['accession']} failed: {exc}")
                continue
            # issue #12: mark seen only on success, so a transient SEC failure
            # is retried next tick instead of being skipped for this process.
            self._seen_accessions.remember(entry["accession"])
        logger.info("edgar source: %d trade(s) from filings", len(tweets))
        return FetchResult(tweets=tweets, errors=errors)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _filing_records(self, entry: dict, now: datetime) -> list[RawTweet]:
        index_html = self._fetch_text(entry["index_url"])
        xml_url = _xml_href(index_html)
        if xml_url is None:
            raise ValueError("no ownership XML link on filing index")
        filing = parse_ownership(self._fetch_text(xml_url))
        filed_at = _parse_ts(entry.get("updated")) or now.timestamp()
        filed_dt = datetime.fromtimestamp(filed_at, tz=timezone.utc)
        records = []
        for owner_idx, owner in enumerate(filing["owners"]):
            for txn_idx, txn in enumerate(filing["transactions"]):
                # Stable per (filing, owner, row): re-polls dedupe downstream,
                # distinct trades never collide.
                rec_id = f"{entry['accession']}-{owner_idx}-{txn_idx}"
                record = _record(
                    rec_id, entry["index_url"], filed_dt,
                    owner, txn, filing["ticker"], now,
                )
                if record is not None:
                    records.append(record)
        return records

    def _download(self, url: str) -> str:
        response = httpx.get(
            url, headers={"User-Agent": self.user_agent}, timeout=self.timeout_seconds
        )
        response.raise_for_status()
        return response.text


def _parse_ts(value: Any) -> float:
    """Parse an Atom timestamp to epoch seconds; unparseable -> 0 (excluded)."""
    if not value or not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Deterministic enrichment: filing facts overwrite model guesses
# ---------------------------------------------------------------------------


def enrich_insider_trade(tweet: RawTweet, event: InsiderTradeEvent) -> InsiderTradeEvent:
    """Ground an insider-trade event in the Form 4 it came from.

    CIKs, ticker, side, dates, and amounts are authoritative from the parsed
    XML. Unknown filers (no CIK) get a stable slug id. Confidence and
    classification stay the model's.
    """
    from app.ingestion.disclosures import _normalize_name, _slug  # shared helpers

    meta = tweet.raw_json or {}
    updates: dict[str, Any] = {}

    ticker = str(meta.get("ticker") or "").strip().upper()
    if 1 <= len(ticker) <= 10:
        updates["ticker"] = ticker
    txn_day = meta.get("transaction_date")
    if txn_day:
        try:
            from datetime import date as _date

            updates["transaction_date"] = _date.fromisoformat(str(txn_day)[:10])
        except ValueError:
            pass
    amount = str(meta.get("amount_range_label") or "").strip()
    if amount:
        updates["amount_range"] = amount
    filer = str(meta.get("filer_name") or "").strip()
    if filer:
        updates["insider_name"] = filer
    side = str(meta.get("transaction_type") or "").strip().lower()
    if side in ("buy", "sell"):
        updates["transaction_type"] = side
    shares = meta.get("shares")
    if isinstance(shares, (int, float)) and shares >= 0:
        updates["shares"] = float(shares)
    price = meta.get("price")
    if isinstance(price, (int, float)) and price >= 0:
        updates["price"] = float(price)
    role = str(meta.get("role") or "").strip()
    if role:
        updates["role"] = role
    cik = str(meta.get("insider_cik") or "").strip()
    if cik:
        updates["insider_cik"] = cik
        updates["insider_id"] = cik
    else:
        updates["insider_id"] = f"slug-{_slug(_normalize_name(str(updates.get('insider_name', event.insider_name))))}"

    if event.event_time is None:
        updates["event_time"] = tweet.created_at

    return event.model_copy(update=updates)


def maybe_enrich_insider(tweet: RawTweet, event: Event) -> Event:
    """Apply Form 4 enrichment when the record came from the EDGAR feed.

    No-op for every other source. Never raises.
    """
    try:
        meta = tweet.raw_json or {}
        if (
            isinstance(event, InsiderTradeEvent)
            and isinstance(meta, dict)
            and meta.get("source_kind") == SOURCE_KIND
        ):
            return enrich_insider_trade(tweet, event)
    except Exception:
        logger.exception("insider enrichment failed for %s; keeping model output", tweet.tweet_id)
    return event
