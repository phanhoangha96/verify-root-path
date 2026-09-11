from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from base64 import b64encode
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlencode

MAX_FILE_CONTENT_LENGTH = 1_000_000
FILE_TEXT_FIELDS = (
    "fileContent",
    "fileServiceId",
    "objectId",
    "deptId",
    "tenantCode",
)

AddDocsFn = Callable[[List[Dict], int], None]
SplitFailure = Tuple[int, str, str]


def escape_solr_query(value: str) -> str:
    return re.sub(r'([+\-&|!(){}[\]^"~*?:\\/])', r"\\\1", value)


def compact_solr_msg(message: str, max_len: int = 500) -> str:
    text = message
    if "Error adding field" in text and "msg=" in text:
        head = text.split("Error adding field", 1)[0]
        tail = text.rsplit("msg=", 1)[-1]
        match = re.search(r"Error adding field '([^']+)'", text)
        name = match.group(1) if match else "?"
        text = f"{head}Error adding field '{name}' msg={tail}"
    return text[:max_len]


def is_solr_unreachable(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "connection failed" in msg
        or "connection refused" in msg
        or "network is unreachable" in msg
        or "solr http 401" in msg
        or "solr http 403" in msg
    )


def add_docs_with_split(
    add_docs: AddDocsFn,
    docs: Sequence[Dict],
    commit_within_ms: int,
    log=None,
) -> Tuple[List[int], List[SplitFailure]]:
    ok: List[int] = []
    failed: List[SplitFailure] = []

    def rec(start: int, end: int) -> None:
        if start >= end:
            return
        chunk = list(docs[start:end])
        try:
            add_docs(chunk, commit_within_ms)
            ok.extend(range(start, end))
            return
        except Exception as ex:
            if is_solr_unreachable(ex):
                detail = compact_solr_msg(str(ex))
                if log:
                    log(f"    ERROR Solr unreachable ({end - start} docs): {detail}")
                for i in range(start, end):
                    failed.append((i, "SOLR_UNREACHABLE", detail))
                return
            if end - start == 1:
                detail = compact_solr_msg(str(ex))
                object_id = chunk[0].get("objectId", "")
                if log:
                    log(f"    ERROR SOLR_WRITE_FAILED objectId={object_id}: {detail}")
                failed.append((start, "SOLR_WRITE_FAILED", detail))
                return
            if log:
                log(
                    f"    WARN Solr batch size={end - start} failed, splitting: "
                    f"{compact_solr_msg(str(ex))}"
                )
            mid = start + (end - start) // 2
            rec(start, mid)
            rec(mid, end)

    rec(0, len(docs))
    return ok, failed


class SolrClient:
    def __init__(
        self,
        host: str,
        core: str,
        user: str = "",
        password: str = "",
        timeout: int = 300,
    ) -> None:
        self.host = host.rstrip("/")
        self.core = core
        self.user = user
        self.password = password
        self.timeout = timeout

    def ping(self) -> None:
        url = f"{self.host}/{self.core}/admin/ping?wt=json"
        self._request("GET", url)

    def ensure_text_fields(self, log=None) -> None:
        for name in FILE_TEXT_FIELDS:
            try:
                self._ensure_text_field(name)
            except Exception as ex:
                if log:
                    log(f"    WARN Solr field {name}: {compact_solr_msg(str(ex))}")

    def add_docs(self, docs: List[Dict], commit_within_ms: int) -> None:
        if not docs:
            return
        params = {"overwrite": "true", "wt": "json"}
        if commit_within_ms > 0:
            params["commitWithin"] = str(commit_within_ms)
        query = urlencode(params)
        url = f"{self.host}/{self.core}/update/json/docs?{query}"
        self._request("POST", url, json.dumps(docs, ensure_ascii=False).encode("utf-8"), "application/json")

    def commit(self) -> None:
        url = f"{self.host}/{self.core}/update?wt=json"
        self._request("POST", url, b'{"commit":{}}', "application/json")

    def existing_file_keys(self, file_service_ids: Iterable[str]) -> Set[Tuple[str, str]]:
        ids = [item for item in dict.fromkeys(file_service_ids) if item]
        found: Set[Tuple[str, str]] = set()
        chunk_size = 50
        for offset in range(0, len(ids), chunk_size):
            chunk = ids[offset : offset + chunk_size]
            quoted = " OR ".join(f'"{escape_solr_query(item)}"' for item in chunk)
            start = 0
            rows = 1000
            while True:
                params = {
                    "q": "*:*",
                    "fq": f"fileServiceId:({quoted})",
                    "fl": "fileServiceId,deptId",
                    "rows": str(rows),
                    "start": str(start),
                    "wt": "json",
                }
                url = f"{self.host}/{self.core}/select?{urlencode(params)}"
                payload = json.loads(self._request("GET", url).decode("utf-8"))
                docs = (payload.get("response") or {}).get("docs") or []
                for doc in docs:
                    file_id = str(doc.get("fileServiceId") or "").strip()
                    dept_id = str(doc.get("deptId") or "").strip()
                    if file_id and dept_id:
                        found.add((file_id, dept_id))
                if len(docs) < rows:
                    break
                start += rows
        return found

    def _request(
        self,
        method: str,
        url: str,
        body: Optional[bytes] = None,
        content_type: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> bytes:
        req = urllib.request.Request(url, data=body, method=method)
        if content_type:
            req.add_header("Content-Type", content_type)
        if extra_headers:
            for key, value in extra_headers.items():
                req.add_header(key, value)
        if self.user:
            token = b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {token}")
        ctx = ssl.create_default_context()
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ctx),
        )
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as ex:
            raw = ex.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Solr HTTP {ex.code}: {_solr_error_detail(raw)}") from ex
        except urllib.error.URLError as ex:
            raise RuntimeError(f"Solr connection failed {url}: {ex.reason}") from ex

    def _ensure_text_field(self, name: str) -> None:
        spec = {
            "name": name,
            "type": "text_general",
            "indexed": True,
            "stored": True,
            "multiValued": False,
        }
        existing = self._get_field(name)
        if existing:
            if str(existing.get("type") or "") == "text_general":
                return
            self._schema_post({"replace-field": spec})
            return
        self._schema_post({"add-field": spec})

    def _get_field(self, name: str) -> Optional[Dict]:
        url = f"{self.host}/{self.core}/schema/fields/{name}?wt=json"
        try:
            raw = self._request("GET", url)
        except RuntimeError as ex:
            if "Solr HTTP 404" in str(ex):
                return None
            raise
        payload = json.loads(raw.decode("utf-8"))
        field = payload.get("field")
        return field if isinstance(field, dict) else None

    def _schema_post(self, body: Dict) -> None:
        url = f"{self.host}/{self.core}/schema?wt=json"
        self._request("POST", url, json.dumps(body).encode("utf-8"), "application/json")


def _solr_error_detail(raw: str) -> str:
    try:
        payload = json.loads(raw)
        err = payload.get("error") or {}
        msg = str(err.get("msg") or err.get("trace") or raw)
        return compact_solr_msg(msg)
    except Exception:
        return compact_solr_msg(raw)
