#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Tuple

_PART_DIR = Path(__file__).resolve().parent
if str(_PART_DIR) not in sys.path:
    sys.path.insert(0, str(_PART_DIR))

from solr_doc_file_backfill import ExtractCache, _solr_doc, file_key, process_batch, solr_file_service_id
from solr_file_db import AttachmentRow, FileIndexRow, collect_index_files, pick_attachment, solr_file_id, unique_dept_ids
from solr_file_download import FileLoadError, disk_candidates, normalize_relative_path
from solr_file_extract import (
    TikaExtractError,
    TikaUnsupportedFormatError,
    clip_content,
    compact_unsupported_detail,
    detect_unsupported_format,
    extract_content,
    is_unsupported_tika_error,
    remove_vietnamese_accents,
)
from solr_file_report import BackfillReport, IssueRow
from solr_file_solr import add_docs_with_split, escape_solr_query, is_solr_unreachable


def _row(**kwargs) -> FileIndexRow:
    data = dict(
        doc_id="DOC1",
        source="NEW",
        object_type=1,
        dept_id="DEPT1",
        tenant_code="bvhttdl.gov.vn",
        attachment_id="ATT1",
        file_service_id="FS1",
        file_path="/2024/a.pdf",
        file_name="a.pdf",
        root_path="",
        extra_dept_ids=[],
    )
    data.update(kwargs)
    return FileIndexRow(**data)


def test_solr_file_id() -> None:
    assert solr_file_id("FS1", "DEPT1") == "FS1_DEPT1_file"


def test_pick_attachment_prefers_file_service_id() -> None:
    rows = [
        AttachmentRow("D", "A1", "", "/a.doc", "a.doc", "", "t"),
        AttachmentRow("D", "A2", "FS2", "/b.pdf", "b.pdf", "", "t"),
    ]
    chosen = pick_attachment(rows)
    assert chosen is not None
    assert chosen.attachment_id == "A2"
    assert pick_attachment([]) is None


def test_collect_index_files_main_plus_relations() -> None:
    main = [
        AttachmentRow("DOC1", "A1", "FS_MAIN", "/main.pdf", "main.pdf", "", "t"),
    ]
    relations = [
        AttachmentRow("DOC1", "R1", "FS_REL1", "/r1.pdf", "r1.pdf", "", "t"),
        AttachmentRow("DOC1", "R2", "FS_REL2", "/r2.pdf", "r2.pdf", "", "t"),
        AttachmentRow("DOC1", "R3", "FS_MAIN", "/dup.pdf", "dup.pdf", "", "t"),
    ]
    files = collect_index_files(main, relations)
    assert [row.attachment_id for row in files] == ["A1", "R1", "R2"]


def test_collect_index_files_relation_only() -> None:
    relations = [
        AttachmentRow("DOC1", "R1", "FS_REL1", "/r1.pdf", "r1.pdf", "", "t"),
    ]
    files = collect_index_files([], relations)
    assert [row.attachment_id for row in files] == ["R1"]
    assert collect_index_files([], []) == []


def test_unique_dept_ids() -> None:
    assert unique_dept_ids(["DEPT1", ""], ["DEPT1", "DEPT2", None]) == ["DEPT1", "DEPT2"]


def test_disk_candidates() -> None:
    paths = disk_candidates("2024/a.pdf", ["/data/upload"], "dir_upload_path2")
    assert "/data/u02/data1/voffice/Upload/2024/a.pdf" in paths
    assert "/data/upload/2024/a.pdf" in paths
    assert normalize_relative_path("/foo/bar.pdf") == "foo/bar.pdf"


def test_clip_content() -> None:
    assert clip_content("abc", 10) == "abc"
    assert clip_content("abcdefghij", 4) == "abcd"


