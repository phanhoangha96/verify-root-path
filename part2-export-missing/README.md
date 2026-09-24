# Part 2 — Export missing files

CLI one-shot: dump toàn bộ attachment từ Oracle → quét `plocate` trên file server → xuất file **không tìm thấy** (CSV/XLSX).

Thay cho luồng HTTP cũ `migration-service` → `storage-service`.

- ~~`migration-service` `GET /vbAttachmentTdhvp/exportMissingFiles`~~ → `python export_missing.py`
- ~~`storage-service` `/api/files/missing/*`~~ → cùng CLI trên

`storage-service` giờ chỉ còn `ready` / `exists` / `download` cho migration download file.

## Setup

```bash
cd part2-export-missing
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# điền ORACLE_PASSWORD và REMOTE_STORAGE_PASSWORD
```

Windows (PowerShell):

```powershell
cd part2-export-missing
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

## Luồng (Oracle + plocate trên máy local)

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

## Chạy

```bash
source .venv/bin/activate

# Full job: dump Oracle + scan plocate + report
python export_missing.py

# Chỉ dump CSV từ Oracle (không SSH)
python export_missing.py --skip-scan

# Reuse CSV đã dump sẵn (bỏ Oracle; chỉ scan plocate)
python export_missing.py --input-csv ./output/missing-export/<jobId>/input.csv
# Windows PowerShell ví dụ:
# python export_missing.py --input-csv .\output\missing-export\aa886dfa05784e35\input.csv

# Hoặc set MISSING_EXPORT_CSV_PATH trong .env trỏ tới file đó rồi: python export_missing.py

# Resume job bị cắt SSH (đọc scan_checkpoint.json, append missing.csv)
python export_missing.py --resume ./output/missing-export/<jobId>
# Job cũ chưa có checkpoint: lấy N từ dòng progress cuối (checked=N)
python export_missing.py --resume ./output/missing-export/<jobId> --skip-checked 930000 --input-csv ./path/to/input.csv

# Chỉ xuất lại report từ missing.csv đã có (không scan lại)
python export_missing.py --export-only ./output/missing-export/<jobId>
python export_missing.py --export-only ./output/missing-export/<jobId> --format csv

# Ép format
python export_missing.py --format csv
python export_missing.py --format xlsx
```

Report mặc định:

- `output/missing-files_YYYYMMDD_HHMMSS.xlsx` nếu số missing ≤ 1_048_575
- ngược lại → `.csv`
- Raw `missing.csv` + `scan_checkpoint.json` giữ trong `MISSING_EXPORT_WORK_DIR/<jobId>/`

## Config (`.env`)

| Key | Ý nghĩa | Default |
|-----|---------|---------|
| `ORACLE_*` | Kết nối `LEGACY.VB_ATTACHMENT` | |
| `REMOTE_STORAGE_HOST` / `PORT` / `USERNAME` / `PASSWORD` | SSH tới file server | |
| `REMOTE_STORAGE_PLOCATE_DB` | DB plocate trên server | `/var/lib/plocate/voffice.db` |
| `REMOTE_STORAGE_ROOT_FOLDERS` | Các root Upload được chấp nhận khi match | |
| `MISSING_EXPORT_CSV_PATH` | CSV cố định; nếu đã có file → reuse, bỏ dump Oracle | *(rỗng)* |
| `MISSING_EXPORT_WORK_DIR` | Thư mục job (input/missing) | `./output/missing-export` |
| `MISSING_EXPORT_LIMIT` | Giới hạn số row dump Oracle (`0` = all) | `0` |
| `MISSING_EXPORT_BATCH_SIZE` | Số path / 1 SSH exec | `100` |
| `MISSING_EXPORT_THROTTLE_EVERY` | Pause sau mỗi N check | `500` |
| `MISSING_EXPORT_THROTTLE_PAUSE_MS` | Thời gian pause (ms) | `200` |
| `MISSING_EXPORT_RECONNECT_EVERY` | Reconnect SSH sau N check | `50000` |
| `MISSING_EXPORT_RETRY_ATTEMPTS` | Số lần retry khi plocate/SSH lỗi | `3` |
| `MISSING_EXPORT_RETRY_BACKOFF_MS` | Backoff giữa các lần retry (× attempt) | `500` |
| `MISSING_EXPORT_SEARCH_BY_FILE_NAME` | `plocate -b` basename (giữ `false` cho job lớn) | `false` |
| `OUTPUT_DIR` | Thư mục report | `./output` |

## Resume sau khi mất SSH

Mỗi batch thành công ghi `scan_checkpoint.json` (`checked`, `missing`, `input_csv`).

```bash
# Job mới (có checkpoint): resume đúng job dir vừa crash
python export_missing.py --resume ./output/missing-export/<jobId>

# Job cũ (chưa có checkpoint): dùng checked từ log progress
python export_missing.py --resume ./output/missing-export/<jobId> \
  --skip-checked 930000 \
  --input-csv ./output/missing-export/<otherJob>/input.csv
```

Gợi ý `.env` khi SSH hay bị drop:

```env
MISSING_EXPORT_BATCH_SIZE=100
MISSING_EXPORT_RECONNECT_EVERY=10000
MISSING_EXPORT_RETRY_ATTEMPTS=10
MISSING_EXPORT_RETRY_BACKOFF_MS=5000
```

## Reindex plocate trên file server

Chạy **trên server** (SSH root), trước khi scan nếu index cũ / thiếu file mới:

```bash
# DB đúng với REMOTE_STORAGE_PLOCATE_DB trong .env
DB=/var/lib/plocate/voffice.db

# Index các root vật lý trên server (khớp REMOTE_STORAGE_ROOT_FOLDERS)
# Host layout: /data/voffice/{u02,u03,u04,u05,Y2026}/...
updatedb -o "$DB" --require-visibility 0 \
  -U /data/voffice/u02/data1/voffice/Upload \
  -U /data/voffice/u03/data1/voffice/Upload \
  -U /data/voffice/u04/data1/voffice/Upload \
  -U /data/voffice/u05/data1/voffice/Upload \
  -U /data/voffice/Y2026 \
  -U /data

# Kiểm tra
ls -lh "$DB"
plocate -d "$DB" -l 5 -- 'Upload'
```

Ghi chú:

- Binary indexer thường là `updatedb` của package `plocate` (một số OS tên `updatedb.plocate`).
- `--require-visibility 0`: index cả file không world-readable (cần khi chạy plocate với quyền phù hợp).
- Job lớn: chạy reindex xong rồi mới `python export_missing.py` (index lệch → `missing == checked`).

## Lưu ý vận hành

- Job **read-only** trên server file: chỉ `plocate`, không `stat`/`get`/`put`/xóa.
- Cần `plocate` + DB index (`REMOTE_STORAGE_PLOCATE_DB`, thường `/var/lib/plocate/voffice.db`).
- Job lớn có thể chạy nhiều giờ; nếu SSH drop → `--resume` (không cần quét lại từ đầu).
