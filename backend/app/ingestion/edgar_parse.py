"""SEC EDGAR Form 4 XML/Atom parsing (L2), split out of :mod:`app.ingestion.edgar`.

Pure functions over filing XML: no network, no state, so they stay unit-testable
without touching the SEC. :mod:`app.ingestion.edgar` re-exports the public names
(``parse_atom``, ``parse_ownership``, the feed constants) it used to own.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from xml.etree import ElementTree as ET

from app.ingestion.source import RawTweet


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

#: Cap on the in-memory seen set (accessions). Bounded so a long-running
#: scheduler doesn't leak memory; eviction only triggers a redundant download
#: that Postgres dedup drops (see #27).
SEEN_CAP = 2000

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
