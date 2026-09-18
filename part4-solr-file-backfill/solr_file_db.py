from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

from solr_file_config import OracleTarget

OBJECT_TYPE_INCOMING = 1
OBJECT_TYPE_OUTGOING = 2
# VB_ATTACHMENT.OBJECT_TYPE for files on a VB_DOC_RELATION row.
ATTACHMENT_OBJECT_TYPE_DOC_RELATION = 5
# VB_DOC_RELATION.OBJECT_TYPE: 0 = incoming doc, 1 = outgoing doc (Java DOC_RELATION_OBJECT_TYPE).
DOC_RELATION_OBJECT_TYPE_INCOMING = 0
DOC_RELATION_OBJECT_TYPE_OUTGOING = 1
# VB_DOC_USER.DOC_TYPE (Java DOC_USER_DOC_TYPE): search inbox is per this dept, not publisher.
DOC_USER_DOC_TYPE_INCOMING = "0"
DOC_USER_DOC_TYPE_OUTGOING = "1"


@dataclass
class DocLite:
    id: str
    source: str
    object_type: int
    dept_id: str
    tenant_code: str


@dataclass
class AttachmentRow:
    object_id: str
    attachment_id: str
    file_service_id: str
    file_path: str
    file_name: str
    root_path: str
    tenant_code: str


@dataclass
class FileIndexRow:
    doc_id: str
    source: str
    object_type: int
    dept_id: str
    tenant_code: str
    attachment_id: str
    file_service_id: str
    file_path: str
    file_name: str
    root_path: str
    extra_dept_ids: List[str] = field(default_factory=list)
    file_role: str = "main"  # main | relation (VB_DOC_RELATION)


def qualify(schema: str, table: str) -> str:
    return f"{schema}.{table}" if schema else table


def connect(target: OracleTarget):
    import oracledb

    return oracledb.connect(user=target.user, password=target.password, dsn=target.dsn)


