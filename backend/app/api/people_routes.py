"""``GET /api/politicians/{id}`` — one politician's card (R17a).

Identity, committees, and latest trades in a single read so the inspector
can render a person view without ad-hoc Cypher. Unknown ids answer 404.
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


@router.get("/politicians/{politician_id}")
def politician(politician_id: str) -> dict:
    """Identity, committee memberships, and latest congress trades."""
    from app.api import routes

    with routes.neo4j_read_slot():
        try:
            person = _engine().person(politician_id)
        except Exception as exc:
            logger.exception("person fetch failed")
            raise HTTPException(status_code=500, detail=f"person fetch failed: {exc}")
    if person is None:
        raise HTTPException(status_code=404, detail="unknown politician")
    return {"person": json_safe(person)}
