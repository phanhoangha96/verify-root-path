"""Write missing-files report (CSV always; XLSX when row count fits Excel limit)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from openpyxl import Workbook

from db import _split_csv_line

EXCEL_MAX_DATA_ROWS = 1_048_575


def write_missing_excel(missing_csv: Path, output_xlsx: Path) -> Path:
    """Match storage-service MissingExportJobService / MissingFileExcelService columns."""
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook(write_only=True)
    sheet = wb.create_sheet("Missing files")
    sheet.append(["STT", "Path", "File name"])

    with missing_csv.open("r", encoding="utf-8") as reader:
        header = reader.readline()
        if not header:
            wb.save(output_xlsx)
            return output_xlsx
        row_index = 1
        for line in reader:
            if not line.strip():
                continue
            parts = _split_csv_line(line.rstrip("\n\r"))
            path = parts[0] if parts else ""
            name = parts[1] if len(parts) > 1 else ""
            sheet.append([row_index, path, name])
            row_index += 1
            if row_index - 1 > EXCEL_MAX_DATA_ROWS:
                wb.close()
                raise SystemExit(
                    f"Too many missing rows for Excel ({row_index - 1}). "
                    f"Excel max is {EXCEL_MAX_DATA_ROWS}. Use CSV output instead."
                )

    wb.save(output_xlsx)
    return output_xlsx


def choose_output_format(missing_count: int, forced: str = "") -> str:
    """Return 'xlsx' or 'csv'. Auto: xlsx if missing fits Excel sheet, else csv."""
    forced = (forced or "").strip().lower()
    if forced in {"csv", "xlsx", "excel"}:
        return "csv" if forced == "csv" else "xlsx"
    return "csv" if missing_count > EXCEL_MAX_DATA_ROWS else "xlsx"


def read_missing_preview(missing_csv: Path, limit: int = 5) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    with missing_csv.open("r", encoding="utf-8") as reader:
        reader.readline()
        for line in reader:
            if not line.strip():
                continue
            parts = _split_csv_line(line.rstrip("\n\r"))
            rows.append((parts[0] if parts else "", parts[1] if len(parts) > 1 else ""))
            if len(rows) >= limit:
                break
    return rows
