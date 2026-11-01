"""``GET /api/news`` backing store: monitored articles from ``raw_tweets``.

News records share the raw table with tweets and filings; ``raw_json`` carries
the article shape and marks it with ``source_kind == "news"``. Mentions are
computed on read from the reference roster, so an article stored before a
roster change still resolves against the current names.
"""

from __future__ import annotations

from sqlalchemy import text

from app.ingestion.news_parse import extract_mentions


class NewsStoreMixin:
    def get_news(self, limit: int = 30, outlet: str | None = None) -> list[dict]:
        """Latest monitored articles, newest first, with deterministic mentions."""
        sql = """
            SELECT tweet_id, author_handle, url, created_at, raw_json
            FROM raw_tweets
            WHERE raw_json->>'source_kind' = 'news'
        """
        params: dict = {"limit": limit}
        if outlet:
            sql += " AND author_handle = :outlet"
            params["outlet"] = outlet
        sql += " ORDER BY created_at DESC LIMIT :limit"

        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()

        articles = []
        for row in rows:
            meta = row["raw_json"] or {}
            title = meta.get("title") or ""
            summary = meta.get("summary") or ""
            articles.append(
                {
                    "id": row["tweet_id"],
                    "outlet": meta.get("outlet") or row["author_handle"],
                    "title": title,
                    "summary": summary,
                    "url": row["url"],
                    "published_at": row["created_at"].isoformat(),
                    "mentions": extract_mentions(title, summary),
                }
            )
        return articles
