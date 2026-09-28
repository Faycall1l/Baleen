"""``GET /api/signals`` — computed pressure signals (J4).

Split out of :mod:`app.api.routes`. The engine is built per request (cheap:
it holds no cache of its own) from the shared store + Neo4j clients.
"""

from __future__ import annotations

import logging

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


@router.get("/signals/similar")
def signals_similar(politician_id: str = Query(min_length=1)) -> dict:
    """Politicians trading the same tickers (shared-ticker similarity)."""
    try:
        rows = _engine().similar(politician_id)
    except Exception as exc:
        logger.exception("similar failed")
        raise HTTPException(status_code=500, detail=f"similar failed: {exc}")
    return {"similar": json_safe(rows)}
