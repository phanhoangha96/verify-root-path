#!/usr/bin/env python3
"""
CLI: export danh sách file không tìm thấy trên remote storage.

Chạy one-shot giống verify_root_path.py — không phải API, không gọi HTTP service.

Port logic từ:
  - migration-service VbAttachmentMissingExportWriter (Oracle → CSV)
  - storage-service MissingExportJobService (plocate scan → missing CSV/XLSX)

Usage:
  python export_missing.py
  python export_missing.py --format csv
  python export_missing.py --input-csv ./output/missing-export/input.csv
"""

from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from config import load_settings
from db import count_csv_data_rows, write_all_attachments_csv
from missing_report import choose_output_format, write_missing_excel
from missing_scan import scan_missing


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export missing VB_ATTACHMENT files from remote storage (plocate)."
    )
    parser.add_argument(
        "--input-csv",
        default="",
        help="Reuse existing path,fileName CSV (skip Oracle dump). "
        "Overrides MISSING_EXPORT_CSV_PATH when set.",
    )
    parser.add_argument(
        "--format",
        default="auto",
        choices=["auto", "csv", "xlsx", "excel"],
        help="Output format. Default auto: xlsx if missing <= 1_048_575 else csv.",
    )
    parser.add_argument(
        "--skip-scan",
        action="store_true",
        help="Only dump Oracle → input CSV (no plocate).",
    )
    args = parser.parse_args(argv)

    settings = load_settings(require_oracle=True, require_ssh=not args.skip_scan)
    work_dir = settings.missing_export_work_dir
    work_dir.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:16]
    job_dir = work_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    input_csv = _resolve_input_csv(settings, args.input_csv, job_dir)
    print(f"Job dir: {job_dir.resolve()}", flush=True)
    print(f"Input CSV: {input_csv.resolve()} (rows={count_csv_data_rows(input_csv)})", flush=True)

    if args.skip_scan:
        print("Skip scan (--skip-scan). Done.", flush=True)
        return 0

    missing_csv = job_dir / "missing.csv"
    stats = scan_missing(settings, input_csv, missing_csv)

    fmt = choose_output_format(stats.missing, "" if args.format == "auto" else args.format)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        out = settings.output_dir / f"missing-files_{stamp}.csv"
        shutil.copyfile(missing_csv, out)
    else:
        out = settings.output_dir / f"missing-files_{stamp}.xlsx"
        write_missing_excel(missing_csv, out)

    print(
        f"Done. checked={stats.checked} missing={stats.missing} report={out.resolve()}",
        flush=True,
    )
    print(f"Raw missing CSV kept at: {missing_csv.resolve()}", flush=True)
    return 0


def _resolve_input_csv(settings, cli_input: str, job_dir: Path) -> Path:
    """
    Resolve input CSV like migration-service resolveMissingExportCsvFile:
    - CLI --input-csv wins
    - else MISSING_EXPORT_CSV_PATH: reuse if exists, else dump there
    - else dump to job_dir/input.csv
    """
    if cli_input and cli_input.strip():
        path = Path(cli_input.strip())
        if not path.is_file():
            raise SystemExit(f"Input CSV not found: {path}")
        print(f"Reuse CLI input CSV: {path}", flush=True)
        return path

    configured = (settings.missing_export_csv_path or "").strip()
    if configured:
        path = Path(configured)
        if path.is_file() and path.stat().st_size > 0:
            print(f"Reuse existing missing-export CSV: {path}", flush=True)
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Dump Oracle attachments to configured CSV: {path}", flush=True)
        write_all_attachments_csv(settings, path)
        return path

    path = job_dir / "input.csv"
    print(f"Dump Oracle attachments to: {path}", flush=True)
    write_all_attachments_csv(settings, path)
    return path


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
