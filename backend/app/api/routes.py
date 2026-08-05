"""REST routes for the three UI surfaces (spec §6):

- ``GET  /api/graph``  — the whole knowledge graph for force-directed rendering
- ``POST /api/query``  — natural-language question through the query agent
- ``GET  /api/alerts`` — recent alerts
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.alerts.engine import AlertEngine
from app.config import Settings, get_settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.extraction.client import LLMClient
from app.query.agent import QueryAgent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# Lazily-built shared clients (service objects are cheap to keep around).
_store: PostgresStore | None = None
_neo4j: Neo4jStore | None = None
_llm: LLMClient | None = None


def _get_settings() -> Settings:
    return get_settings()


def _get_store() -> PostgresStore:
    global _store
    if _store is None:
        _store = PostgresStore(_get_settings().postgres_dsn)
        _store.init_schema()
    return _store


def _get_neo4j() -> Neo4jStore:
    global _neo4j
    if _neo4j is None:
        _neo4j = Neo4jStore(_get_settings())
    return _neo4j


def _get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        s = _get_settings()
        _llm = LLMClient(
            base_url=s.llm_base_url,
            api_key=s.llm_api_key,
            model_name=s.llm_model_name,
            tool_mode=s.llm_tool_mode,
            max_retries=s.llm_max_retries,
            timeout_seconds=s.llm_timeout_seconds,
        )
    return _llm


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)


def _node_key(label: str, props: dict) -> str:
    from app.graph.schema import LABEL_KEYS

    key = LABEL_KEYS.get(label, "id")
    value = props.get(key)
    if value is None:
        value = props.get("id") or str(props)
    return str(value)


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


@router.get("/graph")
def graph() -> dict:
    """Serialize the full graph as {nodes, links} for the force-directed UI."""
    neo4j = _get_neo4j()
    try:
        node_rows = neo4j.run("MATCH (n) RETURN n AS node, labels(n) AS labels")
        link_rows = neo4j.run(
            """
            MATCH (a)-[r]->(b)
            RETURN a AS source, labels(a) AS a_label, type(r) AS rel,
                   b AS target, labels(b) AS b_label
            """
        )
    except Exception as exc:
        logger.exception("graph export failed")
        raise HTTPException(status_code=500, detail=f"graph export failed: {exc}")

    nodes = []
    ids_by_key: dict[tuple[str, str], str] = {}
    for row in node_rows:
        props = dict(row["node"].items())
        label = row["labels"][0]
        key = _node_key(label, props)
        node_id = f"{label}:{key}"
        ids_by_key[(label, key)] = node_id
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "key": key,
                "title": props.get("name") or props.get("ticker") or props.get("id") or node_id,
                "props": props,
                "tweet_url": props.get("tweet_url"),
            }
        )

    links = []
    for row in link_rows:
        a_label, b_label = row["a_label"][0], row["b_label"][0]
        a_key = _node_key(a_label, dict(row["source"].items()))
        b_key = _node_key(b_label, dict(row["target"].items()))
        source = ids_by_key.get((a_label, a_key))
        target = ids_by_key.get((b_label, b_key))
        if source and target:
            links.append(
                {
                    "source": source,
                    "target": target,
                    "type": row["rel"],
                    "tweet_url": (row["source"] or {}).get("tweet_url"),
                }
            )

    return {"nodes": nodes, "links": links, "node_count": len(nodes), "link_count": len(links)}


# ---------------------------------------------------------------------------
# Query agent
# ---------------------------------------------------------------------------


@router.post("/query")
def ask_question(request: QueryRequest) -> dict:
    agent = QueryAgent(llm=_get_llm(), store=_get_neo4j())
    try:
        result = agent.ask(request.question)
    except Exception as exc:
        logger.exception("query agent failed for question %r", request.question)
        raise HTTPException(status_code=500, detail=f"query agent failed: {exc}")
    return result.as_dict()


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@router.get("/alerts")
def alerts(limit: int = 20) -> dict:
    try:
        rows = _get_store().get_alerts(limit=limit)
    except Exception as exc:
        logger.exception("alerts fetch failed")
        raise HTTPException(status_code=500, detail=f"alerts fetch failed: {exc}")
    return {"alerts": rows, "count": len(rows)}


def get_alert_engine() -> AlertEngine:
    return AlertEngine(
        store=_get_store(),
        neo4j=_get_neo4j(),
        llm=_get_llm(),
        settings=_get_settings(),
    )


def _get_processing() -> "ProcessingPipeline":
    from app.extraction.extractor import EventExtractor
    from app.graph.writer import GraphWriter
    from app.pipeline import ProcessingPipeline

    s = _get_settings()
    return ProcessingPipeline(
        extractor=EventExtractor(client=_get_llm(), confidence_threshold=s.confidence_threshold),
        writer=GraphWriter(store=_get_neo4j()),
        alert_engine=get_alert_engine(),
        store=_get_store(),
    )


def close_services() -> None:
    """Close lazily-created clients (drivers, engines) for graceful shutdown."""
    global _store, _neo4j, _llm
    if _neo4j is not None:
        try:
            _neo4j.close()
        except Exception:
            logger.exception("neo4j close failed")
        _neo4j = None
    if _store is not None:
        try:
            _store.engine.dispose()
        except Exception:
            logger.exception("postgres dispose failed")
        _store = None
    _llm = None
    logger.info("shared service clients closed")
