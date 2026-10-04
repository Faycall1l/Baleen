"""LLMClient transport: health, retries, payload guards, session reuse, drafting.

Part of the extraction suite split out of the former ``tests/test_extraction.py``
(M2), mirroring the ``app/extraction`` module split.
"""

from __future__ import annotations

import pytest

from app.extraction.client import LLMClient
from app.extraction.extractor import EventExtractor, build_tool_definitions
from app.extraction.protocol import LLMError, LLMResponse, LLMTransportError


class _BackupStub:
    """Stand-in backup: records the relayed call, replays a canned reply."""

    def __init__(self, reply: LLMResponse | None = None, error: Exception | None = None):
        self.reply = reply if reply is not None else LLMResponse(content="backup-ok")
        self.error = error
        self.calls: list[dict] = []
        self.closed = False
        self.probe_ok = True

    def health(self):
        return self.probe_ok

    def chat(self, messages, *, tools=None, tool_choice=None):
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        if self.error is not None:
            raise self.error
        return self.reply

    def close(self):
        self.closed = True


EXTRACTOR = EventExtractor(client=LLMClient("http://x", "k", "m"), max_tries=2)


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _OkResponse:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


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


def test_auto_mode_does_not_double_pay_for_transport_failures(monkeypatch):
    """#14: transport errors re-raise after their own backoff; the JSON
    fallback must not run a second full retry budget on top."""
    from app.extraction.client import LLMClient, LLMResponse, LLMTransportError

    client = LLMClient("http://x", "k", "m", tool_mode="auto", max_retries=2)
    calls = {"chat": 0, "json": 0}

    def dead_tools(messages, tools, tool_choice):
        calls["chat"] += 1
        raise LLMTransportError("connection refused")

    def json_chat(messages, tools):
        calls["json"] += 1
        return LLMResponse(content="{}", tool_calls=[])

    monkeypatch.setattr(client, "_chat_tools", dead_tools)
    monkeypatch.setattr(client, "_chat_json", json_chat)

    tools = build_tool_definitions()
    with pytest.raises(LLMTransportError):
        client.chat_with_tools([{"role": "user", "content": "hi"}], tools)

    assert calls["json"] == 0  # no JSON fallback after a transport error


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


def test_draft_text_uses_model_output():
    from app.extraction.client import LLMResponse, draft_text

    class Chatty:
        def chat(self, messages, **kwargs):
            return LLMResponse(content="  drafted copy  ")

    assert draft_text(Chatty(), system="s", prompt="p", fallback="fb") == "drafted copy"


def test_draft_text_falls_back_on_empty_or_error():
    from app.extraction.client import LLMResponse, LLMTransportError, draft_text

    class Empty:
        def chat(self, messages, **kwargs):
            return LLMResponse(content="   ")

    class Down:
        def chat(self, messages, **kwargs):
            raise LLMTransportError("down")

    assert draft_text(Empty(), system="s", prompt="p", fallback="fb") == "fb"
    assert draft_text(Down(), system="s", prompt="p", fallback="fb") == "fb"


def test_plain_chat_round_trip(monkeypatch):
    """chat() without tools runs the plain (non-tool) path end to end."""
    client = LLMClient("http://x", "k", "m")
    monkeypatch.setattr(
        client, "_post",
        lambda payload: {"choices": [{"message": {"role": "assistant", "content": "hello"}}]},
    )

    resp = client.chat([{"role": "user", "content": "hi"}])

    assert resp.content == "hello"
    assert not resp.has_tool_call


def test_chat_tools_success_path(monkeypatch):
    client = LLMClient("http://x", "k", "m")
    data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "extract_options_flow", "arguments": '{"ticker": "NVDA"}'}}
                    ],
                }
            }
        ]
    }
    monkeypatch.setattr(client, "_post", lambda payload: data)

    resp = client.chat(
        [{"role": "user", "content": "hi"}],
        tools=build_tool_definitions(),
        tool_choice="required",
    )

    assert resp.has_tool_call
    assert resp.tool_calls[0].name == "extract_options_flow"


def test_chat_tools_raises_when_model_ignores_tool_request(monkeypatch):
    """A 200 response with no tool_calls for a tools request is an LLMError —
    in `tools` mode it propagates (the caller decides what to do)."""
    from app.extraction.client import LLMError

    client = LLMClient("http://x", "k", "m", tool_mode="tools")
    monkeypatch.setattr(
        client, "_post",
        lambda payload: {"choices": [{"message": {"role": "assistant", "content": "sorry"}}]},
    )

    with pytest.raises(LLMError, match="no tool_calls"):
        client.chat(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "t"}}],
        )


