"""Scan attachment CSV against remote storage via SSH plocate (storage-service MissingExportJobService)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from config import Settings
from db import csv_escape, iter_csv_path_filename
from mapping import normalize_relative_path
from ssh_plocate import PlocateClient

CHECKPOINT_NAME = "scan_checkpoint.json"


@dataclass
class ScanStats:
    checked: int = 0
    missing: int = 0


@dataclass
class ScanCheckpoint:
    checked: int = 0
    missing: int = 0
    input_csv: str = ""
    status: str = "running"


def checkpoint_path(job_dir: Path) -> Path:
    return job_dir / CHECKPOINT_NAME


def load_checkpoint(job_dir: Path) -> Optional[ScanCheckpoint]:
    path = checkpoint_path(job_dir)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ScanCheckpoint(
        checked=int(raw.get("checked") or 0),
        missing=int(raw.get("missing") or 0),
        input_csv=str(raw.get("input_csv") or ""),
        status=str(raw.get("status") or "running"),
    )


def save_checkpoint(
    job_dir: Path,
    *,
    checked: int,
    missing: int,
    input_csv: Path,
    status: str = "running",
) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "checked": checked,
        "missing": missing,
        "input_csv": str(input_csv.resolve()),
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = checkpoint_path(job_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def scan_missing(
    settings: Settings,
    input_csv: Path,
    missing_csv: Path,
    *,
    skip_checked: int = 0,
) -> ScanStats:
    """
    Read input CSV (path,fileName), plocate each relative path under REMOTE_STORAGE_ROOT_FOLDERS,
    write missing rows to missing_csv. Mirrors storage-service MissingExportJobService.runJob.

    skip_checked: number of input rows already processed (resume). Appends to missing_csv when > 0.
    """
    missing_csv.parent.mkdir(parents=True, exist_ok=True)
    job_dir = missing_csv.parent
    skip_checked = max(0, skip_checked)
    resume = skip_checked > 0

    stats = ScanStats(checked=skip_checked, missing=0)
    if resume and missing_csv.is_file():
        # Preserve previously written missing rows; recount from file for checkpoint accuracy.
        stats.missing = _count_missing_data_rows(missing_csv)

    batch_size = max(1, settings.missing_export_batch_size)
    throttle_every = max(0, settings.missing_export_throttle_every)
    throttle_pause = max(0, settings.missing_export_throttle_pause_ms) / 1000.0
    per_check_delay = max(0, settings.missing_export_per_check_delay_ms) / 1000.0
    reconnect_every = max(0, settings.missing_export_reconnect_every)
    retry_attempts = max(1, settings.missing_export_retry_attempts)
    retry_backoff = max(0, settings.missing_export_retry_backoff_ms) / 1000.0

    batch: List[Tuple[str, str]] = []
    since_throttle = 0
    since_reconnect = 0
    scan_started = time.monotonic()
    skip_remaining = skip_checked
    checked_at_start = skip_checked

    open_mode = "a" if resume and missing_csv.is_file() else "w"
    with missing_csv.open(open_mode, encoding="utf-8", newline="") as out:
        if open_mode == "w":
            out.write("path,fileName\n")
        client: Optional[PlocateClient] = None
        try:
            client = PlocateClient(settings)
            client.connect()
            print(
                f"Start missing scan: batch={batch_size}, "
                f"throttleEvery={throttle_every}, pauseMs={settings.missing_export_throttle_pause_ms}, "
                f"reconnectEvery={reconnect_every}, plocateDb={settings.plocate_db!r}, "
                f"skipChecked={skip_checked}",
                flush=True,
            )
            if resume:
                print(
                    f"  Resume: skipping first {skip_checked} rows, "
                    f"existing missing={stats.missing}, append={missing_csv}",
                    flush=True,
                )

            save_checkpoint(
                job_dir,
                checked=stats.checked,
                missing=stats.missing,
                input_csv=input_csv,
                status="running",
            )

            for path, file_name in iter_csv_path_filename(input_csv):
                if skip_remaining > 0:
                    skip_remaining -= 1
                    continue

                batch.append((path, file_name))
                if len(batch) < batch_size:
                    continue

                client, since_reconnect = _maybe_reconnect(
                    client, settings, since_reconnect, reconnect_every, len(batch)
                )
                found_flags = _resolve_batch_with_retry(
                    client, batch, retry_attempts, retry_backoff
                )
                _apply_batch_results(out, batch, found_flags, stats, per_check_delay)
                since_throttle += len(batch)
                since_reconnect += len(batch)
                save_checkpoint(
                    job_dir,
                    checked=stats.checked,
                    missing=stats.missing,
                    input_csv=input_csv,
                    status="running",
                )

                if throttle_every and since_throttle >= throttle_every:
                    if throttle_pause:
                        time.sleep(throttle_pause)
                    since_throttle = 0

                if stats.checked % 10000 == 0:
                    out.flush()
                    print(
                        f"  missing scan progress: checked={stats.checked} "
                        f"missing={stats.missing} "
                        f"{_format_scan_timing(scan_started, stats.checked - checked_at_start)}",
                        flush=True,
                    )
                batch = []

            if batch:
                client, since_reconnect = _maybe_reconnect(
                    client, settings, since_reconnect, reconnect_every, len(batch)
                )
                found_flags = _resolve_batch_with_retry(
                    client, batch, retry_attempts, retry_backoff
                )
                _apply_batch_results(out, batch, found_flags, stats, per_check_delay)
                save_checkpoint(
                    job_dir,
                    checked=stats.checked,
                    missing=stats.missing,
                    input_csv=input_csv,
                    status="running",
                )

            out.flush()
            save_checkpoint(
                job_dir,
                checked=stats.checked,
                missing=stats.missing,
                input_csv=input_csv,
                status="completed",
            )
        finally:
            if client is not None:
                client.close()

    print(
        f"Missing scan completed: checked={stats.checked} missing={stats.missing} "
        f"{_format_scan_timing(scan_started, max(0, stats.checked - checked_at_start))} "
        f"file={missing_csv}",
        flush=True,
    )
    return stats


def _count_missing_data_rows(missing_csv: Path) -> int:
    rows = 0
    with missing_csv.open("r", encoding="utf-8") as reader:
        header = reader.readline()
        if not header:
            return 0
        for line in reader:
            if line.strip():
                rows += 1
    return rows


def _apply_batch_results(
    out,
    batch: List[Tuple[str, str]],
    found_flags: List[bool],
    stats: ScanStats,
    per_check_delay: float,
) -> None:
    for (orig_path, orig_name), found in zip(batch, found_flags):
        stats.checked += 1
        if not found:
            out.write(f"{csv_escape(orig_path)},{csv_escape(orig_name)}\n")
            stats.missing += 1
            if stats.missing % 1000 == 0:
                out.flush()
        if per_check_delay:
            time.sleep(per_check_delay)


def _format_scan_timing(started: float, checked_this_run: int) -> str:
    elapsed = max(0.0, time.monotonic() - started)
    rate = (checked_this_run / elapsed) if elapsed > 0 and checked_this_run > 0 else 0.0
    return f"elapsed={_format_duration(elapsed)} rate={rate:.1f}/s"


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _maybe_reconnect(
    client: PlocateClient,
    settings: Settings,
    since_reconnect: int,
    reconnect_every: int,
    upcoming_batch: int,
) -> Tuple[PlocateClient, int]:
    if reconnect_every <= 0 or since_reconnect + upcoming_batch < reconnect_every:
        return client, since_reconnect
    print(f"  Reconnecting SSH after ~{since_reconnect} checks...", flush=True)
    client.close()
    fresh = PlocateClient(settings)
    fresh.connect()
    return fresh, 0


def _resolve_batch_with_retry(
    client: PlocateClient,
    batch: List[Tuple[str, str]],
    retry_attempts: int,
    retry_backoff: float,
) -> List[bool]:
    relatives = [normalize_relative_path(path) for path, _ in batch]
    last_error: Optional[Exception] = None
    for attempt in range(1, retry_attempts + 1):
        try:
            if not client.connected:
                client.connect()
            results = client.resolve_paths(relatives)
            return [bool(path) for path in results]
        except Exception as exc:  # noqa: BLE001 — retry SSH/plocate transient failures
            last_error = exc
            print(
                f"  plocate batch failed (attempt {attempt}/{retry_attempts}): {exc}",
                flush=True,
            )
            if attempt >= retry_attempts:
                break
            wait = retry_backoff * attempt
            if wait:
                print(f"  waiting {wait:.1f}s before SSH reconnect...", flush=True)
                time.sleep(wait)
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                client.connect()
            except Exception as reconnect_exc:  # noqa: BLE001
                last_error = reconnect_exc
                print(
                    f"  SSH reconnect failed (attempt {attempt}/{retry_attempts}): "
                    f"{reconnect_exc}",
                    flush=True,
                )
                # Leave disconnected; next loop iteration will wait then connect again.
    assert last_error is not None
    raise last_error
