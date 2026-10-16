"""Report rows (briefs, alert reports, digests) and the SSE cursor read,
split out of :mod:`app.db.postgres`.

Mixed into ``PostgresStore``, which owns the shared SQLAlchemy engine, so every
method here stays a thin, auditable ``text()`` call.
"""

from __future__ import annotations

import json

from sqlalchemy import text


class ReportStoreMixin:
    def insert_report(
        self,
        kind: str,
        title: str,
        body: str,
        event_id: str | None = None,
        period: str | None = None,
        graph_refs: list[str] | None = None,
    ) -> int | None:
        """Insert a report. Returns its id, or ``None`` when the same
        (kind, event) brief or (kind, period) digest already exists."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO reports (kind, event_id, period, title, body, graph_refs)
                    VALUES (:kind, :event_id, :period, :title, :body, :graph_refs)
                    ON CONFLICT (kind, event_id) DO NOTHING
                    RETURNING id
                    """
                    if event_id is not None
                    else """
                    INSERT INTO reports (kind, period, title, body, graph_refs)
                    VALUES (:kind, :period, :title, :body, :graph_refs)
                    ON CONFLICT (kind, period) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "kind": kind,
                    "event_id": event_id,
                    "period": period,
                    "title": title,
                    "body": body,
                    "graph_refs": json.dumps(graph_refs or []),
                },
            )
            return result.scalar_one_or_none()

    def get_reports(self, limit: int = 20, kind: str | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, kind, event_id, period, title, body, graph_refs, created_at
                    FROM reports
                    WHERE (CAST(:kind AS TEXT) IS NULL OR kind = :kind)
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit, "kind": kind},
            ).mappings()
            return [dict(row) for row in rows]

    def get_digest(self, period: str) -> dict | None:
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        """
                    SELECT id, kind, event_id, period, title, body, graph_refs, created_at
                    FROM reports
                    WHERE kind = 'digest' AND period = :period
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                    ),
                    {"period": period},
                )
                .mappings()
                .first()
            )
            return dict(row) if row is not None else None

    def get_reports_since(self, last_id: int, limit: int = 50) -> list[dict]:
        """Reports with ``id`` above the cursor, oldest first (stream catch-up)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, kind, event_id, period, title, body, graph_refs, created_at
                    FROM reports
                    WHERE id > :last_id
                    ORDER BY id ASC
                    LIMIT :limit
                    """
                ),
                {"last_id": last_id, "limit": limit},
            ).mappings()
            return [dict(row) for row in rows]
