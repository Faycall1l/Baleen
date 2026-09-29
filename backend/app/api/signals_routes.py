"""``GET /api/signals`` — computed pressure signals (J4).

Split out of :mod:`app.api.routes`. The engine is built per request (cheap:
it holds no cache of its own) from the shared store + Neo4j clients.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.graph_payload import json_safe

logger = logging.getLogger(__name__)
router = APIRouter()


def _engine():
    from app.api import routes

    return routes.get_signals_engine()


@router.get("/signals")
def signals(window_days: int = Query(default=90, ge=1, le=365)) -> dict:
    """Computed pressure signals: momentum, insider clusters, options meter,
    uncovered sector bets, suspicious timing."""
    try:
        bundle = _engine().bundle(window_days=window_days)
    except Exception as exc:
        logger.exception("signals failed")
        raise HTTPException(status_code=500, detail=f"signals failed: {exc}")
    return json_safe(bundle)


@router.get("/signals/flow")
def signals_flow(
    tickers: str | None = Query(default=None),
    side: Literal["all", "call", "put"] = Query(default="all"),
    min_ratio: float = Query(default=2.0, ge=0, le=1000),
    window_days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Per-print unusual-options tape, ranked by volume/open-interest ratio."""
    try:
        symbols = (
            [s.strip().upper() for s in tickers.split(",") if s.strip()]
            if tickers
            else []
        )
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
