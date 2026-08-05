"""Minimal OpenAI-compatible LLM server for local development.

Lets Baleen run end-to-end without vLLM/GPU: exposes ``/v1/chat/completions``
with enough tool-call support for the extraction stage (it echoes back a
canned tool call per request). Used by ``scripts/ingest_once.py`` demos.

Usage:
    python -m scripts.llm_mock            # serve on :8001/v1
    LLM_BASE_URL=http://localhost:8001/v1 python -m scripts.ingest_once

This is a dev tool, not part of the production path.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

import uvicorn
from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Baleen LLM mock")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict:
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools", [])
    model = body.get("model", "mock")

    user_text = next((m["content"] for m in messages if m["role"] == "user"), "")

    tool_call = None
    if tools:
        tool_call = _pick_tool(user_text, tools)

    content: str | None = None
    tool_calls: list[dict] = []
    if tool_call is not None:
        tool_calls = [
            {
                "id": f"call_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {"name": tool_call[0], "arguments": json.dumps(tool_call[1])},
            }
        ]
    else:
        content = "Mock summary: a congressional trade was detected. See the source tweet for details."

    logger.info("mock LLM: tool_calls=%s", [t["function"]["name"] for t in tool_calls])

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "tool_calls": tool_calls or None},
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
    }


def _pick_tool(user_text: str, tools: list[dict]) -> tuple[str, dict] | None:
    """Return a plausible (name, arguments) for the given request."""
    lowered = user_text.lower()
    available = {t["function"]["name"]: t["function"] for t in tools}

    # Query agent: generate a read-only Cypher for the question.
    if "generate_cypher" in available:
        return "generate_cypher", {
            "cypher": (
                "MATCH (p:Politician)<-[:INVOLVES_POLITICIAN]-(e:Event)"
                "-[:INVOLVES_TICKER]->(t:Ticker)-[:IN_SECTOR]->(s:Sector) "
                "RETURN p.name AS politician, t.ticker AS ticker, s.name AS sector, "
                "e.transaction_type AS transaction_type, e.amount_range AS amount_range, "
                "e.transaction_date AS transaction_date, e.tweet_url AS tweet_url "
                "ORDER BY e.transaction_date DESC LIMIT 10"
            )
        }

    # Query agent: draft the grounded answer from the result rows.
    if "answer_question" in available:
        return "answer_question", {
            "answer": (
                "Based on the graph, the traded tickers and their sectors are shown "
                "in the query results. The politician most recently active in an "
                "oversight sector is listed first."
            )
        }

    if "sold" in lowered or "purchased" in lowered or "disclosure" in lowered:
        if "extract_congress_trade" in available:
            ticker = _find_ticker(user_text)
            args = {
                "ticker": ticker,
                "politician_name": "Bill Cassidy",
                "politician_id": "C000261",
                "chamber": "senate",
                "party": "R",
                "committee_ids": ["SSHR"],
                "transaction_type": "sell" if "sold" in lowered else "buy",
                "amount_range": "$50,001 - $100,000",
                "transaction_date": "2026-05-14",
                "confidence": 0.95,
            }
            return "extract_congress_trade", args

    if "extract_options_flow" in available:
        args = {
            "ticker": _find_ticker(user_text),
            "contract_type": "call",
            "strike": 130.0,
            "expiration": "2026-06-20",
            "direction": "buy",
            "notional_usd": 97000000,
            "confidence": 0.9,
        }
        return "extract_options_flow", args

    return None


def _find_ticker(text: str) -> str:
    import re

    match = re.search(r"\$([A-Z]{2,5})\b", text.upper())
    return match.group(1) if match else "NVDA"


if __name__ == "__main__":
    import os

    port = int(os.getenv("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
