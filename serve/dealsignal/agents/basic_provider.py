from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dealsignal.agents.web_provider import WebCrawlerProvider

logger = logging.getLogger(__name__)

try:
    import trafilatura
except ModuleNotFoundError:
    trafilatura = None


class BasicProvider(WebCrawlerProvider):
    def __init__(self) -> None:
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.7,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            }
        )
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def search(self, company: str, query: str, max_results: int = 5) -> list[dict]:
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Search failed for %s (%s): %s", company, query, exc)
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for anchor in soup.select("a.result__a"):
            href = anchor.get("href", "")
            clean_url = self._normalize_result_url(href)
            if not clean_url:
                continue
            results.append(
                {
                    "url": clean_url,
                    "title": anchor.get_text(strip=True),
                    "published_at": None,
                }
            )
            if len(results) >= max_results:
                break

        return results

    def fetch_article(self, url: str) -> dict | None:
        downloaded = None
        if trafilatura:
            try:
                downloaded = trafilatura.fetch_url(url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Trafilatura download failed for %s: %s", url, exc)

        title = ""
        published_at = None
        text = ""
        if downloaded:
            extracted = trafilatura.extract(downloaded, include_links=False, include_comments=False)
            metadata = trafilatura.extract_metadata(downloaded)
            if metadata:
                title = metadata.title or ""
                if metadata.date:
                    published_at = _safe_parse_datetime(metadata.date)
            text = extracted or ""

        if not text:
            try:
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                text = soup.get_text(separator="\n", strip=True)
                if not title and soup.title:
                    title = soup.title.get_text(strip=True)
            except requests.RequestException as exc:
                logger.warning("Fetch failed for %s: %s", url, exc)
                return None

        if not text:
            return None

        evidence = text[:400]
        return {
            "url": url,
            "title": title or url,
            "published_at": published_at,
            "text": text,
            "evidence_excerpt": evidence,
            "fetched_at": datetime.utcnow(),
        }

    @staticmethod
    def _normalize_result_url(raw_href: str) -> str | None:
        if not raw_href:
            return None
        parsed = urlparse(raw_href)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            query_params = parse_qs(parsed.query)
            uddg = query_params.get("uddg", [])
            if uddg:
                return unquote(uddg[0])
        if parsed.scheme in {"http", "https"}:
            return raw_href
        return None


def _safe_parse_datetime(value: str) -> datetime | None:
    # Keep parsing local and lightweight for common article date formats.
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None
