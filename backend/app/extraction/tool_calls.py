"""Constrained-JSON tool-call parsing (L3), split out of :mod:`app.extraction.client`.

Models wrap tool arguments in wildly varying envelopes (``arguments``,
``parameters``, ``params``, ``action_input``; object or string-encoded). This
module normalizes those shapes and turns a JSON-only reply into ``ToolCall``s,
raising ``InvalidJSONError`` when nothing maps to a known tool so the caller's
bounded-retry logic can try again.
"""

from __future__ import annotations

import json
import re

from app.extraction.protocol import InvalidJSONError, ToolCall

JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def unwrap_arguments(envelope: dict) -> dict:
    """Extract the field object from a tool-call envelope.

    Accepts ``arguments`` / ``parameters`` / ``params`` / ``action_input``
    (object or string-encoded JSON); falls back to the envelope itself when
    none is present (flat field objects). A bare-string ``action_input``
    (ReAct style) maps to ``{"cypher": ...}`` — the query agent's generator
    is the only single-string-input tool, and the extractor never receives
    bare strings.
    """
    for key in ("arguments", "parameters", "params", "action_input"):
        value = envelope.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                if key == "action_input":
                    return {"cypher": envelope[key]}
                continue
        if isinstance(value, dict):
            return value
    return envelope


def parse_tool_calls_from_json(content: str, tools: list[dict]) -> list[ToolCall]:
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
            # issue #14: a matched-but-malformed block must not leak the raw
            # JSONDecodeError (which no caller catches); normalize to
            # InvalidJSONError so bounding/retry logic keeps working.
            try:
                raw = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise InvalidJSONError("malformed JSON block in model output") from exc

        known = {t["function"]["name"] for t in tools}
        tool_calls: list[ToolCall] = []
        if isinstance(raw, dict) and "tool_calls" in raw:
            for tc in raw["tool_calls"]:
                name = (
                    tc.get("name")
                    or tc.get("tool")
                    or tc.get("action")
                    or tc.get("function", {}).get("name", "")
                )
                arguments = unwrap_arguments(tc)
                tool_calls.append(ToolCall(name=name, arguments=arguments))
        elif isinstance(raw, dict):
            name = raw.get("name") or raw.get("tool") or raw.get("action") or raw.get("event_type")
            if name in known:
                # Models wrap fields in varying envelopes ({"name",
                # "arguments"}, {"tool", "parameters"}, ...). Unwrap so the
                # extractor validates the actual fields, not the envelope.
                tool_calls.append(ToolCall(name=name, arguments=unwrap_arguments(raw)))
            elif name:
                raise InvalidJSONError(f"unknown tool target: {name}")
            else:
                # No name given — leave it to the caller to match against the
                # known schemas (the extractor validates against each model).
                tool_calls.append(ToolCall(name="", arguments=raw))

        if not tool_calls:
            raise InvalidJSONError("no tool call matched a known tool")
        return tool_calls
