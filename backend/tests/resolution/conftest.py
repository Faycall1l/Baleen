"""Shared builders for the entity-resolution suite.

Split out of the former ``tests/test_resolution.py`` (M5).
"""

from __future__ import annotations

import difflib

from app.graph.resolution import (
    AUTO_MERGE_THRESHOLD,
    REVIEW_MIN_THRESHOLD,
    EntityResolver,
    normalize_mention,
)
from app.models.events import CongressTradeEvent


def resolver(postgres_store) -> EntityResolver:
    return EntityResolver(postgres_store)


def congress_event(
    tweet_id: str,
    politician_id: str,
    politician_name: str,
) -> CongressTradeEvent:
    return CongressTradeEvent(
        tweet_id=tweet_id,
        tweet_url=f"https://x.com/congress_trades/status/{tweet_id}",
        confidence=0.95,
        politician_name=politician_name,
        politician_id=politician_id,
        chamber="senate",
        party="D",
        committee_ids=["SSBK"],
        ticker="JPM",
        transaction_type="buy",
        amount_range="$1,001 - $15,000",
    )


def confidence(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_mention(a), normalize_mention(b)).ratio()

WARREN_ID = "W000817"
WARREN_NAME = "Elizabeth Warren"
