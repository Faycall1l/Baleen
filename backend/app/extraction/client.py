"""Thin OpenAI-compatible client for the local vLLM endpoint.

Everything is driven by ``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``LLM_MODEL_NAME``
(plus ``LLM_TOOL_MODE``). No provider SDK is hardcoded — this is the only place
Baleen talks to an LLM.

Tool-calling modes (``LLM_TOOL_MODE``):

- ``tools`` — send native ``tools`` in the chat-completions payload and expect
  ``tool_calls`` back. Requires a model with OpenAI-style tool support.
- ``json`` — constrained-JSON fallback: the caller embeds a JSON schema in the
  system prompt; we strictly parse the reply and raise ``InvalidJSONError`` on
  bad output (the caller retries, bounded).
- ``auto`` (default) — try ``tools`` first; if the endpoint errors on the
  ``tools`` payload or returns no ``tool_calls``, transparently fall back to
  ``json`` mode.

Placeholder credentials are fine: the client is only exercised at call time,
and tests use an in-process stub. See docs/SETUP.md for wiring the real vLLM.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


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


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        tool_mode: str = "auto",
        max_retries: int = 2,
        timeout_seconds: float = 90.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.tool_mode = tool_mode
        self.max_retries = max_retries
        self.timeout = timeout_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        """One chat-completion round trip with tool-mode awareness.

        ``tools`` may be ``None`` (plain completion). For tool extraction use
        :meth:`chat_with_tools` which handles the fallback semantics.
        """
        if tools:
            if self.tool_mode in ("auto", "tools"):
                try:
                    return self._chat_tools(messages, tools, tool_choice)
                except LLMError as exc:
                    if self.tool_mode == "tools":
                        raise
                    logger.warning(
                        "native tool-calling failed (%s); falling back to constrained JSON",
                        exc,
                    )
            return self._chat_json(messages, tools)
        return self._chat_plain(messages)

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
    ) -> LLMResponse:
        """Tool-calling chat honoring ``LLM_TOOL_MODE`` semantics.

        Returns a response carrying ``tool_calls`` (tools/json modes both parse
        into :class:`ToolCall`).
        """
        return self.chat(messages, tools=tools, tool_choice=tool_choice)

    def health(self) -> bool:
        """Probe the OpenAI-compatible ``/models`` endpoint. Never raises.

        Used as a preflight check before live runs so a misconfigured vLLM
        address fails fast with a clear message instead of mid-pipeline.
        Sends the configured API key: auth-gated endpoints (401 without it)
        must report healthy when the key is valid.
        """
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            response = httpx.get(
                f"{self.base_url}/models", headers=headers, timeout=min(self.timeout, 10)
            )
            return response.status_code < 400
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------
    # Mode implementations
    # ------------------------------------------------------------------

    def _post(self, payload: dict) -> dict:
        """POST with bounded exponential backoff on transport failures.

        Network errors, HTTP error statuses, and non-JSON gateway responses are
        retried up to ``max_retries`` times so a transient vLLM restart or
        network blip does not fail an otherwise-good run. Content errors (bad
        model output) are the caller's concern and are never retried here.
        """
        attempt = 0
        while True:
            try:
                return self._post_once(payload)
            except LLMTransportError as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                delay = min(2.0 ** (attempt - 1), 8.0)
                logger.warning(
                    "LLM transport failure (%d/%d): %s; retrying in %.1fs",
                    attempt,
                    self.max_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)

    def _post_once(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise LLMTransportError(f"LLM transport error: {exc}") from exc

        if response.status_code >= 400:
            raise LLMTransportError(
                f"LLM endpoint returned {response.status_code}: {response.text[:300]}"
            )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise LLMTransportError(
                f"LLM returned non-JSON response: {response.text[:300]}"
            ) from exc

    def _chat_plain(self, messages: list[dict]) -> LLMResponse:
        payload = self._payload(messages)
        data = self._post(payload)
        return self._parse(data, parse_tools=False)

    def _chat_tools(
        self, messages: list[dict], tools: list[dict], tool_choice: str | None
    ) -> LLMResponse:
        payload = self._payload(messages, tools=tools, tool_choice=tool_choice)
        data = self._post(payload)
        response = self._parse(data, parse_tools=True)

        # Some servers accept `tools` but silently ignore them. Treat a missing
        # tool call as a fallback trigger (raised so `auto` can retry in json mode).
        if response.has_tool_call:
            return response
        raise LLMError("model returned no tool_calls for tool request")

    def _chat_json(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        """Constrained-JSON fallback: strict parse with bounded retries."""
        attempt = 0
        while True:
            try:
                payload = self._payload(messages, response_format={"type": "json_object"})
                data = self._post(payload)
                response = self._parse(data, parse_tools=False)
                return LLMResponse(
                    content=response.content,
                    tool_calls=self._parse_tool_calls_from_json(response.content, tools),
                    raw=response.raw,
                )
            except LLMTransportError:
                # Already retried with backoff inside _post; do not retry here
                # (that would make transport attempts quadratic).
                raise
            except (InvalidJSONError, LLMError) as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                logger.warning("constrained-JSON parse failed (%s), retry %d", exc, attempt)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _payload(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        response_format: dict | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.0,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if response_format:
            payload["response_format"] = response_format
        return payload

    def _parse(self, data: dict, *, parse_tools: bool) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("LLM response had no choices")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        tool_calls: list[ToolCall] = []
        if parse_tools:
            for tc in message.get("tool_calls") or []:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                try:
                    arguments = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                tool_calls.append(ToolCall(name=name, arguments=arguments))
        return LLMResponse(content=content, tool_calls=tool_calls, raw=data)

    def _parse_tool_calls_from_json(
        self, content: str, tools: list[dict]
    ) -> list[ToolCall]:
        """Parse a constrained-JSON reply into tool calls.

        Accepts either ``{"tool_calls": [...]}`` (matching the tools payload
        shape) or a single object shaped like one tool's schema. Raises
        ``InvalidJSONError`` when the content cannot be mapped to a known tool.
        """
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            match = JSON_BLOCK_RE.search(content)
            if not match:
                raise InvalidJSONError("no JSON block found in model output")
            raw = json.loads(match.group(0))

        known = {t["function"]["name"] for t in tools}
        tool_calls: list[ToolCall] = []
        if isinstance(raw, dict) and "tool_calls" in raw:
            for tc in raw["tool_calls"]:
                name = tc.get("name") or tc.get("function", {}).get("name", "")
                arguments = tc.get("arguments", {})
                tool_calls.append(ToolCall(name=name, arguments=arguments))
        elif isinstance(raw, dict):
            name = raw.get("name") or raw.get("event_type")
            if name in known:
                tool_calls.append(ToolCall(name=name, arguments=raw))
            elif name:
                raise InvalidJSONError(f"unknown tool target: {name}")
            else:
                # No name given — leave it to the caller to match against the
                # known schemas (the extractor validates against each model).
                tool_calls.append(ToolCall(name="", arguments=raw))

        if not tool_calls:
            raise InvalidJSONError("no tool call matched a known tool")
        return tool_calls
