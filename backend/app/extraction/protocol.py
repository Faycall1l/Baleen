"""The LLM response/error contract, split out of :mod:`app.extraction.client`.

These types are the only thing callers should know about the model: a
``LLMResponse`` with optional ``ToolCall``s, and a small error hierarchy that
distinguishes transient transport failures (retry later) from bad model output
(bounded retry, then mark unparsed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class LLMError(Exception):
    """Base error for any LLM call failure."""


class LLMTransportError(LLMError):
    """Transient infrastructure failure (network, timeout, HTTP error status).

    Raised for conditions that will not improve by marking the tweet as
    unparsed — the endpoint is unreachable or misbehaving at the transport
    level. Callers should leave the tweet pending and retry later.
    """


class InvalidJSONError(LLMError):
    """The model returned content that could not be parsed as JSON."""


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_call(self) -> bool:
        return bool(self.tool_calls)