def _as_str(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def is_true(value: Optional[str]) -> bool:
    return value is not None and str(value).strip() != ""


def _in_clause(prefix: str, ids: Sequence[str]) -> Tuple[str, Dict[str, str]]:
    binds: Dict[str, str] = {}
    names: List[str] = []
    for i, doc_id in enumerate(ids):
        name = f"{prefix}{i}"
        names.append(f":{name}")
        binds[name] = doc_id
    return ", ".join(names), binds


def _fetch_map(conn, sql: str, binds: Dict) -> Dict[str, List[str]]:
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


def unique_dept_ids(*groups: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for group in groups:
        for item in group:
            text = _as_str(item)
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
    return out


def pick_attachment(rows: Sequence[AttachmentRow]) -> Optional[AttachmentRow]:
    """Match Java findByDoc: first active attachment, prefer one with fileServiceId."""
    if not rows:
        return None
    with_id = [row for row in rows if is_true(row.file_service_id)]
    return with_id[0] if with_id else rows[0]


def _file_dedupe_key(row: AttachmentRow) -> str:
    if is_true(row.file_service_id):
        return row.file_service_id
    return row.attachment_id


def collect_index_files(
    main_rows: Sequence[AttachmentRow],
    relation_rows: Sequence[AttachmentRow],
) -> List[AttachmentRow]:
    """Match Java collectIndexSolrFileServiceIds: first main file + all VB_DOC_RELATION files."""
    out: List[AttachmentRow] = []
    seen: Set[str] = set()
    main = pick_attachment(main_rows)
    if main:
        out.append(main)
        key = _file_dedupe_key(main)
        if key:
            seen.add(key)
    for row in relation_rows:
        key = _file_dedupe_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def solr_file_id(file_service_id: str, dept_id: str) -> str:
    return f"{file_service_id}_{dept_id}_file"


def iter_new_incoming(
    conn,
    target: OracleTarget,
    batch_size: int,
    limit: int,
    tenant_filter: str,
) -> Iterator[List[FileIndexRow]]:
    table = qualify(target.schema, "VB_INCOMING_DOC")
    tenant_sql = "AND TENANT_CODE = :tenant_code" if tenant_filter else ""
    sql = f"""
        SELECT * FROM (
            SELECT ID, TO_DEPT_ID, TENANT_CODE
            FROM {table}
            WHERE NVL(IS_DELETE, 0) = 0
              AND (:last_id IS NULL OR ID > :last_id)
              {tenant_sql}
            ORDER BY ID
        ) WHERE ROWNUM <= :batch_size
    """
    extra = {"tenant_code": tenant_filter} if tenant_filter else {}
    yield from _iter_docs(
        conn,
        sql,
        extra,
        batch_size,
        limit,
        source=target.name,
        object_type=OBJECT_TYPE_INCOMING,
        fallback_tenant=target.tenant_code,
        schema=target.schema,
        incoming=True,
        incoming_process_id_col="DOC_ID",
        incoming_process_table_filter="AND NVL(IS_DELETE, 0) = 0",
    )


def iter_new_outgoing(
    conn,
    target: OracleTarget,
    batch_size: int,
    limit: int,
    tenant_filter: str,
) -> Iterator[List[FileIndexRow]]:
    table = qualify(target.schema, "VB_OUTGOING_DOC")
    tenant_sql = "AND TENANT_CODE = :tenant_code" if tenant_filter else ""
    sql = f"""
        SELECT * FROM (
            SELECT ID, PUBLISHER_ID, TENANT_CODE
            FROM {table}
            WHERE NVL(IS_DELETE, 0) = 0
              AND (:last_id IS NULL OR ID > :last_id)
              {tenant_sql}
            ORDER BY ID
        ) WHERE ROWNUM <= :batch_size
    """
    extra = {"tenant_code": tenant_filter} if tenant_filter else {}
    yield from _iter_docs(
        conn,
        sql,
        extra,
        batch_size,
        limit,
        source=target.name,
        object_type=OBJECT_TYPE_OUTGOING,
        fallback_tenant=target.tenant_code,
        schema=target.schema,
        incoming=False,
        incoming_process_id_col="DOC_ID",
        incoming_process_table_filter="AND NVL(IS_DELETE, 0) = 0",
    )


def iter_legacy_incoming(
    conn,
    target: OracleTarget,
    batch_size: int,
    limit: int,
) -> Iterator[List[FileIndexRow]]:
    table = qualify(target.schema, "V_VB_INCOMING_DOC_V2")
    sql = f"""
        SELECT * FROM (
            SELECT ID, EOFFICE_TO_DEPT_ID, CAST(NULL AS VARCHAR2(100)) AS TENANT_CODE
            FROM {table}
            WHERE NVL(IS_DELETE, 0) = 0
              AND (:last_id IS NULL OR ID > :last_id)
            ORDER BY ID
        ) WHERE ROWNUM <= :batch_size
    """
    yield from _iter_docs(
        conn,
        sql,
        {},
        batch_size,
        limit,
        source="LEGACY",
        object_type=OBJECT_TYPE_INCOMING,
        fallback_tenant=target.tenant_code,
        schema=target.schema,
        incoming=True,
        incoming_process_id_col="OBJECT_ID",
        incoming_process_table_filter="",
    )


def iter_legacy_outgoing(
    conn,
    target: OracleTarget,
    batch_size: int,
    limit: int,
) -> Iterator[List[FileIndexRow]]:
    table = qualify(target.schema, "VB_OUTGOING_DOC")
    sql = f"""
        SELECT * FROM (
            SELECT ID, NVL(PUBLISHER_ID, TDHVP_PUBLISHER_ID) AS PUBLISHER_ID,
                   CAST(NULL AS VARCHAR2(100)) AS TENANT_CODE
            FROM {table}
            WHERE NVL(IS_DELETE, 0) = 0
              AND (:last_id IS NULL OR ID > :last_id)
            ORDER BY ID
        ) WHERE ROWNUM <= :batch_size
    """
    yield from _iter_docs(
        conn,
        sql,
        {},
        batch_size,
        limit,
        source="LEGACY",
        object_type=OBJECT_TYPE_OUTGOING,
        fallback_tenant=target.tenant_code,
        schema=target.schema,
        incoming=False,
        incoming_process_id_col="DOC_ID",
        incoming_process_table_filter="AND NVL(IS_DELETE, 0) = 0",
    )


def _iter_docs(
    conn,
    sql: str,
    extra_binds: Dict,
    batch_size: int,
    limit: int,
    *,
    source: str,
    object_type: int,
    fallback_tenant: str,
    schema: str,
    incoming: bool,
    incoming_process_id_col: str,
    incoming_process_table_filter: str,
) -> Iterator[List[FileIndexRow]]:
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
        docs: List[DocLite] = []
        for doc_id, dept_id, tenant_code in raw_rows:
            docs.append(
                DocLite(
                    id=str(doc_id),
                    source=source,
                    object_type=object_type,
                    dept_id=_as_str(dept_id) or "",
                    tenant_code=_as_str(tenant_code) or fallback_tenant,
                )
            )
        yield _attach_files(
            conn,
            schema,
            docs,
            incoming=incoming,
            incoming_process_id_col=incoming_process_id_col,
            incoming_process_table_filter=incoming_process_table_filter,
            fallback_tenant=fallback_tenant,
        )
        fetched += len(docs)
        last_id = docs[-1].id
        if len(raw_rows) < remaining:
            return


def _attachment_sql_new(schema: str, in_sql: str) -> str:
    return f"""
        SELECT OBJECT_ID, ID, FILE_SERVICE_ID, FILE_PATH, FILE_NAME,
               CAST(NULL AS VARCHAR2(200)) AS ROOT_PATH, TENANT_CODE
        FROM {qualify(schema, "VB_ATTACHMENT")}
        WHERE OBJECT_ID IN ({in_sql})
          AND OBJECT_TYPE = :object_type
          AND NVL(IS_DELETE, 0) = 0
          AND (STATUS IS NULL OR STATUS = 'active')
        ORDER BY OBJECT_ID, CREATE_TIME ASC NULLS LAST, ID ASC
    """


def _attachment_sql_hybrid(schema: str, in_sql: str) -> str:
    return f"""
        SELECT OBJECT_ID, ID, FILE_SERVICE_ID, FILE_PATH, FILE_NAME, ROOT_PATH,
               CAST(NULL AS VARCHAR2(100)) AS TENANT_CODE
        FROM {qualify(schema, "VB_ATTACHMENT")}
        WHERE OBJECT_ID IN ({in_sql})
          AND OBJECT_TYPE = :object_type
          AND NVL(IS_DELETE, 0) = 0
        ORDER BY OBJECT_ID, ID ASC
    """


def _attachment_sql_legacy(schema: str, in_sql: str) -> str:
    return f"""
        SELECT OBJECT_ID, ID,
               CAST(NULL AS VARCHAR2(100)) AS FILE_SERVICE_ID,
               FILE_PATH, FILE_NAME, ROOT_PATH,
               CAST(NULL AS VARCHAR2(100)) AS TENANT_CODE
        FROM {qualify(schema, "VB_ATTACHMENT")}
        WHERE OBJECT_ID IN ({in_sql})
          AND OBJECT_TYPE = :object_type
          AND NVL(IS_DELETE, 0) = 0
        ORDER BY OBJECT_ID, ID ASC
    """


def _is_invalid_identifier(exc: BaseException) -> bool:
    message = str(exc).upper()
    return "ORA-00904" in message or "INVALID IDENTIFIER" in message


def _is_missing_object(exc: BaseException) -> bool:
    message = str(exc).upper()
    return "ORA-00942" in message or "TABLE OR VIEW DOES NOT EXIST" in message


def _fetch_attachments(
    conn,
    schema: str,
    ids: Sequence[str],
    object_type: int,
    fallback_tenant: str,
) -> Dict[str, List[AttachmentRow]]:
    if not ids:
        return {}
    in_sql, binds = _in_clause("aid", ids)
    binds["object_type"] = object_type
    grouped: Dict[str, List[AttachmentRow]] = defaultdict(list)
    with conn.cursor() as cur:
        cur.arraysize = 1000
        raw_rows = None
        last_ex: Optional[BaseException] = None
        for sql in (
            _attachment_sql_new(schema, in_sql),
            _attachment_sql_hybrid(schema, in_sql),
            _attachment_sql_legacy(schema, in_sql),
        ):
            try:
                cur.execute(sql, binds)
                raw_rows = cur.fetchall()
                last_ex = None
                break
            except Exception as ex:
                last_ex = ex
                if not _is_invalid_identifier(ex):
                    raise
        if last_ex is not None:
            raise last_ex
        if raw_rows is None:
            return {}
        for object_id, attachment_id, file_service_id, file_path, file_name, root_path, tenant_code in raw_rows:
            if object_id is None or attachment_id is None:
                continue
            grouped[str(object_id)].append(
                AttachmentRow(
                    object_id=str(object_id),
                    attachment_id=str(attachment_id),
                    file_service_id=_as_str(file_service_id) or "",
                    file_path=_as_str(file_path) or "",
                    file_name=_as_str(file_name) or "",
                    root_path=_as_str(root_path) or "",
                    tenant_code=_as_str(tenant_code) or fallback_tenant,
                )
            )
    return grouped


def _relation_attachment_sql_new(schema: str, in_sql: str) -> str:
    return f"""
        SELECT r.OBJECT_ID, a.ID, a.FILE_SERVICE_ID, a.FILE_PATH, a.FILE_NAME,
               CAST(NULL AS VARCHAR2(200)) AS ROOT_PATH, a.TENANT_CODE
        FROM {qualify(schema, "VB_DOC_RELATION")} r
        JOIN {qualify(schema, "VB_ATTACHMENT")} a ON a.OBJECT_ID = r.ID
        WHERE r.OBJECT_ID IN ({in_sql})
          AND r.OBJECT_TYPE = :rel_object_type
          AND NVL(r.IS_DELETE, 0) = 0
          AND a.OBJECT_TYPE = :att_object_type
          AND NVL(a.IS_DELETE, 0) = 0
        ORDER BY r.OBJECT_ID, r.CREATE_TIME ASC NULLS LAST, a.CREATE_TIME ASC NULLS LAST, a.ID ASC
    """


def _relation_attachment_sql_hybrid(schema: str, in_sql: str) -> str:
    return f"""
        SELECT r.OBJECT_ID, a.ID, a.FILE_SERVICE_ID, a.FILE_PATH, a.FILE_NAME, a.ROOT_PATH,
               CAST(NULL AS VARCHAR2(100)) AS TENANT_CODE
        FROM {qualify(schema, "VB_DOC_RELATION")} r
        JOIN {qualify(schema, "VB_ATTACHMENT")} a ON a.OBJECT_ID = r.ID
        WHERE r.OBJECT_ID IN ({in_sql})
          AND r.OBJECT_TYPE = :rel_object_type
          AND NVL(r.IS_DELETE, 0) = 0
          AND a.OBJECT_TYPE = :att_object_type
          AND NVL(a.IS_DELETE, 0) = 0
        ORDER BY r.OBJECT_ID, r.ID ASC, a.ID ASC
    """


def _relation_attachment_sql_legacy(schema: str, in_sql: str) -> str:
    return f"""
        SELECT r.OBJECT_ID, a.ID,
               CAST(NULL AS VARCHAR2(100)) AS FILE_SERVICE_ID,
               a.FILE_PATH, a.FILE_NAME, a.ROOT_PATH,
               CAST(NULL AS VARCHAR2(100)) AS TENANT_CODE
        FROM {qualify(schema, "VB_DOC_RELATION")} r
        JOIN {qualify(schema, "VB_ATTACHMENT")} a ON a.OBJECT_ID = r.ID
        WHERE r.OBJECT_ID IN ({in_sql})
          AND r.OBJECT_TYPE = :rel_object_type
          AND NVL(r.IS_DELETE, 0) = 0
          AND a.OBJECT_TYPE = :att_object_type
          AND NVL(a.IS_DELETE, 0) = 0
        ORDER BY r.OBJECT_ID, r.ID ASC, a.ID ASC
    """


def _fetch_relation_attachments(
    conn,
    schema: str,
    ids: Sequence[str],
    relation_object_type: int,
    fallback_tenant: str,
) -> Dict[str, List[AttachmentRow]]:
    """Files linked via VB_DOC_RELATION: VB_ATTACHMENT.OBJECT_ID = VB_DOC_RELATION.ID, OBJECT_TYPE=5."""
    if not ids:
        return {}
    in_sql, binds = _in_clause("rid", ids)
    binds["rel_object_type"] = relation_object_type
    binds["att_object_type"] = ATTACHMENT_OBJECT_TYPE_DOC_RELATION
    grouped: Dict[str, List[AttachmentRow]] = defaultdict(list)
    with conn.cursor() as cur:
        cur.arraysize = 1000
        raw_rows = None
        last_ex: Optional[BaseException] = None
        for sql in (
            _relation_attachment_sql_new(schema, in_sql),
            _relation_attachment_sql_hybrid(schema, in_sql),
            _relation_attachment_sql_legacy(schema, in_sql),
        ):
            try:
                cur.execute(sql, binds)
                raw_rows = cur.fetchall()
                last_ex = None
                break
            except Exception as ex:
                last_ex = ex
                if _is_missing_object(ex):
                    return {}
                if not _is_invalid_identifier(ex):
                    raise
        if last_ex is not None:
            if _is_missing_object(last_ex) or _is_invalid_identifier(last_ex):
                return {}
            raise last_ex
        if raw_rows is None:
            return {}
        for object_id, attachment_id, file_service_id, file_path, file_name, root_path, tenant_code in raw_rows:
            if object_id is None or attachment_id is None:
                continue
            grouped[str(object_id)].append(
                AttachmentRow(
                    object_id=str(object_id),
                    attachment_id=str(attachment_id),
                    file_service_id=_as_str(file_service_id) or "",
                    file_path=_as_str(file_path) or "",
                    file_name=_as_str(file_name) or "",
                    root_path=_as_str(root_path) or "",
                    tenant_code=_as_str(tenant_code) or fallback_tenant,
                )
            )
    return grouped


def _fetch_extra_depts(
    conn,
    schema: str,
    docs: Sequence[DocLite],
    *,
    incoming: bool,
    incoming_process_id_col: str,
    incoming_process_table_filter: str,
) -> Dict[str, List[str]]:
    if not docs:
        return {}
    ids = [doc.id for doc in docs]
    in_sql, binds = _in_clause("did", ids)
    extra: Dict[str, List[str]] = defaultdict(list)
    if incoming:
        process_sql = f"""
            SELECT {incoming_process_id_col}, DEPT_RECEIVER_ID
            FROM {qualify(schema, "VB_INCOMING_PROCESS")}
            WHERE {incoming_process_id_col} IN ({in_sql})
              {incoming_process_table_filter}
              AND DEPT_RECEIVER_ID IS NOT NULL
        """
        try:
            for doc_id, values in _fetch_map(conn, process_sql, binds).items():
                extra[doc_id].extend(values)
        except Exception as ex:
            if not _is_invalid_identifier(ex):
                raise
        _append_doc_user_depts(conn, schema, extra, in_sql, binds, incoming=True)
        return extra

    out_process_sql = f"""
        SELECT DOC_ID, DEPT_RECEIVER_ID
        FROM {qualify(schema, "VB_OUTGOING_PROCESS")}
        WHERE DOC_ID IN ({in_sql})
          AND NVL(IS_DELETE, 0) = 0
          AND DEPT_RECEIVER_ID IS NOT NULL
    """
    try:
        for doc_id, values in _fetch_map(conn, out_process_sql, binds).items():
            extra[doc_id].extend(values)
    except Exception as ex:
        if not _is_invalid_identifier(ex):
            raise

    cc_sql = f"""
        SELECT DOC_ID, RECEIVED_DEPT_ID
        FROM {qualify(schema, "VB_CC_INFO")}
        WHERE DOC_ID IN ({in_sql})
          AND NVL(IS_DELETE, 0) = 0
          AND RECEIVED_DEPT_ID IS NOT NULL
    """
    try:
        for doc_id, values in _fetch_map(conn, cc_sql, binds).items():
            extra[doc_id].extend(values)
    except Exception as ex:
        if not _is_invalid_identifier(ex):
            raise

    user_dept_sql = f"""
        SELECT DOC_ID, USER_DEPT_ID
        FROM {qualify(schema, "VB_CC_INFO")}
        WHERE DOC_ID IN ({in_sql})
          AND NVL(IS_DELETE, 0) = 0
          AND USER_DEPT_ID IS NOT NULL
    """
    try:
        for doc_id, values in _fetch_map(conn, user_dept_sql, binds).items():
            extra[doc_id].extend(values)
    except Exception as ex:
        if not _is_invalid_identifier(ex):
            raise
    _append_doc_user_depts(conn, schema, extra, in_sql, binds, incoming=False)
    return extra


def _append_doc_user_depts(
    conn,
    schema: str,
    extra: Dict[str, List[str]],
    in_sql: str,
    binds: Dict[str, str],
    *,
    incoming: bool,
) -> None:
    """Clone FILE docs for inbox depts (VB_DOC_USER), not only publisher/toDept.

    findAllCombined keyword search filters Solr by the logged-in user's dept.
    Người soạn thảo / người được chuyển can sit in a different dept than PUBLISHER_ID.
    """
    sql = f"""
        SELECT DOC_ID, DEPT_ID
        FROM {qualify(schema, "VB_DOC_USER")}
        WHERE DOC_ID IN ({in_sql})
          AND NVL(IS_DELETE, 0) = 0
          AND DEPT_ID IS NOT NULL
          AND DOC_TYPE = :doc_type
    """
    query_binds = dict(binds)
    query_binds["doc_type"] = DOC_USER_DOC_TYPE_INCOMING if incoming else DOC_USER_DOC_TYPE_OUTGOING
    try:
        for doc_id, values in _fetch_map(conn, sql, query_binds).items():
            extra[doc_id].extend(values)
    except Exception as ex:
        if not _is_invalid_identifier(ex) and not _is_missing_object(ex):
            raise


def _file_index_row(
    doc: DocLite,
    chosen: Optional[AttachmentRow],
    extra: List[str],
    file_role: str = "main",
) -> FileIndexRow:
    return FileIndexRow(
        doc_id=doc.id,
        source=doc.source,
        object_type=doc.object_type,
        dept_id=doc.dept_id,
        tenant_code=chosen.tenant_code if chosen and is_true(chosen.tenant_code) else doc.tenant_code,
        attachment_id=chosen.attachment_id if chosen else "",
        file_service_id=chosen.file_service_id if chosen else "",
        file_path=chosen.file_path if chosen else "",
        file_name=chosen.file_name if chosen else "",
        root_path=chosen.root_path if chosen else "",
        extra_dept_ids=extra,
        file_role=file_role,
    )


def _attach_files(
    conn,
    schema: str,
    docs: List[DocLite],
    *,
    incoming: bool,
    incoming_process_id_col: str,
    incoming_process_table_filter: str,
    fallback_tenant: str,
) -> List[FileIndexRow]:
    if not docs:
        return []
    attachments = _fetch_attachments(
        conn,
        schema,
        [doc.id for doc in docs],
        docs[0].object_type,
        fallback_tenant,
    )
    relation_attachments = _fetch_relation_attachments(
        conn,
        schema,
        [doc.id for doc in docs],
        DOC_RELATION_OBJECT_TYPE_INCOMING if incoming else DOC_RELATION_OBJECT_TYPE_OUTGOING,
        fallback_tenant,
    )
    extra_depts = _fetch_extra_depts(
        conn,
        schema,
        docs,
        incoming=incoming,
        incoming_process_id_col=incoming_process_id_col,
        incoming_process_table_filter=incoming_process_table_filter,
    )
    rows: List[FileIndexRow] = []
    for doc in docs:
        extra = unique_dept_ids(extra_depts.get(doc.id, []))
        main_rows = attachments.get(doc.id, [])
        files = collect_index_files(main_rows, relation_attachments.get(doc.id, []))
        if not files:
            rows.append(_file_index_row(doc, None, extra, "main"))
            continue
        main = pick_attachment(main_rows)
        main_id = main.attachment_id if main else ""
        for chosen in files:
            role = "main" if chosen.attachment_id == main_id else "relation"
            rows.append(_file_index_row(doc, chosen, extra, role))
    return rows
