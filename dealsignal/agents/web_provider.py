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


def get_provider() -> WebCrawlerProvider:
    if os.getenv("TINYFISH_API_KEY"):
        from dealsignal.agents.tinyfish_provider import TinyFishProvider

        return TinyFishProvider()

    from dealsignal.agents.basic_provider import BasicProvider

    return BasicProvider()

