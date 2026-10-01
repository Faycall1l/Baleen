"""``GET /api/tickers/{sym}`` — one ticker's card (R24a).

Sector, latest options prints, and latest congress/insider trades in a
single read so the inspector can render a ticker view without ad-hoc
Cypher. Unknown symbols answer 404.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.api.graph_payload import json_safe

logger = logging.getLogger(__name__)
router = APIRouter()


def _engine():
    from app.api import routes

    return routes.get_signals_engine()


@router.get("/tickers/{sym}")
def ticker(sym: str) -> dict:
    """Sector, recent flow prints, and recent trades for one ticker."""
    from app.api import routes

    with routes.neo4j_read_slot():
        try:
            card = _engine().ticker(sym)
        except Exception as exc:
            logger.exception("ticker fetch failed")
            raise HTTPException(status_code=500, detail=f"ticker fetch failed: {exc}")
    if card is None:
        raise HTTPException(status_code=404, detail="unknown ticker")
    return {"ticker": json_safe(card)}
