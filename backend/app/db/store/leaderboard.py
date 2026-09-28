"""Congressional-trade leaderboard projection, split out of
:mod:`app.db.postgres` (K2).

Mixed into ``PostgresStore``, which owns the shared SQLAlchemy engine, so every
method here stays a thin, auditable ``text()`` call.
"""

from __future__ import annotations

from sqlalchemy import text


class LeaderboardStoreMixin:
    def get_leaderboard(self, limit: int = 20) -> list[dict]:
        """Best-performing congressional trades by return since transaction.

        Reads the mirror-computed ``ret_since`` (percent) stored in each
        disclosure record — no LLM, no graph walk, just the filings.
        """
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT raw_json->>'filer_name' AS politician,
                           raw_json->>'ticker' AS ticker,
                           raw_json->>'transaction_type' AS side,
                           raw_json->>'transaction_date' AS date,
                           raw_json->>'amount_range_label' AS amount,
                           (raw_json->>'ret_since')::float AS ret_pct,
                           (raw_json->>'excess_since')::float AS excess_pct,
                           url AS source_url
    FROM raw_tweets
                   WHERE tweet_id LIKE 'ptr-%'
                     AND jsonb_typeof(raw_json->'ret_since') = 'number'
                     AND jsonb_typeof(raw_json->'excess_since') = 'number'
                   ORDER BY (raw_json->>'ret_since')::float DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings()
            return [dict(row) for row in rows]
