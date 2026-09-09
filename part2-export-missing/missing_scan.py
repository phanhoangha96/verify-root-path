"""Scan attachment CSV against remote storage via SSH plocate (storage-service MissingExportJobService)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from config import Settings
from db import csv_escape, iter_csv_path_filename
from mapping import normalize_relative_path
from ssh_plocate import PlocateClient


@dataclass
class ScanStats:
    checked: int = 0
    missing: int = 0


def scan_missing(
    settings: Settings,
    input_csv: Path,
    missing_csv: Path,
) -> ScanStats:
    """
    Read input CSV (path,fileName), plocate each relative path under REMOTE_STORAGE_ROOT_FOLDERS,
    write missing rows to missing_csv. Mirrors storage-service MissingExportJobService.runJob.
    """
    missing_csv.parent.mkdir(parents=True, exist_ok=True)
    stats = ScanStats()
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

    with missing_csv.open("w", encoding="utf-8", newline="") as out:
        out.write("path,fileName\n")
        client: Optional[PlocateClient] = None
        try:
            client = PlocateClient(settings)
            client.connect()
            print(
                f"Start missing scan: batch={batch_size}, "
                f"throttleEvery={throttle_every}, pauseMs={settings.missing_export_throttle_pause_ms}, "
                f"reconnectEvery={reconnect_every}, plocateDb={settings.plocate_db!r}",
                flush=True,
            )

            for path, file_name in iter_csv_path_filename(input_csv):
                batch.append((path, file_name))
                if len(batch) < batch_size:
                    continue

                client, since_reconnect = _maybe_reconnect(
                    client, settings, since_reconnect, reconnect_every, len(batch)
                )
                found_flags = _resolve_batch_with_retry(
                    client, batch, retry_attempts, retry_backoff
                )
                for (orig_path, orig_name), found in zip(batch, found_flags):
                    stats.checked += 1
                    since_throttle += 1
                    since_reconnect += 1
                    if not found:
                        out.write(f"{csv_escape(orig_path)},{csv_escape(orig_name)}\n")
                        stats.missing += 1
                        if stats.missing % 1000 == 0:
                            out.flush()
                    if per_check_delay:
                        time.sleep(per_check_delay)

                if throttle_every and since_throttle >= throttle_every:
                    if throttle_pause:
                        time.sleep(throttle_pause)
                    since_throttle = 0

                if stats.checked % 10000 == 0:
                    out.flush()
                    print(
                        f"  missing scan progress: checked={stats.checked} missing={stats.missing}",
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
                for (orig_path, orig_name), found in zip(batch, found_flags):
                    stats.checked += 1
                    if not found:
                        out.write(f"{csv_escape(orig_path)},{csv_escape(orig_name)}\n")
                        stats.missing += 1
                    if per_check_delay:
                        time.sleep(per_check_delay)

            out.flush()
        finally:
            if client is not None:
                client.close()

    print(
        f"Missing scan completed: checked={stats.checked} missing={stats.missing} "
        f"file={missing_csv}",
        flush=True,
    )
    return stats


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
            results = client.resolve_paths(relatives)
            return [bool(path) for path in results]
        except Exception as exc:  # noqa: BLE001 — retry SSH/plocate transient failures
            last_error = exc
            print(
                f"  plocate batch failed (attempt {attempt}/{retry_attempts}): {exc}",
                flush=True,
            )
            if attempt < retry_attempts:
                client.close()
                client.connect()
                if retry_backoff:
                    time.sleep(retry_backoff * attempt)
    assert last_error is not None
    raise last_error
