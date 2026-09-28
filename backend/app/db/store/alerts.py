"""Alert rules, alert rows, and the SSE cursor read, split out of
:mod:`app.db.postgres` (K2).

Mixed into ``PostgresStore``, which owns the shared SQLAlchemy engine, so every
method here stays a thin, auditable ``text()`` call.
"""

from __future__ import annotations

import json
from sqlalchemy import text


class AlertStoreMixin:
    def ensure_rule(self, name: str, description: str, config: dict | None = None) -> None:
        """Idempotently register a rule (no-op if it already exists)."""
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO alert_rules (name, description, config)
                    VALUES (:name, :description, :config)
                    ON CONFLICT (name) DO NOTHING
                    """
                ),
                {"name": name, "description": description, "config": json.dumps(config or {})},
            )

    def get_active_rules(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, name, description, config FROM alert_rules WHERE active = true"
                )
            ).mappings()
            return [dict(row) for row in rows]

    def insert_alert(
        self,
        rule_id: int,
        event_id: str,
        tweet_id: str,
        tweet_url: str,
        title: str,
        summary: str,
        graph_refs: list[str] | None = None,
    ) -> int | None:
        """Insert an alert. Returns its id, or ``None`` if the same (rule, event)
        already has an alert (idempotency)."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO alerts (rule_id, event_id, tweet_id, tweet_url, title, summary, graph_refs)
                    VALUES (:rule_id, :event_id, :tweet_id, :tweet_url, :title, :summary, :graph_refs)
                    ON CONFLICT (rule_id, event_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "rule_id": rule_id,
                    "event_id": event_id,
                    "tweet_id": tweet_id,
                    "tweet_url": tweet_url,
                    "title": title,
                    "summary": summary,
                    "graph_refs": json.dumps(graph_refs or []),
                },
            )
            return result.scalar_one_or_none()

    def get_alerts(self, limit: int = 20) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT a.id, a.event_id, a.tweet_id, a.tweet_url, a.title, a.summary,
                           a.graph_refs, a.created_at, r.name AS rule_name
                    FROM alerts a
                    JOIN alert_rules r ON r.id = a.rule_id
                    ORDER BY a.created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings()
            return [dict(row) for row in rows]

    def get_alerts_since(self, last_id: int, limit: int = 50) -> list[dict]:
        """Alerts with ``id`` above the cursor, oldest first (stream catch-up)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT a.id, a.event_id, a.tweet_id, a.tweet_url, a.title, a.summary,
                           a.graph_refs, a.created_at, r.name AS rule_name
                    FROM alerts a
                    JOIN alert_rules r ON r.id = a.rule_id
                    WHERE a.id > :last_id
                    ORDER BY a.id ASC
                    LIMIT :limit
                    """
                ),
                {"last_id": last_id, "limit": limit},
            ).mappings()
            return [dict(row) for row in rows]
