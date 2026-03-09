from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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

HIGH_SIGNAL_DOMAINS = {
    "reuters.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "techcrunch.com",
    "theinformation.com",
    "cnbc.com",
    "sec.gov",
    "prnewswire.com",
    "businesswire.com",
}

BLOCKED_DOMAINS = {
    "newsbreak.com",
    "nomadwayhome.com",
    "pinterest.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
}

STRATEGIC_TERMS = {
    "expansion",
    "fundraising",
    "funding",
    "round",
    "acquisition",
    "acquire",
    "merger",
    "partnership",
    "strategy",
    "hiring",
    "leadership",
    "regulatory",
}

POLICY_PATH = "config/source_policy.yaml"


@dataclass
class WatchlistEntity:
    name: str
    execs: list[str]
    themes: list[str]
    aliases: list[str]
    sector: str | None = None


BASE_QUERY_TEMPLATES = [
    "{company} CEO interview strategy",
    "{company} expansion plans",
    "{company} partnership announcement",
    "{company} fundraising round",
    "{company} acquisition plans",
]


def load_watchlist(path: str | Path = "config/watchlist.yaml") -> list[WatchlistEntity]:
    watchlist_path = Path(path)
    if not watchlist_path.exists():
        logger.warning("Watchlist not found at %s", watchlist_path)
        return []
    data = yaml.safe_load(watchlist_path.read_text(encoding="utf-8")) or {}
    raw_companies = data.get("companies", [])
    entities: list[WatchlistEntity] = []
    for entry in raw_companies:
        parsed = _parse_watchlist_entity(entry)
        if parsed:
            entities.append(parsed)
    return entities


def seed_companies(session: Session, entities: list[WatchlistEntity]) -> list[Company]:
    companies: list[Company] = []
    for entity in entities:
        existing = session.scalar(select(Company).where(Company.name == entity.name))
        if existing:
            if entity.aliases and (not existing.aliases):
                existing.aliases = entity.aliases
            if entity.sector and not existing.sector:
                existing.sector = entity.sector
            companies.append(existing)
            continue
        company = Company(
            name=entity.name,
            aliases=entity.aliases,
            sector=entity.sector,
        )
        session.add(company)
        session.flush()
        companies.append(company)
    return companies


def discover_sources(
    session: Session,
    provider: WebCrawlerProvider,
    watchlist_entities: list[WatchlistEntity],
    fallback_provider: WebCrawlerProvider | None = None,
) -> int:
    companies = seed_companies(session, watchlist_entities)
    entities_by_name = {entity.name: entity for entity in watchlist_entities}
    inserted = 0
    max_workers = _max_workers(provider)
    results_per_query = _results_per_query()
    source_policy = _load_source_policy()
    logger.info("Discovery concurrency: %d worker(s)", max_workers)
    logger.info("Discovery results per query: %d", results_per_query)

    for company in companies:
        entity = entities_by_name.get(
            company.name,
            WatchlistEntity(name=company.name, execs=[], themes=[], aliases=[], sector=company.sector),
        )
        company_queries = _build_company_queries(entity)
        query_results: list[tuple[str, list[dict]]] = []
        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                future_map = {
                    pool.submit(
                        provider.search,
                        company.name,
                        query,
                        results_per_query,
                    ): query
                    for query in company_queries
                }
                for future in as_completed(future_map):
                    query = future_map[future]
                    try:
                        results = future.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Search task failed for '%s': %s", query, exc)
                        results = []
                    results = _fallback_search_if_needed(
                        primary_provider=provider,
                        fallback_provider=fallback_provider,
                        company_name=company.name,
                        query=query,
                        max_results=results_per_query,
                        results=results,
                    )
                    query_results.append((query, results))
        else:
            for query in company_queries:
                results = provider.search(company.name, query, max_results=results_per_query)
                results = _fallback_search_if_needed(
                    primary_provider=provider,
                    fallback_provider=fallback_provider,
                    company_name=company.name,
                    query=query,
                    max_results=results_per_query,
                    results=results,
                )
                query_results.append((query, results))

        for _, results in query_results:
            for item in results:
                raw_url = item.get("url")
                url = _canonicalize_url(raw_url)
                if not url:
                    continue
                if not _is_candidate_source(company.name, url, item.get("title", ""), source_policy):
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


