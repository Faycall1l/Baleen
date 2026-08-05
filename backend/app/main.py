"""FastAPI entrypoint for the Baleen backend.

Phase 1 exposes a minimal app (health + pipeline trigger). Later phases add the
``/api`` routes (graph, query, alerts) and the scheduler startup.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(title="Baleen API", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": "baleen"}
