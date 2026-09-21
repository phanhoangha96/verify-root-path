#!/usr/bin/env python3
"""Match eoffice-business DocMetaSolrServiceImplTest."""

from __future__ import annotations

import sys
from pathlib import Path

_PART_DIR = Path(__file__).resolve().parent
if str(_PART_DIR) not in sys.path:
    sys.path.insert(0, str(_PART_DIR))

from solr_backfill_text import build_search_text, normalize_place_list, normalize_search_value, strip_html


def test_normalize_search_value() -> None:
    assert normalize_search_value("công văn") == "CONGVAN"
    assert normalize_search_value("Số 123") == "SO123"
    assert normalize_search_value("test đồng nhất 18/9") == "TESTDONGNHAT18/9"
    assert normalize_search_value("123/QĐ-UBND") == "123/QD-UBND"
    # Dau cau va ky hieu duoc GIU de search khop dung ky tu nguoi dung nhap.
    assert normalize_search_value("mua thu, bảo trì") == "MUATHU,BAOTRI"
    assert normalize_search_value("V/v: mua sắm") == "V/V:MUASAM"
    assert normalize_search_value("%") == "%"
    assert normalize_search_value("50%") == "50%"
    assert normalize_search_value("tăng (10%)") == "TANG(10%)"
    assert normalize_search_value("*\\a|b&\"c\"~?") == "*\\A|B&\"C\"~?"
    # Khoang trang (ke ca Unicode), ky tu dieu khien (Cc) va format vo hinh (Cf) van bi xoa.
    assert normalize_search_value("   ") == ""
    assert normalize_search_value("A B") == "AB"
    assert normalize_search_value("A B") == "AB"
    assert normalize_search_value("AB") == "AB"
    assert normalize_search_value("A​B") == "AB"
    assert normalize_search_value("A﻿B") == "AB"
    assert normalize_search_value("A­B") == "AB"


def test_build_search_text() -> None:
    assert build_search_text("CV-001", "Trích yếu", "10") == "CV-001TRICHYEU10"
    assert build_search_text(None, "CV 001", "") == "CV001"
    assert build_search_text("ý kiến", "trả lời") == "YKIENTRALOI"


def test_normalize_place_list() -> None:
    assert (
        normalize_place_list("Bộ Công An, Bộ Văn Hóa, Văn phòng Bộ")
        == "BOCONGAN BOVANHOA VANPHONGBO"
    )
    assert normalize_place_list("Bộ Công An") == "BOCONGAN"
    assert normalize_place_list("  ,  ,  ") == ""
    huge = "A" * 40000
    tokens = normalize_place_list(huge).split(" ")
    assert tokens[0] == "A" * 32766
    assert "".join(tokens) == huge


def test_strip_html() -> None:
    assert strip_html("<p>Dong y xu ly</p>") == " Dong y xu ly "


if __name__ == "__main__":
    test_normalize_search_value()
    test_build_search_text()
    test_normalize_place_list()
    test_strip_html()
    print("ok")
