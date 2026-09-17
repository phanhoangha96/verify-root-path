"""Extract file text the same way as eoffice-solr DocServiceImpl.addDocument.

Java:
  String content = TextUtils.removeVietnameseAccents(
      TikaAnalysis.extractContentUsingParser(fileInputStream));

Backends (first match wins):
  1. TIKA_SERVER_URL — Apache Tika Server HTTP (PRD: máy Tika riêng)
  2. TIKA_APP_JAR — local `java -jar tika-app.jar -t`
  3. package `tika` — auto-start local tika-server (cần Java)
"""

from __future__ import annotations

import os
import ssl
import subprocess
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

# Java BodyContentHandler default write limit (Tika 1.x–3.x).
DEFAULT_WRITE_LIMIT = 100000
DEFAULT_TIKA_SERVER_TIMEOUT = 120

TIKA_BACKEND_SERVER = "server"
TIKA_BACKEND_JAR = "jar"
TIKA_BACKEND_LOCAL = "local"

_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class TikaExtractError(Exception):
    pass


def clip_content(text: str, write_limit: int) -> str:
    if write_limit > 0 and len(text) > write_limit:
        return text[:write_limit]
    return text


def remove_vietnamese_accents(value: str) -> str:
    """Match eoffice-solr TextUtils.removeVietnameseAccents.

    NFD, drop Unicode non-spacing marks (Mn), map đ/Đ → d/D.
    Preserves case, whitespace, and punctuation (unlike part3 searchText).
    """
    if value is None or value == "":
        return value
    nfd = unicodedata.normalize("NFD", value)
    out = []
    for ch in nfd:
        if unicodedata.category(ch) == "Mn":
            continue
        if ch == "đ":
            out.append("d")
        elif ch == "Đ":
            out.append("D")
        else:
            out.append(ch)
    return "".join(out)


def normalize_tika_server_url(url: str) -> str:
    """Base URL of tika-server, e.g. http://host:9998.

    Accepts `http://host:9998`, `http://host:9998/`, or `http://host:9998/tika`.
    """
    base = (url or "").strip().rstrip("/")
    if base.lower().endswith("/tika"):
        base = base[:-5].rstrip("/")
    return base


def tika_put_url(server_url: str) -> str:
    base = normalize_tika_server_url(server_url)
    if not base:
        raise TikaExtractError("TIKA_SERVER_URL is empty")
    return f"{base}/tika"


def resolve_tika_backend(server_url: str = "", tika_app_jar: str = "") -> str:
    if (server_url or "").strip():
        return TIKA_BACKEND_SERVER
    jar = (tika_app_jar or os.getenv("TIKA_APP_JAR") or "").strip()
    if jar:
        return TIKA_BACKEND_JAR
    return TIKA_BACKEND_LOCAL


def describe_tika_backend(server_url: str = "", tika_app_jar: str = "") -> str:
    backend = resolve_tika_backend(server_url, tika_app_jar)
    if backend == TIKA_BACKEND_SERVER:
        return f"Tika mode=server url={normalize_tika_server_url(server_url)}"
    if backend == TIKA_BACKEND_JAR:
        jar = (tika_app_jar or os.getenv("TIKA_APP_JAR") or "").strip()
        return f"Tika mode=jar path={jar}"
    return "Tika mode=local (Python tika package, needs Java)"


def extract_content(
    data: bytes,
    filename: str = "",
    *,
    tika_server_url: str = "",
    tika_app_jar: str = "",
    tika_server_timeout: int = DEFAULT_TIKA_SERVER_TIMEOUT,
    write_limit: int = DEFAULT_WRITE_LIMIT,
) -> str:
    if not data:
        return ""
    backend = resolve_tika_backend(tika_server_url, tika_app_jar)
    if backend == TIKA_BACKEND_SERVER:
        text = _extract_with_tika_server(
            data, filename, tika_server_url, timeout=tika_server_timeout
        )
    elif backend == TIKA_BACKEND_JAR:
        jar = (tika_app_jar or os.getenv("TIKA_APP_JAR") or "").strip()
        text = _extract_with_jar(data, filename, jar)
    else:
        text = _extract_with_tika_python(data, filename)
    return remove_vietnamese_accents(clip_content(text, write_limit))


