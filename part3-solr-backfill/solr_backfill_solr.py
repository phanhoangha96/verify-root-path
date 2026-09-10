from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from base64 import b64encode
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

# Lucene MAX_TERM_LENGTH. searchText / otherReceivePlaces are one token
# (whitespace already stripped), so anything longer is rejected and used to
# fail the whole Solr batch.
MAX_SOLR_TERM_LENGTH = 32766

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
            trimmed = clip_solr_term(value)
            if log and trimmed is not None and len(trimmed) < len(value):
                log(f"    WARN truncated {key} objectId={object_id} {len(value)}->{len(trimmed)}")
            clipped[key] = trimmed
        else:
            clipped[key] = value
    return clipped


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
                if log:
                    log(f"    ERROR Solr unreachable ({end - start} docs): {ex}"[:400])
                detail = str(ex)[:500]
                for i in range(start, end):
                    failed.append((i, "SOLR_UNREACHABLE", detail))
                return
            if end - start == 1:
                object_id = chunk[0].get("objectId", "")
                if log:
                    log(f"    ERROR SOLR_WRITE_FAILED objectId={object_id}: {ex}"[:400])
                failed.append((start, "SOLR_WRITE_FAILED", str(ex)[:500]))
                return
            if log:
                log(f"    WARN Solr batch size={end - start} failed, splitting: {ex}"[:400])
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


def _solr_error_detail(raw: str) -> str:
    try:
        payload = json.loads(raw)
        err = payload.get("error") or {}
        msg = err.get("msg") or err.get("trace") or raw
        return str(msg)[:800]
    except Exception:
        return raw[:800]
