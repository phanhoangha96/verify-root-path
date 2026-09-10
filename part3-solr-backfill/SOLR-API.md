# Solr HTTP API — core, schema, index

Hướng dẫn gọi API Solr **standalone** (Core Admin) trên Linux, macOS và Windows. Khớp backfill META eoffice.

Mọi lệnh dùng `curl` + biến `SOLR` / `CORE`. Đổi host cho đúng môi trường (`.env`: `SOLR_HOST`, `SOLR_CORE`).

---

## Shell

**Linux / macOS / WSL / Git Bash** (bash hoặc zsh):

```bash
export SOLR="http://localhost:8983/solr"
export CORE="truclt_agency"
# remote: export SOLR="http://10.16.150.233:8983/solr"

curl -sS "$SOLR/$CORE/admin/ping?wt=json"
```

**Windows PowerShell:** `curl` là alias của `Invoke-WebRequest` — dùng `curl.exe`. Cú pháp `$SOLR` vẫn được; JSON POST nên dùng file (mục dưới) hoặc Git Bash/WSL.

```powershell
$env:SOLR = "http://localhost:8983/solr"
$env:CORE = "truclt_agency"
curl.exe -sS "$env:SOLR/$env:CORE/admin/ping?wt=json"
```

Auth Basic (mọi OS):

```bash
curl -sS -u "$SOLR_USER:$SOLR_PASSWORD" "$SOLR/$CORE/admin/ping?wt=json"
```

POST JSON: bash/zsh dùng nháy đơn. Tránh nháy kép lồng JSON trên PowerShell — ghi file rồi `--data-binary @file.json` (chạy được mọi OS):

```bash
cat > /tmp/solr-body.json <<'EOF'
{"commit":{}}
EOF
curl -sS -X POST "$SOLR/$CORE/update?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/solr-body.json
```

Windows: đường dẫn kiểu `.\solr-body.json`. Các ví dụ POST bên dưới ghi JSON trong nháy đơn (bash); PowerShell đổi sang `@file.json`.

---

## 0. Phân biệt nhanh

| Việc | API | Xóa FILE? | Gỡ `FieldInfo` Lucene? |
|------|-----|-----------|-------------------------|
| Xóa doc META (script backfill) | `update` delete-by-query `indexType:META` | Không | **Không** |
| Xóa hết document | `update` `*:*` | Có (mất search FILE + META) | **Không** |
| Tạo lại **index** (giữ schema) | `UNLOAD deleteIndex=true` + `CREATE` hoặc xóa thư mục `data/index` | Có | **Có** |
| Xóa luôn core + conf | `UNLOAD deleteInstanceDir=true` | Có | Có — **mất schema** |

`FieldInfo` (lỗi `index options=NONE` vs `POSITIONS` trên `otherReceivePlaces`) chỉ hết khi tạo lại **index**, không hết khi xóa document.

Doc script backfill: `id = {objectId}_{deptId}_meta`, `indexType=META`.

---

## 1. Kiểm tra Solr / core

### Ping

```bash
curl -sS "$SOLR/$CORE/admin/ping?wt=json"
```

### Danh sách core

```bash
curl -sS "$SOLR/admin/cores?action=STATUS&wt=json"
```

### Chi tiết 1 core (`instanceDir`, `dataDir`)

```bash
curl -sS "$SOLR/admin/cores?action=STATUS&core=$CORE&wt=json"
```

Nếu JSON có khối `"cloud"` → SolrCloud: dùng Collections API (`/solr/admin/collections`), không dùng `UNLOAD` / `CREATE` như mục 2.

### Đếm document

```bash
curl -sS "$SOLR/$CORE/select?q=*:*&rows=0&wt=json"
curl -sS "$SOLR/$CORE/select?q=indexType:META&rows=0&wt=json"
```

### Luke — flag thật trên Lucene (schema API có thể khác index cũ)

```bash
curl -sS "$SOLR/$CORE/admin/luke?fl=otherReceivePlaces&numTerms=10&wt=json"
curl -sS "$SOLR/$CORE/admin/luke?fl=docCode&numTerms=0&wt=json"
```

---

## 2. Core: tạo, reload, unload, tạo lại index

### Reload (đọc lại `solrconfig` / managed-schema, **không** xóa data)

```bash
curl -sS "$SOLR/admin/cores?action=RELOAD&core=$CORE"
```

### Tạo core (thư mục instance đã có `conf/`)

`instanceDir` lấy từ STATUS; thường trùng tên core.

```bash
curl -sS "$SOLR/admin/cores?action=CREATE&name=$CORE&instanceDir=$CORE"
```

Đường dẫn đầy đủ nếu CREATE lỗi (đổi cho đúng máy):

