from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

from dealsignal.state_sync import blob_sync_enabled, download_sqlite_from_blob, sqlite_db_path, upload_sqlite_to_blob
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
    load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
    load_dotenv()
    configure_logging()
    _log_start_banner()

    missing = _validate_env()
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        return 2

    db_path = None
    blob_sync = blob_sync_enabled()
    if blob_sync:
        db_path = sqlite_db_path()
        if db_path is None:
            logger.error("Blob sync requires sqlite DATABASE_URL. Current DATABASE_URL=%s", os.getenv("DATABASE_URL", ""))
            return 2
        download_sqlite_from_blob(logger=logger)

    started = perf_counter()
    try:
        run_pipeline()
    except Exception:
        logger.exception("Pipeline execution failed")
        return 1

    if blob_sync and db_path is not None:
        upload_sqlite_to_blob(db_path=db_path, logger=logger)

    logger.info("Pipeline execution finished in %.2fs", perf_counter() - started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
