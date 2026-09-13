"""Real insider-trade feed: SEC EDGAR Form 4 filings, keyless and live.

Polls the SEC's Atom feed of latest Form 4 submissions (accepted filings
appear within seconds), downloads each filing's ownership XML, and emits one
record per insider transaction. No API key — the SEC only asks for an
identifying User-Agent.

Only open-market transactions (codes P/S) become records; grants, gifts, and
exercises are skipped. Each record carries ``raw_json["source_kind"] ==
"insider_filing"`` so the pipeline can ground the model output in parsed
filing facts (see :func:`enrich_insider_trade`): CIKs are authoritative and
never hallucinated.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from app.ingestion.source import FetchResult, RawTweet, TweetSource
from app.models.events import Event, InsiderTradeEvent

logger = logging.getLogger(__name__)

ATOM_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=4&count=100&output=atom"
)

#: SEC fair-access policy admits only clients that identify with contact
#: info (undeclared traffic from datacenter IPs gets 403). Override with
#: your own contact address in production.
USER_AGENT = "Baleen/1.0 (SEC EDGAR Form 4 research; contact: admin@example.com)"

SOURCE_KIND = "insider_filing"

#: Bound per-poll filing downloads (newest first).
MAX_FILINGS_PER_POLL = 15

#: Emission window floor: filings trickle 24/7, so always cover the last day.
LOOKBACK_SECONDS = 86400

#: Open-market codes only — the classic insider signal. Everything else
#: (grants, gifts, exercises) is a different economic event.
OPEN_MARKET_CODES = {"P", "S"}


def parse_atom(xml_text: str) -> list[dict]:
    """Parse the EDGAR getcurrent Atom feed into filing entries."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"unparseable Atom feed: {exc}") from exc
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = []
    for entry in root.findall("a:entry", ns):
        accession = None
        entry_id = (entry.findtext("a:id", default="", namespaces=ns) or "")
        match = re.search(r"accession-number=([\d-]+)", entry_id)
        if match:
            accession = match.group(1)
        link = ""
        for link_el in entry.findall("a:link", ns):
            href = link_el.get("href") or ""
            if href and link_el.get("type") == "text/html":
                link = href if href.startswith("http") else f"https://www.sec.gov{href}"
                break
        updated = entry.findtext("a:updated", default="", namespaces=ns) or ""
        if not accession or not link:
            continue
        entries.append({"accession": accession, "index_url": link, "updated": updated})
    return entries


def _text(parent: ET.Element | None, tag: str) -> str | None:
    if parent is None:
        return None
    el = parent.find(tag)
    if el is None or el.text is None:
        return None
    value = el.text.strip()
    return value or None


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "y")


def _owner_role(owner: ET.Element) -> str | None:
    rel = owner.find("reportingOwnerRelationship")
    if rel is None:
        return None
    title = _text(rel, "officerTitle")
    if _flag(_text(rel, "isOfficer")) and title:
        return title
    roles = []
    if _flag(_text(rel, "isDirector")):
        roles.append("Director")
    if _flag(_text(rel, "isTenPercentOwner")):
        roles.append("10% Owner")
    if _flag(_text(rel, "isOfficer")):
        roles.append("Officer")
    return ", ".join(roles) or None


def _number(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _parse_day(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_ownership(xml_text: str) -> dict:
    """Parse a Form 4 ownership document into issuer + owners + transactions."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"unparseable ownership document: {exc}") from exc
    issuer = root.find("issuer")
    owners = []
    for owner in root.findall("reportingOwner"):
        owner_id = owner.find("reportingOwnerId")
        name = _text(owner_id, "rptOwnerName")
        if not name:
            continue
        owners.append(
            {
                "cik": (_text(owner_id, "rptOwnerCik") or "").lstrip("0") or None,
                "name": re.sub(r"\s+", " ", name),
                "role": _owner_role(owner),
            }
        )
    transactions = []
    table = root.find("nonDerivativeTable")
    if table is not None:
        for txn in table.findall("nonDerivativeTransaction"):
            coding = txn.find("transactionCoding")
            code = (_text(coding, "transactionCode") or "").strip() if coding is not None else ""
            if code not in OPEN_MARKET_CODES:
                continue
            amounts = txn.find("transactionAmounts")
            acquired = (
                _text(amounts, "transactionAcquiredDisposedCode/value")
                if amounts is not None
                else None
            )
            if acquired not in ("A", "D"):
                continue
            transactions.append(
                {
                    "security": _text(txn, "securityTitle/value"),
                    "transaction_date": _parse_day(_text(txn, "transactionDate/value")),
                    "code": code,
                    "side": "buy" if acquired == "A" else "sell",
                    "shares": _number(_text(amounts, "transactionShares/value") if amounts is not None else None),
                    "price": _number(_text(amounts, "transactionPricePerShare/value") if amounts is not None else None),
                }
            )
    return {
        "ticker": (_text(issuer, "issuerTradingSymbol") or "").strip().upper() if issuer is not None else "",
        "issuer_name": _text(issuer, "issuerName") if issuer is not None else None,
        "owners": owners,
        "transactions": transactions,
    }


def _xml_href(index_html: str) -> str | None:
    """Find the raw ownership XML link on a filing index page."""
    hrefs = re.findall(r'href="([^"]*\.xml)"', index_html)
    if not hrefs:
        return None
    plain = [h for h in hrefs if "xslF" not in h]
    chosen = plain[0] if plain else hrefs[0]
    return chosen if chosen.startswith("http") else f"https://www.sec.gov{chosen}"


def _fmt_amount(shares: float | None, price: float | None) -> str | None:
    if shares is None or price is None:
        return None
    return f"${shares * price:,.2f}"


def _record(rec_id: str, index_url: str, filed_at: datetime,
            owner: dict, txn: dict, ticker: str, now: datetime) -> RawTweet | None:
    if not ticker or not owner.get("name"):
        return None
    verb = "bought" if txn["side"] == "buy" else "sold"
    shares = txn.get("shares")
    price = txn.get("price")
    qty = f" {shares:,.0f} shares" if shares is not None else " shares"
    at = f" @ ${price:,.2f}" if price is not None else ""
    when = txn.get("transaction_date")
    role = f" ({owner['role']})" if owner.get("role") else ""
    text = (
        f"INSIDER FILING: {owner['name']}{role}, ${ticker} — "
        f"{verb}{qty}{at}"
        f"{f' on {when.isoformat()}' if when else ''} "
        f"(Form 4 filed {filed_at.date().isoformat()}). Source: {index_url}"
    )
    meta = {
        "source_kind": SOURCE_KIND,
        "filer_name": owner["name"],
        "insider_cik": owner.get("cik"),
        "role": owner.get("role"),
        "ticker": ticker,
        "transaction_type": txn["side"],
        "shares": shares,
        "price": price,
        "amount_range_label": _fmt_amount(shares, price),
        "transaction_date": when.isoformat() if when else None,
        "doc_url": index_url,
    }
    return RawTweet(
        tweet_id=f"edgar-{rec_id}",
        author_handle="edgar_form4",
        author_id="edgar_form4",
        text=text,
        created_at=filed_at,
        url=index_url,
        raw_json=meta,
    )


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
        self._seen_accessions: set[str] = set()

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
            self._seen_accessions.add(entry["accession"])
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
