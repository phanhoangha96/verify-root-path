#!/usr/bin/env python3
"""
CLI: backfill Solr FILE documents (fileContent) for existing incoming/outgoing docs.

Matches eoffice-solr DocServiceImpl.addDocument:
  download/read file → Apache Tika extract → Solr add
  fields: id, objectId, objectType, fileContent, fileServiceId, deptId, tenantCode

Does not call eoffice-business. Re-runs skip existing (fileServiceId + deptId)
unless --force. Solr id: {fileServiceId}_{deptId}_file (overwrite=true).

Usage:
  python solr_doc_file_backfill.py --source NEW --dry-run --limit 20
  python solr_doc_file_backfill.py --source NEW
  python solr_doc_file_backfill.py --source LEGACY --object-type 1
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

_PART_DIR = Path(__file__).resolve().parent
if str(_PART_DIR) not in sys.path:
    sys.path.insert(0, str(_PART_DIR))

from solr_file_config import SolrFileBackfillSettings, load_solr_file_backfill_settings
from solr_file_db import (
    OBJECT_TYPE_INCOMING,
    OBJECT_TYPE_OUTGOING,
    FileIndexRow,
    connect,
    is_true,
    iter_legacy_incoming,
    iter_legacy_outgoing,
    iter_new_incoming,
    iter_new_outgoing,
    solr_file_id,
    unique_dept_ids,
)
from solr_file_download import FileBytesLoader, FileLoadError
from solr_file_extract import TikaExtractError, extract_content
from solr_file_report import BackfillReport, IssueRow, write_reports
from solr_file_solr import SolrClient, add_docs_with_split


ExtractFn = Callable[[bytes, str], str]


class ExtractCache:
    def __init__(self, max_size: int = 256) -> None:
        self.max_size = max_size
        self._data: "OrderedDict[str, str]" = OrderedDict()

    def get(self, key: str) -> Optional[str]:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: str, value: str) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.max_size:
            self._data.popitem(last=False)


def _log(message: str) -> None:
    print(message, flush=True)


def file_key(row: FileIndexRow) -> str:
    if is_true(row.file_service_id):
        return row.file_service_id
    if is_true(row.attachment_id):
        return f"att:{row.attachment_id}"
    return f"path:{row.file_path}"


def solr_file_service_id(row: FileIndexRow) -> str:
    return row.file_service_id or row.attachment_id


def _solr_doc(row: FileIndexRow, dept_id: str, file_content: str) -> Dict:
    file_id = solr_file_service_id(row)
    return {
        "id": solr_file_id(file_id, dept_id),
        "objectId": row.doc_id,
        "objectType": row.object_type,
        "fileContent": file_content,
        "fileServiceId": file_id,
        "deptId": dept_id,
        "tenantCode": row.tenant_code,
    }


def _load_and_extract(
    row: FileIndexRow,
    loader: FileBytesLoader,
    extract_fn: ExtractFn,
    cache: ExtractCache,
) -> str:
    key = file_key(row)
    cached = cache.get(key)
    if cached is not None:
        return cached
    data = loader.load(
        file_service_id=row.file_service_id,
        file_path=row.file_path,
        tenant_code=row.tenant_code,
        root_path=row.root_path,
    )
    text = extract_fn(data, row.file_name)
    cache.put(key, text)
    return text


def _index_ready(
    ready_rows: List[Tuple[FileIndexRow, str]],
    ready: List[Dict],
    report: BackfillReport,
    solr: SolrClient,
    commit_within_ms: int,
) -> None:
    def _add(docs: List[Dict], commit_ms: int) -> None:
        solr.add_docs(docs, commit_ms)
        report.solr_batches += 1

    ok, failed = add_docs_with_split(_add, ready, commit_within_ms, log=_log)
    for index in ok:
        row, _dept = ready_rows[index]
        report.bump(row.source, row.object_type, "indexed")
    for index, reason, detail in failed:
        row, dept_id = ready_rows[index]
        report.add_error(IssueRow(row.source, row.object_type, row.doc_id, reason, f"deptId={dept_id} {detail}"))


def process_batch(
    rows: List[FileIndexRow],
    report: BackfillReport,
    solr: Optional[SolrClient],
    loader: Optional[FileBytesLoader],
    extract_fn: ExtractFn,
    cache: ExtractCache,
    commit_within_ms: int,
    dry_run: bool,
    skip_existing: bool,
    existing_keys: Optional[Set[Tuple[str, str]]] = None,
) -> None:
    ready: List[Dict] = []
    ready_rows: List[Tuple[FileIndexRow, str]] = []
    existing = existing_keys or set()

    for row in rows:
        report.bump(row.source, row.object_type, "scanned")
        if not is_true(row.attachment_id):
            report.add_skip(IssueRow(row.source, row.object_type, row.doc_id, "MISSING_ATTACHMENT"))
            continue
        depts = unique_dept_ids([row.dept_id], row.extra_dept_ids)
        if not depts:
            report.add_skip(
                IssueRow(
                    row.source,
                    row.object_type,
                    row.doc_id,
                    "MISSING_DEPT_ID",
                    "incoming needs toDeptId / outgoing needs publisherId",
                )
            )
            continue
        file_id = solr_file_service_id(row)
        pending_depts = []
        for dept_id in depts:
            if skip_existing and (file_id, dept_id) in existing:
                report.add_skip(
                    IssueRow(
                        row.source,
                        row.object_type,
                        row.doc_id,
                        "ALREADY_INDEXED",
                        f"fileServiceId={file_id} deptId={dept_id}",
                    )
                )
                continue
            pending_depts.append(dept_id)
        if not pending_depts:
            continue
        if dry_run or solr is None or loader is None:
            for _dept_id in pending_depts:
                report.bump(row.source, row.object_type, "indexed")
            continue
        try:
            content = _load_and_extract(row, loader, extract_fn, cache)
        except FileLoadError as ex:
            if ex.reason in {"MISSING_FILE_SERVICE_ID", "FILE_NOT_FOUND", "MISSING_TENANT_CODE"}:
                report.add_skip(IssueRow(row.source, row.object_type, row.doc_id, ex.reason, ex.detail))
            else:
                report.add_error(IssueRow(row.source, row.object_type, row.doc_id, ex.reason, ex.detail[:1000]))
            continue
        except TikaExtractError as ex:
            report.add_error(IssueRow(row.source, row.object_type, row.doc_id, "TIKA_FAILED", str(ex)[:1000]))
            continue
        except Exception as ex:
            report.add_error(IssueRow(row.source, row.object_type, row.doc_id, "FILE_READ_FAILED", str(ex)[:1000]))
            continue
        if not (content or "").strip():
            report.add_skip(IssueRow(row.source, row.object_type, row.doc_id, "EMPTY_FILE_CONTENT", row.file_name))
            continue
        for dept_id in pending_depts:
            ready.append(_solr_doc(row, dept_id, content))
            ready_rows.append((row, dept_id))

    if not ready or dry_run or solr is None:
        return
    _index_ready(ready_rows, ready, report, solr, commit_within_ms)


def _existing_for_page(solr: Optional[SolrClient], rows: List[FileIndexRow], skip_existing: bool) -> Set[Tuple[str, str]]:
    if solr is None or not skip_existing:
        return set()
    ids = [solr_file_service_id(row) for row in rows if is_true(solr_file_service_id(row))]
    if not ids:
        return set()
    return solr.existing_file_keys(ids)


def _run_stream(
    pages: Iterable[List[FileIndexRow]],
    label: str,
    report: BackfillReport,
    solr: Optional[SolrClient],
    loader: Optional[FileBytesLoader],
    extract_fn: ExtractFn,
    cache: ExtractCache,
    commit_within_ms: int,
    dry_run: bool,
    skip_existing: bool,
) -> None:
    total = 0
    started = time.monotonic()
    skipped = lambda: (
        report.skipped_missing_dept
        + report.skipped_missing_attachment
        + report.skipped_missing_file
        + report.skipped_empty_content
        + report.skipped_already_indexed
    )
    for page in pages:
        total += len(page)
        existing = _existing_for_page(solr, page, skip_existing and not dry_run)
        process_batch(
            page,
            report,
            solr,
            loader,
            extract_fn,
            cache,
            commit_within_ms,
            dry_run,
            skip_existing and not dry_run,
            existing,
        )
        elapsed = time.monotonic() - started
        print(
            f"  {label}: scanned={total} indexed={report.indexed} "
            f"skipped={skipped()} errors={report.errors} elapsed={elapsed:.1f}s",
            flush=True,
        )
    if total == 0:
        print(f"  {label}: scanned=0 (no rows from Oracle)", flush=True)


def _wanted(object_type_arg: str) -> List[int]:
    if object_type_arg == "ALL":
        return [OBJECT_TYPE_INCOMING, OBJECT_TYPE_OUTGOING]
    return [int(object_type_arg)]


def run_new(
    settings: SolrFileBackfillSettings,
    object_types: List[int],
    batch_size: int,
    limit: int,
    tenant_filter: str,
    report: BackfillReport,
    solr: Optional[SolrClient],
    loader: Optional[FileBytesLoader],
    extract_fn: ExtractFn,
    cache: ExtractCache,
    dry_run: bool,
    skip_existing: bool,
) -> None:
    for target in settings.new_targets:
        print(f"NEW Oracle {target.name} dsn={target.dsn} schema={target.schema or '(user default)'}", flush=True)
        conn = connect(target)
        try:
            if OBJECT_TYPE_INCOMING in object_types:
                _run_stream(
                    iter_new_incoming(conn, target, batch_size, limit, tenant_filter),
                    f"NEW incoming/{target.name}",
                    report,
                    solr,
                    loader,
                    extract_fn,
                    cache,
                    settings.commit_within_ms,
                    dry_run,
                    skip_existing,
                )
            if OBJECT_TYPE_OUTGOING in object_types:
                _run_stream(
                    iter_new_outgoing(conn, target, batch_size, limit, tenant_filter),
                    f"NEW outgoing/{target.name}",
                    report,
                    solr,
                    loader,
                    extract_fn,
                    cache,
                    settings.commit_within_ms,
                    dry_run,
                    skip_existing,
                )
        finally:
            conn.close()


def run_legacy(
    settings: SolrFileBackfillSettings,
    object_types: List[int],
    batch_size: int,
    limit: int,
    report: BackfillReport,
    solr: Optional[SolrClient],
    loader: Optional[FileBytesLoader],
    extract_fn: ExtractFn,
    cache: ExtractCache,
    dry_run: bool,
    skip_existing: bool,
) -> None:
    target = settings.legacy
    print(f"LEGACY Oracle dsn={target.dsn} schema={target.schema}", flush=True)
    conn = connect(target)
    try:
        if OBJECT_TYPE_INCOMING in object_types:
            _run_stream(
                iter_legacy_incoming(conn, target, batch_size, limit),
                "LEGACY incoming",
                report,
                solr,
                loader,
                extract_fn,
                cache,
                settings.commit_within_ms,
                dry_run,
                skip_existing,
            )
        if OBJECT_TYPE_OUTGOING in object_types:
            _run_stream(
                iter_legacy_outgoing(conn, target, batch_size, limit),
                "LEGACY outgoing",
                report,
                solr,
                loader,
                extract_fn,
                cache,
                settings.commit_within_ms,
                dry_run,
                skip_existing,
            )
    finally:
        conn.close()


def _print_targets(settings: SolrFileBackfillSettings, source: str) -> None:
    print("Oracle connections (two databases):", flush=True)
    if source in {"ALL", "LEGACY"}:
        t = settings.legacy
        print(
            f"  LEGACY  user={t.user} dsn={t.dsn} schema={t.schema or '(user default)'}",
            flush=True,
        )
    if source in {"ALL", "NEW"}:
        if not settings.new_targets:
            print("  NEW     (not configured)", flush=True)
        for t in settings.new_targets:
            print(
                f"  NEW     name={t.name} user={t.user} dsn={t.dsn} "
                f"schema={t.schema or '(user default)'} tenant={t.tenant_code}",
                flush=True,
            )
    print(
        f"File source={settings.file_source} download={settings.file_service_download_url}",
        flush=True,
    )


def _make_extract_fn(settings: SolrFileBackfillSettings) -> ExtractFn:
    def _extract(data: bytes, filename: str) -> str:
        return extract_content(
            data,
            filename,
            tika_app_jar=settings.tika_app_jar,
            write_limit=settings.tika_write_limit,
        )

    return _extract


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Solr FILE documents (fileContent via Tika) from NEW + LEGACY Oracle."
    )
    parser.add_argument("--source", default="ALL", choices=["ALL", "NEW", "LEGACY"])
    parser.add_argument("--object-type", default="ALL", choices=["ALL", "1", "2"], help="1=incoming, 2=outgoing")
    parser.add_argument("--dry-run", action="store_true", help="Scan Oracle, do not download files or write Solr")
    parser.add_argument("--force", action="store_true", help="Re-index even if fileServiceId+deptId already exists")
    parser.add_argument("--limit", type=int, default=0, help="Max docs per source/object-type (0 = all)")
    parser.add_argument("--batch-size", type=int, default=0, help="Override SOLR_BACKFILL_BATCH_SIZE")
    parser.add_argument("--tenant-code", default="", help="Filter NEW docs by TENANT_CODE")
    parser.add_argument("--new-tenants-file", default="", help="JSON array of NEW Oracle connections")
    parser.add_argument("--solr-host", default="", help="Override SOLR_HOST")
    parser.add_argument(
        "--file-source",
        default=None,
        choices=["auto", "file-service", "disk"],
        help="Override FILE_SOURCE",
    )
    args = parser.parse_args(argv)

    require_legacy = args.source in {"ALL", "LEGACY"}
    require_new = args.source in {"ALL", "NEW"}
    settings = load_solr_file_backfill_settings(
        require_legacy=require_legacy,
        require_new=require_new,
        new_tenants_file=args.new_tenants_file,
    )
    if args.solr_host:
        settings = replace(settings, solr_host=args.solr_host.rstrip("/"))
    if args.file_source:
        settings = replace(settings, file_source=args.file_source)
    _print_targets(settings, args.source)
    batch_size = args.batch_size or settings.batch_size
    object_types = _wanted(args.object_type)
    skip_existing = not args.force
    started = time.monotonic()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = BackfillReport(
        started_at=datetime.now().isoformat(timespec="seconds"),
        dry_run=args.dry_run,
        source=args.source,
        object_type=args.object_type,
        solr_host=settings.solr_host,
        solr_core=settings.solr_core,
        file_source=settings.file_source,
    )

    solr: Optional[SolrClient] = None
    loader: Optional[FileBytesLoader] = None
    extract_fn = _make_extract_fn(settings)
    cache = ExtractCache()
    try:
        if not args.dry_run:
            solr = SolrClient(
                settings.solr_host,
                settings.solr_core,
                settings.solr_user,
                settings.solr_password,
                timeout=settings.solr_timeout,
            )
            print(f"Pinging Solr {settings.solr_host}/{settings.solr_core} ...", flush=True)
            solr.ping()
            solr.ensure_text_fields(log=_log)
            loader = FileBytesLoader(
                settings.file_source,
                settings.file_service_download_url,
                settings.file_service_timeout,
                settings.file_storage_roots,
            )

        if args.source in {"ALL", "NEW"}:
            run_new(
                settings,
                object_types,
                batch_size,
                args.limit,
                args.tenant_code.strip(),
                report,
                solr,
                loader,
                extract_fn,
                cache,
                args.dry_run,
                skip_existing,
            )
        if args.source in {"ALL", "LEGACY"}:
            run_legacy(
                settings,
                object_types,
                batch_size,
                args.limit,
                report,
                solr,
                loader,
                extract_fn,
                cache,
                args.dry_run,
                skip_existing,
            )

        if solr is not None and not args.dry_run:
            print("Final Solr commit...", flush=True)
            solr.commit()
    except Exception as ex:
        report.add_error(IssueRow("ALL", 0, "", "FATAL", str(ex)[:1000]))
        print(f"FATAL: {ex}", flush=True)
    finally:
        report.finished_at = datetime.now().isoformat(timespec="seconds")
        report.duration_seconds = round(time.monotonic() - started, 2)
        paths = write_reports(report, settings.output_dir, stamp, settings.output_file)
        skipped = (
            report.skipped_missing_dept
            + report.skipped_missing_attachment
            + report.skipped_missing_file
            + report.skipped_empty_content
            + report.skipped_already_indexed
        )
        print(
            f"Done. scanned={report.scanned} indexed={report.indexed} "
            f"skipped={skipped} errors={report.errors} duration={report.duration_seconds}s",
            flush=True,
        )
        print(f"Excel: {paths['xlsx'].resolve()}", flush=True)
        print(f"JSON:  {paths['json'].resolve()}", flush=True)
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
