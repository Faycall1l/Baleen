"""Real congressional-trade feed: public STOCK Act disclosure data.

Pulls normalized Periodic Transaction Reports (House + Senate) from a
community-maintained mirror of the official filings (Clerk of the House PTR
PDFs, Senate EFD), updated daily. No API key, no scraping paywalls — the data
is public-domain government disclosure data.

Each filing becomes a :class:`RawTweet` whose ``url`` points at the official
filing PDF, so the graph stays traceable to the primary source. Records carry
``raw_json["source_kind"] == "congress_disclosure"`` so the processing
pipeline can apply deterministic enrichment (see :func:`maybe_enrich_event`):
tickers, dates, amounts, party/chamber come straight from the filing — the LLM
contributes classification + confidence, never hallucinated identifiers.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Callable

import httpx

from app.graph.seed_data import POLITICIANS
from app.ingestion.source import FetchResult, RawTweet, TweetSource
from app.models.events import CongressTradeEvent, Event

logger = logging.getLogger(__name__)

#: Community mirror of official House/Senate PTR filings (clean JSON, stable
#: ids, both chambers, refreshed daily). The legacy S3 bucket this replaces
#: went dark (403); the Clerk source it wrapped is still authoritative.
MIRROR_URL = (
    "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor"
    "/main/public/data/trades.json"
)

SOURCE_KIND = "congress_disclosure"

#: Mirror rows are ~4MB; the feed refreshes daily, so cache aggressively and
#: let the scheduler tick cheaply. Re-polls are deduped downstream by tweet_id.
MIRROR_TTL_SECONDS = 1800


def _parse_day(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z\s]", "", name.lower())).strip()


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unknown"


#: filer name (normalized) -> reference roster entry. Committees come from this
#: table — never from the LLM — so MEMBER_OF edges (and therefore alerts) rest
#: on curated membership data, not model output.
ROSTER: dict[str, dict] = {_normalize_name(p["name"]): p for p in POLITICIANS}


def _stable_id(row: dict) -> str:
    raw = str(row.get("id") or "").strip()
    if raw:
        return f"ptr-{raw}"
    seed = "|".join(
        str(row.get(k) or "")
        for k in ("filer_name", "ticker", "transaction_date", "amount_range_label")
    )
    import hashlib

    return f"ptr-{hashlib.sha1(seed.encode()).hexdigest()[:12]}"


def _side_verb(transaction_type: str) -> str:
    text = (transaction_type or "").lower()
    if "purchase" in text or text.startswith("buy"):
        return "purchased"
    if "sale" in text or "sell" in text:
        return "sold"
    if "exchange" in text:
        return "exchanged"
    return "traded"


def _record(row: dict, now: datetime) -> RawTweet | None:
    ticker = str(row.get("ticker") or "").strip().upper()
    filer = str(row.get("filer_name") or "").strip()
    if not ticker or not filer:
        return None
    chamber = str(row.get("chamber") or "").strip().lower()
    title = "Sen." if chamber == "senate" else "Rep."
    party = str(row.get("party") or "").strip()
    state = str(row.get("state") or "").strip()
    where = "-".join(p for p in [party, state] if p) or chamber or "unknown"
    txn_date = _parse_day(row.get("transaction_date"))
    filing_day = _parse_day(row.get("filing_date"))
    amount = str(row.get("amount_range_label") or "").strip() or "an undisclosed amount"
    asset = str(row.get("asset_name") or "").strip()
    what = f" ({asset})" if asset and ticker not in asset else ""
    doc = str(row.get("doc_url") or "").strip()
    text = (
        f"REPORTED: {title} {filer} ({where}, {chamber}) "
        f"{_side_verb(str(row.get('transaction_type') or ''))} "
        f"${ticker} shares{what} worth {amount}"
        f"{f' on {txn_date.isoformat()}' if txn_date else ''}."
        f"{f' Filed {filing_day.isoformat()}' if filing_day else ''}."
        f"{f' Disclosure: {doc}' if doc else ''}"
    )
    created = (
        datetime(filing_day.year, filing_day.month, filing_day.day, tzinfo=timezone.utc)
        if filing_day
        else now
    )
    meta = dict(row)
    meta["source_kind"] = SOURCE_KIND
    return RawTweet(
        tweet_id=_stable_id(row),
        author_handle=f"congress_{chamber or 'unknown'}",
        author_id=f"congress_{chamber or 'unknown'}",
        text=text,
        created_at=created,
        url=doc or f"https://disclosures-clerk.house.gov/FinancialDisclosure",
        raw_json=meta,
    )


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
