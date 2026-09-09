#!/usr/bin/env python3
"""
CLI: backfill Solr metadata (searchText) for existing incoming/outgoing documents.

One-shot, no HTTP API. Reads Oracle (NEW tenant schema + LEGACY schema) and writes
Solr core eoffice_document. Matching eoffice-business DocMetaSolrServiceImpl.

Usage:
  python solr_doc_meta_backfill.py
  python solr_doc_meta_backfill.py --source LEGACY --object-type 1
  python solr_doc_meta_backfill.py --source NEW --dry-run --limit 50
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from solr_backfill_config import SolrBackfillSettings, load_solr_backfill_settings
from solr_backfill_db import (
    OBJECT_TYPE_INCOMING,
    OBJECT_TYPE_OUTGOING,
    DocRow,
    connect,
    iter_legacy_incoming,
    iter_legacy_outgoing,
    iter_new_incoming,
    iter_new_outgoing,
)
from solr_backfill_report import BackfillReport, IssueRow, write_reports
from solr_backfill_solr import SolrClient
from solr_backfill_text import build_search_text, is_true, strip_html


def _search_text(row: DocRow) -> str:
    comments = [strip_html(item) for item in row.comments]
    if row.object_type == OBJECT_TYPE_INCOMING:
        return build_search_text(
            row.doc_code,
            row.quote,
            row.publisher_name,
            row.outside_publisher_name,
            row.book_number,
            row.note,
            *comments,
            *row.process_notes,
        )
    return build_search_text(
        row.doc_code,
        row.quote,
        row.publisher_name,
        row.sub_book_number,
        row.book_number,
        row.outgoing_number,
        row.note,
        *comments,
        *row.process_notes,
    )


def _solr_doc(row: DocRow, search_text: str) -> Dict:
    return {
        "id": f"{row.id}_{row.dept_id}_meta",
        "objectId": row.id,
        "objectType": row.object_type,
        "deptId": row.dept_id,
        "tenantCode": row.tenant_code,
        "indexType": "META",
        "searchText": search_text,
    }


def _process_batch(
    rows: List[DocRow],
    report: BackfillReport,
    solr: Optional[SolrClient],
    commit_within_ms: int,
    dry_run: bool,
) -> None:
    ready: List[Dict] = []
    ready_rows: List[DocRow] = []
    for row in rows:
        report.bump(row.source, row.object_type, "scanned")
        if not is_true(row.dept_id):
            report.add_skip(
                IssueRow(row.source, row.object_type, row.id, "MISSING_DEPT_ID", "incoming needs toDeptId / outgoing needs publisherId")
            )
            continue
        search_text = _search_text(row)
        if not search_text:
            report.add_skip(IssueRow(row.source, row.object_type, row.id, "EMPTY_SEARCH_TEXT"))
            continue
        ready.append(_solr_doc(row, search_text))
        ready_rows.append(row)

    if not ready:
        return
    if dry_run or solr is None:
        for row in ready_rows:
            report.bump(row.source, row.object_type, "indexed")
        return

    try:
        solr.add_docs(ready, commit_within_ms)
        report.solr_batches += 1
        for row in ready_rows:
            report.bump(row.source, row.object_type, "indexed")
        return
    except Exception as batch_ex:
        for row, doc in zip(ready_rows, ready):
            try:
                solr.add_docs([doc], commit_within_ms)
                report.bump(row.source, row.object_type, "indexed")
            except Exception as ex:
                report.add_error(
                    IssueRow(row.source, row.object_type, row.id, "SOLR_WRITE_FAILED", str(ex)[:500])
                )
        if not report.error_rows:
            report.add_error(
                IssueRow("ALL", 0, "", "SOLR_BATCH_FAILED", str(batch_ex)[:500])
            )


def _run_stream(
    pages: Iterable[List[DocRow]],
    label: str,
    report: BackfillReport,
    solr: Optional[SolrClient],
    commit_within_ms: int,
    dry_run: bool,
) -> None:
    total = 0
    for page in pages:
        total += len(page)
        _process_batch(page, report, solr, commit_within_ms, dry_run)
        print(
            f"  {label}: scanned={total} indexed={report.indexed} "
            f"skipped={report.skipped_missing_dept + report.skipped_empty_search_text} "
            f"errors={report.errors}",
            flush=True,
        )
    if total == 0:
        print(f"  {label}: scanned=0 (no rows from Oracle)", flush=True)


def _wanted(object_type_arg: str) -> List[int]:
    if object_type_arg == "ALL":
        return [OBJECT_TYPE_INCOMING, OBJECT_TYPE_OUTGOING]
    return [int(object_type_arg)]


def run_new(
    settings: SolrBackfillSettings,
    object_types: List[int],
    batch_size: int,
    limit: int,
    tenant_filter: str,
    report: BackfillReport,
    solr: Optional[SolrClient],
    dry_run: bool,
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
                    settings.commit_within_ms,
                    dry_run,
                )
            if OBJECT_TYPE_OUTGOING in object_types:
                _run_stream(
                    iter_new_outgoing(conn, target, batch_size, limit, tenant_filter),
                    f"NEW outgoing/{target.name}",
                    report,
                    solr,
                    settings.commit_within_ms,
                    dry_run,
                )
        finally:
            conn.close()


def run_legacy(
    settings: SolrBackfillSettings,
    object_types: List[int],
    batch_size: int,
    limit: int,
    report: BackfillReport,
    solr: Optional[SolrClient],
    dry_run: bool,
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
                settings.commit_within_ms,
                dry_run,
            )
        if OBJECT_TYPE_OUTGOING in object_types:
            _run_stream(
                iter_legacy_outgoing(conn, target, batch_size, limit),
                "LEGACY outgoing",
                report,
                solr,
                settings.commit_within_ms,
                dry_run,
            )
    finally:
        conn.close()


def _print_targets(settings: SolrBackfillSettings, source: str) -> None:
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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Solr document metadata (searchText) from NEW + LEGACY Oracle."
    )
    parser.add_argument("--source", default="ALL", choices=["ALL", "NEW", "LEGACY"])
    parser.add_argument("--object-type", default="ALL", choices=["ALL", "1", "2"], help="1=incoming, 2=outgoing")
    parser.add_argument("--dry-run", action="store_true", help="Scan Oracle and build searchText, do not write Solr")
    parser.add_argument("--limit", type=int, default=0, help="Max docs per source/object-type (0 = all)")
    parser.add_argument("--batch-size", type=int, default=0, help="Override SOLR_BACKFILL_BATCH_SIZE")
    parser.add_argument("--tenant-code", default="", help="Filter NEW docs by TENANT_CODE")
    parser.add_argument("--new-tenants-file", default="", help="JSON array of NEW Oracle connections")
    parser.add_argument("--solr-host", default="", help="Override SOLR_HOST")
    args = parser.parse_args(argv)

    require_legacy = args.source in {"ALL", "LEGACY"}
    require_new = args.source in {"ALL", "NEW"}
    settings = load_solr_backfill_settings(
        require_legacy=require_legacy,
        require_new=require_new,
        new_tenants_file=args.new_tenants_file,
    )
    if args.solr_host:
        settings = replace(settings, solr_host=args.solr_host.rstrip("/"))
    _print_targets(settings, args.source)
    batch_size = args.batch_size or settings.batch_size
    object_types = _wanted(args.object_type)
    started = time.monotonic()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = BackfillReport(
        started_at=datetime.now().isoformat(timespec="seconds"),
        dry_run=args.dry_run,
        source=args.source,
        object_type=args.object_type,
        solr_host=settings.solr_host,
        solr_core=settings.solr_core,
    )

    solr: Optional[SolrClient] = None
    try:
        if not args.dry_run:
            solr = SolrClient(
                settings.solr_host,
                settings.solr_core,
                settings.solr_user,
                settings.solr_password,
            )
            print(f"Pinging Solr {settings.solr_host}/{settings.solr_core} ...", flush=True)
            solr.ping()

        if args.source in {"ALL", "NEW"}:
            run_new(
                settings,
                object_types,
                batch_size,
                args.limit,
                args.tenant_code.strip(),
                report,
                solr,
                args.dry_run,
            )
        if args.source in {"ALL", "LEGACY"}:
            run_legacy(settings, object_types, batch_size, args.limit, report, solr, args.dry_run)

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
        print(
            f"Done. scanned={report.scanned} indexed={report.indexed} "
            f"skipped={report.skipped_missing_dept + report.skipped_empty_search_text} "
            f"errors={report.errors} duration={report.duration_seconds}s",
            flush=True,
        )
        print(f"Excel: {paths['xlsx'].resolve()}", flush=True)
        print(f"JSON:  {paths['json'].resolve()}", flush=True)
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
