"""Expected ROOT_PATH -> physical Upload folders (aligned with migration-service)."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

EXPECTED_ROOT_PATH_FOLDERS: Dict[str, List[str]] = {
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


def collapse_slashes(path: str) -> str:
    if not path:
        return path
    out = path.replace("\\", "/")
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


def trim_trailing_slash(path: str) -> str:
    path = collapse_slashes(path)
    while path.endswith("/") and len(path) > 1:
        path = path[:-1]
    return path


def expected_folders_for(root_path: Optional[str]) -> List[str]:
    if not root_path or not str(root_path).strip():
        return []
    return list(EXPECTED_ROOT_PATH_FOLDERS.get(str(root_path).strip(), []))


def detect_server_root(absolute_path: str, configured_roots: List[str]) -> str:
    """Return the longest configured root that prefixes absolute_path."""
    abs_path = trim_trailing_slash(absolute_path)
    best = ""
    for root in configured_roots:
        r = trim_trailing_slash(root)
        if abs_path == r or abs_path.startswith(r + "/"):
            if len(r) > len(best):
                best = r
    return best


def classify_result(
    root_path: Optional[str],
    found_path: Optional[str],
    matched_server_root: str,
) -> Tuple[str, str]:
    """
    Returns (status, note).
    """
    has_root = bool(root_path and str(root_path).strip())
    if not has_root:
        if found_path:
            return "NO_ROOT_PATH_BUT_FOUND", "DB ROOT_PATH empty; file still found on server"
        return "NO_ROOT_PATH_NOT_FOUND", "DB ROOT_PATH empty; file not found via plocate"

    expected = expected_folders_for(root_path)
    if not found_path:
        if expected:
            return "NOT_FOUND", "plocate did not find file under configured roots"
        return "UNKNOWN_ROOT_PATH_NOT_FOUND", f"Unknown ROOT_PATH={root_path!r}; file not found"

    if not expected:
        return "UNKNOWN_ROOT_PATH_FOUND", f"Unknown ROOT_PATH={root_path!r}; found under {matched_server_root}"

    expected_norm = {trim_trailing_slash(x) for x in expected}
    if matched_server_root and matched_server_root in expected_norm:
        return "MATCHED", "Found under expected folder for ROOT_PATH"
    return "MISMATCH", (
        f"Expected one of {sorted(expected_norm)}; "
        f"found under {matched_server_root or '(unknown root)'}"
    )
