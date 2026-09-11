# Part 4 — Solr file content backfill

CLI one-shot: đọc văn bản từ Oracle (NEW và/hoặc LEGACY) → lấy file chính (`VB_ATTACHMENT`) → extract text bằng Apache Tika → ghi Solr `fileContent` → xuất Excel/JSON.

Không gọi `eoffice-business`. Khớp luồng realtime:

`IncomingDocController.indexFileUsingSolr` → `SolrService` → RabbitMQ → `eoffice-solr DocServiceImpl.addDocument` (tải file, Tika, `solrClient.add`).

Chạy lại **không nhân bản** cùng `fileServiceId` + `deptId` (bỏ qua, giống `POST /document/add`). Solr id deterministic: `{fileServiceId}_{deptId}_file` (`overwrite=true`). `--force` ghi đè id đó.

Máy chạy phải reach **Oracle + Solr**, và **file-service** (NEW) hoặc **đĩa file** (LEGACY).

`eoffice-business` chỉ index FILE khi tạo/sửa văn bản có file. Script này backfill dữ liệu cũ.

Solr FILE doc (không có `indexType=META`):

- `id` = `{fileServiceId}_{deptId}_file`
- `objectId`, `objectType` (1 incoming / 2 outgoing)
- `fileContent` (Tika raw text)
- `fileServiceId`, `deptId`, `tenantCode`

Incoming: file chính `OBJECT_TYPE=1`, index theo `TO_DEPT_ID` + `DEPT_RECEIVER_ID` trên `VB_INCOMING_PROCESS`.

Outgoing: file chính `OBJECT_TYPE=2`, index theo `PUBLISHER_ID` + `DEPT_RECEIVER_ID` process + CC (`VB_CC_INFO`).

---

## 1. Yêu cầu

- Python 3.9+ (`python3 --version` / `python --version`)
- Java (Tika). Package `tika` tự chạy tika-server lần đầu; hoặc đặt `TIKA_APP_JAR` trỏ `tika-app.jar`
- Network: Oracle + Solr HTTP + file-service (nếu `FILE_SOURCE=file-service` / `auto`)
- `oracledb` thin mode: không cần Oracle Instant Client (DB 12.1+)

---

## 2. Cài đặt

### Linux / macOS

```bash
cd part4-solr-file-backfill
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# sửa .env
```

### Windows (PowerShell)

```powershell
cd part4-solr-file-backfill
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Nếu PowerShell chặn script: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

---

## 3. Điền `.env`

NEW và LEGACY nằm trên **hai Oracle khác nhau**.

| Nguồn | Khi nào cần | Key |
|-------|-------------|-----|
| **NEW** | `--source NEW` hoặc `ALL` | `NEW_ORACLE_USER` / `PASSWORD` / `DSN` / `SCHEMA` |
| **LEGACY** | `--source LEGACY` hoặc `ALL` | `ORACLE_*` (hoặc `LEGACY_ORACLE_*`) |
| **Solr** | luôn | `SOLR_HOST`, `SOLR_CORE` |
| **File-service** | `FILE_SOURCE=file-service` hoặc `auto` | `FILE_SERVICE_DOWNLOAD_URL` |
| **Disk** | `FILE_SOURCE=disk` hoặc `auto` khi không có `FILE_SERVICE_ID` | `FILE_STORAGE_ROOTS` |

`FILE_SOURCE`:

| Giá trị | Cách lấy bytes |
|---------|----------------|
| `auto` (mặc định) | Có `FILE_SERVICE_ID` → GET file-service (giống eoffice-solr). Fail / không có id → đọc disk `ROOT_PATH`/`FILE_PATH` |
| `file-service` | Chỉ file-service: `{url}/{fileServiceId}?type=original`, header `TenantCode` |
| `disk` | Chỉ local/NFS: thử từng `FILE_STORAGE_ROOTS` + `FILE_PATH` (LEGACY thêm mapping `dir_upload_path*` như part 1) |

- Chạy trên server Solr: `SOLR_HOST=http://localhost:8983/solr`
- `SOLR_CORE` phải đúng core môi trường
- `SOLR_TENANT_CODE` / `NEW_ORACLE_TENANT_CODE`: header `TenantCode` khi tải file-service
- `TIKA_WRITE_LIMIT=100000` khớp Java `BodyContentHandler` mặc định
- Nhiều tenant NEW: `NEW_ORACLE_TENANTS_FILE=./new_oracle_tenants.json`

`SOLR_BACKFILL_BATCH_SIZE` mặc định **20** (mỗi doc phải tải file + Tika, nhỏ hơn part 3).

---

## 4. Chạy

Luôn smoke trước (đọc Oracle, **không** tải file, **không** ghi Solr):

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

