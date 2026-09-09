from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import oracledb

from config import Settings


@dataclass
class AttachmentRow:
    id: str
    file_name: Optional[str]
    file_path: Optional[str]
    root_path: Optional[str]


def fetch_attachments(settings: Settings) -> List[AttachmentRow]:
    """Fetch sampled attachments from LEGACY.VB_ATTACHMENT."""
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
