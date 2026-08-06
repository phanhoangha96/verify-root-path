from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import oracledb

from config import Settings
from mapping import collapse_slashes


@dataclass
class AttachmentRow:
    id: str
    file_name: Optional[str]
    file_path: Optional[str]
    root_path: Optional[str]


def fetch_attachments(settings: Settings) -> List[AttachmentRow]:
    """Fetch sampled attachments from LEGACY.VB_ATTACHMENT (Part 1 — root-path audit)."""
    schema = settings.oracle_schema
    where_parts = ["FILE_PATH IS NOT NULL", "TRIM(FILE_PATH) IS NOT NULL"]
    if settings.exclude_deleted:
        where_parts.append("NVL(IS_DELETE, 0) = 0")
    if settings.require_has_file:
        where_parts.append("NVL(TO_CHAR(IS_HAS_FILE), '1') = '1'")

    where_sql = " AND ".join(where_parts)
    table = f"{schema}.VB_ATTACHMENT"

    if settings.sample_per_root_path > 0:
        sql = f"""
            SELECT ID, FILE_NAME, FILE_PATH, ROOT_PATH
            FROM (
                SELECT
                    ID,
                    FILE_NAME,
                    FILE_PATH,
                    ROOT_PATH,
                    ROW_NUMBER() OVER (
                        PARTITION BY NVL(ROOT_PATH, '__NULL__')
                        ORDER BY ID
                    ) AS RN
                FROM {table}
                WHERE {where_sql}
            )
            WHERE RN <= :sample_per_group
        """
        binds = {"sample_per_group": settings.sample_per_root_path}
    else:
        sql = f"""
            SELECT ID, FILE_NAME, FILE_PATH, ROOT_PATH
            FROM {table}
            WHERE {where_sql}
            ORDER BY NVL(ROOT_PATH, '__NULL__'), ID
        """
        binds = {}

    if settings.sample_limit > 0:
        sql = f"""
            SELECT * FROM (
                {sql}
            ) WHERE ROWNUM <= :sample_limit
        """
        binds = dict(binds)
        binds["sample_limit"] = settings.sample_limit

    conn = oracledb.connect(
        user=settings.oracle_user,
        password=settings.oracle_password,
        dsn=settings.oracle_dsn,
    )
    try:
        with conn.cursor() as cur:
            cur.arraysize = 1000
            cur.execute(sql, binds)
            rows: List[AttachmentRow] = []
            for id_, file_name, file_path, root_path in cur:
                rows.append(
                    AttachmentRow(
                        id=str(id_) if id_ is not None else "",
                        file_name=str(file_name) if file_name is not None else None,
                        file_path=str(file_path) if file_path is not None else None,
                        root_path=str(root_path).strip()
                        if root_path is not None and str(root_path).strip()
                        else None,
                    )
                )
            return rows
    finally:
        conn.close()


def standardize_file_path(file_path: Optional[str]) -> str:
    """Match migration-service VbAttachmentMissingExportWriter.standardizeFilePath."""
    if not file_path or not str(file_path).strip():
        return "" if file_path is None else str(file_path)
    normalized = collapse_slashes(str(file_path).strip())
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def csv_escape(value: Optional[str]) -> str:
    if value is None:
        return ""
    need_quote = any(c in value for c in (",", '"', "\n", "\r"))
    escaped = value.replace('"', '""')
    return f'"{escaped}"' if need_quote else escaped


def write_all_attachments_csv(settings: Settings, csv_file: Path) -> int:
    """
    Stream FILE_PATH,FILE_NAME from LEGACY.VB_ATTACHMENT → CSV.
    Equivalent to migration-service VbAttachmentMissingExportWriter.
    """
    schema = settings.oracle_schema
    table = f"{schema}.VB_ATTACHMENT"
    sql = f"SELECT FILE_PATH, FILE_NAME FROM {table}"
    if settings.missing_export_limit > 0:
        sql = f"SELECT * FROM ({sql}) WHERE ROWNUM <= :row_limit"

    csv_file.parent.mkdir(parents=True, exist_ok=True)
    conn = oracledb.connect(
        user=settings.oracle_user,
        password=settings.oracle_password,
        dsn=settings.oracle_dsn,
    )
    count = 0
    try:
        with conn.cursor() as cur:
            cur.arraysize = 500
            if settings.missing_export_limit > 0:
                cur.execute(sql, {"row_limit": settings.missing_export_limit})
            else:
                cur.execute(sql)
            with csv_file.open("w", encoding="utf-8", newline="") as writer:
                writer.write("path,fileName\n")
                for file_path, file_name in cur:
                    path = standardize_file_path(
                        str(file_path) if file_path is not None else None
                    )
                    name = "" if file_name is None else str(file_name)
                    writer.write(f"{csv_escape(path)},{csv_escape(name)}\n")
                    count += 1
                    if count % 10000 == 0:
                        writer.flush()
                        print(f"  Writing attachment CSV progress: rows={count}", flush=True)
                writer.flush()
    finally:
        conn.close()
    print(f"Finished writing attachment CSV: rows={count}, file={csv_file}", flush=True)
    return count


def count_csv_data_rows(csv_file: Path) -> int:
    rows = 0
    with csv_file.open("r", encoding="utf-8") as reader:
        header = reader.readline()
        if not header:
            return 0
        for line in reader:
            if line.strip():
                rows += 1
    return rows


def iter_csv_path_filename(csv_file: Path) -> Iterator[Tuple[str, str]]:
    """Yield (path, fileName) from missing-export input CSV (header: path,fileName)."""
    with csv_file.open("r", encoding="utf-8") as reader:
        header = reader.readline()
        if not header:
            return
        path_idx, name_idx = _resolve_csv_header(header)
        for line in reader:
            if not line.strip():
                continue
            parts = _split_csv_line(line.rstrip("\n\r"))
            path = parts[path_idx].strip() if path_idx < len(parts) else ""
            name = parts[name_idx].strip() if name_idx < len(parts) else ""
            yield path, name


def _resolve_csv_header(header_line: str) -> Tuple[int, int]:
    parts = _split_csv_line(header_line.rstrip("\n\r"))
    path_idx = -1
    name_idx = -1
    for i, raw in enumerate(parts):
        h = (raw or "").strip().lower()
        if h in {"path", "filepath", "relativepath"}:
            path_idx = i
        elif h in {"filename", "file_name", "name"}:
            name_idx = i
    if path_idx < 0 and name_idx < 0:
        return 0, 1 if len(parts) > 1 else 0
    if path_idx < 0:
        path_idx = 0
    if name_idx < 0:
        name_idx = min(1, max(0, len(parts) - 1))
    return path_idx, name_idx


def _split_csv_line(line: str) -> List[str]:
    fields: List[str] = []
    current: List[str] = []
    in_quotes = False
    i = 0
    while i < len(line):
        c = line[i]
        if in_quotes:
            if c == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    current.append('"')
                    i += 1
                else:
                    in_quotes = False
            else:
                current.append(c)
        elif c == '"':
            in_quotes = True
        elif c == ",":
            fields.append("".join(current))
            current = []
        else:
            current.append(c)
        i += 1
    fields.append("".join(current))
    return fields


def iter_batches(items: List[AttachmentRow], batch_size: int) -> Iterator[List[AttachmentRow]]:
    for i in range(0, len(items), batch_size):
        yield items[i : i + batch_size]
