# root-path-audit

CLI Python gom 2 công cụ audit file attachment (không cần chạy `migration-service` / `storage-service`):

| Phần | Entry point | Việc làm |
|------|-------------|----------|
| **1. Root-path audit** | `python verify_root_path.py` | Sample `VB_ATTACHMENT.ROOT_PATH` ↔ folder thật trên server (plocate) → Excel |
| **2. Export missing files** | `python export_missing.py` | Dump toàn bộ attachment → quét plocate → xuất file **không tìm thấy** (CSV/XLSX) |

Cả hai đều là **CLI one-shot** (giống nhau về cách chạy). Không có HTTP API, không gọi `migration-service` / `storage-service`.

Nguồn gốc Part 2 (đã gỡ endpoint cũ):

- ~~`migration-service` `GET /vbAttachmentTdhvp/exportMissingFiles`~~ → `python export_missing.py`
- ~~`storage-service` `/api/files/missing/*`~~ → cùng CLI trên

`storage-service` giờ chỉ còn `ready` / `exists` / `download` cho migration download file.

---

## Setup chung (một lần)

### macOS / Linux

```bash
cd /Users/phanhoangha/IdeaProjects/Sotatek/root-path-audit
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# điền ORACLE_PASSWORD và REMOTE_STORAGE_PASSWORD
```

### Windows (PowerShell)

```powershell
cd C:\path\to\root-path-audit
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Nếu bị chặn script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## Part 1 — Root-path audit

Kiểm tra `LEGACY.VB_ATTACHMENT.ROOT_PATH` khớp đường dẫn thật trên file server thế nào.

### Luồng

1. Đọc sample attachment từ Oracle (`ID`, `FILE_NAME`, `FILE_PATH`, `ROOT_PATH`)
2. Gọi SSH + `plocate` để tìm absolute path trên server
3. So với mapping kỳ vọng (`dir_upload_path*`)
4. Xuất Excel tổng hợp

### Chạy

```bash
source .venv/bin/activate
python verify_root_path.py
```

Report mặc định: `output/root_path_audit_YYYYMMDD_HHMMSS.xlsx`

### Excel sheets

| Sheet | Nội dung |
|-------|----------|
| `Overview` | Tổng số liệu theo STATUS |
| `Summary_by_ROOT_PATH` | Mỗi `ROOT_PATH` → folder kỳ vọng, số found/matched/mismatch |
| `Detail` | Từng file sample |
| `No_ROOT_PATH` | File không có / null `ROOT_PATH` |
| `Mismatch` | File tìm thấy nhưng nằm ngoài folder kỳ vọng |
| `Expected_Mapping` | Mapping hardcode tham chiếu |

### Mapping kỳ vọng

| ROOT_PATH | Folders |
|-----------|---------|
| `dir_upload_path1` | `/data/u02/data1/voffice/Upload`, `/data/u03/data1/voffice/Upload` |
| `dir_upload_path2` | `/data/u02/data1/voffice/Upload` |
| `dir_upload_path4` | `/data/u02/data1/voffice/Upload`, `/data/u04/data1/voffice/Upload` |
| `dir_upload_path5` | `/data/u03/data1/voffice/Upload` |
| `dir_upload_path_Current` | `/data/u05/data1/voffice/Upload` |

### Config Part 1 (`.env`)

| Key | Ý nghĩa | Default |
|-----|---------|---------|
| `SAMPLE_PER_ROOT_PATH` | Sample tối đa mỗi giá trị `ROOT_PATH` (kể cả NULL). `0` = all | `200` |
| `SAMPLE_LIMIT` | Giới hạn tổng số row sau sample | `0` |
| `PLOCATE_BATCH_SIZE` | Số path / 1 lần SSH exec | `50` |
| `REQUIRE_HAS_FILE` | Chỉ lấy `IS_HAS_FILE=1` | `true` |
| `EXCLUDE_DELETED` | Bỏ `IS_DELETE!=0` | `true` |

### STATUS

- `MATCHED` — tìm thấy trong folder kỳ vọng của `ROOT_PATH`
- `MISMATCH` — tìm thấy nhưng folder khác kỳ vọng
- `NOT_FOUND` — plocate không thấy
- `NO_ROOT_PATH_BUT_FOUND` / `NO_ROOT_PATH_NOT_FOUND` — thiếu `ROOT_PATH`
- `UNKNOWN_ROOT_PATH_*` — `ROOT_PATH` không nằm trong mapping

---

## Part 2 — Export missing files

CLI one-shot (cùng kiểu với `verify_root_path.py`). Thay cho luồng HTTP cũ
`migration-service` → `storage-service`.

### Luồng (Oracle + plocate trên máy local)

```
export_missing.py (local)                         Server file (SSH)
       |                                                |
  1. Oracle: SELECT FILE_PATH, FILE_NAME                |
     → input.csv (path,fileName)                        |
       |                                                |
  2. Với mỗi dòng CSV (batch plocate):                  |
     normalize path → plocate ─────────────────────────>|
     khớp absolute path với REMOTE_STORAGE_ROOT_FOLDERS |
     không thấy → ghi missing.csv (local only)          |
       |                                                |
  3. Xuất missing-files_*.xlsx hoặc *.csv               |
