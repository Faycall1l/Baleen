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


def test_transport_failure_raises_instead_of_unparsed(settings):
    from app.extraction.client import LLMTransportError

    class DownLLM:
        def chat_with_tools(self, messages, tools, tool_choice="auto"):
            raise LLMTransportError("connection refused")

    extractor = EventExtractor(client=DownLLM())

    with pytest.raises(LLMTransportError):
        extractor.extract(_tweet(text="big $NVDA options flow"))


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


def test_constrained_json_unwraps_name_arguments_envelope():
    """Some models wrap fields OpenAI-style ({"name", "arguments"}) instead of
    emitting them flat. The parser must validate the inner object, not the
    envelope — otherwise every such reply becomes unparsed."""
    client = LLMClient("http://x", "k", "m", tool_mode="json")
    tools = build_tool_definitions()

    calls = client._parse_tool_calls_from_json(
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

    calls = client._parse_tool_calls_from_json(
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
    from app.query.agent import GENERATE_TOOL

    client = LLMClient("http://x", "k", "m", tool_mode="json")

    calls = client._parse_tool_calls_from_json(
        '{"action": "generate_cypher", "action_input": "MATCH (t:Ticker) RETURN t"}',
        [GENERATE_TOOL],
    )

    assert len(calls) == 1
    assert calls[0].name == "generate_cypher"
    assert calls[0].arguments["cypher"] == "MATCH (t:Ticker) RETURN t"


def test_prompt_omits_internal_tweet_id():
    """The internal tweet_id is provenance injected post-hoc; pasting it into
    the prompt derails some models' structured output on identifier-shaped
    strings. The model must see handle + text only."""
    tweet = _tweet(tweet_id="opt-NVDA-2026-09-09-225-C-2026-09-09", text="hello")
    messages = EXTRACTOR._messages(tweet, build_tool_definitions())
    user = next(m["content"] for m in messages if m["role"] == "user")

    assert "opt-NVDA-2026-09-09-225-C-2026-09-09" not in user
    assert "@" in user and "hello" in user


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


# ---------------------------------------------------------------------------
# LLMClient resilience to ragged vLLM responses
# ---------------------------------------------------------------------------


def test_parse_tolerates_malformed_tool_call_arguments():
    """A tool call whose `arguments` is not valid JSON must not crash; the
    caller validates the (empty) payload and routes to unparsed/retry."""
    client = LLMClient("http://x", "k", "m")
    data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "1",
                            "type": "function",
                            "function": {
                                "name": "extract_options_flow",
                                "arguments": "not-json{{{",
                            },
                        }
                    ],
                }
            }
        ]
    }

    response = client._parse(data, parse_tools=True)

    assert response.has_tool_call
    assert response.tool_calls[0].name == "extract_options_flow"
    assert response.tool_calls[0].arguments == {}


def test_parse_raises_when_no_choices():
    from app.extraction.client import LLMError

    client = LLMClient("http://x", "k", "m")
    with pytest.raises(LLMError, match="no choices"):
        client._parse({"choices": []}, parse_tools=False)


def test_json_mode_gives_up_after_invalid_json_retries(monkeypatch):
    """Constrained-JSON mode retries bounded times on bad model output, then
    surfaces InvalidJSONError to the extractor (which routes to unparsed)."""
    from app.extraction.client import InvalidJSONError

    calls = {"n": 0}

    def junk_post(*_args, **_kwargs):
        calls["n"] += 1
        return {"choices": [{"message": {"role": "assistant", "content": "no json here"}}]}

    monkeypatch.setattr("app.extraction.client.time.sleep", lambda s: None)
    monkeypatch.setattr(
        "app.extraction.client.LLMClient._post_once",
        junk_post,
    )

    client = LLMClient("http://x", "k", "m", tool_mode="json", max_retries=2)
    tools = build_tool_definitions()
    with pytest.raises(InvalidJSONError):
        client.chat_with_tools([{"role": "user", "content": "hi"}], tools)

    assert calls["n"] == 3  # 1 attempt + 2 retries


# ---------------------------------------------------------------------------
# LLM endpoint health probe
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def test_health_true_when_models_endpoint_reachable(monkeypatch):
    client = LLMClient("http://llm", "k", "m")
    monkeypatch.setattr(client._http, "get", lambda *a, **k: _FakeResponse(200))
    assert client.health() is True


