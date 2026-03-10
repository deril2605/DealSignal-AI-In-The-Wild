from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import unquote, urlparse


def blob_sync_enabled() -> bool:
    return os.getenv("BLOB_SYNC_ENABLED", "").strip().lower() in {"1", "true", "yes"} or bool(
        os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    )


def sqlite_db_path() -> Path | None:
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


def download_sqlite_from_blob(logger: logging.Logger | None = None) -> Path | None:
    log = logger or logging.getLogger(__name__)
    if not blob_sync_enabled():
        return None

    db_path = sqlite_db_path()
    if db_path is None:
        log.warning("Blob sync requires sqlite DATABASE_URL. Current DATABASE_URL=%s", os.getenv("DATABASE_URL", ""))
        return None

    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        log.error("Blob sync enabled but azure-storage-blob is not installed.")
        return db_path

    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    container = os.getenv("BLOB_CONTAINER", "dealsignal")
    blob_name = os.getenv("BLOB_DB_BLOB_NAME", "state/dealsignal.db")
    if not conn_str:
        log.warning("Blob sync is enabled but AZURE_STORAGE_CONNECTION_STRING is missing.")
        return db_path

    service = BlobServiceClient.from_connection_string(conn_str)
    container_client = service.get_container_client(container)
    blob_client = container_client.get_blob_client(blob_name)
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        data = blob_client.download_blob().readall()
        db_path.write_bytes(data)
        log.info("Downloaded SQLite state from blob %s/%s", container, blob_name)
    except Exception as exc:  # noqa: BLE001
        log.info("No existing DB downloaded from blob (%s/%s): %s", container, blob_name, exc)

    return db_path


def upload_sqlite_to_blob(db_path: Path | None = None, logger: logging.Logger | None = None) -> None:
    log = logger or logging.getLogger(__name__)
    if not blob_sync_enabled():
        return

    path = db_path or sqlite_db_path()
    if path is None:
        log.warning("Blob sync requires sqlite DATABASE_URL. Current DATABASE_URL=%s", os.getenv("DATABASE_URL", ""))
        return
    if not path.exists():
        log.warning("Local SQLite DB not found at %s; skipping blob upload.", path)
        return

    try:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        log.error("Blob sync enabled but azure-storage-blob is not installed.")
        return

    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    container = os.getenv("BLOB_CONTAINER", "dealsignal")
    blob_name = os.getenv("BLOB_DB_BLOB_NAME", "state/dealsignal.db")
    if not conn_str:
        log.warning("Blob sync is enabled but AZURE_STORAGE_CONNECTION_STRING is missing.")
        return

    service = BlobServiceClient.from_connection_string(conn_str)
    container_client = service.get_container_client(container)
    try:
        container_client.create_container()
    except ResourceExistsError:
        pass

    blob_client = container_client.get_blob_client(blob_name)
    with path.open("rb") as handle:
        blob_client.upload_blob(handle, overwrite=True)
    log.info("Uploaded SQLite state to blob %s/%s", container, blob_name)
