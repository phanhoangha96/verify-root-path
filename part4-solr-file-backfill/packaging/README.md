# Solr file content backfill (Part 4)

CLI one-shot: đọc văn bản từ Oracle (NEW và/hoặc LEGACY) → lấy file chính (`VB_ATTACHMENT`) → Apache Tika → ghi Solr `fileContent` → Excel/JSON.

Không gọi `eoffice-business`. Chạy lại **không nhân bản** cùng `fileServiceId` + `deptId` (bỏ qua, trừ `--force`). Solr id: `{fileServiceId}_{deptId}_file`.

Máy chạy phải reach **Oracle + Solr**, và **file-service** hoặc **đĩa file**. Cần **Java** cho Tika.

---

## 1. Yêu cầu

- Python 3.9+ (`python3 --version` / `python --version`)
- Java (Tika). Package `tika` tự chạy tika-server lần đầu; hoặc `TIKA_APP_JAR`
- Network: Oracle listener + Solr HTTP + file-service (nếu dùng)
- `oracledb` thin mode: không cần Oracle Instant Client (DB 12.1+)

---

## 2. Cài đặt

### Linux / macOS

```bash
cd solr-doc-file-backfill
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# sửa .env
```

### Windows (PowerShell)

```powershell
cd solr-doc-file-backfill
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Nếu PowerShell chặn script: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

---

## 3. Điền `.env`

| Nguồn | Khi nào cần | Key |
|-------|-------------|-----|
| **NEW** | `--source NEW` hoặc `ALL` | `NEW_ORACLE_USER` / `PASSWORD` / `DSN` / `SCHEMA` |
| **LEGACY** | `--source LEGACY` hoặc `ALL` | `ORACLE_*` (hoặc `LEGACY_ORACLE_*`) |
| **Solr** | luôn | `SOLR_HOST`, `SOLR_CORE` |
| **File-service** | `FILE_SOURCE=file-service` hoặc `auto` | `FILE_SERVICE_DOWNLOAD_URL` |
| **Disk** | `FILE_SOURCE=disk` hoặc `auto` khi không có `FILE_SERVICE_ID` | `FILE_STORAGE_ROOTS` |

- NEW và LEGACY là **hai database khác nhau**.
- Chỉ backfill NEW: không cần điền `ORACLE_PASSWORD`.
- Chạy trên server Solr: `SOLR_HOST=http://localhost:8983/solr`
- `FILE_SOURCE=auto` (mặc định): có `FILE_SERVICE_ID` thì tải file-service; không thì đọc disk.

Nhiều tenant NEW: copy `new_oracle_tenants.example.json` → `new_oracle_tenants.json`, trong `.env` set `NEW_ORACLE_TENANTS_FILE=./new_oracle_tenants.json`.

---

## 4. Chạy

Luôn smoke trước (đọc Oracle, **không** tải file, **không ghi Solr**):

```bash
python solr_doc_file_backfill.py --source NEW --dry-run --limit 20
```

Full NEW:

```bash
python solr_doc_file_backfill.py --source NEW
```

NEW + LEGACY:

```bash
python solr_doc_file_backfill.py
```

Chỉ văn bản đến (1) hoặc đi (2):

```bash
python solr_doc_file_backfill.py --source NEW --object-type 1
python solr_doc_file_backfill.py --source LEGACY --object-type 2 --file-source disk
```

Ghi đè FILE đã có:

```bash
python solr_doc_file_backfill.py --source NEW --force
```

Job lớn (Linux, chạy nền):

```bash
nohup python solr_doc_file_backfill.py --source NEW > file-backfill.log 2>&1 &
tail -f file-backfill.log
```

`--dry-run`: cột Indexed = số Solr doc **sẽ** ghi.

---

## 5. Kết quả

- `output/solr_doc_file_backfill_YYYYMMDD_HHMMSS.xlsx`
- `output/solr_doc_file_backfill_YYYYMMDD_HHMMSS.json`

`scanned` = số văn bản Oracle. `indexed` = số Solr FILE doc (một văn bản × nhiều dept).

Exit code `0` = không error; `1` = có error trong report.

---

## 6. Lưu ý

- Chạy lại skip `fileServiceId` + `deptId` đã có (trừ `--force`).
- Doc Oracle `IS_DELETE=1` không bị xóa khỏi Solr.
- Cần Java. Lần đầu `tika` có thể tải jar; hoặc `TIKA_APP_JAR=/opt/tika/tika-app-3.3.2.jar`.

---

## 7. Troubleshooting

**Solr HTTP 503 + HTML Squid / proxy**  
Unset `HTTP_PROXY`/`HTTPS_PROXY`, hoặc chạy trên chính server Solr (`localhost`).

**Ping Solr fail / connection refused**  
Sai `SOLR_HOST`/`SOLR_CORE`. Thử:

```bash
curl -sS "http://localhost:8983/solr/<CORE>/admin/ping?wt=json"
```

**FILE_DOWNLOAD_FAILED**  
Sai `FILE_SERVICE_DOWNLOAD_URL` / `TenantCode`, hoặc file chưa có trên file-service.

**MISSING_FILE_SERVICE_ID**  
LEGACY: `--file-source disk` trên máy mount `FILE_STORAGE_ROOTS`.

**TIKA_FAILED**  
Cần Java trên PATH (`java -version`).

**scanned=0**  
Sai schema / bảng trống / `IS_DELETE=1`.

**Missing NEW_ORACLE_***  
`--source NEW` cần `NEW_ORACLE_USER` + `PASSWORD` + `DSN`. File phải tên đúng `.env` (cùng thư mục khi chạy).
