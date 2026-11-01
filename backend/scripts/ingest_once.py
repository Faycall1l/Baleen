"""One-shot ingest + full processing pipeline.

Pushes tweets through the entire stack:

    ingest -> store -> extract (LLM) -> graph -> alerts

Default is a local demo: it loads ``SampleSource`` tweets (the same corpus the
tests use) so the graph and the alert panel are populated end-to-end. Skips
tweets already stored.

With ``--live`` it instead pulls from the real X API for the configured
handles and the search window from env, using the configured LLM endpoint
(e.g. vLLM).

Usage:
    python -m scripts.ingest_once                 # demo corpus
    python -m scripts.ingest_once --seed-ref      # also seed reference graph first
    python -m scripts.ingest_once --live          # real X API + configured LLM
    python -m scripts.ingest_once --real          # live public feeds:
                                                  # congressional disclosures +
                                                  # options radar (no keys needed)

Needs the Postgres and Neo4j containers running (``docker compose up -d``).
"""

from __future__ import annotations

import argparse
import logging

from app.config import Settings, get_settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore
from app.extraction.client import LLMClient, LLMTransportError
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
        max_retries=settings.llm_max_retries,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    from app.alerts.engine import AlertEngine
    from app.reports.engine import ReportEngine

    return ProcessingPipeline(
        extractor=EventExtractor(client=llm, confidence_threshold=settings.confidence_threshold),
        writer=GraphWriter(store=neo4j),
        alert_engine=AlertEngine(store=store, neo4j=neo4j, llm=llm),
        store=store,
        report_engine=ReportEngine(store=store, neo4j=neo4j, llm=llm),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Baleen one-shot ingest")
    parser.add_argument("--seed-ref", action="store_true", help="seed the reference graph first")
    parser.add_argument("--live", action="store_true", help="use the real X API instead of the sample corpus")
    parser.add_argument(
        "--real",
        action="store_true",
        help="use the live public feeds (congressional disclosures + options radar)",
    )
    args = parser.parse_args()

    settings = get_settings()
    store = PostgresStore(settings.postgres_dsn)
    store.init_schema()

    if args.seed_ref:
        from scripts.seed_reference import seed as seed_reference

        neo4j = Neo4jStore(settings)
        counts = seed_reference(neo4j)
        logger.info("reference graph seeded: %s", counts)

    if args.live:
        if not settings.x_api_configured:
            raise SystemExit(
                "X_API_BEARER_TOKEN is not set (or is a placeholder). "
                "Add a real token to your env (or repo-root .env) and retry."
            )
        llm = LLMClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model_name=settings.llm_model_name,
            tool_mode=settings.llm_tool_mode,
            max_retries=settings.llm_max_retries,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        if not llm.health():
            raise SystemExit(
                f"LLM endpoint is unreachable at {settings.llm_base_url} "
                "(probed GET /models). Is vLLM running? Check LLM_BASE_URL."
            )
        logger.info("LLM endpoint reachable: %s (model=%s)", settings.llm_base_url, settings.llm_model_name)

        from app.ingestion.x_api import XApiSource

        source = XApiSource(
            settings.x_api_bearer_token,
            max_pages=settings.x_max_pages,
            timeout_seconds=settings.x_api_timeout_seconds,
        )
        logger.info(
            "live source: handles=%s window=%ss llm=%s/%s",
            settings.x_handles,
            settings.x_search_window_seconds,
            settings.llm_base_url,
            settings.llm_model_name,
        )
    elif args.real:
        from app.ingestion.clerk import ClerkHouseSource
        from app.ingestion.disclosures import CongressDisclosureSource
        from app.ingestion.edgar import EdgarForm4Source
        from app.ingestion.news import NewsSource
        from app.ingestion.options import OptionsChainSource
        from app.ingestion.source import MultiSource

        source = MultiSource(
            [
                CongressDisclosureSource(),
                ClerkHouseSource(),
                EdgarForm4Source(user_agent=settings.edgar_user_agent),
                OptionsChainSource(),
                NewsSource(
                    window_seconds=settings.news_window_seconds,
                    poll_seconds=settings.news_poll_seconds,
                ),
            ]
        )
        logger.info("live public feeds: disclosures + insider filings + options radar + news")
    else:
        source = SampleSource()

    pipeline = IngestionPipeline(source, store, settings)
    metrics = pipeline.run_once()
    inserted, skipped = metrics["inserted"], metrics["duplicates_skipped"]
    logger.info("ingested %s new tweets (%s duplicates skipped)", inserted, skipped)

    processing = build_pipeline(settings)
    try:
        summary = processing.process_new(limit=settings.process_batch_size)
    except LLMTransportError:
        raise SystemExit(
            f"Processing failed because the LLM endpoint is unreachable "
            f"({settings.llm_base_url}). Tweets are left pending and will be "
            f"processed once vLLM recovers."
        ) from None
    logger.info("processing summary: %s", summary)
    print(f"\nDone. Ingested {inserted}, processed {summary}.")


if __name__ == "__main__":
    main()
