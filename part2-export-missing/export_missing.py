from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

_PART_DIR = Path(__file__).resolve().parent
if str(_PART_DIR) not in sys.path:
    sys.path.insert(0, str(_PART_DIR))

from config import load_settings
from db import count_csv_data_rows, write_all_attachments_csv
from missing_report import (
    ReportSummary,
    choose_output_format,
    count_unique_path_filename,
    write_missing_csv_unique,
    write_missing_excel,
)
from missing_scan import ScanStats, load_checkpoint, scan_missing


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
        "--resume",
        default="",
        help="Resume an interrupted job dir (reads scan_checkpoint.json, appends missing.csv).",
    )
    parser.add_argument(
        "--skip-checked",
        type=int,
        default=-1,
        help="Override resume skip count (rows already checked). "
        "Use when checkpoint is missing: --resume <jobDir> --skip-checked 930000",
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
    parser.add_argument(
        "--export-only",
        default="",
        help="Only rebuild report from an existing job dir missing.csv (no Oracle/SSH scan).",
    )
    args = parser.parse_args(argv)

    resume_dir = (args.resume or "").strip()
    export_only_dir = (args.export_only or "").strip()
    if resume_dir and args.skip_scan:
        raise SystemExit("Cannot combine --resume with --skip-scan")
    if export_only_dir and (resume_dir or args.skip_scan):
        raise SystemExit("Cannot combine --export-only with --resume/--skip-scan")

    if export_only_dir:
        return _export_only_report(
            Path(export_only_dir),
            format_arg="" if args.format == "auto" else args.format,
        )

    reuse_csv = _will_reuse_input_csv(args.input_csv) or bool(resume_dir)
    settings = load_settings(
        require_oracle=not reuse_csv,
        require_ssh=not args.skip_scan,
    )
    work_dir = settings.missing_export_work_dir
    work_dir.mkdir(parents=True, exist_ok=True)

    if resume_dir:
        job_dir, input_csv, skip_checked = _resolve_resume(
            Path(resume_dir),
            cli_input=args.input_csv,
            skip_checked_override=args.skip_checked,
        )
    else:
        job_id = uuid.uuid4().hex[:16]
        job_dir = work_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        input_csv = _resolve_input_csv(settings, args.input_csv, job_dir)
        skip_checked = max(0, args.skip_checked) if args.skip_checked >= 0 else 0

    print(f"Job dir: {job_dir.resolve()}", flush=True)
    print(
        f"Input CSV: {input_csv.resolve()} (rows={count_csv_data_rows(input_csv)})",
        flush=True,
    )
    if skip_checked:
        print(f"Resume skip_checked={skip_checked}", flush=True)
    print(
        f"Reuse next run (skip Oracle): python export_missing.py --input-csv {input_csv}",
        flush=True,
    )
    print(
        f"Resume if interrupted: python export_missing.py --resume {job_dir}",
        flush=True,
    )

    if args.skip_scan:
        print("Skip scan (--skip-scan). Done.", flush=True)
        return 0

    missing_csv = job_dir / "missing.csv"
    started_at = datetime.now()
    stats = scan_missing(
        settings,
        input_csv,
        missing_csv,
        skip_checked=skip_checked,
    )
    finished_at = datetime.now()

    unique_missing, _ = count_unique_path_filename(missing_csv)
    summary = _build_summary(
        stats=stats,
        job_dir=job_dir,
        input_csv=input_csv,
        missing_csv=missing_csv,
        started_at=started_at,
        finished_at=finished_at,
        unique_missing=unique_missing,
    )

    out = _write_report(
        missing_csv,
        missing_count=unique_missing,
        format_arg="" if args.format == "auto" else args.format,
        output_dir=settings.output_dir,
        summary=summary,
    )
    print(
        f"Done. checked={stats.checked} unique_files={stats.unique_files} "
        f"missing={unique_missing} report={out.resolve()}",
        flush=True,
    )
    print(f"Raw missing CSV kept at: {missing_csv.resolve()}", flush=True)
    return 0


def _export_only_report(job_dir: Path, *, format_arg: str) -> int:
    """Rebuild CSV/XLSX report from an already-finished job (no rescan)."""
    job_dir = job_dir.resolve()
    missing_csv = job_dir / "missing.csv"
    if not missing_csv.is_file():
        raise SystemExit(f"missing.csv not found in job dir: {job_dir}")

    settings = load_settings(require_oracle=False, require_ssh=False)
    unique_missing, raw_missing = count_unique_path_filename(missing_csv)
    checkpoint = load_checkpoint(job_dir)
    input_csv = job_dir / "input.csv"
    if checkpoint is not None and checkpoint.input_csv:
        candidate = Path(checkpoint.input_csv)
        if candidate.is_file():
            input_csv = candidate

    total_input = count_csv_data_rows(input_csv) if input_csv.is_file() else 0
    unique_input = 0
    if input_csv.is_file():
        unique_input, _ = count_unique_path_filename(input_csv)

    checked = checkpoint.checked if checkpoint is not None else total_input
    unique_files = unique_input or checked
    summary = ReportSummary(
        total_input_rows=total_input,
        unique_files=unique_files,
        missing_files=unique_missing,
        found_files=max(0, unique_files - unique_missing) if unique_files else 0,
        duplicate_input_skipped=max(0, total_input - unique_input) if total_input else 0,
        job_dir=str(job_dir),
        input_csv=str(input_csv) if input_csv.is_file() else "",
        missing_csv=str(missing_csv),
        extra={
            "Export mode": "export-only (no rescan)",
            "Raw missing.csv rows": str(raw_missing),
        },
    )

    print(
        f"Export-only from {job_dir} "
        f"(raw missing={raw_missing}, unique missing={unique_missing})",
        flush=True,
    )
    out = _write_report(
        missing_csv,
        missing_count=unique_missing,
        format_arg=format_arg,
        output_dir=settings.output_dir,
        summary=summary,
    )
    print(f"Done. missing={unique_missing} report={out.resolve()}", flush=True)
    print(f"Raw missing CSV: {missing_csv.resolve()}", flush=True)
    return 0


def _build_summary(
    *,
    stats: ScanStats,
    job_dir: Path,
    input_csv: Path,
    missing_csv: Path,
    started_at: datetime,
    finished_at: datetime,
    unique_missing: int,
) -> ReportSummary:
    unique_files = stats.unique_files or max(0, stats.checked - stats.duplicate_input_skipped)
    rate = 0.0
    if stats.elapsed_seconds > 0 and stats.checked > 0:
        rate = stats.checked / stats.elapsed_seconds
    return ReportSummary(
        total_input_rows=stats.input_rows or stats.checked,
        unique_files=unique_files,
        missing_files=unique_missing,
        found_files=max(0, unique_files - unique_missing),
        duplicate_input_skipped=stats.duplicate_input_skipped,
        elapsed_seconds=stats.elapsed_seconds,
        check_rate_per_sec=rate,
        started_at=started_at.strftime("%Y-%m-%d %H:%M:%S"),
        finished_at=finished_at.strftime("%Y-%m-%d %H:%M:%S"),
        job_dir=str(job_dir.resolve()),
        input_csv=str(input_csv.resolve()),
        missing_csv=str(missing_csv.resolve()),
    )


def _write_report(
    missing_csv: Path,
    *,
    missing_count: int,
    format_arg: str,
    output_dir: Path,
    summary: Optional[ReportSummary] = None,
) -> Path:
    fmt = choose_output_format(missing_count, format_arg)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        out = output_dir / f"missing-files_{stamp}.csv"
        write_missing_csv_unique(missing_csv, out, summary=summary)
        return out

    out = output_dir / f"missing-files_{stamp}.xlsx"
    try:
        write_missing_excel(missing_csv, out, summary=summary)
        return out
    except Exception as exc:  # noqa: BLE001 — fall back so scan result is never lost
        print(f"Excel export failed ({exc}); falling back to CSV.", flush=True)
        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass
        csv_out = output_dir / f"missing-files_{stamp}.csv"
        write_missing_csv_unique(missing_csv, csv_out, summary=summary)
        return csv_out


def _resolve_resume(
    job_dir: Path,
    *,
    cli_input: str,
    skip_checked_override: int,
) -> Tuple[Path, Path, int]:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise SystemExit(f"Resume job dir not found: {job_dir}")

    checkpoint = load_checkpoint(job_dir)
    if checkpoint is not None and checkpoint.status == "completed":
        raise SystemExit(
            f"Job already completed (checked={checkpoint.checked}, "
            f"missing={checkpoint.missing}): {job_dir}"
        )

    if skip_checked_override >= 0:
        skip_checked = skip_checked_override
    elif checkpoint is not None:
        skip_checked = checkpoint.checked
    else:
        raise SystemExit(
            f"No {job_dir / 'scan_checkpoint.json'} found. "
            "Pass --skip-checked N from the last progress line "
            "(e.g. checked=930000 → --skip-checked 930000)."
        )

    if cli_input and cli_input.strip():
        input_csv = Path(cli_input.strip())
    elif checkpoint is not None and checkpoint.input_csv:
        input_csv = Path(checkpoint.input_csv)
    else:
        sibling = job_dir / "input.csv"
        if sibling.is_file():
            input_csv = sibling
        else:
            raise SystemExit(
                "Cannot resolve input CSV for resume. Pass --input-csv <path>."
            )

    if not input_csv.is_file():
        raise SystemExit(f"Input CSV not found for resume: {input_csv}")

    missing_csv = job_dir / "missing.csv"
    if skip_checked > 0 and not missing_csv.is_file():
        print(
            f"Warning: missing.csv not found in {job_dir}; "
            "will create new file (previous missing rows lost).",
            flush=True,
        )

    print(
        f"Resume job dir: {job_dir} (checkpoint checked={skip_checked}"
        + (f", status={checkpoint.status}" if checkpoint else ", no checkpoint file")
        + ")",
        flush=True,
    )
    return job_dir, input_csv, skip_checked


def _will_reuse_input_csv(cli_input: str) -> bool:
    """True when Oracle dump will be skipped (CLI path or existing MISSING_EXPORT_CSV_PATH)."""
    if cli_input and cli_input.strip():
        return Path(cli_input.strip()).is_file()
    configured = (os.getenv("MISSING_EXPORT_CSV_PATH") or "").strip()
    if not configured:
        return False
    path = Path(configured)
    return path.is_file() and path.stat().st_size > 0


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