Ghi đè FILE đã có (`fileServiceId` + `deptId`):

```bash
python solr_doc_file_backfill.py --source NEW --force
```

Job lớn (Linux, chạy nền):

```bash
nohup python solr_doc_file_backfill.py --source NEW > file-backfill.log 2>&1 &
tail -f file-backfill.log
```

`--dry-run`: cột Indexed = số Solr doc **sẽ** ghi (mỗi dept một doc).

---

## 5. Kết quả

Mỗi lần chạy tạo:

- `output/solr_doc_file_backfill_YYYYMMDD_HHMMSS.xlsx`
- `output/solr_doc_file_backfill_YYYYMMDD_HHMMSS.json`

| Sheet | Nội dung |
|-------|----------|
| `Overview` | scanned / indexed / skipped / errors |
| `By_source` | NEW vs LEGACY × incoming (1) / outgoing (2) |
| `Skipped` | thiếu attachment / dept / file, content rỗng, đã index (tối đa 20_000) |
| `Errors` | tải file / Tika / ghi Solr / FATAL |
| `Skip_reasons` | gom theo lý do skip |

`scanned` = số văn bản Oracle. `indexed` = số Solr FILE doc (một văn bản × nhiều dept).

Exit code `0` = không error; `1` = có error trong report.

---

## 6. Lưu ý

- Không gọi eoffice-solr HTTP; ghi Solr trực tiếp như part 3.
- Java realtime dùng UUID cho `id`. Script dùng `{fileServiceId}_{deptId}_file`. Doc UUID cũ vẫn được nhận diện qua query `fileServiceId` + `deptId` và **skip** (trừ `--force`).
- `--force` ghi đè id deterministic; không xóa doc UUID cũ do Java tạo.
- Cùng một file index nhiều dept: Tika chỉ chạy **một lần** (cache theo `fileServiceId`).
- Doc Oracle `IS_DELETE=1` không bị xóa khỏi Solr.
- PDF scan (không text layer): Tika ra rỗng → skip `EMPTY_FILE_CONTENT` (không OCR trừ khi Tika/Tesseract được cài trên máy).
- Attachment lấy file chính `findByDoc`: `OBJECT_TYPE` 1/2, `IS_DELETE=0`, `STATUS` null hoặc `active`.

---

## 7. Troubleshooting

**Ping Solr fail** — giống part 3: sai host/core, firewall, HTTP proxy. Script bypass proxy.

**FILE_DOWNLOAD_FAILED HTTP 404 / 500**  
Sai `FILE_SERVICE_DOWNLOAD_URL`, tenant header, hoặc file chưa nằm trên file-service. Thử:

```bash
curl -sS -D - -o /tmp/f.bin \
  -H "TenantCode: bvhttdl.gov.vn" \
  "http://localhost:10005/FileService/file/downloadFile/<FILE_SERVICE_ID>?type=original"
```

**MISSING_FILE_SERVICE_ID**  
Attachment LEGACY chưa có id file-service. Chạy `--file-source disk` trên máy mount được `FILE_STORAGE_ROOTS`.

**TIKA_FAILED / tika server**  
Cần Java. Lần đầu `tika` tải jar. Hoặc:

```bash
# Tika 3.x (gần eoffice-solr 3.3.2)
export TIKA_APP_JAR=/opt/tika/tika-app-3.3.2.jar
```

**scanned=0**  
Sai schema / toàn `IS_DELETE=1`. Kiểm tra:

```sql
SELECT COUNT(*) FROM <SCHEMA>.VB_INCOMING_DOC WHERE NVL(IS_DELETE, 0) = 0;
SELECT COUNT(*) FROM <SCHEMA>.VB_ATTACHMENT WHERE OBJECT_TYPE IN (1, 2) AND NVL(IS_DELETE, 0) = 0;
```

**Đếm FILE trên Solr**

```bash
curl -sS "$SOLR/$CORE/select?q=fileServiceId:*&rows=0&wt=json"
```

---

## 8. Đóng gói cho DevOps

Từ thư mục `part4-solr-file-backfill`:

```bash
python build/build_solr_file_backfill_dist.py              # source (.py, cần Python trên server)
python build/build_solr_file_backfill_dist.py --standalone # binary OS hiện tại (không cần Python)
python build/build_solr_file_backfill_dist.py --linux      # binary Linux amd64 qua Docker (PRD)
```

Gói ra `dist/solr-doc-file-backfill/` (+ zip). README trong zip là bản dành cho máy chạy (không chứa hướng dẫn build).

Binary **không kèm Java**. Server chạy Tika phải có `java` trên PATH, hoặc `TIKA_APP_JAR`.
