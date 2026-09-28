"""Constrained-JSON tool-call parsing and the tools/json/auto mode policy.

Part of the extraction suite split out of the former ``tests/test_extraction.py``
(M2), mirroring the ``app/extraction`` module split.
"""

from __future__ import annotations

import pytest

from app.extraction.client import LLMClient, LLMError, LLMResponse, ToolCall
from app.extraction.extractor import EventExtractor, build_tool_definitions
from app.extraction.tool_calls import parse_tool_calls_from_json
from app.models.events import CongressTradeEvent, OptionsFlowEvent

from tests.extraction.conftest import _tweet

EXTRACTOR = EventExtractor(client=LLMClient("http://x", "k", "m"), max_tries=2)


def test_constrained_json_parse_tool_call():
    client = LLMClient("http://x", "k", "m", tool_mode="json")
    tools = build_tool_definitions()

    calls = parse_tool_calls_from_json(
        '{"tool_calls": [{"name": "extract_options_flow", "arguments": {"ticker": "NVDA", "contract_type": "call", "confidence": 0.8}}]}',
        tools,
    )

    assert len(calls) == 1
    assert calls[0].name == "extract_options_flow"
    assert calls[0].arguments["ticker"] == "NVDA"


def test_constrained_json_parse_single_object():
    client = LLMClient("http://x", "k", "m", tool_mode="json")
    tools = build_tool_definitions()

    calls = parse_tool_calls_from_json(
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
        parse_tool_calls_from_json(
            '{"name": "extract_dark_pool", "ticker": "NVDA"}', tools
        )


def test_constrained_json_unwraps_name_arguments_envelope():
    """Some models wrap fields OpenAI-style ({"name", "arguments"}) instead of
    emitting them flat. The parser must validate the inner object, not the
    envelope — otherwise every such reply becomes unparsed."""
    client = LLMClient("http://x", "k", "m", tool_mode="json")
    tools = build_tool_definitions()

    calls = parse_tool_calls_from_json(
        '{"name": "extract_congress_trade", "arguments": {'
        '"politician_name": "David J. Taylor", "politician_id": "x", '
        '"chamber": "house", "party": "R", "ticker": "VRTX", '
        '"transaction_type": "sell", "amount_range": "$1,001 - $15,000", '
        '"transaction_date": "2026-08-26", "confidence": 1.0}}',
        tools,
    )

    assert len(calls) == 1
    assert calls[0].name == "extract_congress_trade"
    assert calls[0].arguments["ticker"] == "VRTX"
    assert calls[0].arguments["politician_name"] == "David J. Taylor"

    event = EXTRACTOR._validate_call(_tweet(), calls[0].name, calls[0].arguments)
    assert isinstance(event, CongressTradeEvent)


def test_constrained_json_unwraps_tool_parameters_envelope():
    """Same unwrapping for the {"tool", "parameters"} envelope shape."""
    client = LLMClient("http://x", "k", "m", tool_mode="json")
    tools = build_tool_definitions()

    calls = parse_tool_calls_from_json(
        '{"tool": "extract_options_flow", "parameters": {'
        '"ticker": "AAPL", "contract_type": "call", "confidence": 0.9}}',
        tools,
    )

    assert len(calls) == 1
    assert calls[0].name == "extract_options_flow"
    assert calls[0].arguments["ticker"] == "AAPL"

    event = EXTRACTOR._validate_call(_tweet(), calls[0].name, calls[0].arguments)
    assert isinstance(event, OptionsFlowEvent)


def test_constrained_json_unwraps_action_envelope():
    """ReAct-style {"action", "action_input"} envelope, where the input is a
    bare string (the query agent's single-input generator tool)."""
    from app.query.tools import GENERATE_TOOL

    client = LLMClient("http://x", "k", "m", tool_mode="json")

    calls = parse_tool_calls_from_json(
        '{"action": "generate_cypher", "action_input": "MATCH (t:Ticker) RETURN t"}',
        [GENERATE_TOOL],
    )

    assert len(calls) == 1
    assert calls[0].name == "generate_cypher"
    assert calls[0].arguments["cypher"] == "MATCH (t:Ticker) RETURN t"


def test_auto_mode_falls_back_to_json(monkeypatch):

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


def test_tools_mode_does_not_fallback_to_json(monkeypatch):

    client = LLMClient("http://x", "k", "m", tool_mode="tools")

    def broken_tools(messages, tools, tool_choice):
        raise LLMError("native tool-calling is broken")

    monkeypatch.setattr(client, "_chat_tools", broken_tools)

    with pytest.raises(LLMError, match="native tool-calling is broken"):
        client.chat([{"role": "user", "content": "hi"}], tools=[{"x": 1}])


def test_unwrap_arguments_handles_envelopes_and_bare_action_input():
    from app.extraction.tool_calls import unwrap_arguments

    assert unwrap_arguments({"arguments": {"cypher": "MATCH (n) RETURN n"}}) == {"cypher": "MATCH (n) RETURN n"}
    assert unwrap_arguments({"arguments": '{"a": 1}'}) == {"a": 1}
    assert unwrap_arguments({"parameters": {"b": 2}}) == {"b": 2}
    # ReAct-style bare string input maps to the query agent's single cypher field
    assert unwrap_arguments({"action_input": "MATCH (n) RETURN n"}) == {"cypher": "MATCH (n) RETURN n"}
    # Unparseable string in a non-action_input key falls through to the envelope
    assert unwrap_arguments({"arguments": "not-json", "x": 1}) == {"arguments": "not-json", "x": 1}
    # No known envelope key: flat field objects pass through unchanged
    assert unwrap_arguments({"flat": True}) == {"flat": True}


def test_constrained_json_empty_array_raises():
    from app.extraction.client import InvalidJSONError

    client = LLMClient("http://x", "k", "m")

    with pytest.raises(InvalidJSONError, match="no tool call matched"):
        parse_tool_calls_from_json("[]", [])


def test_malformed_closed_json_block_is_invalid_json_error():
    """A JSON-looking block that is balanced-brace but invalid inside surfaces
    InvalidJSONError (issue #14), not a bare JSONDecodeError."""
    from app.extraction.client import InvalidJSONError

    client = LLMClient("http://x", "k", "m")

    with pytest.raises(InvalidJSONError, match="malformed JSON block"):
        parse_tool_calls_from_json('prefix {"ticker": } trailing', [])


def test_malformed_json_block_is_invalid_json_error(monkeypatch):
    """#14: a matched-but-truncated JSON block surfaces InvalidJSONError,
    not a bare json.JSONDecodeError that no caller catches."""
    from app.extraction.client import InvalidJSONError, LLMClient

    monkeypatch.setattr("app.extraction.client.time.sleep", lambda s: None)
    monkeypatch.setattr(
        "app.extraction.client.LLMClient._post_once",
        lambda *a, **k: {"choices": [{"message": {
            "role": "assistant",
            "content": "prefix {\"ticker\": \"NVDA\", \"conf",
        }}]},
    )

    client = LLMClient("http://x", "k", "m", tool_mode="json", max_retries=0)
    with pytest.raises(InvalidJSONError):
        client.chat_with_tools([{"role": "user", "content": "hi"}], build_tool_definitions())
