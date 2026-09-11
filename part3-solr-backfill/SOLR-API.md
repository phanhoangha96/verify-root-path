# Solr HTTP API — core, schema, index

Hướng dẫn gọi API Solr **standalone** (Core Admin) trên Linux, macOS và Windows. Khớp backfill META eoffice.

Mọi lệnh dùng `curl` + biến `SOLR` / `CORE`. Đổi host cho đúng môi trường (`.env`: `SOLR_HOST`, `SOLR_CORE`).

Standalone: `/solr/admin/cores`. SolrCloud: `/solr/admin/collections` (không CREATE core lẻ).

Nội dung: [Shell](#shell) · [Tạo core từng bước](#hướng-dẫn-tạo-core--từng-bước) · [Core Admin](#2-core-admin--tạo-sửa-xóa-core) · [ConfigSet](#3-configset) · [Backup / snapshot](#4-backup--restore--snapshot) · [Schema](#5-schema-xem-thêm-sửa-xóa-field) · [Document](#6-document-thêm--sửa--xóa--commit) · [Query](#7-query) · [SolrCloud](#9-solrcloud)

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

## 2. Core Admin — tạo, sửa, xóa core

Endpoint: `$SOLR/admin/cores?action=...` (`action` viết HOA). Không gắn vào một core; quản lý **mọi** core trên node.

CLI: `bin/solr create|delete|status` (Windows: `bin\solr.cmd ...`).

### CREATE — tạo core mới

`name` **bắt buộc**. Không dùng tham số `core`. File `core.properties` **không** được tồn tại sẵn — CREATE sẽ tạo; nếu đã có thì lỗi “core already defined”.

SolrCloud: **không** CREATE core lẻ — [mục 9](#9-solrcloud-nếu-status-có-cloud).

#### Hướng dẫn tạo core — từng bước

Chọn **một** hướng:

| Mục tiêu | Dùng |
|----------|------|
| Core **trống từ đầu** (schemaless `_default`) | Cách 1 |
| Core search eoffice / cùng schema `truclt_agency` | Cách 2 |
| Đã có sẵn thư mục `conf/` trên đĩa | Cách 3 |

---

**Bước 0 — trên máy Solr** (SSH; không dán `curl -sS "$SOLR/..."` nguyên xi vào Postman).

```bash
export SOLR="http://localhost:8983/solr"
export SOLR_HOME=/var/solr/data          # Docker/package; tarball thường /opt/solr/server/solr
export NEW_CORE=onecst-document          # đổi tên core
# user Solr: thường solr — lệnh ghi đĩa dùng sudo -u solr nếu cần
```

Ping + chưa có core trùng tên + không phải SolrCloud:

```bash
curl -sS "$SOLR/admin/ping?wt=json"
curl -sS "$SOLR/admin/cores?action=STATUS&wt=json"
curl -sS "$SOLR/admin/cores?action=STATUS&core=$NEW_CORE&wt=json"
```

STATUS có khối `"cloud"` → dừng, dùng Collections API. STATUS core mới trả 404 / không có tên đó → được. `ls "$SOLR_HOME"` phải thấy các core cũ (`truclt_agency`, …).

---

**Cách 1 — core trống từ đầu (`_default`)**

Docker/package hay lỗi `Could not load configuration from directory /var/solr/data/configsets/_default` vì bộ `_default` nằm ở `/opt/solr/server/solr/configsets/`, chưa copy sang `SOLR_HOME`.

1. Tìm `_default` trên đĩa:

```bash
ls "$SOLR_HOME/configsets/_default/conf"
ls /opt/solr/server/solr/configsets/_default/conf
find /opt/solr /var/solr -type d -name '_default' 2>/dev/null
```

Phải có `solrconfig.xml` trong `.../_default/conf/`.

2. Nếu `$SOLR_HOME/configsets/_default` **không** có — copy:

```bash
sudo mkdir -p "$SOLR_HOME/configsets"
sudo cp -a /opt/solr/server/solr/configsets/_default "$SOLR_HOME/configsets/"
sudo chown -R solr:solr "$SOLR_HOME/configsets"
ls "$SOLR_HOME/configsets/_default/conf/solrconfig.xml"
```

Đường dẫn nguồn khác (tarball): `/opt/solr/server/solr/configsets/_default`.

3. CREATE (API). Làm **sau** bước 2.

```bash
curl -sS "$SOLR/admin/cores?action=CREATE&name=$NEW_CORE&configSet=_default&wt=json"
```

Hoặc CLI:

```bash
bin/solr create -c "$NEW_CORE" -n _default
# Windows: bin\solr.cmd create -c onecst-document -n _default
```

4. Kiểm tra (mục **Sau khi CREATE** bên dưới).

Core này **schemaless trống**: chưa có `searchText` / `indexType`. Backfill hoặc Java ghi lần đầu mới tạo field. Search form eoffice có thể lệch — cần Cách 2 nếu phải giống `truclt_agency`.

---

**Cách 2 — copy `conf` từ core eoffice đang chạy** (index mới trống, schema giống)

1. Xác định core nguồn (STATUS / `ls "$SOLR_HOME"`), ví dụ `truclt_agency`.

```bash
export SRC_CORE=truclt_agency
ls "$SOLR_HOME/$SRC_CORE/conf/solrconfig.xml"
ls "$SOLR_HOME/$SRC_CORE/conf/"
```

2. Copy **chỉ** `conf/` — không copy `core.properties`, không copy `data/`.

```bash
sudo mkdir -p "$SOLR_HOME/$NEW_CORE/conf"
sudo cp -a "$SOLR_HOME/$SRC_CORE/conf/." "$SOLR_HOME/$NEW_CORE/conf/"
sudo chown -R solr:solr "$SOLR_HOME/$NEW_CORE"
ls "$SOLR_HOME/$NEW_CORE/conf/solrconfig.xml"
```

3. CREATE bằng `instanceDir` (tên thư mục = tên core, relative `SOLR_HOME`):

```bash
curl -sS "$SOLR/admin/cores?action=CREATE&name=$NEW_CORE&instanceDir=$NEW_CORE&wt=json"
```

---

**Cách 3 — đã chuẩn bị `conf/` trên đĩa**

`instanceDir` phải nằm trong `SOLR_HOME` / `SOLR_DATA_HOME` / `solr.security.allow.paths`.

```bash
curl -sS "$SOLR/admin/cores?action=CREATE&name=$NEW_CORE&instanceDir=$NEW_CORE&wt=json"
curl -sS "$SOLR/admin/cores?action=CREATE&name=$NEW_CORE&instanceDir=/var/solr/data/$NEW_CORE&wt=json"
curl -sS "$SOLR/admin/cores?action=CREATE&name=$NEW_CORE&instanceDir=/opt/solr/server/solr/$NEW_CORE&wt=json"
curl.exe -sS "$SOLR/admin/cores?action=CREATE&name=$NEW_CORE&instanceDir=C:/solr/server/solr/$NEW_CORE&wt=json"
```

---

**Sau khi CREATE — kiểm tra**

JSON `responseHeader.status` = 0, không có `error`.

```bash
curl -sS "$SOLR/admin/cores?action=STATUS&core=$NEW_CORE&wt=json"
curl -sS "$SOLR/$NEW_CORE/admin/ping?wt=json"
curl -sS "$SOLR/$NEW_CORE/select?q=*:*&rows=0&wt=json"
```

`numFound` = 0. Admin UI: `http://<host>:8983/solr/#/$NEW_CORE`.

---

**Postman** (không dán cả dòng `curl` / `$SOLR`)

1. SSH làm xong bước copy `conf` hoặc copy `_default` (Cách 1 bước 2 / Cách 2 bước 2).
2. Method **GET**.
3. URL: `http://<host>:8983/solr/admin/cores`
4. Params:

| KEY | VALUE (Cách 1) | VALUE (Cách 2 / 3) |
|-----|----------------|---------------------|
| `action` | `CREATE` | `CREATE` |
| `name` | `onecst-document` | `onecst-document` |
| `wt` | `json` | `json` |
| `configSet` | `_default` | *(bỏ)* |
| `instanceDir` | *(bỏ)* | `onecst-document` |

Import curl: thay `$SOLR` thành URL thật trước, ví dụ:

```text
curl -sS "http://10.16.150.233:8983/solr/admin/cores?action=CREATE&name=onecst-document&configSet=_default&wt=json"
```

---

**Tham số CREATE**

| Tham số | Bắt buộc | Ý nghĩa |
|---------|----------|---------|
| `name` | Có | Tên core |
| `instanceDir` | Không | Thư mục core; mặc định = `name` |
| `configSet` | Không | `_default` hoặc tên bộ trong `$SOLR_HOME/configsets/` |
| `config` | Không | File config, mặc định `solrconfig.xml` |
| `schema` | Không | File schema (managed schema có thể convert) |
| `dataDir` | Không | Thư mục data, mặc định `data` |
| `property.KEY=VALUE` | Không | Ghi `core.properties` |
| `async` | Không | Chạy nền; kiểm tra `REQUESTSTATUS` |

---

**Lỗi CREATE thường gặp**

| `error.msg` | Việc cần làm |
|-------------|--------------|
| `Could not load configuration from directory .../configsets/_default` | Cách 1 bước 2: copy `_default` vào `$SOLR_HOME/configsets/` |
| `Core with name '...' already exists` | Đổi `name` hoặc UNLOAD core cũ |
| `already defined` / `core.properties` | Xóa `core.properties` trong instance dir **hoặc** dùng thư mục trống; không CREATE đè |
| `Not found` `conf/solrconfig.xml` | Cách 2/3: copy `conf/` trước khi gọi API |
| `instanceDir` / `allow.paths` | `instanceDir` phải dưới `SOLR_HOME` |
| STATUS có `"cloud"` | Dùng Collections `CREATE` (mục 9), không Core Admin |

---
### RELOAD — áp config trên đĩa, không xóa data

`dataDir` và một số mục IndexWriter **không** đổi được bằng RELOAD.

```bash
curl -sS "$SOLR/admin/cores?action=RELOAD&core=$CORE&wt=json"
```

### UNLOAD — gỡ core

```bash
curl -sS "$SOLR/admin/cores?action=UNLOAD&core=$CORE&wt=json"
```

| Query | Tác dụng |
|-------|----------|
| (không thêm) | Unload, giữ index + conf |
| `deleteIndex=true` | Xóa **index**, giữ `conf` |
| `deleteDataDir=true` | Xóa thư mục `data/` |
| `deleteInstanceDir=true` | Xóa conf + schema — chỉ khi cố ý |

```bash
bin/solr delete -c "$CORE"
```

`bin/solr delete` thường xóa cả instance dir — khác UNLOAD không cờ.

### Tạo lại index, giữ schema

```bash
curl -sS "$SOLR/admin/cores?action=UNLOAD&core=$CORE&deleteIndex=true&wt=json"
curl -sS "$SOLR/admin/cores?action=CREATE&name=$CORE&instanceDir=$CORE&wt=json"
curl -sS "$SOLR/$CORE/select?q=*:*&rows=0&wt=json"
```

`numFound` = 0. Mất META và FILE. Index lại FILE rồi script META.

Hoặc: stop Solr → xóa `$DATA_DIR/index` và `$DATA_DIR/tlog` (không xóa `conf/`) → start.

```bash
bin/solr stop -p 8983
rm -rf "$DATA_DIR/index" "$DATA_DIR/tlog"
bin/solr start -p 8983
```

### RENAME / SWAP

SWAP đổi tên hai core nguyên tử (đưa index mới lên, rollback được).

```bash
curl -sS "$SOLR/admin/cores?action=RENAME&core=$CORE&other=truclt_agency_old&wt=json"
curl -sS "$SOLR/admin/cores?action=SWAP&core=$CORE&other=truclt_agency_new&wt=json"
```

### MERGEINDEXES / SPLIT

Schema nguồn và đích phải tương thích. MERGE: commit nguồn, khóa ghi, commit đích sau merge.

```bash
curl -sS "$SOLR/admin/cores?action=MERGEINDEXES&core=target_core&srcCore=core1&srcCore=core2&wt=json"
curl -sS "$SOLR/admin/cores?action=SPLIT&core=core0&targetCore=core1&targetCore=core2&wt=json"
```

### REQUESTSTATUS / REQUESTRECOVERY

```bash
curl -sS "$SOLR/admin/cores?action=REQUESTSTATUS&requestid=JOB_ID&wt=json"
curl -sS "$SOLR/admin/cores?action=REQUESTRECOVERY&core=$CORE&wt=json"
```

---

## 3. ConfigSet

Bộ `solrconfig.xml` + schema. CREATE core dùng `configSet=...`. Standalone: `server/solr/configsets/<tên>/conf/`. SolrCloud: ZooKeeper.

```bash
curl -sS "$SOLR/admin/configs?action=LIST&wt=json"
curl -sS "$SOLR/admin/configs?action=CREATE&name=eoffice_copy&baseConfigSet=_default&wt=json"
curl -sS -X POST "$SOLR/admin/configs?action=UPLOAD&name=eoffice_document" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @eoffice-conf.zip
curl -sS "$SOLR/admin/configs?action=DELETE&name=eoffice_copy&wt=json"
```

`DELETE` configset không xóa collection/core đang dùng bộ đó. Zip upload chứa thư mục `conf/`.

---

## 4. Backup / restore / snapshot

### Snapshot (cùng thư mục index)

```bash
curl -sS "$SOLR/admin/cores?action=CREATESNAPSHOT&core=$CORE&commitName=snap1&wt=json"
curl -sS "$SOLR/admin/cores?action=LISTSNAPSHOTS&core=$CORE&wt=json"
curl -sS "$SOLR/admin/cores?action=DELETESNAPSHOT&core=$CORE&commitName=snap1&wt=json"
```

### Backup / restore standalone — Replication Handler

```bash
curl -sS "$SOLR/$CORE/replication?command=backup&name=meta_20260911&wt=json"
curl -sS "$SOLR/$CORE/replication?command=details&wt=json"
curl -sS "$SOLR/$CORE/replication?command=restore&name=meta_20260911&wt=json"
curl -sS "$SOLR/$CORE/replication?command=restorestatus&wt=json"
```

Thư mục `snapshot.<tên>` dưới data dir. SolrCloud dùng Collections `BACKUP` / `RESTORE` (mục 9).

---

## 5. Schema: xem, thêm, sửa, xóa field

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

### Field type / uniqueKey / dynamic field

```bash
curl -sS "$SOLR/$CORE/schema/uniquekey?wt=json"
curl -sS "$SOLR/$CORE/schema/copyfields?wt=json"
curl -sS "$SOLR/$CORE/schema/dynamicfields?wt=json"

curl -sS -X POST "$SOLR/$CORE/schema?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"add-field-type":{"name":"text_vi","class":"solr.TextField","positionIncrementGap":"100"}}'

curl -sS -X POST "$SOLR/$CORE/schema?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"delete-field-type":{"name":"text_vi"}}'

curl -sS -X POST "$SOLR/$CORE/schema?wt=json" \
  -H "Content-Type: application/json" \
  --data-binary '{"add-dynamic-field":{"name":"*_s","type":"string","indexed":true,"stored":true}}'
```

Sau khi sửa schema trên đĩa (không qua API): `RELOAD` core.

---

## 6. Document: thêm / sửa / xóa / commit

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

## 7. Query

```bash
curl -sS "$SOLR/$CORE/select?q=searchText:CV001&rows=5&fl=id,objectId,indexType,docCode&wt=json"
curl -sS "$SOLR/$CORE/select?q=id:OBJECTID_DEPTID_meta&wt=json"
curl -sS "$SOLR/$CORE/select?q=otherReceivePlaces:*&rows=0&wt=json"
```

---

## 8. Lỗi thường gặp

### `cannot change field "X" from index options=NONE to ... POSITIONS`

Field đã tồn tại trên Lucene kiểu stored-only. Schema `text_general indexed=true` không sửa được index cũ.

- Ghi tiếp: `replace-field` `indexed:false` **hoặc** không gửi field đó (vẫn nằm trong `searchText`).
- Muốn search inverted-index field đó: tạo lại **index** (mục 2) trên **đúng** host, rồi reindex FILE + META.

### `Error adding field 'otherReceivePlaces'='BOCONGAN,...'`

Giá trị quá dài (một term Lucene tối đa 32766). Log hay cắt mất `msg=`. Tách từng nơi nhận, ghép bằng dấu cách; hoặc stored-only.

### `Could not load configuration from directory .../configsets/_default`

`configSet=_default` nhưng Solr (Docker: `SOLR_HOME=/var/solr/data`) không có thư mục đó. Copy `_default` từ `/opt/solr/server/solr/configsets/` vào `$SOLR_HOME/configsets/`, **hoặc** CREATE bằng `instanceDir` sau khi copy `conf/` từ core eoffice sẵn (không dùng `_default` cho search văn bản).

### `unknown field`

Schema chưa có field, schemaless tắt. `add-field` rồi ghi lại.

### `connection refused` / proxy 503

Solr nội bộ. Bypass proxy: `export NO_PROXY=localhost,127.0.0.1,10.0.0.0/8` (bash) hoặc tắt proxy hệ thống cho dải đó. Script backfill đã tắt HTTP(S)_PROXY khi gọi Solr.

---

## 9. SolrCloud (nếu STATUS có `cloud`)

Không UNLOAD/CREATE core lẻ. Dùng collection + configset trên ZooKeeper.

```bash
curl -sS "$SOLR/admin/collections?action=LIST&wt=json"
curl -sS "$SOLR/admin/collections?action=CLUSTERSTATUS&wt=json"
curl -sS "$SOLR/admin/collections?action=COLSTATUS&collection=$CORE&wt=json"

# Tạo collection (configName = configset đã upload)
curl -sS "$SOLR/admin/collections?action=CREATE&name=truclt_agency&numShards=1&replicationFactor=1&collection.configName=eoffice_document&wt=json"

# Reload sau khi sửa config trên ZK
curl -sS "$SOLR/admin/collections?action=RELOAD&name=$CORE&wt=json"

# Replica
curl -sS "$SOLR/admin/collections?action=ADDREPLICA&collection=$CORE&shard=shard1&wt=json"
curl -sS "$SOLR/admin/collections?action=DELETEREPLICA&collection=$CORE&shard=shard1&replica=core_node2&wt=json"

# Backup / restore collection
curl -sS "$SOLR/admin/collections?action=BACKUP&name=bk_20260911&collection=$CORE&location=/solr_backups&wt=json"
curl -sS "$SOLR/admin/collections?action=RESTORE&name=bk_20260911&location=/solr_backups&collection=truclt_agency_restored&wt=json"

# Xóa collection (xóa hết data collection đó)
curl -sS "$SOLR/admin/collections?action=DELETE&name=$CORE&wt=json"
```

`collection.configName` phải đúng configset eoffice, không dùng `_default` nếu schema đang chạy là bộ riêng.

CLI:

```bash
bin/solr create -c truclt_agency -n eoffice_document -s 1 -rf 1
bin/solr delete -c truclt_agency
```

---

## 10. Checklist backfill META

1. Đúng `SOLR_HOST` / `SOLR_CORE` (`localhost` và IP LAN là hai Solr khác nhau).
2. Chỉ reset data script: xóa `indexType:META`, không UNLOAD.
3. Lỗi `index options` trên `otherReceivePlaces`: xóa META **không** đủ; tạo lại index trên đúng host hoặc ghi stored-only / bỏ field.
4. Sau UNLOAD+CREATE: eoffice index lại FILE, rồi:

```bash
python3 solr_doc_meta_backfill.py --source NEW
# Windows venv: python solr_doc_meta_backfill.py --source NEW
```
