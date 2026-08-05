"""Event schemas for structured extraction (spec §5.2).

These models are the *only* thing the LLM may populate during extraction. The
LLM is never asked to emit freeform JSON — it calls tools whose JSON schemas are
generated from these classes (or, in the constrained-JSON fallback, is told to
emit exactly these shapes, which are then parsed by Pydantic).

Every event carries tweet provenance (``tweet_id`` / ``tweet_url``) and a
``confidence`` score, so anything written to the graph is traceable.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class EventBase(BaseModel):
    """Common fields for every extracted event."""

    event_type: str
    tweet_id: str
    tweet_url: str
    confidence: Confidence
    event_time: datetime | None = None

    @field_validator("tweet_id")
    @classmethod
    def _tweet_id_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tweet_id must not be empty")
        return value.strip()

    @property
    def deterministic_id(self) -> str:
        """Stable identifier used as the ``MERGE`` key in Neo4j.

        Recomputable from the event alone, so re-writing the same event never
        creates a duplicate.
        """
        payload = self.model_dump(mode="json", exclude={"event_type"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha1(f"{self.event_type}|{canonical}".encode()).hexdigest()[:16]
        return f"evt_{digest}"


class OptionsFlowEvent(EventBase):
    """A tweet reporting unusual options flow (calls/puts in a ticker)."""

    event_type: Literal["options_flow"] = "options_flow"
    ticker: str = Field(min_length=1, max_length=10)
    contract_type: Literal["call", "put"]
    strike: float | None = None
    expiration: date | None = None
    premium: float | None = Field(default=None, ge=0)
    volume: int | None = Field(default=None, ge=0)
    open_interest: int | None = Field(default=None, ge=0)
    sentiment: Literal["bullish", "bearish", "neutral"] = "neutral"

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, value: str) -> str:
        return value.strip().upper()


class CongressTradeEvent(EventBase):
    """A tweet reporting a congressional trade disclosure."""

    event_type: Literal["congress_trade"] = "congress_trade"
    politician_name: str = Field(min_length=1)
    politician_id: str = Field(min_length=1)
    chamber: Literal["house", "senate"]
    party: Literal["D", "R", "I"] | None = None
    committee_ids: list[str] = Field(default_factory=list)
    ticker: str = Field(min_length=1, max_length=10)
    transaction_type: Literal["buy", "sell"]
    amount_range: str | None = None
    transaction_date: date | None = None

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("committee_ids")
    @classmethod
    def _upper_committee_ids(cls, value: list[str]) -> list[str]:
        return [c.strip().upper() for c in value if c.strip()]


class UnparsedEvent(EventBase):
    """A tweet we could not confidently extract into a typed event.

    Never dropped, never force-fit into a wrong schema — stored as-is so the
    failure is visible and recoverable.
    """

    event_type: Literal["unparsed"] = "unparsed"
    raw_text: str
    reason: str


Event = Annotated[
    OptionsFlowEvent | CongressTradeEvent | UnparsedEvent,
    Field(discriminator="event_type"),
]

#: All event types the MVP understands (DarkPoolEvent intentionally omitted).
EVENT_TYPES = ["options_flow", "congress_trade", "unparsed"]

#: Regex used by the constrained-JSON fallback to carve JSON out of prose.
JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def as_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema of a model, cleaned for use in LLM tool definitions."""
    schema = model.model_json_schema()
    schema.pop("title", None)
    _clean_schema(schema)
    return schema


def _clean_schema(node: dict[str, Any]) -> None:
    for key in ("title", "definitions"):
        node.pop(key, None)
    props = node.get("properties")
    if isinstance(props, dict):
        for sub in props.values():
            if isinstance(sub, dict):
                _clean_schema(sub)
    for ref in node.get("$defs", {}).values():
        if isinstance(ref, dict):
            _clean_schema(ref)
