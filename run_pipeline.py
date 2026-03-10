from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from urllib.parse import unquote, urlparse

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


def _blob_sync_enabled() -> bool:
    return os.getenv("BLOB_SYNC_ENABLED", "").strip().lower() in {"1", "true", "yes"} or bool(
        os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    )


def _sqlite_db_path() -> Path | None:
    database_url = os.getenv("DATABASE_URL", "sqlite:///./dealsignal.db").strip()
    if not database_url.startswith("sqlite:"):
        return None

    if database_url.startswith("sqlite:////"):
        parsed = urlparse(database_url)
        return Path(unquote(parsed.path))

    if database_url.startswith("sqlite:///"):
        raw = database_url[len("sqlite:///") :]
        return Path(unquote(raw))

    return None


def _download_db_from_blob(db_path: Path) -> None:
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        logger.error("Blob sync enabled but azure-storage-blob is not installed.")
        return

    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    container = os.getenv("BLOB_CONTAINER", "dealsignal")
    blob_name = os.getenv("BLOB_DB_BLOB_NAME", "state/dealsignal.db")
    if not conn_str:
        logger.warning("Blob sync is enabled but AZURE_STORAGE_CONNECTION_STRING is missing.")
        return

    service = BlobServiceClient.from_connection_string(conn_str)
    container_client = service.get_container_client(container)
    blob_client = container_client.get_blob_client(blob_name)

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        data = blob_client.download_blob().readall()
        db_path.write_bytes(data)
        logger.info("Downloaded SQLite state from blob %s/%s", container, blob_name)
    except Exception as exc:  # noqa: BLE001
        logger.info("No existing DB downloaded from blob (%s/%s): %s", container, blob_name, exc)


def _upload_db_to_blob(db_path: Path) -> None:
    try:
        from azure.storage.blob import BlobServiceClient
        from azure.core.exceptions import ResourceExistsError
    except ImportError:
        logger.error("Blob sync enabled but azure-storage-blob is not installed.")
        return

    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    container = os.getenv("BLOB_CONTAINER", "dealsignal")
    blob_name = os.getenv("BLOB_DB_BLOB_NAME", "state/dealsignal.db")
    if not conn_str:
        logger.warning("Blob sync is enabled but AZURE_STORAGE_CONNECTION_STRING is missing.")
        return
    if not db_path.exists():
        logger.warning("Local SQLite DB not found at %s; skipping blob upload.", db_path)
        return

    service = BlobServiceClient.from_connection_string(conn_str)
    container_client = service.get_container_client(container)
    try:
        container_client.create_container()
    except ResourceExistsError:
        pass
    blob_client = container_client.get_blob_client(blob_name)

    with db_path.open("rb") as handle:
        blob_client.upload_blob(handle, overwrite=True)
    logger.info("Uploaded SQLite state to blob %s/%s", container, blob_name)


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

    db_path: Path | None = None
    blob_sync = _blob_sync_enabled()
    if blob_sync:
        db_path = _sqlite_db_path()
        if db_path is None:
            logger.error("Blob sync requires sqlite DATABASE_URL. Current DATABASE_URL=%s", os.getenv("DATABASE_URL", ""))
            return 2
        _download_db_from_blob(db_path)

    started = perf_counter()
    try:
        run_pipeline()
    except Exception:
        logger.exception("Pipeline execution failed")
        return 1

    if blob_sync and db_path is not None:
        _upload_db_to_blob(db_path)

    logger.info("Pipeline execution finished in %.2fs", perf_counter() - started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