def ping_tika_server(server_url: str, timeout: int = 30) -> str:
    """Fail fast if remote tika-server is not reachable. Returns a short status string."""
    base = normalize_tika_server_url(server_url)
    if not base:
        raise TikaExtractError("TIKA_SERVER_URL is empty")
    last_error: Optional[TikaExtractError] = None
    for path in ("/tika", "/version"):
        url = f"{base}{path}"
        try:
            raw = _tika_http("GET", url, timeout=timeout)
            text = raw.decode("utf-8", errors="replace").strip().replace("\n", " ")
            return text[:200] or url
        except TikaExtractError as ex:
            last_error = ex
            if "HTTP 404" not in str(ex):
                raise
    raise last_error or TikaExtractError(f"tika-server unreachable: {base}")


def _extract_with_tika_server(
    data: bytes,
    filename: str,
    server_url: str,
    timeout: int = DEFAULT_TIKA_SERVER_TIMEOUT,
) -> str:
    url = tika_put_url(server_url)
    content_type, disposition = _tika_request_headers(filename)
    raw = _tika_http(
        "PUT",
        url,
        body=data,
        content_type=content_type,
        extra_headers={
            "Accept": "text/plain",
            "Content-Disposition": disposition,
        },
        timeout=timeout,
    )
    return raw.decode("utf-8", errors="replace")


def _tika_request_headers(filename: str) -> Tuple[str, str]:
    suffix = Path(filename).suffix.lower() if filename else ""
    content_type = _MIME_BY_SUFFIX.get(suffix, "application/octet-stream")
    safe_name = _safe_filename(filename)
    quoted = urllib.parse.quote(Path(filename).name if filename else safe_name)
    disposition = f'attachment; filename="{safe_name}"'
    if quoted and quoted != safe_name:
        disposition = f"{disposition}; filename*=UTF-8''{quoted}"
    return content_type, disposition


def _safe_filename(filename: str) -> str:
    suffix = Path(filename).suffix if filename else ""
    name = Path(filename).name if filename else "file"
    ascii_name = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in '\\/:*?"<>|' else "_" for ch in name
    ).strip("._")
    if not ascii_name:
        ascii_name = f"file{suffix.lower() or '.bin'}"
    return ascii_name


def _tika_http(
    method: str,
    url: str,
    *,
    body: Optional[bytes] = None,
    content_type: str = "",
    extra_headers: Optional[dict] = None,
    timeout: int = DEFAULT_TIKA_SERVER_TIMEOUT,
) -> bytes:
    req = urllib.request.Request(url, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    if extra_headers:
        for key, value in extra_headers.items():
            req.add_header(key, value)
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx),
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as ex:
        detail = (ex.read() or b"").decode("utf-8", errors="replace")[:300]
        raise TikaExtractError(f"tika-server HTTP {ex.code} {url} {detail}".strip()) from ex
    except urllib.error.URLError as ex:
        raise TikaExtractError(f"tika-server connection failed {url}: {ex.reason}") from ex
    except TimeoutError as ex:
        raise TikaExtractError(f"tika-server timeout {url}") from ex


def _extract_with_jar(data: bytes, filename: str, jar: str) -> str:
    jar_path = Path(jar)
    if not jar_path.is_file():
        raise TikaExtractError(f"TIKA_APP_JAR not found: {jar}")
    suffix = Path(filename).suffix if filename else ""
    tmp = tempfile.NamedTemporaryFile(prefix="solr-file-", suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        proc = subprocess.run(
            ["java", "-jar", str(jar_path), "-t", tmp.name],
            check=False,
            capture_output=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).decode("utf-8", errors="replace")[:500]
            raise TikaExtractError(f"tika-app exit {proc.returncode}: {err}")
        return proc.stdout.decode("utf-8", errors="replace")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _extract_with_tika_python(data: bytes, filename: str) -> str:
    try:
        from tika import parser
    except ImportError as ex:
        raise TikaExtractError(
            "Apache Tika is required. Set TIKA_SERVER_URL, TIKA_APP_JAR, or pip install tika (needs Java)"
        ) from ex
    parsed = parser.from_buffer(data, xmlContent=False, requestOptions={"timeout": 120})
    if not isinstance(parsed, dict):
        raise TikaExtractError(f"unexpected tika response for {filename or 'buffer'}")
    content = parsed.get("content")
    return content if isinstance(content, str) else ""
