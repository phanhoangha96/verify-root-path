# Solr metadata backfill (Part 3)

CLI one-shot: đọc văn bản từ Oracle (NEW và/hoặc LEGACY) → ghi `searchText` và field riêng (`docCode`, `quote`, `outsidePublisherName`, `otherReceivePlaces`) lên Solr → xuất Excel/JSON.

Không gọi `eoffice-business`. Chạy lại nhiều lần **không nhân bản** (cùng Solr `id` thì ghi đè, `overwrite=true`).

Solr doc id: `{objectId}_{deptId}_meta`, `indexType=META`.

Máy chạy phải **reach được Oracle và Solr** (thường chạy ngay trên server Solr).

---

## 1. Yêu cầu

- Python 3.9+ (`python3 --version` / `python --version`)
- Network: Oracle listener + Solr HTTP (thường `8983`)
- `oracledb` thin mode: không cần Oracle Instant Client (DB 12.1+)

---

## 2. Cài đặt

### Linux / macOS

```bash
cd solr-doc-meta-backfill
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# sửa .env
```

### Windows (PowerShell)

```powershell
cd solr-doc-meta-backfill
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

- NEW và LEGACY là **hai database khác nhau**. Không dùng chung một DSN trừ khi đúng là vậy.
- Chỉ backfill NEW: không cần điền `ORACLE_PASSWORD`.
- Chạy trên server Solr: `SOLR_HOST=http://localhost:8983/solr`
- `SOLR_CORE` phải đúng core môi trường (ví dụ `truclt_agency`, `eoffice_document`).

Nhiều tenant NEW: copy `new_oracle_tenants.example.json` → `new_oracle_tenants.json`, điền từng schema, trong `.env` set `NEW_ORACLE_TENANTS_FILE=./new_oracle_tenants.json`.

---

## 4. Chạy

Luôn smoke trước (đọc Oracle, **không ghi Solr**):

```bash
python solr_doc_meta_backfill.py --source NEW --dry-run --limit 50
```

Full NEW:

```bash
python solr_doc_meta_backfill.py --source NEW
```

NEW + LEGACY (cần đủ cả hai khối trong `.env`):

```bash
python solr_doc_meta_backfill.py
```

Chỉ văn bản đến (1) hoặc đi (2):

```bash
python solr_doc_meta_backfill.py --source NEW --object-type 1
python solr_doc_meta_backfill.py --source LEGACY --object-type 2
```

Job lớn (Linux, chạy nền):

```bash
nohup python solr_doc_meta_backfill.py --source NEW > backfill.log 2>&1 &
tail -f backfill.log
```

`--dry-run`: cột Indexed = số doc **sẽ** ghi Solr.

---

## 5. Kết quả

Mỗi lần chạy tạo:

- `output/solr_doc_meta_backfill_YYYYMMDD_HHMMSS.xlsx`
- `output/solr_doc_meta_backfill_YYYYMMDD_HHMMSS.json`

| Sheet | Nội dung |
|-------|----------|
| `Overview` | scanned / indexed / skipped / errors, Solr host |
| `By_source` | NEW vs LEGACY × incoming (1) / outgoing (2) |
| `Skipped` | thiếu `toDeptId`/`publisherId`, hoặc `searchText` rỗng |
| `Errors` | lỗi ghi Solr / FATAL |
| `Skip_reasons` | gom theo lý do skip |

Exit code `0` = không error; `1` = có error trong report.

---

## 6. Lưu ý

- Chạy lại = **upsert**, không duplicate cùng `{objectId}_{deptId}_meta`.
- Doc Oracle `IS_DELETE=1` không bị xóa khỏi Solr.
- Incoming NEW lấy `TO_DEPT_ID`; outgoing lấy `PUBLISHER_ID`. Thiếu → skip `MISSING_DEPT_ID`.
- Incoming LEGACY lấy `EOFFICE_TO_DEPT_ID` từ `V_VB_INCOMING_DOC_V2`.

---

## 7. Troubleshooting

**Solr HTTP 503 + HTML Squid / proxy**  
Request Solr đang đi qua HTTP proxy. Script đã bypass proxy. Nếu vẫn lỗi: unset `HTTP_PROXY`/`HTTPS_PROXY`, hoặc chạy trên chính server Solr (`localhost`).

**Ping Solr fail / connection refused**  
Sai `SOLR_HOST`/`SOLR_CORE`, Solr chưa lên, hoặc firewall. Thử:

```bash
curl -sS "http://localhost:8983/solr/<CORE>/admin/ping?wt=json"
```

**scanned=0, errors=0**  
Query không ra hàng: sai schema, bảng trống, hoặc toàn `IS_DELETE=1`. Kiểm tra:

```sql
SELECT COUNT(*) FROM <SCHEMA>.VB_INCOMING_DOC WHERE NVL(IS_DELETE, 0) = 0;
SELECT COUNT(*) FROM <SCHEMA>.VB_OUTGOING_DOC WHERE NVL(IS_DELETE, 0) = 0;
```

**Missing NEW_ORACLE_***  
`--source NEW` cần `NEW_ORACLE_USER` + `PASSWORD` + `DSN` trong `.env`. File phải tên đúng `.env` (cùng thư mục khi chạy).
