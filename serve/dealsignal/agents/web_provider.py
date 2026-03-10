from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any


class WebCrawlerProvider(ABC):
    @abstractmethod
    def search(self, company: str, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def fetch_article(self, url: str) -> dict[str, Any] | None:
        raise NotImplementedError


def _require_tinyfish_key() -> None:
    if not os.getenv("TINYFISH_API_KEY"):
        raise RuntimeError("TINYFISH_API_KEY is required. BasicProvider has been disabled.")


def get_provider() -> WebCrawlerProvider:
    _require_tinyfish_key()
    from dealsignal.agents.tinyfish_provider import TinyFishProvider

    return TinyFishProvider()


def get_discovery_provider() -> WebCrawlerProvider:
    _require_tinyfish_key()
    from dealsignal.agents.tinyfish_provider import TinyFishProvider

    return TinyFishProvider()


def get_fetch_primary_provider() -> WebCrawlerProvider:
    _require_tinyfish_key()
    from dealsignal.agents.tinyfish_provider import TinyFishProvider

    return TinyFishProvider()


def get_fetch_fallback_provider() -> WebCrawlerProvider | None:
    return None


def get_discovery_fallback_provider() -> WebCrawlerProvider | None:
    return None
