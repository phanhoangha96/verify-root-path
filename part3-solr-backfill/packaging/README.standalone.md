# Solr metadata backfill — gói binary (không cần Python)

Giống fat JAR: một file chạy được, **không cài Python** trên server.

CLI: Oracle NEW/LEGACY → Solr `searchText` + field riêng (`docCode`, `quote`, ...) → Excel/JSON.

Chạy lại nhiều lần **không nhân bản** (`overwrite=true`, id `{objectId}_{deptId}_meta`).

Máy chạy phải **reach được Oracle và Solr**. Nên chạy trên server Solr.

Binary đúng OS: Linux amd64 **không** chạy trên Windows và ngược lại. Gói này là **Linux** nếu bạn nhận từ `--linux`.

---

## 1. Giải nén + cấu hình

```bash
unzip solr-doc-meta-backfill.zip
cd solr-doc-meta-backfill
chmod +x solr-doc-meta-backfill
cp .env.example .env
# sửa .env
```

Windows: chạy `solr-doc-meta-backfill.exe` (gói build trên Windows, không dùng gói Linux).

---

## 2. Điền `.env`

| Nguồn | Khi nào cần | Key |
|-------|-------------|-----|
| **NEW** | `--source NEW` hoặc `ALL` | `NEW_ORACLE_USER` / `PASSWORD` / `DSN` / `SCHEMA` |
| **LEGACY** | `--source LEGACY` hoặc `ALL` | `ORACLE_*` |
| **Solr** | luôn | `SOLR_HOST`, `SOLR_CORE` |

- Chỉ NEW: không cần `ORACLE_PASSWORD`.
- Trên server Solr: `SOLR_HOST=http://localhost:8983/solr`
- `SOLR_CORE` đúng core (ví dụ `truclt_agency`).

Nhiều tenant: copy `new_oracle_tenants.example.json` → `new_oracle_tenants.json`, set `NEW_ORACLE_TENANTS_FILE=./new_oracle_tenants.json`.

---

## 3. Chạy

File `.env` phải nằm **cùng thư mục** lúc chạy.

Smoke (không ghi Solr):

```bash
./solr-doc-meta-backfill --source NEW --dry-run --limit 50
```

Full NEW:

```bash
./solr-doc-meta-backfill --source NEW
```

NEW + LEGACY:

```bash
./solr-doc-meta-backfill
```

Chạy nền:

```bash
nohup ./solr-doc-meta-backfill --source NEW > backfill.log 2>&1 &
tail -f backfill.log
```

Windows:

```powershell
.\solr-doc-meta-backfill.exe --source NEW --dry-run --limit 50
```

---

## 4. Kết quả

- `output/solr_doc_meta_backfill_YYYYMMDD_HHMMSS.xlsx`
- `output/solr_doc_meta_backfill_YYYYMMDD_HHMMSS.json`

Exit `0` = OK, `1` = có error.

---

## 5. Troubleshooting

**Permission denied**  
`chmod +x solr-doc-meta-backfill`

**cannot execute: required file not found / Exec format error**  
Sai OS/arch. Cần binary Linux amd64 (build lại bằng `python build_solr_backfill_dist.py --linux` trong `part3-solr-backfill`).

**Lỗi extract /tmp (onefile)**  
Server gắn `noexec` trên `/tmp`. Xin lại gói `--onedir`, hoặc:

```bash
export TMPDIR="$PWD/tmp"
mkdir -p "$TMPDIR"
./solr-doc-meta-backfill --source NEW
```

**Solr HTTP 503 + HTML Squid**  
HTTP proxy. Unset `HTTP_PROXY`/`HTTPS_PROXY`, hoặc chạy localhost trên máy Solr.

**scanned=0**  
Sai schema / bảng trống / `IS_DELETE=1`. Kiểm tra `COUNT(*)` trên `VB_INCOMING_DOC` / `VB_OUTGOING_DOC`.
