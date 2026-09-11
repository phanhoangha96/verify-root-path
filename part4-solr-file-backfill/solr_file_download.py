"""Read file bytes: file-service (eoffice-solr FileServiceImpl) or local disk."""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Sequence
from urllib.parse import quote

from solr_file_config import FILE_SOURCE_AUTO, FILE_SOURCE_DISK, FILE_SOURCE_SERVICE

# Same mapping as part1-root-path-audit (LEGACY ROOT_PATH → Upload folders).
EXPECTED_ROOT_PATH_FOLDERS = {
    "dir_upload_path1": [
        "/data/u02/data1/voffice/Upload",
        "/data/u03/data1/voffice/Upload",
    ],
    "dir_upload_path2": [
        "/data/u02/data1/voffice/Upload",
    ],
    "dir_upload_path4": [
        "/data/u02/data1/voffice/Upload",
        "/data/u04/data1/voffice/Upload",
    ],
    "dir_upload_path5": [
        "/data/u03/data1/voffice/Upload",
    ],
    "dir_upload_path_Current": [
        "/data/u05/data1/voffice/Upload",
    ],
}


class FileLoadError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def collapse_slashes(path: str) -> str:
    out = (path or "").replace("\\", "/")
    while "//" in out:
        out = out.replace("//", "/")
    return out


def normalize_relative_path(file_path: Optional[str]) -> str:
    if not file_path or not str(file_path).strip():
        return ""
    path = collapse_slashes(str(file_path).strip())
    while path.startswith("/"):
        path = path[1:]
    return path


def disk_candidates(
    file_path: str,
    roots: Sequence[str],
    root_path: str = "",
) -> List[str]:
    rel = normalize_relative_path(file_path)
    raw = collapse_slashes(file_path or "")
    out: List[str] = []
    if raw.startswith("/") and Path(raw).is_file():
        out.append(raw)
    folders = list(EXPECTED_ROOT_PATH_FOLDERS.get((root_path or "").strip(), []))
    for folder in list(folders) + [item.rstrip("/") for item in roots]:
        if not folder:
            continue
        if rel:
            out.append(f"{folder.rstrip('/')}/{rel}")
        if raw.startswith("/"):
            out.append(f"{folder.rstrip('/')}{raw}")
    # Keep order, drop duplicates.
    seen = set()
    unique: List[str] = []
    for item in out:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def read_disk_file(file_path: str, roots: Sequence[str], root_path: str = "") -> bytes:
    candidates = disk_candidates(file_path, roots, root_path)
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path.read_bytes()
    preview = ", ".join(candidates[:4]) if candidates else "(none)"
    raise FileLoadError("FILE_NOT_FOUND", f"tried {preview}")


def download_file_service(
    download_url: str,
    file_service_id: str,
    tenant_code: str,
    timeout: int = 120,
) -> bytes:
    if not file_service_id:
        raise FileLoadError("MISSING_FILE_SERVICE_ID")
    if not tenant_code:
        raise FileLoadError("MISSING_TENANT_CODE", "file-service requires TenantCode header")
    url = f"{download_url.rstrip('/')}/{quote(file_service_id, safe='')}?type=original"
    req = urllib.request.Request(url, method="GET")
    req.add_header("TenantCode", tenant_code)
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx),
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as ex:
        raise FileLoadError("FILE_DOWNLOAD_FAILED", f"HTTP {ex.code} {url}") from ex
    except urllib.error.URLError as ex:
        raise FileLoadError("FILE_DOWNLOAD_FAILED", f"{ex.reason} {url}") from ex
    if not body:
        raise FileLoadError("FILE_DOWNLOAD_FAILED", f"empty body {url}")
    return body


class FileBytesLoader:
    def __init__(
        self,
        file_source: str,
        download_url: str,
        timeout: int,
        storage_roots: Sequence[str],
    ) -> None:
        self.file_source = file_source
        self.download_url = download_url
        self.timeout = timeout
        self.storage_roots = list(storage_roots)

    def load(
        self,
        *,
        file_service_id: str,
        file_path: str,
        tenant_code: str,
        root_path: str = "",
    ) -> bytes:
        source = self.file_source
        if source == FILE_SOURCE_SERVICE:
            return download_file_service(self.download_url, file_service_id, tenant_code, self.timeout)
        if source == FILE_SOURCE_DISK:
            if not file_path:
                raise FileLoadError("FILE_NOT_FOUND", "empty FILE_PATH")
            return read_disk_file(file_path, self.storage_roots, root_path)
        # auto
        if file_service_id and self.download_url:
            try:
                return download_file_service(self.download_url, file_service_id, tenant_code, self.timeout)
            except FileLoadError as ex:
                if file_path:
                    try:
                        return read_disk_file(file_path, self.storage_roots, root_path)
                    except FileLoadError:
                        raise ex
                raise
        if file_path:
            return read_disk_file(file_path, self.storage_roots, root_path)
        raise FileLoadError("MISSING_FILE_SERVICE_ID")
