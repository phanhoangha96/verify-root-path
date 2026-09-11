from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from base64 import b64encode
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

# Lucene MAX_TERM_LENGTH. searchText is one token (whitespace stripped).
MAX_SOLR_TERM_LENGTH = 32766
# quote / docCode stay one token after normalize — keep them short.
MAX_SOLR_DEDICATED_FIELD = 4000
# otherReceivePlaces is tokenized (spaces between places); stored value can be large.
MAX_SOLR_STORED_FIELD = 1_048_576
DEDICATED_TEXT_FIELDS = (
    "searchText",
    "docCode",
    "quote",
    "outsidePublisherName",
    "otherReceivePlaces",
    "bookNumber",
)
_OPTIONAL_ON_RETRY = ("otherReceivePlaces",)

AddDocsFn = Callable[[List[Dict], int], None]
# (index, reason, detail)
SplitFailure = Tuple[int, str, str]


def clip_solr_term(value: Optional[str], max_len: int = MAX_SOLR_TERM_LENGTH) -> Optional[str]:
    if value is None or value == "":
        return value
    if len(value) <= max_len:
        return value
    return value[:max_len]


def clip_solr_doc(doc: Dict, object_id: str = "", log=None) -> Dict:
    clipped: Dict = {}
    for key, value in doc.items():
        if value in (None, ""):
            continue
        if isinstance(value, str):
            if key == "otherReceivePlaces":
                limit = MAX_SOLR_STORED_FIELD
            elif key in {"docCode", "quote", "outsidePublisherName", "bookNumber"}:
                limit = MAX_SOLR_DEDICATED_FIELD
            else:
                limit = MAX_SOLR_TERM_LENGTH
            trimmed = clip_solr_term(value, limit)
            if log and trimmed is not None and len(trimmed) < len(value):
                log(f"    WARN truncated {key} objectId={object_id} {len(value)}->{len(trimmed)}")
            clipped[key] = trimmed
        else:
            clipped[key] = value
    return clipped


def compact_solr_msg(message: str, max_len: int = 500) -> str:
    """Drop huge field values Solr echoes so the real cause (msg=...) is visible."""
    text = message
    if "Error adding field" in text and "msg=" in text:
        head = text.split("Error adding field", 1)[0]
        tail = text.rsplit("msg=", 1)[-1]
        match = re.search(r"Error adding field '([^']+)'", text)
        name = match.group(1) if match else "?"
        text = f"{head}Error adding field '{name}' msg={tail}"
    elif "Error adding field" in text:
        match = re.search(r"ERROR: \[doc=([^\]]+)\].*Error adding field '([^']+)'", text)
        if match:
            text = f"ERROR: [doc={match.group(1)}] Error adding field '{match.group(2)}'"
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
    """On batch failure, split in half instead of retrying every document."""
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
                doc = dict(chunk[0])
                object_id = doc.get("objectId", "")
                dropped = [name for name in _OPTIONAL_ON_RETRY if name in doc]
                if dropped:
                    for name in dropped:
                        doc.pop(name, None)
                    try:
                        add_docs([doc], commit_within_ms)
                        if log:
                            log(
                                f"    WARN dropped {','.join(dropped)} objectId={object_id} "
                                f"(Solr rejected the field); indexed without it"
                            )
                        ok.append(start)
                        return
                    except Exception as retry_ex:
                        if is_solr_unreachable(retry_ex):
                            detail = compact_solr_msg(str(retry_ex))
                            if log:
                                log(f"    ERROR Solr unreachable objectId={object_id}: {detail}")
                            failed.append((start, "SOLR_UNREACHABLE", detail))
                            return
                        ex = retry_ex
                detail = compact_solr_msg(str(ex))
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
        timeout: int = 120,
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
        for name in DEDICATED_TEXT_FIELDS:
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
        self._request("POST", url, json.dumps(docs).encode("utf-8"), "application/json")

    def commit(self) -> None:
        url = f"{self.host}/{self.core}/update?wt=json"
        self._request("POST", url, b'{"commit":{}}', "application/json")

    def _request(
        self,
        method: str,
        url: str,
        body: Optional[bytes] = None,
        content_type: str = "",
    ) -> bytes:
        req = urllib.request.Request(url, data=body, method=method)
        if content_type:
            req.add_header("Content-Type", content_type)
        if self.user:
            token = b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {token}")
        ctx = ssl.create_default_context()
        # Bypass HTTP(S)_PROXY / Windows system proxy. Solr is an internal service
        # (often localhost); corporate Squid intercepts urllib and returns 503 HTML.
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
