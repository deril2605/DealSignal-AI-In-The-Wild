from __future__ import annotations

import hashlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from dealsignal.agents.web_provider import WebCrawlerProvider
from dealsignal.models.source import Source

logger = logging.getLogger(__name__)


def fetch_sources(session: Session, provider: WebCrawlerProvider, raw_dir: str | Path = "data/raw") -> int:
    target_dir = Path(raw_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    pending_sources = session.scalars(
        select(Source).where(Source.status.in_(["discovered", "fetch_error"]))
    ).all()

    max_workers = _max_workers(provider)
    logger.info("Fetch concurrency: %d worker(s)", max_workers)

    fetched_by_source_id: dict[int, dict | None] = {}
    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {
                pool.submit(provider.fetch_article, source.url): source.id for source in pending_sources
            }
            for future in as_completed(future_map):
                source_id = future_map[future]
                try:
                    fetched_by_source_id[source_id] = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Fetch task failed for source_id=%s: %s", source_id, exc)
                    fetched_by_source_id[source_id] = None
    else:
        for source in pending_sources:
            fetched_by_source_id[source.id] = provider.fetch_article(source.url)

    processed = 0
    for source in pending_sources:
        article = fetched_by_source_id.get(source.id)
        if not article or not article.get("text"):
            source.status = "fetch_error"
            continue

        raw_text = article["text"]
        digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        text_path = target_dir / f"{digest}.txt"
        text_path.write_text(raw_text, encoding="utf-8")

        source.raw_text_hash = digest
        source.raw_text_path = str(text_path)
        source.title = article.get("title") or source.title
        source.published_at = _coerce_datetime(article.get("published_at")) or source.published_at
        source.status = "fetched"
        if not source.discovered_at:
            source.discovered_at = datetime.utcnow()
        processed += 1

    logger.info("Fetch complete. Processed: %s", processed)
    return processed


def _max_workers(provider: WebCrawlerProvider) -> int:
    requested = os.getenv("TINYFISH_MAX_AGENTS", "2")
    try:
        workers = int(requested)
    except ValueError:
        workers = 2
    workers = max(1, workers)
    if provider.__class__.__name__ != "TinyFishProvider":
        return 1
    return workers


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        value = value.strip().replace("Z", "")
        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
        ):
            try:
                return datetime.strptime(value[:26], fmt)
            except ValueError:
                continue
    return None
