"""Postgres-backed read endpoints: alerts, reports, digest, leaderboard.

Split out of :mod:`app.api.routes`. Shared clients and engines are resolved
through that module at call time so tests keep one monkeypatch surface.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class RuleActive(BaseModel):
    active: bool


@router.get("/news")
def news(
    limit: int = Query(default=30, ge=1, le=100),
    outlet: str | None = Query(default=None),
) -> dict:
    """Monitored media headlines (keyless RSS/Atom), newest first.

    Articles are stored independently of event extraction, so coverage stays
    visible even when a story contains no typed trade event. Mentions are
    deterministic: cashtags and exact roster names.
    """
    from app.api import routes

    try:
        articles = routes._get_store().get_news(limit=limit, outlet=outlet)
    except Exception as exc:
        logger.exception("news fetch failed")
        raise HTTPException(status_code=500, detail=f"news fetch failed: {exc}") from None
    return {"articles": articles, "count": len(articles)}


@router.get("/alerts")
def alerts(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    from app.api import routes

    try:
        rows = routes._get_store().get_alerts(limit=limit)
    except Exception as exc:
        logger.exception("alerts fetch failed")
        raise HTTPException(status_code=500, detail=f"alerts fetch failed: {exc}") from None
    return {"alerts": rows, "count": len(rows)}


@router.get("/reports/digest")
def reports_digest(period: str | None = Query(default=None)) -> dict:
    """Serve the daily digest, generating it on first request for the period."""
    from app.api import routes

    try:
        report = routes.get_report_engine().ensure_digest(period)
    except Exception as exc:
        logger.exception("digest generation failed")
        raise HTTPException(status_code=500, detail=f"digest failed: {exc}") from None
    return {"report": report}


@router.get("/reports")
def reports(
    limit: int = Query(default=20, ge=1, le=100),
    kind: str | None = Query(default=None),
) -> dict:
    from app.api import routes

    try:
        rows = routes._get_store().get_reports(limit=limit, kind=kind)
    except Exception as exc:
        logger.exception("reports fetch failed")
        raise HTTPException(status_code=500, detail=f"reports fetch failed: {exc}") from None
    return {"reports": rows, "count": len(rows)}


@router.get("/leaderboard")
def leaderboard(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """Best-performing congressional trades by return since transaction."""
    from app.api import routes

    try:
        rows = routes._get_store().get_leaderboard(limit=limit)
    except Exception as exc:
        logger.exception("leaderboard fetch failed")
        raise HTTPException(status_code=500, detail=f"leaderboard fetch failed: {exc}") from None
    return {"leaders": rows, "count": len(rows)}


@router.get("/rules")
def rules() -> dict:
    """Alert rules with their muted state, for the inbox filter bar."""
    from app.api import routes

    try:
        rows = routes._get_store().get_rules()
    except Exception as exc:
        logger.exception("rules fetch failed")
        raise HTTPException(status_code=500, detail=f"rules fetch failed: {exc}") from None
    return {"rules": rows, "count": len(rows)}


@router.patch("/rules/{name}")
def set_rule(name: str, body: RuleActive) -> dict:
    """Mute/unmute an alert rule. Unknown names answer 404."""
    from app.api import routes

    try:
        found = routes._get_store().set_rule_active(name, body.active)
    except Exception as exc:
        logger.exception("rule update failed")
        raise HTTPException(status_code=500, detail=f"rule update failed: {exc}") from None
    if not found:
        raise HTTPException(status_code=404, detail="unknown rule")
    return {"name": name, "active": body.active}