def test_remove_vietnamese_accents() -> None:
    assert remove_vietnamese_accents("") == ""
    assert remove_vietnamese_accents("hello") == "hello"
    assert remove_vietnamese_accents("Công văn số 123") == "Cong van so 123"
    assert remove_vietnamese_accents("Độc lập - Tự do") == "Doc lap - Tu do"
    assert remove_vietnamese_accents("đường phố") == "duong pho"
    assert remove_vietnamese_accents("ỦY BAN NHÂN DÂN") == "UY BAN NHAN DAN"
    assert remove_vietnamese_accents("việc xử lý") == "viec xu ly"


def test_clip_then_remove_accents_matches_java_order() -> None:
    # Java: BodyContentHandler limit, then TextUtils.removeVietnameseAccents.
    raw = "Công văn " + ("ế" * 20)
    clipped = clip_content(raw, 8)
    assert clipped == "Công văn"
    assert remove_vietnamese_accents(clipped) == "Cong van"


def test_solr_doc_shape() -> None:
    doc = _solr_doc(_row(), "DEPT1", "hello world")
    assert doc["id"] == "FS1_DEPT1_file"
    assert doc["objectId"] == "DOC1"
    assert doc["objectType"] == 1
    assert doc["fileContent"] == "hello world"
    assert doc["fileServiceId"] == "FS1"
    assert doc["deptId"] == "DEPT1"
    assert doc["tenantCode"] == "bvhttdl.gov.vn"
    assert "indexType" not in doc
    assert "searchText" not in doc


def test_file_key_and_fallback_id() -> None:
    with_fs = _row()
    assert file_key(with_fs) == "FS1"
    disk_only = _row(file_service_id="", attachment_id="ATT9")
    assert solr_file_service_id(disk_only) == "ATT9"
    assert file_key(disk_only) == "att:ATT9"


def test_process_batch_dry_run_indexes_per_dept() -> None:
    report = BackfillReport(started_at="now")
    rows = [_row(extra_dept_ids=["DEPT2", "DEPT1"])]
    process_batch(
        rows,
        report,
        solr=None,
        loader=None,
        extract_fn=lambda data, name: "text",
        cache=ExtractCache(),
        commit_within_ms=1000,
        dry_run=True,
        skip_existing=False,
    )
    assert report.scanned == 1
    assert report.indexed == 2


def test_process_batch_skips() -> None:
    report = BackfillReport(started_at="now")
    rows = [
        _row(attachment_id="", file_service_id=""),
        _row(doc_id="DOC2", dept_id="", extra_dept_ids=[]),
        _row(doc_id="DOC3", extra_dept_ids=["DEPTX"]),
    ]
    process_batch(
        rows,
        report,
        solr=None,
        loader=None,
        extract_fn=lambda data, name: "text",
        cache=ExtractCache(),
        commit_within_ms=1000,
        dry_run=True,
        skip_existing=True,
        existing_keys={("FS1", "DEPT1"), ("FS1", "DEPTX")},
    )
    reasons = [item.reason for item in report.skipped_rows]
    assert "MISSING_ATTACHMENT" in reasons
    assert "MISSING_DEPT_ID" in reasons
    assert reasons.count("ALREADY_INDEXED") == 2
    assert report.indexed == 0


def test_process_batch_extract_once_per_file() -> None:
    class FakeSolr:
        def __init__(self) -> None:
            self.docs: List[Dict] = []

        def add_docs(self, docs: List[Dict], commit_within_ms: int) -> None:
            self.docs.extend(docs)

    class FakeLoader:
        def load(self, **kwargs) -> bytes:
            return b"PDFDATA"

    calls = {"n": 0}

    def extract(data: bytes, name: str) -> str:
        calls["n"] += 1
        assert data == b"PDFDATA"
        return "extracted"

    report = BackfillReport(started_at="now")
    solr = FakeSolr()
    rows = [_row(extra_dept_ids=["DEPT2"])]
    process_batch(
        rows,
        report,
        solr=solr,
        loader=FakeLoader(),
        extract_fn=extract,
        cache=ExtractCache(),
        commit_within_ms=1000,
        dry_run=False,
        skip_existing=False,
    )
    assert calls["n"] == 1
    assert report.indexed == 2
    assert [doc["id"] for doc in solr.docs] == ["FS1_DEPT1_file", "FS1_DEPT2_file"]
    assert all(doc["fileContent"] == "extracted" for doc in solr.docs)


