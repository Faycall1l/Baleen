"""Authoritative House PTR feed, straight from the Clerk (no middlemen).

Downloads the yearly filing index ZIP from disclosures-clerk.house.gov, picks
out Periodic Transaction Reports, and parses each e-filed PTR PDF's text layer
into individual trades. Scanned paper filings (7-digit DocIDs, image-only) are
skipped — they need OCR, which is out of scope.

Why a second congress lane when the mirror already covers the House? Freshness
and independence: the Clerk publishes intraday while mirrors lag ~a day, and a
dead mirror (like the abandoned S3 bucket) must never blind us. Rows are
synthesized into the same shape as the mirror feed so record mapping and
deterministic enrichment are shared verbatim (see :mod:`app.ingestion.disclosures`).
"""

from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from datetime import datetime, timezone
from typing import Callable

import httpx
from pypdf import PdfReader

from app.ingestion.disclosures import _record
from app.ingestion.source import BoundedSeen, FetchResult, RawTweet, TweetSource

logger = logging.getLogger(__name__)

INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PTR_PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

#: The Clerk refreshes intraday; cache the small index briefly, parse PDFs live
#: (each filing is fetched at most once per process lifetime — see _seen).
INDEX_TTL_SECONDS = 3600

#: Bound per-poll PDF downloads (newest filings first).
MAX_PDFS_PER_POLL = 20

#: Default emission window (filing date). The mirror backfills history; this
#: lane exists for fresh filings.
LOOKBACK_DAYS = 3

#: Cap on the in-memory seen set (doc ids). Bounded so a long-running
#: scheduler doesn't leak memory; eviction only causes a redundant download
#: that Postgres dedup drops (see #27).
SEEN_CAP = 500


