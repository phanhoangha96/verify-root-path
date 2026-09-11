from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv

_DIR = Path(__file__).resolve().parent
load_dotenv(_DIR / ".env")
load_dotenv()

FILE_SOURCE_AUTO = "auto"
FILE_SOURCE_SERVICE = "file-service"
FILE_SOURCE_DISK = "disk"
VALID_FILE_SOURCES = {FILE_SOURCE_AUTO, FILE_SOURCE_SERVICE, FILE_SOURCE_DISK}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw.strip())


def _env(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and raw.strip():
            return raw.strip()
    return default


def _csv_paths(raw: str) -> List[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


@dataclass(frozen=True)
class OracleTarget:
    name: str
    user: str
    password: str
    dsn: str
    schema: str
    tenant_code: str


@dataclass(frozen=True)
class SolrFileBackfillSettings:
    legacy: OracleTarget
    new_targets: List[OracleTarget]
    solr_host: str
    solr_core: str
    solr_user: str
    solr_password: str
    solr_timeout: int
    default_tenant_code: str
    batch_size: int
    commit_within_ms: int
    file_source: str
    file_service_download_url: str
    file_service_timeout: int
    file_storage_roots: List[str]
    tika_app_jar: str
    tika_write_limit: int
    output_dir: Path
    output_file: str


def _oracle_target(
    name: str,
    user_keys: List[str],
    password_keys: List[str],
    dsn_keys: List[str],
    schema_keys: List[str],
    user_default: str,
    dsn_default: str,
    schema_default: str,
    tenant_code: str,
) -> OracleTarget:
    return OracleTarget(
        name=name,
        user=_env(*user_keys, default=user_default),
        password=_env(*password_keys),
        dsn=_env(*dsn_keys, default=dsn_default),
        schema=_env(*schema_keys, default=schema_default),
        tenant_code=tenant_code,
    )


def load_new_targets_file(path: str, fallback_tenant: str) -> List[OracleTarget]:
    file_path = Path(path)
    if not file_path.is_file():
        raise SystemExit(f"NEW tenants file not found: {file_path}")
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"NEW tenants file must be a non-empty JSON array: {file_path}")
    targets: List[OracleTarget] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SystemExit(f"Invalid tenant entry at index {idx}")
        password = str(item.get("password") or "")
        if not password:
            raise SystemExit(f"Missing password for tenant entry {idx}")
        targets.append(
            OracleTarget(
                name=str(item.get("name") or item.get("tenant_code") or f"new-{idx + 1}"),
                user=str(item.get("user") or ""),
                password=password,
                dsn=str(item.get("dsn") or ""),
                schema=str(item.get("schema") or ""),
                tenant_code=str(item.get("tenant_code") or fallback_tenant),
            )
        )
        if not targets[-1].user or not targets[-1].dsn:
            raise SystemExit(f"user/dsn required for tenant entry {idx}")
    return targets


def load_solr_file_backfill_settings(
    *,
    require_legacy: bool,
    require_new: bool,
    new_tenants_file: str = "",
) -> SolrFileBackfillSettings:
    default_tenant = _env("SOLR_TENANT_CODE", "TENANT_CODE")
    legacy = _oracle_target(
        name="LEGACY",
        user_keys=["LEGACY_ORACLE_USER", "ORACLE_USER"],
        password_keys=["LEGACY_ORACLE_PASSWORD", "ORACLE_PASSWORD"],
        dsn_keys=["LEGACY_ORACLE_DSN", "ORACLE_DSN"],
        schema_keys=["LEGACY_ORACLE_SCHEMA", "ORACLE_SCHEMA"],
        user_default="LEGACY",
        dsn_default="10.16.150.233:1521/ORCLPDB1",
        schema_default="LEGACY",
        tenant_code=default_tenant,
    )
    new_from_env = _oracle_target(
        name="NEW",
        user_keys=["NEW_ORACLE_USER"],
        password_keys=["NEW_ORACLE_PASSWORD"],
        dsn_keys=["NEW_ORACLE_DSN"],
        schema_keys=["NEW_ORACLE_SCHEMA"],
        user_default="",
        dsn_default="",
        schema_default="",
        tenant_code=_env("NEW_ORACLE_TENANT_CODE", default=default_tenant),
    )

    new_targets: List[OracleTarget] = []
    file_path = (new_tenants_file or os.getenv("NEW_ORACLE_TENANTS_FILE", "") or "").strip()
    if file_path:
        new_targets = load_new_targets_file(file_path, default_tenant)
    elif new_from_env.password and new_from_env.user and new_from_env.dsn:
        new_targets = [new_from_env]

    file_source = (_env("FILE_SOURCE", default=FILE_SOURCE_AUTO) or FILE_SOURCE_AUTO).lower()
    if file_source not in VALID_FILE_SOURCES:
        raise SystemExit(f"FILE_SOURCE must be one of {sorted(VALID_FILE_SOURCES)}")

    download_url = _env(
        "FILE_SERVICE_DOWNLOAD_URL",
        default="http://localhost:10005/FileService/file/downloadFile",
    ).rstrip("/")

    settings = SolrFileBackfillSettings(
        legacy=legacy,
        new_targets=new_targets,
        solr_host=(os.getenv("SOLR_HOST", "http://localhost:8983/solr") or "http://localhost:8983/solr").rstrip("/"),
        solr_core=os.getenv("SOLR_CORE", "eoffice_document") or "eoffice_document",
        solr_user=os.getenv("SOLR_USER", "") or "",
        solr_password=os.getenv("SOLR_PASSWORD", "") or "",
        solr_timeout=_int("SOLR_TIMEOUT_SECONDS", 300),
        default_tenant_code=default_tenant,
        batch_size=_int("SOLR_BACKFILL_BATCH_SIZE", 20),
        commit_within_ms=_int("SOLR_COMMIT_WITHIN_MS", 10000),
        file_source=file_source,
        file_service_download_url=download_url,
        file_service_timeout=_int("FILE_SERVICE_TIMEOUT_SECONDS", 120),
        file_storage_roots=_csv_paths(os.getenv("FILE_STORAGE_ROOTS", "") or ""),
        tika_app_jar=_env("TIKA_APP_JAR"),
        tika_write_limit=_int("TIKA_WRITE_LIMIT", 100000),
        output_dir=Path(os.getenv("OUTPUT_DIR") or str(_DIR / "output")),
        output_file=os.getenv("OUTPUT_FILE", "") or "",
    )

    missing: List[str] = []
    if require_legacy and (not settings.legacy.password or not settings.legacy.dsn):
        missing.append("LEGACY: ORACLE_DSN + ORACLE_PASSWORD (or LEGACY_ORACLE_*)")
    if require_new and not settings.new_targets:
        missing.append(
            "NEW: NEW_ORACLE_USER + NEW_ORACLE_PASSWORD + NEW_ORACLE_DSN "
            "(second Oracle DB, different from LEGACY) or NEW_ORACLE_TENANTS_FILE"
        )
    if not settings.solr_host:
        missing.append("SOLR_HOST")
    if settings.file_source == FILE_SOURCE_SERVICE and not settings.file_service_download_url:
        missing.append("FILE_SERVICE_DOWNLOAD_URL")
    if settings.file_source == FILE_SOURCE_DISK and not settings.file_storage_roots:
        missing.append("FILE_STORAGE_ROOTS")
    if missing:
        raise SystemExit(
            "Missing config for Solr file backfill.\n"
            + "\n".join(f"  - {item}" for item in missing)
            + "\nCopy keys from part4-solr-file-backfill/.env.example into .env"
        )
    return settings
