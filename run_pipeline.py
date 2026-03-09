from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from time import perf_counter

from dotenv import load_dotenv

from main import configure_logging, run_pipeline


logger = logging.getLogger("dealsignal.batch")


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


def _validate_env() -> list[str]:
    required = ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "TINYFISH_API_KEY"]
    missing: list[str] = [name for name in required if not os.getenv(name)]
    if missing:
        return missing
    return []


def _log_start_banner() -> None:
    payload = {
        "event": "pipeline_batch_start",
        "utc": datetime.now(timezone.utc).isoformat(),
        "model": os.getenv("LLM_MODEL", ""),
        "llm_base_url": os.getenv("LLM_BASE_URL", ""),
        "tinyfish_base_url": os.getenv("TINYFISH_BASE_URL", ""),
        "database_url": _mask(os.getenv("DATABASE_URL", "sqlite:///./dealsignal.db")),
    }
    logger.info(json.dumps(payload, ensure_ascii=True))


def main() -> int:
    load_dotenv()
    configure_logging()
    _log_start_banner()

    missing = _validate_env()
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        return 2

    started = perf_counter()
    try:
        run_pipeline()
    except Exception:
        logger.exception("Pipeline execution failed")
        return 1

    logger.info("Pipeline execution finished in %.2fs", perf_counter() - started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