```bash
# Linux (package / tarball)
curl -sS "$SOLR/admin/cores?action=CREATE&name=$CORE&instanceDir=/var/solr/data/$CORE"
# macOS / tarball tự giải nén
curl -sS "$SOLR/admin/cores?action=CREATE&name=$CORE&instanceDir=/opt/solr/server/solr/$CORE"
# Windows
curl -sS "$SOLR/admin/cores?action=CREATE&name=$CORE&instanceDir=C:/solr/server/solr/$CORE"
```

### Unload (gỡ core khỏi Solr, mặc định **giữ** file)

```bash
curl -sS "$SOLR/admin/cores?action=UNLOAD&core=$CORE"
```

| Query | Tác dụng |
|-------|----------|
| (không thêm gì) | Unload, giữ index + conf |
| `deleteIndex=true` | Xóa **index**, giữ `conf` / managed-schema |
| `deleteDataDir=true` | Xóa cả thư mục `data/` |
| `deleteInstanceDir=true` | Xóa luôn conf + schema — **không dùng** trừ khi cố ý |

### Tạo lại index, giữ schema (cách API)

Dừng backfill / eoffice index nếu đang ghi Solr.

```bash
curl -sS "$SOLR/admin/cores?action=UNLOAD&core=$CORE&deleteIndex=true"
curl -sS "$SOLR/admin/cores?action=CREATE&name=$CORE&instanceDir=$CORE"
curl -sS "$SOLR/$CORE/select?q=*:*&rows=0&wt=json"
```

`numFound` phải = 0. Core trống: mất META **và** FILE. Index lại FILE (eoffice) rồi chạy script META.

### Tạo lại index, giữ schema (cách file)

1. `STATUS` → lấy `dataDir`
2. Stop Solr hoặc UNLOAD core:

```bash
# tarball / zip Solr
solr/bin/solr stop -p 8983
# Windows (cùng bộ Solr)
solr\bin\solr.cmd stop -p 8983
# systemd
sudo systemctl stop solr
```

3. Xóa `dataDir/index` và `dataDir/tlog` (nếu có). **Không** xóa `conf/`.

```bash
rm -rf "$DAT_DIR/index" "$DATA_DIR/tlog"
# Windows PowerShell: Remove-Item -Recurse -Force $dataDir\index, $dataDir\tlog
```

4. Start Solr / CREATE lại (`bin/solr start -p 8983` hoặc `solr.cmd start` / `systemctl start solr`).

### Rename / swap

```bash
curl -sS "$SOLR/admin/cores?action=RENAME&core=old_name&other=new_name"
curl -sS "$SOLR/admin/cores?action=SWAP&core=coreA&other=coreB"
```

---

## 3. Schema: xem, thêm, sửa, xóa field

Managed schema (`/schema`). Core phải `Mutable`.

### Xem 1 field

```bash
curl -sS "$SOLR/$CORE/schema/fields/otherReceivePlaces?wt=json"
curl -sS "$SOLR/$CORE/schema/fields/docCode?wt=json"
curl -sS "$SOLR/$CORE/schema/fields/searchText?wt=json"
```

### Liệt kê field / field type

```bash
curl -sS "$SOLR/$CORE/schema/fields?wt=json"
curl -sS "$SOLR/$CORE/schema/fieldtypes?wt=json"
```

### Thêm field

```bash
curl -sS -X POST "$SOLR/$CORE/schema?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"add-field":{"name":"otherReceivePlaces","type":"text_general","indexed":true,"stored":true,"multiValued":false}}'
```

### Sửa field (`replace-field`)

Đổi `type` / `indexed` **không** sửa `FieldInfo` đã có trong index. Doc mới phải cùng index options với doc cũ; không được thì Lucene 400.

```bash
curl -sS -X POST "$SOLR/$CORE/schema?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"replace-field":{"name":"otherReceivePlaces","type":"string","indexed":false,"stored":true,"docValues":false,"multiValued":false}}'
```

- `indexed:true` + `text_general` → inverted index (search ô form).
- `indexed:false` + `stored:true` → chỉ lưu, khớp `FieldInfo=NONE`; search qua `searchText`.

### Xóa field khỏi schema

Không xóa data đã index. Nên xóa doc dùng field đó trước, hoặc tạo lại index.

```bash
curl -sS -X POST "$SOLR/$CORE/schema?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"delete-field":{"name":"otherReceivePlaces"}}'
```

### Copy field (thêm / xóa)

```bash
curl -sS -X POST "$SOLR/$CORE/schema?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"add-copy-field":{"source":"searchText","dest":"_text_"}}'

curl -sS -X POST "$SOLR/$CORE/schema?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"delete-copy-field":{"source":"searchText","dest":"_text_"}}'
```

---

## 4. Document: thêm / sửa / xóa / commit

### Thêm hoặc ghi đè (cùng `id` → upsert)

```bash
curl -sS -X POST "$SOLR/$CORE/update?overwrite=true&commitWithin=10000&wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '[{"id":"OBJECTID_DEPTID_meta","objectId":"OBJECTID","indexType":"META","searchText":"...","docCode":"CV001"}]'
```

