# Solr file content backfill — gói binary (không cần Python)

Giống fat JAR: một file chạy được, **không cài Python** trên server.

CLI: Oracle NEW/LEGACY → tải file (file-service hoặc disk) → Apache Tika → Solr `fileContent` → Excel/JSON.

Chạy lại **không nhân bản** cùng `fileServiceId` + `deptId` (trừ `--force`). Solr id `{fileServiceId}_{deptId}_file`.

Máy chạy phải **reach được Oracle, Solr**, và file-service hoặc đĩa file. Cần **Java** cho Tika (không đóng trong binary).

Binary đúng OS: Linux amd64 **không** chạy trên Windows và ngược lại. Gói này là **Linux** nếu bạn nhận từ `--linux`.

---

## 1. Giải nén + cấu hình

```bash
unzip solr-doc-file-backfill.zip
cd solr-doc-file-backfill
chmod +x solr-doc-file-backfill
cp .env.example .env
# sửa .env
```

Windows: chạy `solr-doc-file-backfill.exe` (gói build trên Windows, không dùng gói Linux).

Kiểm tra Java:

```bash
java -version
```

Tuỳ chọn (Tika 3.x, gần eoffice-solr):

```bash
export TIKA_APP_JAR=/opt/tika/tika-app-3.3.2.jar
```

---

## 2. Điền `.env`

| Nguồn | Khi nào cần | Key |
|-------|-------------|-----|
| **NEW** | `--source NEW` hoặc `ALL` | `NEW_ORACLE_USER` / `PASSWORD` / `DSN` / `SCHEMA` |
| **LEGACY** | `--source LEGACY` hoặc `ALL` | `ORACLE_*` |
| **Solr** | luôn | `SOLR_HOST`, `SOLR_CORE` |
| **File-service** | `FILE_SOURCE=file-service` hoặc `auto` | `FILE_SERVICE_DOWNLOAD_URL` |
| **Disk** | `FILE_SOURCE=disk` hoặc `auto` | `FILE_STORAGE_ROOTS` |

- Chỉ NEW: không cần `ORACLE_PASSWORD`.
- Trên server Solr: `SOLR_HOST=http://localhost:8983/solr`
- `SOLR_CORE` đúng core (ví dụ `truclt_agency`).
- `FILE_SOURCE=auto`: có `FILE_SERVICE_ID` thì tải file-service; không thì đọc disk.

Nhiều tenant: copy `new_oracle_tenants.example.json` → `new_oracle_tenants.json`, set `NEW_ORACLE_TENANTS_FILE=./new_oracle_tenants.json`.

---

## 3. Chạy

File `.env` phải nằm **cùng thư mục** lúc chạy.

Smoke (không tải file, không ghi Solr):

```bash
./solr-doc-file-backfill --source NEW --dry-run --limit 20
```

Full NEW:

```bash
./solr-doc-file-backfill --source NEW
```

NEW + LEGACY:

```bash
./solr-doc-file-backfill
```

Chạy nền:

```bash
nohup ./solr-doc-file-backfill --source NEW > file-backfill.log 2>&1 &
tail -f file-backfill.log
```

Windows:

```powershell
.\solr-doc-file-backfill.exe --source NEW --dry-run --limit 20
```

---

## 4. Kết quả

- `output/solr_doc_file_backfill_YYYYMMDD_HHMMSS.xlsx`
- `output/solr_doc_file_backfill_YYYYMMDD_HHMMSS.json`

Exit `0` = OK, `1` = có error.

---

## 5. Troubleshooting

**Permission denied**  
`chmod +x solr-doc-file-backfill`

**cannot execute: required file not found / Exec format error**  
Sai OS/arch. Cần binary Linux amd64 (build lại bằng `python build/build_solr_file_backfill_dist.py --linux` trong `part4-solr-file-backfill`).

**Lỗi extract /tmp (onefile)**  
Server gắn `noexec` trên `/tmp`. Xin lại gói `--onedir`, hoặc:

```bash
export TMPDIR="$PWD/tmp"
mkdir -p "$TMPDIR"
./solr-doc-file-backfill --source NEW
```

**TIKA_FAILED / java not found**  
Binary không kèm JRE. Cài Java trên server, hoặc `TIKA_APP_JAR`.

**Solr HTTP 503 + HTML Squid**  
HTTP proxy. Unset `HTTP_PROXY`/`HTTPS_PROXY`, hoặc chạy localhost trên máy Solr.

**FILE_DOWNLOAD_FAILED**  
Sai URL file-service / header `TenantCode`.

**scanned=0**  
Sai schema / bảng trống / `IS_DELETE=1`.
