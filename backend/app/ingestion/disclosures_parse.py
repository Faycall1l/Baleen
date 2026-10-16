"""Congressional-disclosure row parsing, split out of :mod:`app.ingestion.disclosures`.

Pure functions over the Clerk mirror's JSON rows: name normalization, stable
identifiers, transaction verbs, and the ``RawTweet`` each row becomes. No
network, so the record shape is unit-testable without touching the mirror.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime
from typing import Any

from app.graph.seed_data import POLITICIANS
from app.ingestion.source import RawTweet

#: Community mirror of official House/Senate PTR filings (clean JSON, stable
#: ids, both chambers, refreshed daily). The legacy S3 bucket this replaces
#: went dark (403); the Clerk source it wrapped is still authoritative.
MIRROR_URL = "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/trades.json"

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


#: Seed roster keyed by normalized name, used to attach a stable politician id
#: (and chamber/party) to a mirror row that only carries a display name.
ROSTER: dict[str, dict] = {_normalize_name(p["name"]): p for p in POLITICIANS}


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unknown"


def _stable_id(row: dict) -> str:
    raw = str(row.get("id") or "").strip()
    if raw:
        return f"ptr-{raw}"
    seed = "|".join(str(row.get(k) or "") for k in ("filer_name", "ticker", "transaction_date", "amount_range_label"))
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


def _coerce_returns(meta: dict) -> None:
    """Normalize mirror ``ret_since``/``excess_since`` to JSON numbers.

    The mirror occasionally strands junk sentinels (``"N/A"``, ``"."``, empty
    strings) in these return fields. Storing them verbatim forces every query
    to defensively regex-parse text (and silently drops scientific notation).
    Coerce to a finite float here — or drop the field — so the graph only ever
    sees real numbers.
    """
    for key in ("ret_since", "excess_since"):
        if key not in meta:
            continue
        try:
            parsed = float(meta[key])
        except (TypeError, ValueError):
            meta.pop(key, None)
            continue
        if not math.isfinite(parsed):
            meta.pop(key, None)
        else:
            meta[key] = parsed


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
    created = datetime(filing_day.year, filing_day.month, filing_day.day, tzinfo=UTC) if filing_day else now
    meta = dict(row)
    meta["source_kind"] = SOURCE_KIND
    _coerce_returns(meta)
    return RawTweet(
        tweet_id=_stable_id(row),
        author_handle=f"congress_{chamber or 'unknown'}",
        author_id=f"congress_{chamber or 'unknown'}",
        text=text,
        created_at=created,
        url=doc or "https://disclosures-clerk.house.gov/FinancialDisclosure",
        raw_json=meta,
    )
