# root-path-audit

CLI Python gom công cụ one-shot (không cần chạy `eoffice-business` / `migration-service` / `storage-service`):

| Phần | Entry point | Việc làm |
|------|-------------|----------|
| **1. Root-path audit** | `python verify_root_path.py` | Sample `VB_ATTACHMENT.ROOT_PATH` ↔ folder thật trên server (plocate) → Excel |
| **2. Export missing files** | `python export_missing.py` | Dump toàn bộ attachment → quét plocate → xuất file **không tìm thấy** (CSV/XLSX) |
| **3. Solr metadata backfill** | `python solr_doc_meta_backfill.py` | Đọc Oracle NEW + LEGACY → ghi Solr `searchText` → Excel/JSON report |

Cả ba đều là **CLI one-shot**. Không có HTTP API, không gọi `eoffice-business` / `migration-service`.

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

## Part 3 — Solr metadata backfill

Index metadata (`searchText`) cho văn bản **đã có** trong DB. Không phải API trong `eoffice-business`.

NEW và LEGACY nằm trên **hai Oracle khác nhau**. Script mở **hai connection** riêng:

| Nguồn | Env | DB |
|-------|-----|-----|
| **LEGACY** | `ORACLE_*` (hoặc `LEGACY_ORACLE_*`) | DB migration, schema `LEGACY` — cùng chỗ Part 1/2 |
| **NEW** | `NEW_ORACLE_*` | DB eoffice tenant — **không** dùng `ORACLE_DSN` |

`--source ALL` (mặc định) yêu cầu **cả hai** khối đã điền password + DSN. Chỉ một nguồn thì `--source LEGACY` hoặc `--source NEW`.

Nếu `.env` cũ chỉ có `ORACLE_*`, copy thêm `NEW_ORACLE_*` từ `.env.example` rồi điền DSN máy eoffice.

`eoffice-business` chỉ index **realtime** khi tạo/sửa văn bản, comment, process. Script này backfill dữ liệu cũ (NEW tenant schema + LEGACY).

Chuỗi `searchText` giống Java `DocMetaSolrServiceImpl`: bỏ dấu tiếng Việt, bỏ khoảng trắng / ký tự Solr đặc biệt, uppercase. Incoming gồm `docCode`, `quote`, `publisherName`, `outsidePublisherName`, `bookNumber`, `note`, comment, process note. Outgoing gồm `docCode`, `quote`, `publisherName`, `subBookNumber`, `bookNumber`, `outgoingNumber`, `note`, comment, process note.

Solr doc id: `{objectId}_{deptId}_meta`, `indexType=META`.

### Chạy

```bash
source .venv/bin/activate

# NEW + LEGACY, incoming + outgoing
python solr_doc_meta_backfill.py

# Chỉ LEGACY, văn bản đến
python solr_doc_meta_backfill.py --source LEGACY --object-type 1

# Thử NEW, không ghi Solr
python solr_doc_meta_backfill.py --source NEW --dry-run --limit 50

# Nhiều tenant NEW
python solr_doc_meta_backfill.py --source NEW --new-tenants-file ./new_oracle_tenants.json
```

Report mặc định:

- `output/solr_doc_meta_backfill_YYYYMMDD_HHMMSS.xlsx`
- `output/solr_doc_meta_backfill_YYYYMMDD_HHMMSS.json`

`--dry-run`: cột `Indexed` = số doc **sẽ** ghi Solr.

### Excel sheets

| Sheet | Nội dung |
|-------|----------|
| `Overview` | Tổng scanned / indexed / skipped / errors, thời gian, Solr host |
| `By_source` | NEW vs LEGACY × incoming (1) / outgoing (2) |
| `Skipped` | Thiếu `toDeptId`/`publisherId`, hoặc `searchText` rỗng (tối đa 20_000) |
| `Errors` | Lỗi ghi Solr / FATAL (tối đa 20_000) |
| `Skip_reasons` | Gom theo lý do skip |

### Config Part 3 (`.env`)

| Key | Ý nghĩa | Default |
|-----|---------|---------|
| `SOLR_HOST` | Solr base, ví dụ `http://localhost:8983/solr` | `http://localhost:8983/solr` |
| `SOLR_CORE` | Core name | `eoffice_document` |
| `SOLR_USER` / `SOLR_PASSWORD` | HTTP basic (để trống nếu không auth) | *(rỗng)* |
| `SOLR_TENANT_CODE` | `tenantCode` ghi lên Solr (LEGACY; NEW fallback) | *(rỗng)* |
| `SOLR_BACKFILL_BATCH_SIZE` | Số doc / 1 page Oracle + 1 POST Solr | `200` |
| `ORACLE_*` / `LEGACY_ORACLE_*` | Oracle **LEGACY** (DB migration) | như Part 1 |
| `NEW_ORACLE_USER` / `PASSWORD` / `DSN` / `SCHEMA` | Oracle **NEW** (DB eoffice, host khác) | *(bắt buộc nếu `--source ALL` hoặc `NEW`)* |
| `NEW_ORACLE_TENANTS_FILE` | JSON nhiều tenant NEW | *(rỗng)* |

NEW là multi-tenant: mỗi schema/DSN chạy một lần, hoặc tự tạo JSON rồi `--new-tenants-file`.

Incoming LEGACY lấy `EOFFICE_TO_DEPT_ID` từ `V_VB_INCOMING_DOC_V2`. Doc không map phòng ban → skip `MISSING_DEPT_ID`.

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
| `solr_doc_meta_backfill.py` | Part 3 CLI — backfill Solr metadata |
| `solr_backfill_*.py` | Oracle / Solr / normalize / report cho Part 3 |
| `db.py` | Oracle sample + dump CSV toàn bộ attachment |
| `ssh_plocate.py` | SSH batch `plocate` |
| `missing_scan.py` | Quét missing (logic storage-service job) |
| `missing_report.py` | Xuất CSV/XLSX missing |
| `excel_report.py` | Excel Part 1 |
| `mapping.py` | Normalize path + mapping `ROOT_PATH` |
| `docs/` | Tài liệu DevOps / luồng missing-export |
