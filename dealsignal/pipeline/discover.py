from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from dealsignal.agents.web_provider import WebCrawlerProvider
from dealsignal.models.company import Company
from dealsignal.models.source import Source

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "{company} CEO interview strategy",
    "{company} expansion plans",
    "{company} partnership announcement",
    "{company} fundraising round",
    "{company} acquisition plans",
]


def load_watchlist(path: str | Path = "config/watchlist.yaml") -> list[str]:
    watchlist_path = Path(path)
    if not watchlist_path.exists():
        logger.warning("Watchlist not found at %s", watchlist_path)
        return []
    data = yaml.safe_load(watchlist_path.read_text(encoding="utf-8")) or {}
    companies = data.get("companies", [])
    return [str(name).strip() for name in companies if str(name).strip()]


def seed_companies(session: Session, names: list[str]) -> list[Company]:
    companies: list[Company] = []
    for name in names:
        existing = session.scalar(select(Company).where(Company.name == name))
        if existing:
            companies.append(existing)
            continue
        company = Company(name=name, aliases=[], sector=None)
        session.add(company)
        session.flush()
        companies.append(company)
    return companies


def discover_sources(session: Session, provider: WebCrawlerProvider, company_names: list[str]) -> int:
    companies = seed_companies(session, company_names)
    inserted = 0
    max_workers = _max_workers(provider)
    logger.info("Discovery concurrency: %d worker(s)", max_workers)

    for company in companies:
        query_results: list[tuple[str, list[dict]]] = []
        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                future_map = {
                    pool.submit(provider.search, company.name, template.format(company=company.name), 5): template
                    for template in SEARCH_QUERIES
                }
                for future in as_completed(future_map):
                    template = future_map[future]
                    query = template.format(company=company.name)
                    try:
                        results = future.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Search task failed for '%s': %s", query, exc)
                        results = []
                    query_results.append((query, results))
        else:
            for template in SEARCH_QUERIES:
                query = template.format(company=company.name)
                results = provider.search(company.name, query, max_results=5)
                query_results.append((query, results))

        for _, results in query_results:
            for item in results:
                url = item.get("url")
                if not url:
                    continue
                exists = session.scalar(
                    select(Source).where(Source.company_id == company.id, Source.url == url)
                )
                if exists:
                    continue
                source = Source(
                    company_id=company.id,
                    url=url,
                    title=item.get("title"),
                    published_at=_coerce_datetime(item.get("published_at")),
                    status="discovered",
                )
                session.add(source)
                session.flush()
                inserted += 1
    logger.info("Discovery complete. New sources: %s", inserted)
    return inserted


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
        raw = value.strip().replace("Z", "")
        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
        ):
            try:
                return datetime.strptime(raw[:26], fmt)
            except ValueError:
                continue
    return None
