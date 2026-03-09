from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote_plus

import httpx

from dealsignal.agents.web_provider import WebCrawlerProvider

logger = logging.getLogger(__name__)


class TinyFishProvider(WebCrawlerProvider):
    def __init__(self) -> None:
        self.api_key = os.getenv("TINYFISH_API_KEY", "")
        self.base_url = os.getenv("TINYFISH_BASE_URL", "https://agent.tinyfish.ai").rstrip("/")
        self.client = httpx.Client(
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
            },
            timeout=30.0,
        )

    def search(self, company: str, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        try:
            search_url = f"https://duckduckgo.com/?q={quote_plus(query)}"
            goal = (
                "Open the search page and return strict JSON only:\n"
                '{"results":[{"url":"...","title":"...","published_at":""}]}\n'
                f"Return at most {max_results} high-quality article or news URLs relevant to company {company}. "
                "Avoid ads, duplicate links, login pages, and low-quality aggregators."
            )
            data = self._run_goal(target_url=search_url, goal=goal)
            items = data.get("results", [])
            if not isinstance(items, list):
                return []
            normalized: list[dict[str, Any]] = []
            for item in items[:max_results]:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                normalized.append(
                    {
                        "url": url,
                        "title": str(item.get("title") or "").strip(),
                        "published_at": item.get("published_at"),
                    }
                )
            return normalized
        except Exception as exc:  # noqa: BLE001
            logger.warning("TinyFish search failed for query '%s': %s", query, exc)
            return []

    def fetch_article(self, url: str) -> dict[str, Any] | None:
        try:
            goal = (
                "Extract the main article content and return strict JSON only:\n"
                '{"title":"...","published_at":"","text":"...","evidence_excerpt":"..."}\n'
                "If no article content is found, return empty strings."
            )
            data = self._run_goal(target_url=url, goal=goal)
            text = data.get("text", "")
            if not text:
                return None
            return {
                "url": url,
                "title": data.get("title", url),
                "published_at": data.get("published_at"),
                "text": text,
                "evidence_excerpt": data.get("evidence_excerpt", text[:400]),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("TinyFish fetch failed for %s: %s", url, exc)
            return None

    def _run_goal(self, target_url: str, goal: str) -> dict[str, Any]:
        payload = {
            "url": target_url,
            "goal": goal,
            "browser_profile": os.getenv("TINYFISH_BROWSER_PROFILE", "lite"),
            "proxy_config": {
                "enabled": os.getenv("TINYFISH_PROXY_ENABLED", "false").lower() == "true",
            },
        }
        country = os.getenv("TINYFISH_PROXY_COUNTRY")
        if payload["proxy_config"]["enabled"] and country:
            payload["proxy_config"]["country_code"] = country

        endpoint = f"{self.base_url}/v1/automation/run"
        attempts = 3
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self.client.post(endpoint, json=payload, timeout=120.0)
                response.raise_for_status()
                body = response.json()
                result = body.get("result", body.get("data", body))
                return result if isinstance(result, dict) else {}
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < attempts - 1:
                    continue
        raise RuntimeError(f"TinyFish run failed after retries: {last_exc}") from last_exc
