from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from time import perf_counter

import uvicorn
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from dealsignal.agents.web_provider import (
    get_discovery_fallback_provider,
    get_discovery_provider,
    get_fetch_fallback_provider,
    get_fetch_primary_provider,
)
from dealsignal.models.database import SessionLocal, init_db, session_scope
from dealsignal.models.pipeline_run import PipelineRun
from dealsignal.pipeline.digest import generate_daily_digest
from dealsignal.pipeline.discover import discover_sources, load_watchlist
from dealsignal.pipeline.evals import store_daily_eval_snapshot
from dealsignal.pipeline.extract import extract_from_fetched_sources
from dealsignal.pipeline.fetch import fetch_sources
from dealsignal.state_sync import download_sqlite_from_blob

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

    discovery_provider = get_discovery_provider()
    discovery_fallback_provider = get_discovery_fallback_provider()
    fetch_primary_provider = get_fetch_primary_provider()
    fetch_fallback_provider = get_fetch_fallback_provider()
    logger.info(
        "Providers selected | discover=%s | discover_fallback=%s | fetch_primary=%s | fetch_fallback=%s",
        discovery_provider.__class__.__name__,
        discovery_fallback_provider.__class__.__name__ if discovery_fallback_provider else "None",
        fetch_primary_provider.__class__.__name__,
        fetch_fallback_provider.__class__.__name__ if fetch_fallback_provider else "None",
    )

    watchlist_entities = load_watchlist()
    if not watchlist_entities:
        logger.warning("No companies in watchlist.")
        return
    logger.info("Loaded watchlist with %d companies", len(watchlist_entities))

    run_id = _create_pipeline_run(fetch_primary_provider.__class__.__name__, len(watchlist_entities))
    stage_seconds: dict[str, float] = {}
    discovered = 0
    fetched = 0
    extracted = 0
    failed = 0

    try:
        with session_scope() as session:
            t0 = perf_counter()
            discovered = discover_sources(
                session,
                discovery_provider,
                watchlist_entities,
                fallback_provider=discovery_fallback_provider,
            )
            stage_seconds["discover"] = round(perf_counter() - t0, 2)
            logger.info("Stage discover complete: %d new sources (%.2fs)", discovered, stage_seconds["discover"])

            t0 = perf_counter()
            fetched = fetch_sources(
                session,
                fetch_primary_provider,
                fallback_provider=fetch_fallback_provider,
            )
            stage_seconds["fetch"] = round(perf_counter() - t0, 2)
            logger.info("Stage fetch complete: %d sources fetched (%.2fs)", fetched, stage_seconds["fetch"])

            t0 = perf_counter()
            extracted = extract_from_fetched_sources(session)
            stage_seconds["extract"] = round(perf_counter() - t0, 2)
            logger.info("Stage extract complete: %d new events (%.2fs)", extracted, stage_seconds["extract"])

            t0 = perf_counter()
            generate_daily_digest(session)
            stage_seconds["digest"] = round(perf_counter() - t0, 2)
            logger.info("Stage digest complete: reports/daily_digest.md updated (%.2fs)", stage_seconds["digest"])

            t0 = perf_counter()
            eval_rows = store_daily_eval_snapshot(session)
            stage_seconds["evals"] = round(perf_counter() - t0, 2)
            logger.info("Stage evals complete: %d rows stored (%.2fs)", eval_rows, stage_seconds["evals"])

            failed = _count_failed_sources(session)
        _finish_pipeline_run(
            run_id=run_id,
            status="completed",
            discovered=discovered,
            fetched=fetched,
            extracted=extracted,
            failed=failed,
            stage_seconds=stage_seconds,
        )
    except Exception as exc:
        _finish_pipeline_run(
            run_id=run_id,
            status="failed",
            discovered=discovered,
            fetched=fetched,
            extracted=extracted,
            failed=failed,
            stage_seconds=stage_seconds,
            error_message=str(exc)[:1000],
        )
        raise

    logger.info("Pipeline finished in %.2fs", perf_counter() - started_at)


def serve() -> None:
    # Pull latest persisted SQLite snapshot before starting local UI.
    download_sqlite_from_blob(logger=logger)
    init_db()
    uvicorn.run("dealsignal.app.main:app", host="127.0.0.1", port=8001, reload=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DealSignal CLI")
    parser.add_argument("command", choices=["run-pipeline", "serve"])
    return parser.parse_args()


def _create_pipeline_run(provider_name: str, watchlist_count: int) -> int:
    with SessionLocal() as session:
        run = PipelineRun(
            status="running",
            provider=provider_name,
            watchlist_count=watchlist_count,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def _finish_pipeline_run(
    run_id: int,
    status: str,
    discovered: int,
    fetched: int,
    extracted: int,
    failed: int,
    stage_seconds: dict[str, float],
    error_message: str | None = None,
) -> None:
    with SessionLocal() as session:
        run = session.get(PipelineRun, run_id)
        if not run:
            return
        run.status = status
        run.discovered_count = discovered
        run.fetched_count = fetched
        run.extracted_count = extracted
        run.failed_count = failed
        run.stage_seconds = stage_seconds
        run.error_message = error_message
        run.ended_at = datetime.utcnow()
        session.commit()


def _count_failed_sources(session: Session) -> int:
    from dealsignal.models.source import Source
    from sqlalchemy import func, select

    return int(session.scalar(select(func.count()).select_from(Source).where(Source.status == "fetch_error")) or 0)


if __name__ == "__main__":
    load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
    load_dotenv()
    configure_logging()
    args = parse_args()
    if args.command == "run-pipeline":
        run_pipeline()
    elif args.command == "serve":
        serve()
