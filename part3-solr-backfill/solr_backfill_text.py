"""Normalize searchText the same way as eoffice-business DocMetaSolrServiceImpl."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional

# Java: Normalizer.NFD + \\p{InCombiningDiacriticalMarks} + đ/Đ
_COMBINING_MARKS = re.compile(r"[\u0300-\u036f]+")
_HTML_TAG = re.compile(r"<[^>]+>")
_PLACE_SEP = re.compile(r"[,;|/\n\r]+")


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
    """Khop eoffice-business DocMetaSolrServiceImpl.normalizeSearchValue va
    eoffice-solr DocServiceImpl.normalizeSearchPhrase: bo dau, IN HOA, xoa moi
    ky tu khong phai chu/so (giu / va - cho so ky hieu 18/9, 123/QD-UBND) —
    khoang trang lan dau cau deu bi xoa de search theo cum (phrase) bang mot
    wildcard duy nhat.
    """
    if not is_true(value):
        return ""
    text = remove_vietnamese_tones(value)
    # Java [^\p{L}\p{N}/\-]: unicodedata.category "L*"/"N*" tuong duong \p{L}/\p{N}.
    kept = [ch for ch in text if ch in "/-" or unicodedata.category(ch)[0] in ("L", "N")]
    return "".join(kept).upper()


def normalize_place_list(value: Optional[str], max_token_len: int = 32766) -> str:
    """Normalize each receive-place separately and join with spaces.

    Field meta la type string (1 term nguyen ven) nen wildcard substring van
    match xuyen dau cach; gia tri cuoi duoc clip_solr_doc cat o 32766 ky tu
    (Lucene MAX_TERM_LENGTH).
    """
    if not is_true(value):
        return ""
    parts = _PLACE_SEP.split(str(value))
    if len(parts) == 1:
        parts = [str(value)]
    tokens: List[str] = []
    for part in parts:
        token = normalize_search_value(part)
        if not token:
            continue
        if len(token) <= max_token_len:
            tokens.append(token)
            continue
        for start in range(0, len(token), max_token_len):
            tokens.append(token[start : start + max_token_len])
    return " ".join(tokens)


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
