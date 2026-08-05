"""Structured extraction: ``RawTweet -> Event``.

The LLM is asked to call one of two tools — ``extract_options_flow`` or
``extract_congress_trade`` — whose JSON schemas are generated from the Pydantic
models in :mod:`app.models.events`. The response is validated by Pydantic and
never trusted raw. Anything that fails validation or scores below
``CONFIDENCE_THRESHOLD`` becomes an :class:`UnparsedEvent` (never dropped,
never force-fit).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.extraction.client import InvalidJSONError, LLMClient, LLMError
from app.ingestion.source import RawTweet
from app.models.events import (
    CongressTradeEvent,
    Event,
    OptionsFlowEvent,
    UnparsedEvent,
    as_json_schema,
)

logger = logging.getLogger(__name__)

#: Fields Baleen injects itself; the LLM is never asked for them.
PROVENANCE_FIELDS = {"event_type", "tweet_id", "tweet_url"}

#: Tool name -> (description, pydantic model) for each extractable event.
TOOLS: dict[str, tuple[str, type]] = {
    "extract_options_flow": (
        "Extract an unusual options flow report from a financial tweet. "
        "Set confidence based on how clearly the tweet reports actual flow.",
        OptionsFlowEvent,
    ),
    "extract_congress_trade": (
        "Extract a congressional trading disclosure from a financial tweet. "
        "Set confidence based on how clearly the tweet reports a real disclosure.",
        CongressTradeEvent,
    ),
}


def build_tool_definitions() -> list[dict[str, Any]]:
    """JSON tool definitions derived from the Pydantic event schemas."""
    tools = []
    for name, (description, model) in TOOLS.items():
        schema = as_json_schema(model)
        properties = {
            key: value
            for key, value in schema["properties"].items()
            if key not in PROVENANCE_FIELDS
        }
        required = [
            key
            for key in schema.get("required", [])
            if key not in PROVENANCE_FIELDS
        ]
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
        )
    return tools


class EventExtractor:
    def __init__(
        self,
        client: LLMClient,
        confidence_threshold: float = 0.6,
        max_tries: int = 2,
    ) -> None:
        self.client = client
        self.threshold = confidence_threshold
        self.max_tries = max_tries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, tweet: RawTweet) -> Event:
        """Classify one raw tweet into a typed event (never raises)."""
        tools = build_tool_definitions()
        messages = self._messages(tweet, tools)

        for attempt in range(self.max_tries):
            try:
                response = self.client.chat_with_tools(messages, tools)
            except LLMError as exc:
                logger.warning("LLM call failed for tweet %s: %s", tweet.tweet_id, exc)
                return self._unparsed(tweet, f"LLM call failed: {exc}")

            for call in response.tool_calls:
                event = self._validate_call(tweet, call.name, call.arguments)
                if event is not None:
                    return self._route(tweet, event)
                # Invalid call: fall through to retry / unparsed.

            if not response.tool_calls:
                # Native mode returned prose only (e.g. auto-mode fallback missed).
                return self._unparsed(tweet, "no tool call produced")

        return self._unparsed(tweet, "extraction failed after retries")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _messages(self, tweet: RawTweet, tools: list[dict]) -> list[dict]:
        schema_blocks = "\n\n".join(
            self._schema_block(tool) for tool in tools
        )
        system = (
            "You are Baleen, a strict financial-data extractor. Given a tweet you "
            "must call exactly one of the provided tools to extract structured "
            "event data. Populate every field you can support with evidence from "
            "the tweet; leave fields blank (null) when absent. Always include a "
            "numeric `confidence` from 0.0 to 1.0 reflecting how confidently the "
            "tweet matches the requested event type. Never invent tickers, "
            "politicians, amounts, or dates. If the tweet is not an options-flow "
            "or congress-trade report, call no tool.\n\n"
            "Possible output schemas:\n"
            f"{schema_blocks}"
        )
        user = (
            f"Tweet by @{tweet.author_handle} (id {tweet.tweet_id}):\n\n{tweet.text}"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _schema_block(self, tool: dict) -> str:
        params = tool["function"]["parameters"]
        return f"### {tool['function']['name']}\n{params}"

    def _validate_call(self, tweet: RawTweet, name: str, arguments: dict) -> Event | None:
        if name in TOOLS:
            _, model = TOOLS[name]
            return self._try_model(tweet, model, arguments)
        if name == "":
            # No tool name provided (constrained-JSON single object): match the
            # first schema that fully validates, options-flow then congress.
            for _, model in TOOLS.values():
                event = self._try_model(tweet, model, arguments)
                if event is not None:
                    return event
        logger.warning("unknown tool called: %s", name)
        return None

    def _try_model(self, tweet: RawTweet, model: type, arguments: dict) -> Event | None:
        payload = {
            "tweet_id": tweet.tweet_id,
            "tweet_url": tweet.url,
            **arguments,
        }
        try:
            return model.model_validate(payload)  # type: ignore[return-value]
        except ValidationError as exc:
            logger.warning("tool arguments failed schema validation: %s", exc)
            return None

    def _route(self, tweet: RawTweet, event: Event) -> Event:
        if event.confidence < self.threshold:
            logger.info(
                "tweet %s confidence %.2f below threshold %.2f -> unparsed",
                tweet.tweet_id,
                event.confidence,
                self.threshold,
            )
            return self._unparsed(
                tweet, f"confidence {event.confidence:.2f} below threshold {self.threshold}"
            )
        logger.info(
            "tweet %s extracted as %s (confidence=%.2f)",
            tweet.tweet_id,
            event.event_type,
            event.confidence,
        )
        return event

    def _unparsed(self, tweet: RawTweet, reason: str) -> UnparsedEvent:
        return UnparsedEvent(
            tweet_id=tweet.tweet_id,
            tweet_url=tweet.url,
            confidence=0.0,
            raw_text=tweet.text,
            reason=reason,
        )
