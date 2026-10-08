"""``GET /api/signals`` — computed pressure signals (J4).

Split out of :mod:`app.api.routes`. The engine is built per request (cheap:
it holds no cache of its own) from the shared store + Neo4j clients.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.graph_payload import json_safe

logger = logging.getLogger(__name__)
router = APIRouter()

# Short-TTL cache for the /api/signals bundle (keyed by ``window_days``),
# mirroring the /api/graph cache in routes.py: the UI polls every 30s and the
# bundle costs five sequential Neo4j round-trips, which pile up into gateway
# timeouts under load. 0 TTL disables caching.
_signals_cache: dict[int, tuple[float, dict]] = {}
_signals_cache_lock = threading.Lock()


def _engine():
    from app.api import routes

    return routes.get_signals_engine()


@router.get("/signals")
def signals(window_days: int = Query(default=90, ge=1, le=365)) -> dict:
    """Computed pressure signals: momentum, insider clusters, options meter,
    uncovered sector bets, suspicious timing."""
    from app.api import routes

    ttl = routes.get_settings().signals_cache_ttl_seconds
    if ttl > 0:
        with _signals_cache_lock:
            hit = _signals_cache.get(window_days)
        if hit and hit[0] > time.monotonic():
            return hit[1]

    # The gate raises 503 itself when saturated; hold it outside the try so
    # an engine failure still maps to 500 without swallowing the 503.
    with routes.neo4j_read_slot():
        try:
            bundle = _engine().bundle(window_days=window_days)
        except Exception as exc:
            logger.exception("signals failed")
            raise HTTPException(status_code=500, detail=f"signals failed: {exc}")
    payload = json_safe(bundle)

    if ttl > 0:
        with _signals_cache_lock:
            _signals_cache[window_days] = (time.monotonic() + ttl, payload)
    return payload


@router.get("/signals/activity")
def signals_activity(window_days: int = Query(default=7, ge=1, le=30)) -> dict:
    """What is moving right now: accelerating tickers, first-seen tickers,
    and the freshest events. Uncached — three cheap indexed queries."""
    from app.api import routes

    # Gate outside the try (see /signals): saturation stays a 503.
    with routes.neo4j_read_slot():
        try:
            payload = _engine().activity(recent_days=window_days)
        except Exception as exc:
            logger.exception("signals activity failed")
            raise HTTPException(status_code=500, detail=f"signals activity failed: {exc}")
    return json_safe(payload)


@router.get("/signals/flow")
def signals_flow(
    tickers: str | None = Query(default=None),
    side: Literal["all", "call", "put"] = Query(default="all"),
    min_ratio: float = Query(default=2.0, ge=0, le=1000),
    window_days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Per-print unusual-options tape, ranked by volume/open-interest ratio."""
    from app.api import routes

    symbols = (
        [s.strip().upper() for s in tickers.split(",") if s.strip()]
        if tickers
        else []
    )
    # Gate outside the try (see /signals): saturation stays a 503.
    with routes.neo4j_read_slot():
        try:
            rows = _engine().flow(
                tickers=symbols,
                side=side,
                min_ratio=min_ratio,
                window_days=window_days,
                limit=limit,
            )
        except Exception as exc:
            logger.exception("options flow failed")
            raise HTTPException(status_code=500, detail=f"options flow failed: {exc}")
    return {
        "flow": json_safe(rows),
        "count": len(rows),
        "filters": {
            "tickers": symbols,
            "side": side,
            "min_ratio": min_ratio,
            "window_days": window_days,
            "limit": limit,
        },
    }


@router.get("/signals/similar")
def signals_similar(politician_id: str = Query(min_length=1)) -> dict:
    """Politicians trading the same tickers (shared-ticker similarity)."""
    try:
        rows = _engine().similar(politician_id)
    except Exception as exc:
        logger.exception("similar failed")
        raise HTTPException(status_code=500, detail=f"similar failed: {exc}")
    return {"similar": json_safe(rows)}
