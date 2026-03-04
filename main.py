from __future__ import annotations

import argparse
import logging
from time import perf_counter

import uvicorn
from dotenv import load_dotenv

from dealsignal.agents.web_provider import get_provider
from dealsignal.models.database import init_db, session_scope
from dealsignal.pipeline.digest import generate_daily_digest
from dealsignal.pipeline.discover import discover_sources, load_watchlist
from dealsignal.pipeline.extract import extract_from_fetched_sources
from dealsignal.pipeline.fetch import fetch_sources

logger = logging.getLogger("dealsignal.cli")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )


def run_pipeline() -> None:
    started_at = perf_counter()
    logger.info("Pipeline started")

    init_db()
    logger.info("Database initialized")

    provider = get_provider()
    logger.info("Web provider selected: %s", provider.__class__.__name__)

    company_names = load_watchlist()
    if not company_names:
        logger.warning("No companies in watchlist.")
        return
    logger.info("Loaded watchlist with %d companies", len(company_names))

    with session_scope() as session:
        t0 = perf_counter()
        discovered = discover_sources(session, provider, company_names)
        logger.info("Stage discover complete: %d new sources (%.2fs)", discovered, perf_counter() - t0)

        t0 = perf_counter()
        fetched = fetch_sources(session, provider)
        logger.info("Stage fetch complete: %d sources fetched (%.2fs)", fetched, perf_counter() - t0)

        t0 = perf_counter()
        extracted = extract_from_fetched_sources(session)
        logger.info("Stage extract complete: %d new events (%.2fs)", extracted, perf_counter() - t0)

        t0 = perf_counter()
        generate_daily_digest(session)
        logger.info("Stage digest complete: reports/daily_digest.md updated (%.2fs)", perf_counter() - t0)

    logger.info("Pipeline finished in %.2fs", perf_counter() - started_at)


def serve() -> None:
    init_db()
    uvicorn.run("dealsignal.app.main:app", host="127.0.0.1", port=8000, reload=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DealSignal CLI")
    parser.add_argument("command", choices=["run-pipeline", "serve"])
    return parser.parse_args()


if __name__ == "__main__":
    load_dotenv()
    configure_logging()
    args = parse_args()
    if args.command == "run-pipeline":
        run_pipeline()
    elif args.command == "serve":
        serve()