```

### Chạy

```bash
source .venv/bin/activate

# Full job: dump Oracle + scan plocate + report
python export_missing.py

# Chỉ dump CSV từ Oracle (không SSH)
python export_missing.py --skip-scan

# Reuse CSV đã dump sẵn
python export_missing.py --input-csv ./output/missing-export/<jobId>/input.csv

# Ép format
python export_missing.py --format csv
python export_missing.py --format xlsx
```

Report mặc định:

- `output/missing-files_YYYYMMDD_HHMMSS.xlsx` nếu số missing ≤ 1_048_575
- ngược lại → `.csv`
- Raw `missing.csv` giữ trong `MISSING_EXPORT_WORK_DIR/<jobId>/`

### Config Part 2 (`.env`)

| Key | Ý nghĩa | Default |
|-----|---------|---------|
| `MISSING_EXPORT_CSV_PATH` | CSV cố định; nếu đã có file → reuse, bỏ dump Oracle | *(rỗng)* |
| `MISSING_EXPORT_WORK_DIR` | Thư mục job (input/missing) | `./output/missing-export` |
| `MISSING_EXPORT_LIMIT` | Giới hạn số row dump Oracle (`0` = all) | `0` |
| `MISSING_EXPORT_BATCH_SIZE` | Số path / 1 SSH exec | `100` |
| `MISSING_EXPORT_THROTTLE_EVERY` | Pause sau mỗi N check | `500` |
| `MISSING_EXPORT_THROTTLE_PAUSE_MS` | Thời gian pause (ms) | `200` |
| `MISSING_EXPORT_RECONNECT_EVERY` | Reconnect SSH sau N check | `50000` |
| `MISSING_EXPORT_SEARCH_BY_FILE_NAME` | `plocate -b` basename (giữ `false` cho job lớn) | `false` |

### Lưu ý vận hành

- Job **read-only** trên server file: chỉ `plocate`, không `stat`/`get`/`put`/xóa.
- Cần `plocate` + DB index (`REMOTE_STORAGE_PLOCATE_DB`, thường `/var/lib/plocate/voffice.db`).
- Job lớn có thể chạy nhiều giờ — xem checklist DevOps trong `docs/`.

---

## Config SSH / Oracle chung

| Key | Ý nghĩa |
|-----|---------|
| `ORACLE_*` | Kết nối `LEGACY.VB_ATTACHMENT` |
| `REMOTE_STORAGE_HOST` / `PORT` / `USERNAME` / `PASSWORD` | SSH tới file server |
| `REMOTE_STORAGE_PLOCATE_DB` | DB plocate trên server |
| `REMOTE_STORAGE_ROOT_FOLDERS` | Các root Upload được chấp nhận khi match |
| `OUTPUT_DIR` | Thư mục report |

---

## Cấu trúc code

| File | Vai trò |
|------|---------|
| `verify_root_path.py` | Part 1 CLI |
| `export_missing.py` | Part 2 CLI |
| `db.py` | Oracle sample + dump CSV toàn bộ attachment |
| `ssh_plocate.py` | SSH batch `plocate` |
| `missing_scan.py` | Quét missing (logic storage-service job) |
| `missing_report.py` | Xuất CSV/XLSX missing |
| `excel_report.py` | Excel Part 1 |
| `mapping.py` | Normalize path + mapping `ROOT_PATH` |
| `docs/` | Tài liệu DevOps / luồng missing-export |
