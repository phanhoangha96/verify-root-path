# root-path-audit

CLI nhẹ (Python) để kiểm tra `LEGACY.VB_ATTACHMENT.ROOT_PATH` khớp đường dẫn thật trên file server thế nào.

Luồng:

1. Đọc sample attachment từ Oracle (`ID`, `FILE_NAME`, `FILE_PATH`, `ROOT_PATH`)
2. Gọi SSH + `plocate` (giống `storage-service`) để tìm absolute path trên server
3. So với mapping kỳ vọng (`dir_upload_path*`)
4. Xuất Excel tổng hợp

## Vì sao chọn Python?

- Ít tài nguyên hơn Java/Spring cho job one-shot
- Thin mode Oracle (`oracledb`) không cần Instant Client
- Paramiko SSH + batch `plocate` stdin (tránh `find` nặng)

## Setup

### Windows (PowerShell)

```powershell
cd C:\Users\ADV 01\Downloads\verify-root-path-main
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# điền ORACLE_PASSWORD và REMOTE_STORAGE_PASSWORD trong .env
```

Nếu bị chặn script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

hoặc kích hoạt không cần đổi policy:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

### macOS / Linux

```bash
cd /Users/phanhoangha/IdeaProjects/Sotatek/root-path-audit
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# điền ORACLE_PASSWORD và REMOTE_STORAGE_PASSWORD
```

## Chạy

### Windows (PowerShell)

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

### macOS / Linux

```bash
source .venv/bin/activate
python main.py
```

Report mặc định: `output/root_path_audit_YYYYMMDD_HHMMSS.xlsx`

## Excel sheets

| Sheet | Nội dung |
|-------|----------|
| `Overview` | Tổng số liệu theo STATUS |
| `Summary_by_ROOT_PATH` | Mỗi `ROOT_PATH` → folder kỳ vọng, số found/matched/mismatch, **DISCOVERED_SERVER_ROOTS** thực tế |
| `Detail` | Từng file sample |
| `No_ROOT_PATH` | File không có / null `ROOT_PATH` |
| `Mismatch` | File tìm thấy nhưng nằm ngoài folder kỳ vọng của `ROOT_PATH` |
| `Expected_Mapping` | Mapping hardcode tham chiếu |

## Mapping kỳ vọng

| ROOT_PATH | Folders |
|-----------|---------|
| `dir_upload_path1` | `/data/u02/data1/voffice/Upload`, `/data/u03/data1/voffice/Upload` |
| `dir_upload_path2` | `/data/u02/data1/voffice/Upload` |
| `dir_upload_path4` | `/data/u02/data1/voffice/Upload`, `/data/u04/data1/voffice/Upload` |
| `dir_upload_path5` | `/data/u03/data1/voffice/Upload` |
| `dir_upload_path_Current` | `/data/u05/data1/voffice/Upload` |

## Config chính (`.env`)

| Key | Ý nghĩa | Default |
|-----|---------|---------|
| `SAMPLE_PER_ROOT_PATH` | Sample tối đa mỗi giá trị `ROOT_PATH` (kể cả NULL). `0` = all | `200` |
| `SAMPLE_LIMIT` | Giới hạn tổng số row sau sample | `0` (không giới hạn) |
| `PLOCATE_BATCH_SIZE` | Số path / 1 lần SSH exec | `50` |
| `REQUIRE_HAS_FILE` | Chỉ lấy `IS_HAS_FILE=1` | `true` |
| `EXCLUDE_DELETED` | Bỏ `IS_DELETE!=0` | `true` |

## STATUS

- `MATCHED` — tìm thấy trong folder kỳ vọng của `ROOT_PATH`
- `MISMATCH` — tìm thấy nhưng folder khác kỳ vọng
- `NOT_FOUND` — plocate không thấy
- `NO_ROOT_PATH_BUT_FOUND` / `NO_ROOT_PATH_NOT_FOUND` — thiếu `ROOT_PATH`
- `UNKNOWN_ROOT_PATH_*` — `ROOT_PATH` không nằm trong mapping