Handler JSON docs (script backfill):

```text
POST $SOLR/$CORE/update/json/docs?overwrite=true&wt=json
```

Atomic update 1 field (doc phải đã tồn tại):

```bash
curl -sS -X POST "$SOLR/$CORE/update?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"add":{"doc":{"id":"OBJECTID_DEPTID_meta","searchText":{"set":"NEWIDX"}}}}'
```

### Commit / optimize

```bash
curl -sS -X POST "$SOLR/$CORE/update?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"commit":{}}'

curl -sS -X POST "$SOLR/$CORE/update?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"optimize":{"waitSearcher":true}}'
```

Optimize **không** gỡ `FieldInfo` cũ.

### Xóa theo id

```bash
curl -sS -X POST "$SOLR/$CORE/update?commit=true&wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"delete":{"id":"OBJECTID_DEPTID_meta"}}'
```

### Xóa theo query — chỉ META (script backfill + META Java realtime)

Đếm trước:

```bash
curl -sS "$SOLR/$CORE/select?q=indexType:META&rows=0&wt=json"
curl -sS "$SOLR/$CORE/select?q=*:*&rows=0&wt=json"
```

```bash
curl -sS -X POST "$SOLR/$CORE/update?commit=true&wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"delete":{"query":"indexType:META"}}'
```

Theo tenant:

```bash
curl -sS -X POST "$SOLR/$CORE/update?commit=true&wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"delete":{"query":"indexType:META AND tenantCode:\"bvhttdl.gov.vn\""}}'
```

Nếu `indexType` không search được:

```bash
curl -sS -X POST "$SOLR/$CORE/update?commit=true&wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"delete":{"query":"id:*_meta"}}'
```

Sau đó `q=indexType:META` → `numFound=0`. `q=*:*` vẫn > 0 nếu còn FILE.

### Xóa toàn bộ document (vẫn **không** reset FieldInfo)

```bash
curl -sS -X POST "$SOLR/$CORE/update?commit=true&wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"delete":{"query":"*:*"}}'
```

---

## 5. Query

```bash
curl -sS "$SOLR/$CORE/select?q=searchText:CV001&rows=5&fl=id,objectId,indexType,docCode&wt=json"
curl -sS "$SOLR/$CORE/select?q=id:OBJECTID_DEPTID_meta&wt=json"
curl -sS "$SOLR/$CORE/select?q=otherReceivePlaces:*&rows=0&wt=json"
```

---

## 6. Lỗi thường gặp

### `cannot change field "X" from index options=NONE to ... POSITIONS`

Field đã tồn tại trên Lucene kiểu stored-only. Schema `text_general indexed=true` không sửa được index cũ.

- Ghi tiếp: `replace-field` `indexed:false` **hoặc** không gửi field đó (vẫn nằm trong `searchText`).
- Muốn search inverted-index field đó: tạo lại **index** (mục 2) trên **đúng** host, rồi reindex FILE + META.

### `Error adding field 'otherReceivePlaces'='BOCONGAN,...'`

Giá trị quá dài (một term Lucene tối đa 32766). Log hay cắt mất `msg=`. Tách từng nơi nhận, ghép bằng dấu cách; hoặc stored-only.

### `unknown field`

Schema chưa có field, schemaless tắt. `add-field` rồi ghi lại.

### `connection refused` / proxy 503

Solr nội bộ. Bypass proxy: `export NO_PROXY=localhost,127.0.0.1,10.0.0.0/8` (bash) hoặc tắt proxy hệ thống cho dải đó. Script backfill đã tắt HTTP(S)_PROXY khi gọi Solr.

---

## 7. SolrCloud (nếu STATUS có `cloud`)

Không UNLOAD core lẻ.

```bash
curl -sS "$SOLR/admin/collections?action=LIST&wt=json"
curl -sS "$SOLR/admin/collections?action=DELETE&name=$CORE"
curl -sS "$SOLR/admin/collections?action=CREATE&name=$CORE&numShards=1&replicationFactor=1&collection.configName=_default"
```

`DELETE` collection xóa hết data collection đó. `configName` phải đúng configset eoffice, không dùng `_default` nếu core đang chạy schema riêng.

---

## 8. Checklist backfill META

1. Đúng `SOLR_HOST` / `SOLR_CORE` (`localhost` và IP LAN là hai Solr khác nhau).
2. Chỉ reset data script: xóa `indexType:META`, không UNLOAD.
3. Lỗi `index options` trên `otherReceivePlaces`: xóa META **không** đủ; tạo lại index trên đúng host hoặc ghi stored-only / bỏ field.
4. Sau UNLOAD+CREATE: eoffice index lại FILE, rồi:

```bash
python3 solr_doc_meta_backfill.py --source NEW
# Windows venv: python solr_doc_meta_backfill.py --source NEW
```
