"""One-shot demo ingest + full processing pipeline.

Pushes the bundled sample tweets through the entire stack:

    ingest (sample source) -> store -> extract (LLM) -> graph -> alerts

Intended for local demos where X API credentials are placeholders: it loads
``SampleSource`` tweets (the same corpus the tests use) so the graph and the
alert panel are populated end-to-end. Skips tweets already stored.

Usage:
    python -m scripts.ingest_once            # ingest + process
    python -m scripts.ingest_once --seed-ref # also seed reference graph first

Needs the Postgres and Neo4j containers running (``docker compose up -d``) and,
for real extraction, an LLM endpoint (vLLM or ``scripts/llm_mock.py``).
"""

from __future__ import annotations

import argparse
import logging

from app.config import Settings, get_settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.extraction.client import LLMClient
from app.extraction.extractor import EventExtractor
from app.graph.writer import GraphWriter
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source import SampleSource
from app.pipeline import ProcessingPipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def build_pipeline(settings: Settings) -> ProcessingPipeline:
    store = PostgresStore(settings.postgres_dsn)
    store.init_schema()
    neo4j = Neo4jStore(settings)
    llm = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model_name=settings.llm_model_name,
        tool_mode=settings.llm_tool_mode,
    )
    from app.alerts.engine import AlertEngine

    return ProcessingPipeline(
        extractor=EventExtractor(client=llm, confidence_threshold=settings.confidence_threshold),
        writer=GraphWriter(store=neo4j),
        alert_engine=AlertEngine(store=store, neo4j=neo4j, llm=llm, settings=settings),
        store=store,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Baleen one-shot demo ingest")
    parser.add_argument("--seed-ref", action="store_true", help="seed the reference graph first")
    args = parser.parse_args()

    settings = get_settings()
    store = PostgresStore(settings.postgres_dsn)
    store.init_schema()

    if args.seed_ref:
        from scripts.seed_reference import seed as seed_reference

        neo4j = Neo4jStore(settings)
        counts = seed_reference(neo4j)
        logger.info("reference graph seeded: %s", counts)

    source = SampleSource()
    pipeline = IngestionPipeline(source, store, settings)
    metrics = pipeline.run_once()
    inserted, skipped = metrics["inserted"], metrics["duplicates_skipped"]
    logger.info("ingested %s new tweets (%s duplicates skipped)", inserted, skipped)

    processing = build_pipeline(settings)
    summary = processing.process_new()
    logger.info("processing summary: %s", summary)
    print(f"\nDone. Ingested {inserted}, processed {summary}.")


if __name__ == "__main__":
    main()
