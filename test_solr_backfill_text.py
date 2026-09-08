#!/usr/bin/env python3
"""Match eoffice-business DocMetaSolrServiceImplTest."""

from __future__ import annotations

from solr_backfill_text import build_search_text, normalize_search_value, strip_html


def test_normalize_search_value() -> None:
    assert normalize_search_value("công văn") == "CONGVAN"
    assert normalize_search_value("Số 123") == "SO123"
    assert normalize_search_value("   ") == ""


def test_build_search_text() -> None:
    assert build_search_text("CV-001", "Trích yếu", "10") == "CV001TRICHYEU10"
    assert build_search_text(None, "CV 001", "") == "CV001"
    assert build_search_text("ý kiến", "trả lời") == "YKIENTRALOI"


def test_strip_html() -> None:
    assert strip_html("<p>Dong y xu ly</p>") == " Dong y xu ly "


if __name__ == "__main__":
    test_normalize_search_value()
    test_build_search_text()
    test_strip_html()
    print("ok")
