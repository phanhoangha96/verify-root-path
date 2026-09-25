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
    """Khớp solr-backfill-service TextNormalizer tại bd118205: bỏ dấu, IN HOA,
    giữ mọi ký tự hiển thị (chữ, số, dấu câu, ký hiệu %, &, ()...). Chỉ xóa
    khoảng trắng, ký tự điều khiển (Cc) và ký tự format vô hình (Cf).
    "V/v: mua sắm" -> "V/V:MUASAM".
    """
    if not is_true(value):
        return ""
    text = remove_vietnamese_tones(value)
    # Java (?U)[\s\p{Cc}\p{Cf}]+
    kept = [ch for ch in text if not ch.isspace() and unicodedata.category(ch) not in ("Cc", "Cf")]
    return "".join(kept).upper()


def normalize_place_list(value: Optional[str], max_token_len: int = 32766) -> str:
    """Normalize each receive-place separately and join with spaces.

    Token dài hơn 32766 ký tự được cắt trước khi ghép. clip_solr_doc cắt
    cả field otherReceivePlaces ở 1_048_576, khớp solr-backfill-service.
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