def test_post_non_json_body_raises_transport(monkeypatch):
    import json

    from app.extraction.client import LLMTransportError

    class _FakeResponse:
        status_code = 200
        text = "<html>not json</html>"

        def json(self):
            raise json.JSONDecodeError("Expecting value", "", 0)

    client = LLMClient("http://llm", "k", "m")
    monkeypatch.setattr(client._http, "post", lambda *a, **k: _FakeResponse())

    with pytest.raises(LLMTransportError, match="non-JSON"):
        client._post({"messages": []})


def _dead_primary(monkeypatch, **kwargs):
    """Primary whose transport always fails; backup injectable via kwargs."""
    client = LLMClient("http://dead", "k", "m", **kwargs)

    def _boom(payload):
        raise LLMTransportError("connection refused")

    monkeypatch.setattr(client, "_post", _boom)
    return client


def test_chat_falls_back_to_backup_on_primary_failure(monkeypatch):
    backup = _BackupStub()
    client = _dead_primary(monkeypatch, backup=backup)
    response = client.chat([{"role": "user", "content": "hi"}])
    assert response.content == "backup-ok"
    assert len(backup.calls) == 1
    assert backup.calls[0]["messages"] == [{"role": "user", "content": "hi"}]


def test_chat_reraises_without_backup(monkeypatch):
    client = _dead_primary(monkeypatch)
    with pytest.raises(LLMTransportError, match="connection refused"):
        client.chat([{"role": "user", "content": "hi"}])


def test_chat_backup_failure_propagates(monkeypatch):
    backup = _BackupStub(error=LLMError("backup quota exhausted"))
    client = _dead_primary(monkeypatch, backup=backup)
    with pytest.raises(LLMError, match="backup quota exhausted"):
        client.chat([{"role": "user", "content": "hi"}])


def test_close_closes_backup():
    backup = _BackupStub()
    client = LLMClient("http://x", "k", "m", backup=backup)
    client.close()
    assert backup.closed is True


def test_json_mode_skips_response_format_when_disabled(monkeypatch):
    """Providers that flake on an explicit json_object constraint (Groq
    gpt-oss) get a plain prompt; the strict parser still guards output."""
    seen: dict = {}
    client = LLMClient("http://x", "k", "m", use_response_format=False)

    def _capture(payload):
        seen.update(payload)
        return {"choices": [{"message": {"role": "assistant", "content": '{"a": 1}'}}]}

    monkeypatch.setattr(client, "_post", _capture)
    response = client._chat_json([{"role": "user", "content": "hi"}], tools=[])
    assert "response_format" not in seen
    assert response.content == '{"a": 1}'


def test_json_mode_sends_response_format_by_default(monkeypatch):
    seen: dict = {}
    client = LLMClient("http://x", "k", "m")

    def _capture(payload):
        seen.update(payload)
        return {"choices": [{"message": {"role": "assistant", "content": '{"a": 1}'}}]}

    monkeypatch.setattr(client, "_post", _capture)
    client._chat_json([{"role": "user", "content": "hi"}], tools=[])
    assert seen.get("response_format") == {"type": "json_object"}


def test_healthy_short_circuits_when_primary_ok(monkeypatch):
    backup = _BackupStub()
    client = LLMClient("http://x", "k", "m", backup=backup)
    monkeypatch.setattr(client, "health", lambda: True)
    monkeypatch.setattr(backup, "health", lambda: (_ for _ in ()).throw(AssertionError("must not probe backup")))
    assert client.healthy() is True


def test_healthy_falls_back_to_backup(monkeypatch):
    backup = _BackupStub()
    client = LLMClient("http://x", "k", "m", backup=backup)
    monkeypatch.setattr(client, "health", lambda: False)
    assert client.healthy() is True


def test_healthy_false_when_both_down(monkeypatch):
    backup = _BackupStub()
    backup.probe_ok = False
    client = LLMClient("http://x", "k", "m", backup=backup)
    monkeypatch.setattr(client, "health", lambda: False)
    assert client.healthy() is False


def test_healthy_false_without_backup(monkeypatch):
    client = LLMClient("http://x", "k", "m")
    monkeypatch.setattr(client, "health", lambda: False)
    assert client.healthy() is False
