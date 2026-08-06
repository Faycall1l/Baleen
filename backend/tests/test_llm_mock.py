"""The bundled LLM mock (dev tool) must satisfy the OpenAI-compatible contract
that preflight and the ``--live`` health probe rely on (``GET /v1/models``),
plus serve tool calls for the extraction stage."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scripts.llm_mock import app

client = TestClient(app)


def test_models_endpoint():
    response = client.get("/v1/models")
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert any(m["id"] for m in payload["data"])


def test_chat_completions_returns_tool_call_for_extraction():
    payload = {
        "model": "mock",
        "messages": [
            {"role": "system", "content": "extract an options flow"},
            {
                "role": "user",
                "content": "$NVDA 7,500 calls at $130 expiring 06/20 are filling right now.",
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "extract_options_flow",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    }
    response = client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 200
    message = response.json()["choices"][0]["message"]
    assert message["tool_calls"][0]["function"]["name"] == "extract_options_flow"


def test_chat_completions_plain_completion_when_no_tool_matches():
    payload = {
        "model": "mock",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [],
    }
    response = client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 200
    message = response.json()["choices"][0]["message"]
    assert message["content"]
