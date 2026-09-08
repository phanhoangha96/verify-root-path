"""Normalize searchText the same way as eoffice-business DocMetaSolrServiceImpl."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional

# Java: Normalizer.NFD + \\p{InCombiningDiacriticalMarks} + đ/Đ
_COMBINING_MARKS = re.compile(r"[\u0300-\u036f]+")
# Java: [\\+\\-\\&\\|\\!\\(\\)\\{\\}\\[\\]\\^\"~\\*\\?:/]
_SPECIAL_CHARS = re.compile(r'[\\+\-&|!(){}[\]^"~*?:/]')
_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def is_true(value: Optional[str]) -> bool:
    return value is not None and str(value).strip() != ""


def strip_html(value: Optional[str]) -> str:
    if not is_true(value):
        return ""
    return _HTML_TAG.sub(" ", str(value))


def remove_vietnamese_tones(value: Optional[str]) -> str:
    if not is_true(value):
        return ""
    try:
        nfd = unicodedata.normalize("NFD", str(value))
        return _COMBINING_MARKS.sub("", nfd).replace("đ", "d").replace("Đ", "D")
    except Exception:
        return str(value)


def normalize_search_value(value: Optional[str]) -> str:
    if not is_true(value):
        return ""
    return _WHITESPACE.sub("", _SPECIAL_CHARS.sub(" ", remove_vietnamese_tones(value))).upper()


def build_search_text(*values: Optional[str]) -> str:
    parts: List[str] = []
    for value in values:
        if is_true(value):
            parts.append(str(value))
    if not parts:
        return ""
    return normalize_search_value(" ".join(parts))


def collect_values(*groups: Iterable[Optional[str]]) -> List[str]:
    out: List[str] = []
    for group in groups:
        if group is None:
            continue
        for value in group:
            if is_true(value):
                out.append(str(value))
    return out
