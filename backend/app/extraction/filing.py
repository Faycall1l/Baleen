"""Deterministic events from structured filing rows (no LLM).

Filing feeds (SEC EDGAR Form 4, congressional disclosures) carry fully
structured facts — filer, ticker, side, shares, price, dates. Running those
rows through the LLM extraction burns a model call per tweet for zero
information gain, which is what makes the free-tier Groq backup unusable as
a scheduler backend (429s within hours). This module builds the event
directly from ``raw_json``; the pipeline still runs the normal enrich
(roster identity, committees), graph write, and alert evaluation downstream.

Returns None when the row lacks the minimum facts — the caller falls back
to LLM extraction. Never raises.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal

from app.ingestion.disclosures_parse import _normalize_name, _slug
from app.ingestion.source import RawTweet
from app.models.events import CongressTradeEvent, Event, InsiderTradeEvent

logger = logging.getLogger(__name__)

# Raw transaction_type spellings seen in filing rows.
_BUY_PREFIXES = ("buy", "purchase", "acquir", "award", "grant")
_SELL_PREFIXES = ("sell", "sale", "dispos")


def _side(raw: Any) -> Literal["buy", "sell"] | None:
    if not isinstance(raw, str):
        return None
    lowered = raw.strip().lower()
    if lowered.startswith(_BUY_PREFIXES):
        return "buy"
    if lowered.startswith(_SELL_PREFIXES):
        return "sell"
    return None


def _ticker(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    ticker = raw.strip().upper()
    return ticker if 1 <= len(ticker) <= 10 else None


def _name(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    return name if name else None


def _day(raw: Any) -> date | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _positive_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _chamber(raw: Any) -> Literal["house", "senate"] | None:
    if isinstance(raw, str) and raw.strip().lower() in ("house", "senate"):
        return raw.strip().lower()  # type: ignore[return-value]
    return None


def _party(raw: Any) -> Literal["D", "R", "I"] | None:
    if isinstance(raw, str) and raw.strip().upper() in ("D", "R", "I"):
        return raw.strip().upper()  # type: ignore[return-value]
    return None


def _text_or_none(raw: Any) -> str | None:
    return raw if isinstance(raw, str) and raw else None


def event_from_filing_row(tweet: RawTweet) -> Event | None:
    """Build a trade event straight from a structured filing row.

    Handles ``congress_disclosure`` and ``insider_filing`` source kinds;
    anything else (or any row missing minimum facts) returns None so the
    caller falls back to LLM extraction.
    """
    try:
        meta = tweet.raw_json or {}
        if not isinstance(meta, dict):
            return None
        kind = meta.get("source_kind")
        if kind == "congress_disclosure":
            return _congress_trade(tweet, meta)
        if kind == "insider_filing":
            return _insider_trade(tweet, meta)
        return None
    except Exception:
        logger.warning("deterministic filing build failed; falling back to LLM", exc_info=True)
        return None


def _congress_trade(tweet: RawTweet, meta: dict[str, Any]) -> CongressTradeEvent | None:
    name = _name(meta.get("filer_name")) or _name(meta.get("politician_name"))
    ticker = _ticker(meta.get("ticker"))
    side = _side(meta.get("transaction_type"))
    chamber = _chamber(meta.get("chamber"))
    if not name or not ticker or not side or not chamber:
        return None
    return CongressTradeEvent(
        tweet_id=tweet.tweet_id,
        tweet_url=tweet.url,
        confidence=0.95,
        politician_name=name,
        politician_id=f"slug-{_slug(_normalize_name(name))}",
        chamber=chamber,
        party=_party(meta.get("party")),
        committee_ids=[],
        ticker=ticker,
        transaction_type=side,
        amount_range=_text_or_none(meta.get("amount_range_label")),
        transaction_date=_day(meta.get("transaction_date")),
    )


def _insider_trade(tweet: RawTweet, meta: dict[str, Any]) -> InsiderTradeEvent | None:
    name = _name(meta.get("filer_name")) or _name(meta.get("insider_name"))
    ticker = _ticker(meta.get("ticker"))
    side = _side(meta.get("transaction_type"))
    if not name or not ticker or not side:
        return None
    cik_raw = meta.get("insider_cik")
    cik = str(cik_raw).lstrip("0") or None if cik_raw is not None else None
    return InsiderTradeEvent(
        tweet_id=tweet.tweet_id,
        tweet_url=tweet.url,
        confidence=0.95,
        insider_name=name,
        insider_cik=cik,
        insider_id=cik or f"slug-{_slug(_normalize_name(name))}",
        role=_text_or_none(meta.get("role")),
        ticker=ticker,
        transaction_type=side,
        shares=_positive_float(meta.get("shares")),
        price=_positive_float(meta.get("price")),
        amount_range=_text_or_none(meta.get("amount_range_label")),
        transaction_date=_day(meta.get("transaction_date")),
    )
