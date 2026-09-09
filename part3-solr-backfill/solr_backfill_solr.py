from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from base64 import b64encode
from typing import Dict, List, Optional
from urllib.parse import urlencode


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
        query = urlencode({"commitWithin": str(commit_within_ms), "overwrite": "true", "wt": "json"})
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
            detail = ex.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"Solr HTTP {ex.code} {url}: {detail}") from ex
        except urllib.error.URLError as ex:
            raise RuntimeError(f"Solr connection failed {url}: {ex.reason}") from ex
