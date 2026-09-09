from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import oracledb

from solr_backfill_config import OracleTarget

OBJECT_TYPE_INCOMING = 1
OBJECT_TYPE_OUTGOING = 2
COMMENT_VB_DI = 0
COMMENT_VB_DEN = 1
COMMENT_HO_SO_HDTV = 4


@dataclass
class DocRow:
    id: str
    source: str
    object_type: int
    dept_id: str
    tenant_code: str
    doc_code: Optional[str] = None
    quote: Optional[str] = None
    publisher_name: Optional[str] = None
    outside_publisher_name: Optional[str] = None
    sub_book_number: Optional[str] = None
    outgoing_number: Optional[str] = None
    note: Optional[str] = None
    book_number: Optional[str] = None
    comments: List[str] = field(default_factory=list)
    process_notes: List[str] = field(default_factory=list)


def qualify(schema: str, table: str) -> str:
    return f"{schema}.{table}" if schema else table


def connect(target: OracleTarget):
    return oracledb.connect(user=target.user, password=target.password, dsn=target.dsn)


def _as_str(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _in_clause(prefix: str, ids: Sequence[str]) -> Tuple[str, Dict[str, str]]:
    binds: Dict[str, str] = {}
    names: List[str] = []
    for i, doc_id in enumerate(ids):
        name = f"{prefix}{i}"
        names.append(f":{name}")
        binds[name] = doc_id
    return ", ".join(names), binds


def _fetch_map(
    conn,
    sql: str,
    binds: Dict,
) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = defaultdict(list)
    with conn.cursor() as cur:
        cur.arraysize = 1000
        cur.execute(sql, binds)
        for object_id, value in cur:
            text = _as_str(value)
            if object_id is None or text is None:
                continue
            grouped[str(object_id)].append(text)
    return grouped


def _fetch_first_map(conn, sql: str, binds: Dict) -> Dict[str, str]:
    out: Dict[str, str] = {}
    with conn.cursor() as cur:
        cur.arraysize = 1000
        cur.execute(sql, binds)
        for object_id, value in cur:
            text = _as_str(value)
            if object_id is None or text is None or str(object_id) in out:
                continue
            out[str(object_id)] = text
    return out


def _attach_related(
    conn,
    schema: str,
    rows: List[DocRow],
    *,
    incoming_process_id_col: str,
    incoming_process_table_filter: str,
) -> None:
    if not rows:
        return
    ids = [row.id for row in rows]
    by_id = {row.id: row for row in rows}
    incoming_ids = [row.id for row in rows if row.object_type == OBJECT_TYPE_INCOMING]
    outgoing_ids = [row.id for row in rows if row.object_type == OBJECT_TYPE_OUTGOING]

    in_sql, binds = _in_clause("id", ids)
    book_sql = f"""
        SELECT DOC_ID, BOOK_NUMBER
        FROM {qualify(schema, "VB_DOC_IN_BOOK")}
        WHERE DOC_ID IN ({in_sql}) AND NVL(IS_DELETE, 0) = 0
        ORDER BY DOC_ID
    """
    book_map = _fetch_first_map(conn, book_sql, binds)

    if incoming_ids:
        c_sql, c_binds = _in_clause("cid", incoming_ids)
        comment_sql = f"""
            SELECT OBJECT_ID, CONTENT
            FROM {qualify(schema, "VB_COMMENT")}
            WHERE OBJECT_ID IN ({c_sql})
              AND OBJECT_TYPE IN ({COMMENT_VB_DEN}, {COMMENT_HO_SO_HDTV})
              AND NVL(IS_DELETE, 0) = 0
        """
        comments = _fetch_map(conn, comment_sql, c_binds)
        p_sql, p_binds = _in_clause("pid", incoming_ids)
        process_sql = f"""
            SELECT {incoming_process_id_col}, NOTE
            FROM {qualify(schema, "VB_INCOMING_PROCESS")}
            WHERE {incoming_process_id_col} IN ({p_sql})
              {incoming_process_table_filter}
              AND NOTE IS NOT NULL
        """
        notes = _fetch_map(conn, process_sql, p_binds)
        for doc_id in incoming_ids:
            by_id[doc_id].comments = comments.get(doc_id, [])
            by_id[doc_id].process_notes = notes.get(doc_id, [])

    if outgoing_ids:
        c_sql, c_binds = _in_clause("oid", outgoing_ids)
        comment_sql = f"""
            SELECT OBJECT_ID, CONTENT
            FROM {qualify(schema, "VB_COMMENT")}
            WHERE OBJECT_ID IN ({c_sql})
              AND OBJECT_TYPE = {COMMENT_VB_DI}
              AND NVL(IS_DELETE, 0) = 0
        """
        comments = _fetch_map(conn, comment_sql, c_binds)
        p_sql, p_binds = _in_clause("opid", outgoing_ids)
        process_sql = f"""
            SELECT DOC_ID, NOTE
            FROM {qualify(schema, "VB_OUTGOING_PROCESS")}
            WHERE DOC_ID IN ({p_sql})
              AND NVL(IS_DELETE, 0) = 0
              AND NOTE IS NOT NULL
        """
        notes = _fetch_map(conn, process_sql, p_binds)
        for doc_id in outgoing_ids:
            by_id[doc_id].comments = comments.get(doc_id, [])
            by_id[doc_id].process_notes = notes.get(doc_id, [])

    for row in rows:
        row.book_number = book_map.get(row.id)


def iter_new_incoming(
    conn,
    target: OracleTarget,
    batch_size: int,
    limit: int,
    tenant_filter: str,
) -> Iterator[List[DocRow]]:
    table = qualify(target.schema, "VB_INCOMING_DOC")
    tenant_sql = "AND TENANT_CODE = :tenant_code" if tenant_filter else ""
    sql = f"""
        SELECT * FROM (
            SELECT ID, DOC_CODE, QUOTE, PUBLISHER_NAME, OUTSIDE_PUBLISHER_NAME,
                   NOTE, TO_DEPT_ID, TENANT_CODE
            FROM {table}
            WHERE NVL(IS_DELETE, 0) = 0
              AND (:last_id IS NULL OR ID > :last_id)
              {tenant_sql}
            ORDER BY ID
        ) WHERE ROWNUM <= :batch_size
    """
    extra = {"tenant_code": tenant_filter} if tenant_filter else {}
    yield from _iter_mapped(
        conn,
        sql,
        extra,
        batch_size,
        limit,
        source=target.name,
        object_type=OBJECT_TYPE_INCOMING,
        fallback_tenant=target.tenant_code,
        incoming=True,
        schema=target.schema,
        incoming_process_id_col="DOC_ID",
        incoming_process_table_filter="AND NVL(IS_DELETE, 0) = 0",
    )


def iter_new_outgoing(
    conn,
    target: OracleTarget,
    batch_size: int,
    limit: int,
    tenant_filter: str,
) -> Iterator[List[DocRow]]:
    table = qualify(target.schema, "VB_OUTGOING_DOC")
    tenant_sql = "AND TENANT_CODE = :tenant_code" if tenant_filter else ""
    sql = f"""
        SELECT * FROM (
            SELECT ID, DOC_CODE, QUOTE, PUBLISHER_NAME, SUB_BOOK_NUMBER,
                   OUTGOING_NUMBER, NOTE, PUBLISHER_ID, TENANT_CODE
            FROM {table}
            WHERE NVL(IS_DELETE, 0) = 0
              AND (:last_id IS NULL OR ID > :last_id)
              {tenant_sql}
            ORDER BY ID
        ) WHERE ROWNUM <= :batch_size
    """
    extra = {"tenant_code": tenant_filter} if tenant_filter else {}
    yield from _iter_mapped(
        conn,
        sql,
        extra,
        batch_size,
        limit,
        source=target.name,
        object_type=OBJECT_TYPE_OUTGOING,
        fallback_tenant=target.tenant_code,
        incoming=False,
        schema=target.schema,
        incoming_process_id_col="DOC_ID",
        incoming_process_table_filter="AND NVL(IS_DELETE, 0) = 0",
    )


def iter_legacy_incoming(
    conn,
    target: OracleTarget,
    batch_size: int,
    limit: int,
) -> Iterator[List[DocRow]]:
    table = qualify(target.schema, "V_VB_INCOMING_DOC_V2")
    sql = f"""
        SELECT * FROM (
            SELECT ID, DOC_CODE, QUOTE, TDHVP_PUBLISHER_NAME, OUTSIDE_PUBLISHER_NAME,
                   NOTE, EOFFICE_TO_DEPT_ID, CAST(NULL AS VARCHAR2(100)) AS TENANT_CODE
            FROM {table}
            WHERE NVL(IS_DELETE, 0) = 0
              AND (:last_id IS NULL OR ID > :last_id)
            ORDER BY ID
        ) WHERE ROWNUM <= :batch_size
    """
    yield from _iter_mapped(
        conn,
        sql,
        {},
        batch_size,
        limit,
        source="LEGACY",
        object_type=OBJECT_TYPE_INCOMING,
        fallback_tenant=target.tenant_code,
        incoming=True,
        schema=target.schema,
        incoming_process_id_col="OBJECT_ID",
        incoming_process_table_filter="",
    )


def iter_legacy_outgoing(
    conn,
    target: OracleTarget,
    batch_size: int,
    limit: int,
) -> Iterator[List[DocRow]]:
    table = qualify(target.schema, "VB_OUTGOING_DOC")
    sql = f"""
        SELECT * FROM (
            SELECT ID, DOC_CODE, QUOTE, PUBLISHER_NAME, SUB_BOOK_NUMBER,
                   CAST(NULL AS NUMBER) AS OUTGOING_NUMBER, NOTE,
                   NVL(PUBLISHER_ID, TDHVP_PUBLISHER_ID) AS PUBLISHER_ID,
                   CAST(NULL AS VARCHAR2(100)) AS TENANT_CODE
            FROM {table}
            WHERE NVL(IS_DELETE, 0) = 0
              AND (:last_id IS NULL OR ID > :last_id)
            ORDER BY ID
        ) WHERE ROWNUM <= :batch_size
    """
    yield from _iter_mapped(
        conn,
        sql,
        {},
        batch_size,
        limit,
        source="LEGACY",
        object_type=OBJECT_TYPE_OUTGOING,
        fallback_tenant=target.tenant_code,
        incoming=False,
        schema=target.schema,
        incoming_process_id_col="DOC_ID",
        incoming_process_table_filter="AND NVL(IS_DELETE, 0) = 0",
    )


def _iter_mapped(
    conn,
    sql: str,
    extra_binds: Dict,
    batch_size: int,
    limit: int,
    *,
    source: str,
    object_type: int,
    fallback_tenant: str,
    incoming: bool,
    schema: str,
    incoming_process_id_col: str,
    incoming_process_table_filter: str,
) -> Iterator[List[DocRow]]:
    # Oracle treats '' as NULL, so "ID > ''" matches nothing. First page uses NULL.
    last_id = None
    fetched = 0
    while True:
        remaining = batch_size
        if limit > 0:
            remaining = min(batch_size, limit - fetched)
            if remaining <= 0:
                return
        binds = {"last_id": last_id, "batch_size": remaining}
        binds.update(extra_binds)
        with conn.cursor() as cur:
            cur.arraysize = batch_size
            cur.execute(sql, binds)
            raw_rows = cur.fetchall()
        if not raw_rows:
            return
        docs: List[DocRow] = []
        for raw in raw_rows:
            if incoming:
                (
                    doc_id,
                    doc_code,
                    quote,
                    publisher_name,
                    outside_publisher_name,
                    note,
                    dept_id,
                    tenant_code,
                ) = raw
                docs.append(
                    DocRow(
                        id=str(doc_id),
                        source=source,
                        object_type=object_type,
                        dept_id=_as_str(dept_id) or "",
                        tenant_code=_as_str(tenant_code) or fallback_tenant,
                        doc_code=_as_str(doc_code),
                        quote=_as_str(quote),
                        publisher_name=_as_str(publisher_name),
                        outside_publisher_name=_as_str(outside_publisher_name),
                        note=_as_str(note),
                    )
                )
            else:
                (
                    doc_id,
                    doc_code,
                    quote,
                    publisher_name,
                    sub_book_number,
                    outgoing_number,
                    note,
                    dept_id,
                    tenant_code,
                ) = raw
                docs.append(
                    DocRow(
                        id=str(doc_id),
                        source=source,
                        object_type=object_type,
                        dept_id=_as_str(dept_id) or "",
                        tenant_code=_as_str(tenant_code) or fallback_tenant,
                        doc_code=_as_str(doc_code),
                        quote=_as_str(quote),
                        publisher_name=_as_str(publisher_name),
                        sub_book_number=_as_str(sub_book_number),
                        outgoing_number=_as_str(outgoing_number),
                        note=_as_str(note),
                    )
                )
        _attach_related(
            conn,
            schema,
            docs,
            incoming_process_id_col=incoming_process_id_col,
            incoming_process_table_filter=incoming_process_table_filter,
        )
        yield docs
        fetched += len(docs)
        last_id = docs[-1].id
        if len(raw_rows) < remaining:
            return