def test_health_false_when_endpoint_errors(monkeypatch):
    client = LLMClient("http://llm", "k", "m")
    monkeypatch.setattr(client._http, "get", lambda *a, **k: _FakeResponse(500))
    assert client.health() is False


def test_health_false_when_endpoint_unreachable(monkeypatch):
    import httpx

    client = LLMClient("http://llm", "k", "m")

    def raise_timeout(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(client._http, "get", raise_timeout)
    assert client.health() is False


# ---------------------------------------------------------------------------
# Transport failures raise LLMTransportError (tweets stay pending, not unparsed)
# ---------------------------------------------------------------------------


def test_post_http_error_status_raises_transport(monkeypatch):
    from app.extraction.client import LLMTransportError

    class _FakeResponse:
        status_code = 503
        text = "service unavailable"

    client = LLMClient("http://llm", "k", "m")
    monkeypatch.setattr(client._http, "post", lambda *a, **k: _FakeResponse())

    with pytest.raises(LLMTransportError, match="503"):
        client._post({"messages": []})


def test_post_network_failure_raises_transport(monkeypatch):
    import httpx

    from app.extraction.client import LLMTransportError

    client = LLMClient("http://llm", "k", "m")

    def raise_connect_error(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(client._http, "post", raise_connect_error)

    with pytest.raises(LLMTransportError):
        client._post({"messages": []})


# ---------------------------------------------------------------------------
# LLM transport retry / backoff
# ---------------------------------------------------------------------------


class _OkResponse:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


def test_post_retries_transport_then_recovers(monkeypatch):
    import httpx

    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("flaky")
        return _OkResponse()

    monkeypatch.setattr("app.extraction.client.time.sleep", lambda s: None)

    client = LLMClient("http://llm", "k", "m", max_retries=2)
    monkeypatch.setattr(client._http, "post", flaky)

    data = client._post({"messages": []})

    assert calls["n"] == 3
    assert data["choices"][0]["message"]["content"] == "ok"


def test_post_gives_up_after_max_retries(monkeypatch):
    import httpx

    calls = {"n": 0}

    def always_down(*a, **k):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    monkeypatch.setattr("app.extraction.client.time.sleep", lambda s: None)

    from app.extraction.client import LLMTransportError

    client = LLMClient("http://llm", "k", "m", max_retries=2)
    monkeypatch.setattr(client._http, "post", always_down)

    with pytest.raises(LLMTransportError):
        client._post({"messages": []})
    assert calls["n"] == 3  # 1 attempt + 2 retries


def test_json_mode_transport_not_double_retried(monkeypatch):
    """A down endpoint must cost (max_retries + 1) requests, not quadratic."""
    import httpx

    calls = {"n": 0}

    def always_down(*a, **k):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    monkeypatch.setattr("app.extraction.client.time.sleep", lambda s: None)

    from app.extraction.client import LLMTransportError

    tools = build_tool_definitions()
    client = LLMClient("http://llm", "k", "m", tool_mode="json", max_retries=1)
    monkeypatch.setattr(client._http, "post", always_down)

    with pytest.raises(LLMTransportError):
        client.chat_with_tools([{"role": "user", "content": "hi"}], tools)

    assert calls["n"] == 2  # 1 attempt + 1 retry, not 4


def test_payload_carries_determinism_guards():
    client = LLMClient("http://x", "k", "m", seed=7, max_tokens=512)
    payload = client._payload([{"role": "user", "content": "hi"}])

    assert payload["temperature"] == 0.0
    assert payload["seed"] == 7
    assert payload["max_tokens"] == 512
    client.close()


def test_payload_determinism_guards_on_by_default():
    payload = LLMClient("http://x", "k", "m")._payload([{"role": "user", "content": "hi"}])

    assert payload["seed"] == 42
    assert payload["max_tokens"] == 1024


def test_client_reuses_single_http_session():
    """Sequential calls share one pooled session (no per-call handshake)."""
    calls = {"n": 0}

    class _Ok:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    client = LLMClient("http://llm", "k", "m")
    session = client._http

    def counting_post(*a, **k):
        calls["n"] += 1
        assert client._http is session
        return _Ok()

    client._http.post = counting_post
    client._post({"messages": []})
    client._post({"messages": []})

    assert calls["n"] == 2
    client.close()
