"""Ingested payload models: required fields, base validation, clean schema.

Part of the ingestion suite split out of the former ``tests/test_ingestion.py`` (M4).
"""

from __future__ import annotations

import pytest

from datetime import datetime, timezone
from pydantic import ValidationError

from app.ingestion.source import RawTweet


def test_raw_tweet_requires_tweet_id():
    with pytest.raises(ValidationError):
        RawTweet(  # type: ignore[call-arg]
            author_handle="unusual_whales",
            author_id="1",
            text="hello",
            created_at=datetime.now(timezone.utc),
            url="https://x.com/x/status/1",
        )


def test_event_base_rejects_blank_tweet_id():
    from app.models.events import EventBase

    with pytest.raises(ValidationError):
        EventBase(  # type: ignore[call-arg]
            event_type="options",
            tweet_id="   ",
            tweet_url="u",
            confidence=0.5,
        )


def test_clean_schema_prunes_titles_and_nested_defs():
    from app.models.events import _clean_schema

    schema = {
        "title": "root",
        "definitions": {"unused": {}},
        "properties": {"a": {"title": "A", "type": "string"}},
        "$defs": {
            "Inner": {
                "title": "Inner",
                "properties": {"b": {"title": "B", "type": "string"}},
            }
        },
    }

    _clean_schema(schema)

    assert "title" not in schema and "definitions" not in schema
    assert "title" not in schema["properties"]["a"]
    assert "title" not in schema["$defs"]["Inner"]
    assert "title" not in schema["$defs"]["Inner"]["properties"]["b"]