def test_process_batch_indexes_relation_file() -> None:
    class FakeSolr:
        def __init__(self) -> None:
            self.docs: List[Dict] = []

        def add_docs(self, docs: List[Dict], commit_within_ms: int) -> None:
            self.docs.extend(docs)

    class FakeLoader:
        def load(self, **kwargs) -> bytes:
            return b"PDFDATA"

    report = BackfillReport(started_at="now")
    rows = [
        _row(file_service_id="FS_MAIN", extra_dept_ids=["DEPT2"]),
        _row(
            attachment_id="REL1",
            file_service_id="FS_REL",
            file_name="related.pdf",
            extra_dept_ids=["DEPT2"],
            file_role="relation",
        ),
    ]
    process_batch(
        rows,
        report,
        solr=FakeSolr(),
        loader=FakeLoader(),
        extract_fn=lambda data, name: "extracted",
        cache=ExtractCache(),
        commit_within_ms=1000,
        dry_run=False,
        skip_existing=False,
    )
    assert report.scanned == 2
    assert report.indexed == 4


def test_process_batch_empty_content_and_download_error() -> None:
    class FakeLoader:
        def __init__(self, error=None) -> None:
            self.error = error

        def load(self, **kwargs) -> bytes:
            if self.error:
                raise self.error
            return b"x"

    report = BackfillReport(started_at="now")
    process_batch(
        [_row(doc_id="E1")],
        report,
        solr=object(),
        loader=FakeLoader(),
        extract_fn=lambda data, name: "   ",
        cache=ExtractCache(),
        commit_within_ms=1000,
        dry_run=False,
        skip_existing=False,
    )
    assert report.skipped_empty_content == 1

    report2 = BackfillReport(started_at="now")
    process_batch(
        [_row(doc_id="E2")],
        report2,
        solr=object(),
        loader=FakeLoader(FileLoadError("FILE_DOWNLOAD_FAILED", "HTTP 404")),
        extract_fn=lambda data, name: "x",
        cache=ExtractCache(),
        commit_within_ms=1000,
        dry_run=False,
        skip_existing=False,
    )
    assert report2.errors == 1
    assert report2.error_rows[0].reason == "FILE_DOWNLOAD_FAILED"


