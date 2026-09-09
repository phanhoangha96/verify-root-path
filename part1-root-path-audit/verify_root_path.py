#!/usr/bin/env python3
"""CLI: audit VB_ATTACHMENT.ROOT_PATH against real server paths via SSH plocate."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_PART_DIR = Path(__file__).resolve().parent
if str(_PART_DIR) not in sys.path:
    sys.path.insert(0, str(_PART_DIR))

from config import load_settings
from db import fetch_attachments
from excel_report import AuditResult, write_excel
from mapping import (
    classify_result,
    detect_server_root,
    expected_folders_for,
    normalize_relative_path,
)
from ssh_plocate import PlocateClient, resolve_many


def main() -> int:
    settings = load_settings()
    print("Loading attachments from Oracle...", flush=True)
    rows = fetch_attachments(settings)
    print(f"Loaded {len(rows)} attachments", flush=True)
    if not rows:
        print("No rows to audit.")
        return 0

    relatives = [normalize_relative_path(r.file_path) for r in rows]
    print(
        f"Resolving via plocate on {settings.ssh_host}:{settings.ssh_port} "
        f"(batch={settings.plocate_batch_size})...",
        flush=True,
    )
    with PlocateClient(settings) as client:
        found_map = resolve_many(client, relatives, settings.plocate_batch_size)

    results = []
    for idx, row in enumerate(rows):
        found = found_map.get(idx)
        matched_root = detect_server_root(found, settings.root_folders) if found else ""
        status, note = classify_result(row.root_path, found, matched_root)
        expected = expected_folders_for(row.root_path)
        results.append(
            AuditResult(
                attachment_id=row.id,
                file_name=row.file_name,
                file_path=row.file_path,
                root_path=row.root_path,
                expected_folders=", ".join(expected),
                found_server_path=found,
                matched_server_root=matched_root,
                status=status,
                note=note,
            )
        )

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    if settings.output_file:
        out = Path(settings.output_file)
        if not out.is_absolute():
            out = settings.output_dir / out
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = settings.output_dir / f"root_path_audit_{stamp}.xlsx"

    write_excel(results, out)
    print(f"Excel report written: {out.resolve()}", flush=True)

    no_root = sum(1 for r in results if not r.root_path)
    matched = sum(1 for r in results if r.status == "MATCHED")
    mismatch = sum(1 for r in results if r.status == "MISMATCH")
    found = sum(1 for r in results if r.found_server_path)
    print(
        f"Done. total={len(results)} found={found} matched={matched} "
        f"mismatch={mismatch} no_root_path={no_root}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
