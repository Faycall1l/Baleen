"""``POST /api/query`` — natural-language question through the query agent (J4).

Split out of :mod:`app.api.routes`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.graph_payload import json_safe

logger = logging.getLogger(__name__)
router = APIRouter()


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)


def _require_api_key(request: Request) -> None:
    """Thin indirection so the guard stays owned by :mod:`app.api.routes`
    without a module-load circular import."""
    from app.api.routes import require_api_key

    require_api_key(request)


@router.post("/query", dependencies=[Depends(_require_api_key)])
def ask_question(request: QueryRequest) -> dict:
    from app.api import routes

    agent = routes.get_query_agent()
    try:
        result = agent.ask(request.question)
    except Exception as exc:
        logger.exception("query agent failed for question %r", request.question)
        raise HTTPException(status_code=500, detail=f"query agent failed: {exc}")
    payload = result.as_dict()
    payload["rows"] = json_safe(payload["rows"])
    return payload
