"""Extraction tests: schema validation, confidence routing, example tweets."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.extraction.client import LLMClient, LLMResponse, ToolCall
from app.extraction.extractor import EventExtractor, build_tool_definitions
from app.ingestion.source import RawTweet
from app.models.events import (
    CongressTradeEvent,
    OptionsFlowEvent,
    UnparsedEvent,
    as_json_schema,
)

from conftest import StubLLM, stub_tool_call

EXTRACTOR = EventExtractor(client=LLMClient("http://x", "k", "m"), max_tries=2)


def _tweet(tweet_id: str = "1", text: str = "hello") -> RawTweet:
    return RawTweet(
        tweet_id=tweet_id,
        author_handle="unusual_whales",
        author_id="9",
        text=text,
        created_at=datetime.now(timezone.utc),
        url=f"https://x.com/unusual_whales/status/{tweet_id}",
    )


# ---------------------------------------------------------------------------
# Tool definitions derived from schemas
# ---------------------------------------------------------------------------


def test_tool_definitions_are_derived_from_schemas():
    tools = build_tool_definitions()
    names = {t["function"]["name"] for t in tools}
    assert names == {"extract_options_flow", "extract_congress_trade"}

    options = next(
        t for t in tools if t["function"]["name"] == "extract_options_flow"
    )
    params = options["function"]["parameters"]
    # Provenance fields are injected by Baleen, not asked of the LLM.
    assert "tweet_id" not in params["properties"]
    assert "ticker" in params["properties"]
    assert "contract_type" in params["required"]


def test_schemas_reject_malformed_output():
    schema = as_json_schema(OptionsFlowEvent)

    class Malformed:
        @property
        def model_fields(self):
            return {}

    # A ticker longer than 10 chars must be rejected by the real model.
    with pytest.raises(ValueError):
        OptionsFlowEvent(
            tweet_id="1",
            tweet_url="u",
            confidence=0.9,
            ticker="A" * 11,
            contract_type="call",
        )
    assert schema["properties"]["contract_type"]["enum"] == ["call", "put"]


# ---------------------------------------------------------------------------
# Example tweets → typed events
# ---------------------------------------------------------------------------


def test_options_flow_tweet_extracts(settings):
    options = {
        "ticker": "NVDA",
        "contract_type": "call",
        "strike": 190.0,
        "expiration": "2026-06-21",
        "premium": 4500000.0,
        "volume": 1000,
        "sentiment": "bullish",
        "confidence": 0.9,
    }
    stub = StubLLM([("NVDA", stub_tool_call("extract_options_flow", options))])
    extractor = EventExtractor(client=stub)

    event = extractor.extract(
        _tweet(text="🚨 $NVDA 1,000 contracts of $190 calls 6/21 for $4.5M 🐂")
    )

    assert isinstance(event, OptionsFlowEvent)
    assert event.ticker == "NVDA"
    assert event.tweet_id == "1"
    assert event.deterministic_id


def test_congress_trade_tweet_extracts(settings):
    trade = {
        "politician_name": "Nancy Pelosi",
        "politician_id": "P000197",
        "chamber": "house",
        "party": "D",
        "committee_ids": ["HSAP"],
        "ticker": "NVDA",
        "transaction_type": "buy",
        "amount_range": "$1,001 - $15,000",
        "transaction_date": "2026-01-15",
        "confidence": 0.92,
    }
    stub = StubLLM([("Pelosi", stub_tool_call("extract_congress_trade", trade))])
    extractor = EventExtractor(client=stub)

    event = extractor.extract(
        _tweet(text="Rep. Nancy Pelosi (D-CA) disclosed a BUY of $NVDA $1,001-$15,000")
    )

    assert isinstance(event, CongressTradeEvent)
    assert event.ticker == "NVDA"
    assert event.politician_name == "Nancy Pelosi"


# ---------------------------------------------------------------------------
# Low confidence / failures route to UnparsedEvent
# ---------------------------------------------------------------------------


def test_low_confidence_routes_to_unparsed(settings):
    low = {
        "ticker": "TSLA",
        "contract_type": "call",
        "confidence": 0.4,  # below 0.6 threshold
    }
    stub = StubLLM([("TSLA", stub_tool_call("extract_options_flow", low))])
    extractor = EventExtractor(client=stub, confidence_threshold=0.6)

    event = extractor.extract(_tweet(text="Maybe something with $TSLA calls"))

    assert isinstance(event, UnparsedEvent)
    assert "below threshold" in event.reason


def test_irrelevant_tweet_routes_to_unparsed(settings):
    extractor = EventExtractor(client=StubLLM([]))  # no tool calls ever
    event = extractor.extract(_tweet(text="RT @friend happy weekend everyone"))

    assert isinstance(event, UnparsedEvent)
    assert event.reason == "no tool call produced"


def test_malformed_tool_arguments_route_to_unparsed(settings):
    bad = {"ticker": "NVDA"}  # missing contract_type (required)
    stub = StubLLM(
        [
            ("NVDA", [stub_tool_call("extract_options_flow", bad)]),
            ("NVDA", [stub_tool_call("extract_options_flow", bad)]),  # both tries fail
        ]
    )
    extractor = EventExtractor(client=stub, max_tries=2)

    event = extractor.extract(_tweet(text="something about $NVDA"))

    assert isinstance(event, UnparsedEvent)
    assert "failed after retries" in event.reason


# ---------------------------------------------------------------------------
# Constrained-JSON fallback parsing
# ---------------------------------------------------------------------------


def test_constrained_json_parse_tool_call():
    client = LLMClient("http://x", "k", "m", tool_mode="json")
    tools = build_tool_definitions()

    calls = client._parse_tool_calls_from_json(
        '{"tool_calls": [{"name": "extract_options_flow", "arguments": {"ticker": "NVDA", "contract_type": "call", "confidence": 0.8}}]}',
        tools,
    )

    assert len(calls) == 1
    assert calls[0].name == "extract_options_flow"
    assert calls[0].arguments["ticker"] == "NVDA"


def test_constrained_json_parse_single_object():
    client = LLMClient("http://x", "k", "m", tool_mode="json")
    tools = build_tool_definitions()

    calls = client._parse_tool_calls_from_json(
        '{"ticker": "NVDA", "contract_type": "put", "confidence": 0.7}',
        tools,
    )

    # No explicit name — the extractor matches it against the schemas.
    assert calls[0].name == ""
    assert calls[0].arguments["ticker"] == "NVDA"

    event = EXTRACTOR._validate_call(_tweet(), calls[0].name, calls[0].arguments)
    assert isinstance(event, OptionsFlowEvent)


def test_constrained_json_rejects_unknown_tool():
    from app.extraction.client import InvalidJSONError

    client = LLMClient("http://x", "k", "m", tool_mode="json")
    tools = build_tool_definitions()
    with pytest.raises(InvalidJSONError):
        client._parse_tool_calls_from_json(
            '{"name": "extract_dark_pool", "ticker": "NVDA"}', tools
        )


def test_auto_mode_falls_back_to_json(monkeypatch):
    from app.extraction.client import LLMError

    client = LLMClient("http://x", "k", "m", tool_mode="auto")

    def broken_tools(messages, tools, tool_choice):
        raise LLMError("tools unsupported")

    def json_chat(messages, tools):
        return LLMResponse(
            content='{"ticker": "NVDA", "contract_type": "call", "confidence": 0.8}',
            tool_calls=[
                ToolCall(
                    "extract_options_flow",
                    {"ticker": "NVDA", "contract_type": "call", "confidence": 0.8},
                )
            ],
        )

    monkeypatch.setattr(client, "_chat_tools", broken_tools)
    monkeypatch.setattr(client, "_chat_json", json_chat)

    tools = build_tool_definitions()
    response = client.chat_with_tools([{"role": "user", "content": "hi"}], tools)

    assert response.has_tool_call
