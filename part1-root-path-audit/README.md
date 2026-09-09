# Part 1 — Root-path audit

Kiểm tra `LEGACY.VB_ATTACHMENT.ROOT_PATH` khớp đường dẫn thật trên file server (SSH + `plocate`) → Excel.

CLI one-shot. Không có HTTP API, không gọi `eoffice-business` / `migration-service`.

## Setup

```bash
cd part1-root-path-audit
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# điền ORACLE_PASSWORD và REMOTE_STORAGE_PASSWORD
```

Windows (PowerShell):

```powershell
cd part1-root-path-audit
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

## Luồng

1. Đọc sample attachment từ Oracle (`ID`, `FILE_NAME`, `FILE_PATH`, `ROOT_PATH`)
2. Gọi SSH + `plocate` để tìm absolute path trên server
3. So với mapping kỳ vọng (`dir_upload_path*`)
4. Xuất Excel tổng hợp

## Chạy

```bash
source .venv/bin/activate
python verify_root_path.py
```

Report mặc định: `output/root_path_audit_YYYYMMDD_HHMMSS.xlsx`

## Excel sheets

| Sheet | Nội dung |
|-------|----------|
| `Overview` | Tổng số liệu theo STATUS |
| `Summary_by_ROOT_PATH` | Mỗi `ROOT_PATH` → folder kỳ vọng, số found/matched/mismatch |
| `Detail` | Từng file sample |
| `No_ROOT_PATH` | File không có / null `ROOT_PATH` |
| `Mismatch` | File tìm thấy nhưng nằm ngoài folder kỳ vọng |
| `Expected_Mapping` | Mapping hardcode tham chiếu |

## Mapping kỳ vọng

| ROOT_PATH | Folders |
|-----------|---------|
| `dir_upload_path1` | `/data/u02/data1/voffice/Upload`, `/data/u03/data1/voffice/Upload` |
| `dir_upload_path2` | `/data/u02/data1/voffice/Upload` |
| `dir_upload_path4` | `/data/u02/data1/voffice/Upload`, `/data/u04/data1/voffice/Upload` |
| `dir_upload_path5` | `/data/u03/data1/voffice/Upload` |
| `dir_upload_path_Current` | `/data/u05/data1/voffice/Upload` |

## Config (`.env`)

| Key | Ý nghĩa | Default |
|-----|---------|---------|
| `ORACLE_*` | Kết nối `LEGACY.VB_ATTACHMENT` | |
| `REMOTE_STORAGE_HOST` / `PORT` / `USERNAME` / `PASSWORD` | SSH tới file server | |
| `REMOTE_STORAGE_PLOCATE_DB` | DB plocate trên server | `/var/lib/plocate/voffice.db` |
| `REMOTE_STORAGE_ROOT_FOLDERS` | Các root Upload được chấp nhận khi match | |
| `SAMPLE_PER_ROOT_PATH` | Sample tối đa mỗi giá trị `ROOT_PATH` (kể cả NULL). `0` = all | `200` |
| `SAMPLE_LIMIT` | Giới hạn tổng số row sau sample | `0` |
| `PLOCATE_BATCH_SIZE` | Số path / 1 lần SSH exec | `50` |
| `REQUIRE_HAS_FILE` | Chỉ lấy `IS_HAS_FILE=1` | `true` |
| `EXCLUDE_DELETED` | Bỏ `IS_DELETE!=0` | `true` |
| `OUTPUT_DIR` | Thư mục report | `./output` |

## STATUS

- `MATCHED` — tìm thấy trong folder kỳ vọng của `ROOT_PATH`
- `MISMATCH` — tìm thấy nhưng folder khác kỳ vọng
- `NOT_FOUND` — plocate không thấy
- `NO_ROOT_PATH_BUT_FOUND` / `NO_ROOT_PATH_NOT_FOUND` — thiếu `ROOT_PATH`
- `UNKNOWN_ROOT_PATH_*` — `ROOT_PATH` không nằm trong mapping
