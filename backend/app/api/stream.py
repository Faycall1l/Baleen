"""Live stream endpoint (Server-Sent Events, ``GET /api/stream``).

Split out of :mod:`app.api.routes`: the route owns nothing else, so it
gets its own module + router. Registered in :mod:`app.main` alongside
``routes.router``. The store is resolved lazily through
:mod:`app.api.routes` at call time so test monkeypatches on
``routes._get_store`` keep working.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from app.api.routes import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.get("/stream", dependencies=[Depends(require_api_key)])
async def stream(
    request: Request,
    after_report: int = Query(default=0, ge=0),
    after_alert: int = Query(default=0, ge=0),
    poll_seconds: float = Query(default=3.0, ge=1.0, le=30.0),
) -> StreamingResponse:
    """Push newly published reports/alerts as they land.

    Cursors are row ids: the client passes the highest id it has seen and
    receives everything newer, oldest first, then stays connected for live
    rows. A ``: keepalive`` comment goes out on quiet polls. Reconnects
    resume from the last cursor — no replay storms, no missed rows.
    """

    async def events():
        from app.api import routes as routes_mod

        async for chunk in stream_events(
            routes_mod._get_store(),
            request.is_disconnected,
            after_report,
            after_alert,
            poll_seconds,
        ):
            yield chunk

    return StreamingResponse(events(), media_type="text/event-stream")


async def stream_events(store, is_disconnected, after_report: int = 0, after_alert: int = 0, poll_seconds: float = 3.0):
    """Yield SSE-formatted chunks for rows newer than the cursors.

    Split out for testability: tests drive this directly with a fake
    ``is_disconnected`` instead of fighting HTTP streaming semantics.
    """
    last_report, last_alert = after_report, after_alert
    while True:
        if await is_disconnected():
            break
        try:
            # get_reports_since / get_alerts_since are blocking psycopg
            # calls; run them off the event loop so one slow query cannot
            # stall every concurrent stream (or the whole server).
            reports = await asyncio.to_thread(store.get_reports_since, last_report)
            alerts = await asyncio.to_thread(store.get_alerts_since, last_alert)
        except Exception:
            logger.exception("stream poll failed")
            break
        for row in reports:
            last_report = row["id"]
            yield f"id: r{row['id']}\nevent: report\ndata: {json.dumps(jsonable_encoder(row))}\n\n"
        for row in alerts:
            last_alert = row["id"]
            yield f"id: a{row['id']}\nevent: alert\ndata: {json.dumps(jsonable_encoder(row))}\n\n"
        if not reports and not alerts:
            yield ": keepalive\n\n"
        await asyncio.sleep(poll_seconds)
