"""Path helpers for missing-export plocate matching."""

from __future__ import annotations

from typing import Optional


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