def parse_index(text: str) -> list[dict]:
    """Parse the Clerk's tab-delimited filing index into PTR filing rows.

    Columns: Prefix, Last, First, Suffix, FilingType, StateDst, Year,
    FilingDate (M/D/YYYY), DocID. Keeps ``FilingType == "P"`` (Periodic
    Transaction Report) with e-filed (8-digit, leading 2) DocIDs only.
    """
    filings = []
    for line in text.splitlines():
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) < 9:
            continue
        prefix, last, first, suffix, ftype, statedst, year, filingdate, docid = parts[:9]
        if (ftype or "").strip() != "P":
            continue
        docid = (docid or "").strip()
        if not re.fullmatch(r"2\d{7}", docid):
            continue  # scanned paper form — image-only, needs OCR
        try:
            filing_day = datetime.strptime(filingdate.strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        name = " ".join(p for p in [first.strip(), last.strip()] if p).strip()
        filings.append(
            {
                "doc_id": docid,
                "filer_name": re.sub(r"\s+", " ", name),
                "state": (statedst or "").strip()[:2].upper(),
                "year": (year or "").strip(),
                "filing_date": filing_day.isoformat(),
            }
        )
    return filings


_TXN_RE = re.compile(
    r"(?P<asset>[\w][\w\s,.\-&']+?)\s*"
    r"\((?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\)\s*"
    r"\[(?P<aclass>[A-Z]{1,4})\]\s*"
    r"(?P<side>[PSE])\s*"
    r"(?P<txndate>\d{2}/\d{2}/\d{4})\s*"
    r"(?P<notifdate>\d{2}/\d{2}/\d{4})?\s*"
    r"(?P<amount>\$[\d,]+\s*-\s*\$[\d,]+)"
)


def parse_ptr_pdf(data: bytes) -> list[dict]:
    """Extract individual trades from an e-filed PTR PDF's text layer.

    Returns dicts with asset/ticker/side/transaction_date/amount_range_label.
    Exchanges (``E``) are dropped — the event model only supports buy/sell and
    we never force-fit. Empty/scanned PDFs yield no rows.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        logger.warning("unreadable PTR pdf: %s", exc)
        return []
    if len(text.strip()) < 500:
        return []  # scanned image-only form
    flat = re.sub(r"\s+", " ", text)
    trades = []
    for match in _TXN_RE.finditer(flat):
        side = match.group("side")
        if side == "E":
            continue
        try:
            txn_day = datetime.strptime(match.group("txndate"), "%m/%d/%Y").date()
        except ValueError:
            continue
        asset = re.sub(r"\s+", " ", match.group("asset")).strip(" ,")
        trades.append(
            {
                "asset_name": asset,
                "ticker": match.group("ticker"),
                "transaction_type": "Purchase" if side == "P" else "Sale",
                "transaction_date": txn_day.isoformat(),
                "amount_range_label": re.sub(r"\s+", " ", match.group("amount")),
            }
        )
    return trades


def _rows_from_filing(filing: dict, trades: list[dict]) -> list[dict]:
    rows = []
    for index, trade in enumerate(trades):
        rows.append(
            {
                "id": f"house_{filing['doc_id']}_{index}",
                "source_id": "house_clerk_direct",
                "transaction_date": trade["transaction_date"],
                "filing_date": filing["filing_date"],
                "ticker": trade["ticker"],
                "asset_name": trade["asset_name"],
                "transaction_type": trade["transaction_type"],
                "amount_range_label": trade["amount_range_label"],
                "filer_name": filing["filer_name"],
                "branch": "congress",
                "chamber": "house",
                "party": None,
                "state": filing["state"],
                "doc_url": PTR_PDF_URL.format(year=filing["year"], doc_id=filing["doc_id"]),
                "filing_type": "PTR",
            }
        )
    return rows


class ClerkHouseSource(TweetSource):
    """Intraday House PTR filings, parsed from the authoritative PDFs."""

    def __init__(
        self,
        year: int | None = None,
        lookback_days: int = LOOKBACK_DAYS,
        max_pdfs_per_poll: int = MAX_PDFS_PER_POLL,
        timeout_seconds: float = 30.0,
        fetch_index: Callable[[int], str] | None = None,
        fetch_pdf: Callable[[str, str], bytes] | None = None,
    ) -> None:
        self.year = year or datetime.now(timezone.utc).year
        self.lookback_days = lookback_days
        self.max_pdfs_per_poll = max_pdfs_per_poll
        self.timeout_seconds = timeout_seconds
        self._fetch_index = fetch_index or self._download_index
        self._fetch_pdf = fetch_pdf or self._download_pdf
        self._index_cache: tuple[float, str] | None = None
        self._seen_docs = BoundedSeen(SEEN_CAP)

    # ------------------------------------------------------------------
    # TweetSource interface
    # ------------------------------------------------------------------

    def fetch_recent(self, handles: list[str], since_seconds: int) -> FetchResult:
        """``handles`` is ignored (the index covers the whole House)."""
        try:
            index_text = self._cached_index()
        except Exception as exc:
            return FetchResult(tweets=[], errors=[f"clerk index fetch failed: {exc}"])
        now = datetime.now(timezone.utc)
        window = max(since_seconds, self.lookback_days * 86400)
        cutoff = now.date().toordinal() - window // 86400 - 1
        filings = [
            f
            for f in parse_index(index_text)
            if datetime.fromisoformat(f["filing_date"]).date().toordinal() >= cutoff
        ]
        filings.sort(key=lambda f: (f["filing_date"], f["doc_id"]), reverse=True)
        tweets: list[RawTweet] = []
        errors: list[str] = []
        fetched = 0
        for filing in filings:
            if filing["doc_id"] in self._seen_docs:
                continue
            if fetched >= self.max_pdfs_per_poll:
                break
            fetched += 1
            try:
                pdf = self._fetch_pdf(filing["year"], filing["doc_id"])
                trades = parse_ptr_pdf(pdf)
            except Exception as exc:
                # issue #12: only mark a filing seen once it parses cleanly,
                # otherwise a transient download failure retries forever off.
                errors.append(f"PTR {filing['doc_id']} failed: {exc}")
                continue
            self._seen_docs.remember(filing["doc_id"])
            for row in _rows_from_filing(filing, trades):
                try:
                    record = _record(row, now)
                except Exception as exc:
                    logger.warning("skipping malformed clerk row: %s", exc)
                    continue
                if record is not None:
                    tweets.append(record)
        logger.info("clerk source: %d trade(s) from %d filing(s)", len(tweets), fetched)
        return FetchResult(tweets=tweets, errors=errors)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cached_index(self) -> str:
        if self._index_cache is not None and time.monotonic() - self._index_cache[0] < INDEX_TTL_SECONDS:
            return self._index_cache[1]
        text = self._fetch_index(self.year)
        self._index_cache = (time.monotonic(), text)
        return text

    def _download_index(self, year: int) -> str:
        response = httpx.get(INDEX_URL.format(year=year), timeout=self.timeout_seconds)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".txt")]
            if not names:
                raise ValueError("clerk ZIP contained no index .txt")
            return archive.read(names[0]).decode("utf-8-sig", errors="replace")

    def _download_pdf(self, year: str, doc_id: str) -> bytes:
        response = httpx.get(
            PTR_PDF_URL.format(year=year, doc_id=doc_id), timeout=self.timeout_seconds
        )
        response.raise_for_status()
        return response.content