def _build_company_queries(entity: WatchlistEntity) -> list[str]:
    queries = [template.format(company=entity.name) for template in BASE_QUERY_TEMPLATES]
    if entity.execs:
        queries[0] = f"{entity.name} {entity.execs[0]} interview strategy"
    if entity.themes:
        for idx, theme in enumerate(entity.themes[:2], start=1):
            if idx < len(queries):
                queries[idx] = f"{entity.name} {theme} strategy announcement"
    unique: list[str] = []
    seen: set[str] = set()
    for q in queries:
        normalized = q.strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(normalized)
    return unique


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


def _results_per_query() -> int:
    requested = os.getenv("DISCOVERY_RESULTS_PER_QUERY", "1")
    try:
        value = int(requested)
    except ValueError:
        value = 1
    return max(1, min(10, value))


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


def _load_source_policy(path: str = POLICY_PATH) -> dict[str, set[str]]:
    allow_domains = set(HIGH_SIGNAL_DOMAINS)
    blocked_domains = set(BLOCKED_DOMAINS)
    strategic_terms = set(STRATEGIC_TERMS)
    policy_file = Path(path)
    if not policy_file.exists():
        return {
            "allow_domains": allow_domains,
            "blocked_domains": blocked_domains,
            "strategic_terms": strategic_terms,
        }

    data = yaml.safe_load(policy_file.read_text(encoding="utf-8")) or {}
    file_allow = {str(x).lower().strip() for x in data.get("allow_domains", []) if str(x).strip()}
    file_block = {str(x).lower().strip() for x in data.get("blocked_domains", []) if str(x).strip()}
    file_terms = {str(x).lower().strip() for x in data.get("strategic_terms", []) if str(x).strip()}

    return {
        "allow_domains": file_allow or allow_domains,
        "blocked_domains": file_block or blocked_domains,
        "strategic_terms": file_terms or strategic_terms,
    }


def _canonicalize_url(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        return None
    kept_params = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        key_lower = key.lower()
        if key_lower.startswith("utm_") or key_lower in {"gclid", "fbclid"}:
            continue
        kept_params.append((key, value))
    normalized_query = urlencode(kept_params, doseq=True)
    normalized = parsed._replace(query=normalized_query, fragment="")
    return urlunparse(normalized)


def _is_candidate_source(
    company_name: str,
    url: str,
    title: object,
    policy: dict[str, set[str]],
) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")
    blocked_domains = policy["blocked_domains"]
    allow_domains = policy["allow_domains"]
    strategic_terms = policy["strategic_terms"]

    if any(domain.endswith(blocked) for blocked in blocked_domains):
        return False
    if parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".svg", ".pdf.zip")):
        return False

    text = f"{str(title or '')} {url}".lower()
    mentions_company = company_name.lower() in text
    has_strategic_term = any(term in text for term in strategic_terms)
    is_high_signal_domain = any(domain.endswith(allowed) for allowed in allow_domains)
    return is_high_signal_domain or (mentions_company and has_strategic_term)


def _parse_watchlist_entity(entry: object) -> WatchlistEntity | None:
    if isinstance(entry, str):
        name = entry.strip()
        if not name:
            return None
        return WatchlistEntity(name=name, execs=[], themes=[], aliases=[], sector=None)

    if isinstance(entry, dict):
        name = str(entry.get("name") or "").strip()
        if not name:
            return None
        execs = _to_clean_list(entry.get("execs", []))
        themes = _to_clean_list(entry.get("themes", []))
        aliases = _to_clean_list(entry.get("aliases", []))
        sector_raw = str(entry.get("sector") or "").strip()
        return WatchlistEntity(
            name=name,
            execs=execs,
            themes=themes,
            aliases=aliases,
            sector=sector_raw or None,
        )
    return None


def _to_clean_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _fallback_search_if_needed(
    primary_provider: WebCrawlerProvider,
    fallback_provider: WebCrawlerProvider | None,
    company_name: str,
    query: str,
    max_results: int,
    results: list[dict],
) -> list[dict]:
    if results:
        return results
    if fallback_provider is None:
        return results
    if fallback_provider.__class__ == primary_provider.__class__:
        return results
    logger.info("Primary discovery returned 0 for '%s', trying fallback provider", query)
    try:
        return fallback_provider.search(company_name, query, max_results=max_results)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fallback discovery failed for '%s': %s", query, exc)
        return results
