"""``GET /api/committees/{cid}`` — one committee's card (R45a).

Overseen sectors, member roster, and member trades in a single read so
the inspector can render a committee view without ad-hoc Cypher.
Unknown ids answer 404.
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


@router.get("/committees/{cid}")
def committee(cid: str) -> dict:
    """Sectors overseen, members, and member trades for one committee."""
    from app.api import routes

    with routes.neo4j_read_slot():
        try:
            card = _engine().committee(cid)
        except Exception as exc:
            logger.exception("committee fetch failed")
            raise HTTPException(status_code=500, detail=f"committee fetch failed: {exc}") from None
    if card is None:
        raise HTTPException(status_code=404, detail="unknown committee")
    return {"committee": json_safe(card)}
