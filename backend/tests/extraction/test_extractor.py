"""EventExtractor: per-event tool output, validation, and routing to unparsed."""

from __future__ import annotations

import pytest
from conftest import StubLLM, stub_tool_call

from app.extraction.client import LLMClient
from app.extraction.extractor import EventExtractor, build_tool_definitions
from app.models.events import (
    CongressTradeEvent,
    OptionsFlowEvent,
    UnparsedEvent,
    as_json_schema,
)
from tests.extraction.conftest import _tweet

EXTRACTOR = EventExtractor(client=LLMClient("http://x", "k", "m"), max_tries=2)


def test_tool_definitions_are_derived_from_schemas():
    tools = build_tool_definitions()
    names = {t["function"]["name"] for t in tools}
    assert names == {"extract_options_flow", "extract_congress_trade", "extract_insider_trade"}

    options = next(t for t in tools if t["function"]["name"] == "extract_options_flow")
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

    event = extractor.extract(_tweet(text="🚨 $NVDA 1,000 contracts of $190 calls 6/21 for $4.5M 🐂"))

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

    event = extractor.extract(_tweet(text="Rep. Nancy Pelosi (D-CA) disclosed a BUY of $NVDA $1,001-$15,000"))

    assert isinstance(event, CongressTradeEvent)
    assert event.ticker == "NVDA"
    assert event.politician_name == "Nancy Pelosi"


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


def test_transport_failure_raises_instead_of_unparsed(settings):
    from app.extraction.client import LLMTransportError

    class DownLLM:
        def chat_with_tools(self, messages, tools, tool_choice="auto"):
            raise LLMTransportError("connection refused")

    extractor = EventExtractor(client=DownLLM())

    with pytest.raises(LLMTransportError):
        extractor.extract(_tweet(text="big $NVDA options flow"))


def test_non_transport_llm_error_becomes_unparsed(settings):
    """A generic LLMError (not a transport failure) decays to an UnparsedEvent
    instead of raising, so one flaky schema/format error can't escape."""
    from app.extraction.client import LLMError

    class _BadLLM:
        def chat_with_tools(self, messages, tools, tool_choice="auto"):
            raise LLMError("bad model response")

    extractor = EventExtractor(client=_BadLLM())

    event = extractor.extract(_tweet(text="$NVDA calls filling", tweet_id="x9"))

    assert isinstance(event, UnparsedEvent)
    assert "LLM call failed" in event.reason


def test_unknown_tool_call_returns_none():
    """A tool call naming something outside TOOLS is refused (returns None)
    rather than silently forwarded downstream."""
    assert EXTRACTOR._validate_call(_tweet(), "made_up_tool", {}) is None


def test_prompt_omits_internal_tweet_id():
    """The internal tweet_id is provenance injected post-hoc; pasting it into
    the prompt derails some models' structured output on identifier-shaped
    strings. The model must see handle + text only."""
    tweet = _tweet(tweet_id="opt-NVDA-2026-09-09-225-C-2026-09-09", text="hello")
    messages = EXTRACTOR._messages(tweet, build_tool_definitions())
    user = next(m["content"] for m in messages if m["role"] == "user")

    assert "opt-NVDA-2026-09-09-225-C-2026-09-09" not in user
    assert "@" in user and "hello" in user


def test_insider_trade_tool_validates():
    from app.models.events import InsiderTradeEvent

    event = EXTRACTOR._validate_call(
        _tweet(),
        "extract_insider_trade",
        {
            "insider_name": "Milton C Ault III",
            "insider_cik": "0001212502",
            "insider_id": "0001212502",
            "role": "Chairman",
            "ticker": "GPUS",
            "transaction_type": "buy",
            "shares": 1000.0,
            "price": 2.5,
            "amount_range": "$2,500.00",
            "transaction_date": "2026-09-09",
            "confidence": 0.95,
        },
    )
    assert isinstance(event, InsiderTradeEvent)
    assert event.ticker == "GPUS"


def test_tool_definitions_are_built_once():
    """Schemas are static; rebuilds on every extract() call are pure waste."""
    first = build_tool_definitions()
    second = build_tool_definitions()

    assert first is second
    assert {t["function"]["name"] for t in first} == {
        "extract_options_flow",
        "extract_congress_trade",
        "extract_insider_trade",
    }
