"""Postgres-backed read endpoints (J3): alerts, reports, digest, leaderboard.

Split out of :mod:`app.api.routes`. Shared clients and engines are resolved
through that module at call time so tests keep one monkeypatch surface.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/alerts")
def alerts(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    from app.api import routes

    try:
        rows = routes._get_store().get_alerts(limit=limit)
    except Exception as exc:
        logger.exception("alerts fetch failed")
        raise HTTPException(status_code=500, detail=f"alerts fetch failed: {exc}")
    return {"alerts": rows, "count": len(rows)}


@router.get("/reports/digest")
def reports_digest(period: str | None = Query(default=None)) -> dict:
    """Serve the daily digest, generating it on first request for the period."""
    from app.api import routes

    try:
        report = routes.get_report_engine().ensure_digest(period)
    except Exception as exc:
        logger.exception("digest generation failed")
        raise HTTPException(status_code=500, detail=f"digest failed: {exc}")
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
        raise HTTPException(status_code=500, detail=f"reports fetch failed: {exc}")
    return {"reports": rows, "count": len(rows)}


@router.get("/leaderboard")
def leaderboard(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """Best-performing congressional trades by return since transaction."""
    from app.api import routes

    try:
        rows = routes._get_store().get_leaderboard(limit=limit)
    except Exception as exc:
        logger.exception("leaderboard fetch failed")
        raise HTTPException(status_code=500, detail=f"leaderboard fetch failed: {exc}")
    return {"leaders": rows, "count": len(rows)}
