from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw.strip())


def _csv(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default) or default
    items: List[str] = []
    for part in raw.split(","):
        value = part.strip()
        if value and value not in items:
            items.append(value.rstrip("/"))
    return items


@dataclass(frozen=True)
class Settings:
    oracle_user: str
    oracle_password: str
    oracle_dsn: str
    oracle_schema: str

    ssh_host: str
    ssh_port: int
    ssh_username: str
    ssh_password: str
    plocate_db: str
    root_folders: List[str]

    sample_per_root_path: int
    sample_limit: int
    plocate_batch_size: int
    plocate_limit: int
    require_has_file: bool
    exclude_deleted: bool

    output_dir: Path
    output_file: str

    # Part 2 — export missing files (migration-service + storage-service)
    missing_export_csv_path: str
    missing_export_batch_size: int
    missing_export_throttle_every: int
    missing_export_throttle_pause_ms: int
    missing_export_per_check_delay_ms: int
    missing_export_reconnect_every: int
    missing_export_retry_attempts: int
    missing_export_retry_backoff_ms: int
    missing_export_search_by_file_name: bool
    missing_export_limit: int
    missing_export_work_dir: Path


def load_settings(*, require_oracle: bool = True, require_ssh: bool = True) -> Settings:
    settings = Settings(
        oracle_user=os.getenv("ORACLE_USER", "LEGACY"),
        oracle_password=os.getenv("ORACLE_PASSWORD", ""),
        oracle_dsn=os.getenv("ORACLE_DSN", "10.16.150.233:1521/ORCLPDB1"),
        oracle_schema=os.getenv("ORACLE_SCHEMA", "LEGACY"),
        ssh_host=os.getenv("REMOTE_STORAGE_HOST", "10.4.202.235"),
        ssh_port=_int("REMOTE_STORAGE_PORT", 2222),
        ssh_username=os.getenv("REMOTE_STORAGE_USERNAME", "root"),
        ssh_password=os.getenv("REMOTE_STORAGE_PASSWORD", ""),
        plocate_db=os.getenv("REMOTE_STORAGE_PLOCATE_DB", "/var/lib/plocate/voffice.db"),
        root_folders=_csv(
            "REMOTE_STORAGE_ROOT_FOLDERS",
            "/data/u02/data1/voffice/Upload,/data/u02/data2/voffice/Upload,"
            "/data/u03/data1/voffice/Upload,/data/u03/data1/voffice/Upload/Upload,"
            "/data/u04/data1/voffice/Upload,/data/u05/data1/voffice/Upload",
        ),
        sample_per_root_path=_int("SAMPLE_PER_ROOT_PATH", 200),
        sample_limit=_int("SAMPLE_LIMIT", 0),
        plocate_batch_size=_int("PLOCATE_BATCH_SIZE", 50),
        plocate_limit=_int("PLOCATE_LIMIT", 32),
        require_has_file=_bool("REQUIRE_HAS_FILE", True),
        exclude_deleted=_bool("EXCLUDE_DELETED", True),
        output_dir=Path(os.getenv("OUTPUT_DIR", "./output")),
        output_file=os.getenv("OUTPUT_FILE", "") or "",
        missing_export_csv_path=os.getenv("MISSING_EXPORT_CSV_PATH", "") or "",
        missing_export_batch_size=_int("MISSING_EXPORT_BATCH_SIZE", 100),
        missing_export_throttle_every=_int("MISSING_EXPORT_THROTTLE_EVERY", 500),
        missing_export_throttle_pause_ms=_int("MISSING_EXPORT_THROTTLE_PAUSE_MS", 200),
        missing_export_per_check_delay_ms=_int("MISSING_EXPORT_PER_CHECK_DELAY_MS", 0),
        missing_export_reconnect_every=_int("MISSING_EXPORT_RECONNECT_EVERY", 50000),
        missing_export_retry_attempts=_int("MISSING_EXPORT_RETRY_ATTEMPTS", 3),
        missing_export_retry_backoff_ms=_int("MISSING_EXPORT_RETRY_BACKOFF_MS", 500),
        missing_export_search_by_file_name=_bool("MISSING_EXPORT_SEARCH_BY_FILE_NAME", False),
        missing_export_limit=_int("MISSING_EXPORT_LIMIT", 0),
        missing_export_work_dir=Path(
            os.getenv("MISSING_EXPORT_WORK_DIR", "") or "./output/missing-export"
        ),
    )
    missing = []
    if require_oracle and not settings.oracle_password:
        missing.append("ORACLE_PASSWORD")
    if require_ssh and not settings.ssh_password:
        missing.append("REMOTE_STORAGE_PASSWORD")
    if missing:
        raise SystemExit(f"Missing required env: {', '.join(missing)}. Copy .env.example -> .env")
    return settings
