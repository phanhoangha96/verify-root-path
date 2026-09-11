# root-path-audit

Bốn CLI Python one-shot, **độc lập** — mỗi part có script, `.env` và README riêng. Không có HTTP API.

| Phần | Thư mục | Việc làm |
|------|---------|----------|
| **1. Root-path audit** | [`part1-root-path-audit/`](part1-root-path-audit/) | Sample `VB_ATTACHMENT.ROOT_PATH` ↔ folder thật trên server (plocate) → Excel |
| **2. Export missing files** | [`part2-export-missing/`](part2-export-missing/) | Dump toàn bộ attachment → quét plocate → xuất file **không tìm thấy** |
| **3. Solr metadata backfill** | [`part3-solr-backfill/`](part3-solr-backfill/) | Đọc Oracle NEW + LEGACY → ghi Solr `searchText` → Excel/JSON |
| **4. Solr file content backfill** | [`part4-solr-file-backfill/`](part4-solr-file-backfill/) | Đọc Oracle + file (file-service / disk) → Tika → ghi Solr `fileContent` → Excel/JSON |

Vào thư mục part rồi làm theo README của part đó (`cp .env.example .env`, cài `requirements.txt`, chạy entry point).

Nếu đang có `.env` ở root repo: copy **chỉ** các key của part đó vào `partN-.../.env` (Part 1/2: Oracle + SSH; Part 3: Oracle NEW/LEGACY + Solr; Part 4: Oracle + Solr + file-service/disk). Không dùng chung một file env cho cả bốn part.