def test_unsupported_rar5_is_skip_not_error() -> None:
    rar5 = b"Rar!\x1a\x07\x01\x00" + b"\x00" * 16
    assert detect_unsupported_format(rar5, "archive.rar") == "archive.rar rar version 5"
    assert detect_unsupported_format(b"Rar!\x1a\x07\x00payload", "old.rar") is None
    try:
        extract_content(rar5, "archive.rar")
        raise AssertionError("expected TikaUnsupportedFormatError")
    except TikaUnsupportedFormatError as ex:
        assert "rar version 5" in str(ex)

    tika_msg = (
        'tika-app exit 1: INFO  [main] org.apache.tika.cli.TikaCLI As a convenience, '
        "Exception in thread \"main\" org.apache.tika.exception.UnsupportedFormatException: "
        "Tika does not yet support rar version 5. at org.apache.tika.parser.pkg.RarParser.parse"
    )
    assert is_unsupported_tika_error(tika_msg)
    assert "rar version 5" in compact_unsupported_detail("archive.rar", tika_msg)
    assert not is_unsupported_tika_error("tika-app exit 1: java not found")

    class FakeLoader:
        def __init__(self, data: bytes) -> None:
            self.data = data

        def load(self, **kwargs) -> bytes:
            return self.data

    report = BackfillReport(started_at="now")
    process_batch(
        [_row(doc_id="RAR5", file_name="archive.rar")],
        report,
        solr=object(),
        loader=FakeLoader(rar5),
        extract_fn=extract_content,
        cache=ExtractCache(),
        commit_within_ms=1000,
        dry_run=False,
        skip_existing=False,
    )
    assert report.errors == 0
    assert report.skipped_unsupported_format == 1
    assert report.skipped_rows[0].reason == "UNSUPPORTED_FILE_FORMAT"

    report2 = BackfillReport(started_at="now")
    process_batch(
        [_row(doc_id="TIKA")],
        report2,
        solr=object(),
        loader=FakeLoader(b"x"),
        extract_fn=lambda data, name: (_ for _ in ()).throw(TikaExtractError(tika_msg)),
        cache=ExtractCache(),
        commit_within_ms=1000,
        dry_run=False,
        skip_existing=False,
    )
    assert report2.errors == 0
    assert report2.skipped_unsupported_format == 1

    report3 = BackfillReport(started_at="now")
    process_batch(
        [_row(doc_id="FAIL")],
        report3,
        solr=object(),
        loader=FakeLoader(b"x"),
        extract_fn=lambda data, name: (_ for _ in ()).throw(TikaExtractError("tika-app exit 1: java not found")),
        cache=ExtractCache(),
        commit_within_ms=1000,
        dry_run=False,
        skip_existing=False,
    )
    assert report3.errors == 1
    assert report3.error_rows[0].reason == "TIKA_FAILED"


def test_extract_cache_lru() -> None:
    cache = ExtractCache(max_size=2)
    cache.put("a", "1")
    cache.put("b", "2")
    cache.put("c", "3")
    assert cache.get("a") is None
    assert cache.get("b") == "2"
    assert cache.get("c") == "3"


def test_split_and_escape() -> None:
    assert "\\+" in escape_solr_query("a+b")
    docs = [{"objectId": "1"}, {"objectId": "2"}]

    def add(chunk: List[Dict], commit_ms: int) -> None:
        if any(doc["objectId"] == "2" for doc in chunk) and len(chunk) > 1:
            raise RuntimeError("Solr HTTP 400 bad")
        if chunk[0]["objectId"] == "2":
            raise RuntimeError("Solr HTTP 400 bad")

    ok, failed = add_docs_with_split(add, docs, 1000)
    assert ok == [0]
    assert failed[0][1] == "SOLR_WRITE_FAILED"
    assert is_solr_unreachable(RuntimeError("Solr connection failed x: Connection refused"))


def test_issue_row_skip_mapping() -> None:
    report = BackfillReport(started_at="now")
    report.add_skip(IssueRow("NEW", 1, "D", "ALREADY_INDEXED"))
    assert report.skipped_already_indexed == 1
    report.add_skip(IssueRow("NEW", 1, "R", "UNSUPPORTED_FILE_FORMAT"))
    assert report.skipped_unsupported_format == 1
    assert report.skipped_total() == 2


if __name__ == "__main__":
    test_solr_file_id()
    test_pick_attachment_prefers_file_service_id()
    test_collect_index_files_main_plus_relations()
    test_collect_index_files_relation_only()
    test_unique_dept_ids()
    test_disk_candidates()
    test_clip_content()
    test_remove_vietnamese_accents()
    test_clip_then_remove_accents_matches_java_order()
    test_solr_doc_shape()
    test_file_key_and_fallback_id()
    test_process_batch_dry_run_indexes_per_dept()
    test_process_batch_skips()
    test_process_batch_extract_once_per_file()
    test_process_batch_indexes_relation_file()
    test_process_batch_empty_content_and_download_error()
    test_unsupported_rar5_is_skip_not_error()
    test_extract_cache_lru()
    test_split_and_escape()
    test_issue_row_skip_mapping()
    print("ok")
