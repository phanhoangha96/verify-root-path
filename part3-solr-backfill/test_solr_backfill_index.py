#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

_PART_DIR = Path(__file__).resolve().parent
if str(_PART_DIR) not in sys.path:
    sys.path.insert(0, str(_PART_DIR))

from solr_backfill_solr import (
    MAX_SOLR_DEDICATED_FIELD,
    MAX_SOLR_TERM_LENGTH,
    add_docs_with_split,
    clip_solr_doc,
    clip_solr_term,
    compact_solr_msg,
    is_solr_unreachable,
)


class FakeSolr:
    def __init__(self, bad_ids: List[str] = None, unreachable: bool = False) -> None:
        self.bad_ids = set(bad_ids or [])
        self.unreachable = unreachable
        self.calls: List[int] = []
        self.indexed_ids: List[str] = []

    def add_docs(self, docs: List[Dict], commit_within_ms: int) -> None:
        self.calls.append(len(docs))
        if self.unreachable:
            raise RuntimeError("Solr connection failed http://localhost:8983/solr/x: Connection refused")
        for doc in docs:
            if str(doc["objectId"]) in self.bad_ids:
                raise RuntimeError(f"Solr HTTP 400 immense term objectId={doc['objectId']}")
        self.indexed_ids.extend(str(doc["objectId"]) for doc in docs)


def test_clip_solr_term() -> None:
    assert clip_solr_term(None) is None
    assert clip_solr_term("") == ""
    assert clip_solr_term("abc") == "abc"
    huge = "X" * (MAX_SOLR_TERM_LENGTH + 50)
    clipped = clip_solr_term(huge)
    assert clipped is not None
    assert len(clipped) == MAX_SOLR_TERM_LENGTH


def test_clip_solr_doc() -> None:
    warnings: List[str] = []
    places = "P" * (MAX_SOLR_TERM_LENGTH + 3)
    doc = clip_solr_doc(
        {
            "objectId": "99",
            "searchText": "S" * (MAX_SOLR_TERM_LENGTH + 10),
            "otherReceivePlaces": places,
            "quote": "Q" * (MAX_SOLR_DEDICATED_FIELD + 5),
            "empty": "",
        },
        object_id="99",
        log=warnings.append,
    )
    assert len(doc["searchText"]) == MAX_SOLR_TERM_LENGTH
    assert doc["otherReceivePlaces"] == places
    assert len(doc["quote"]) == MAX_SOLR_DEDICATED_FIELD
    assert "empty" not in doc
    assert len(warnings) == 2


def test_binary_split_isolates_bad_docs() -> None:
    docs = [{"objectId": str(i)} for i in range(20)]
    solr = FakeSolr(bad_ids=["3", "17"])
    ok, failed = add_docs_with_split(solr.add_docs, docs, 10000)
    assert sorted(int(docs[i]["objectId"]) for i in ok) == [i for i in range(20) if i not in (3, 17)]
    assert sorted(int(docs[i]["objectId"]) for i, reason, _ in failed) == [3, 17]
    assert all(reason == "SOLR_WRITE_FAILED" for _, reason, _ in failed)
    assert max(solr.calls) == 20
    assert len(solr.calls) < 21


def test_unreachable_does_not_split() -> None:
    docs = [{"objectId": str(i)} for i in range(10)]
    solr = FakeSolr(unreachable=True)
    ok, failed = add_docs_with_split(solr.add_docs, docs, 10000)
    assert solr.calls == [10]
    assert ok == []
    assert len(failed) == 10
    assert all(reason == "SOLR_UNREACHABLE" for _, reason, _ in failed)


def test_compact_solr_msg_strips_huge_value() -> None:
    huge = "BOCONGAN," * 200
    raw = (
        "Solr HTTP 400: ERROR: [doc=abc_meta] Error adding field "
        f"'otherReceivePlaces'='{huge}' msg=immense term in field"
    )
    compact = compact_solr_msg(raw)
    assert "immense term" in compact
    assert "otherReceivePlaces" in compact
    assert huge not in compact


def test_retry_drops_other_receive_places() -> None:
    class Solr:
        def __init__(self) -> None:
            self.calls: List[List[str]] = []

        def add_docs(self, docs: List[Dict], commit_within_ms: int) -> None:
            self.calls.append(sorted(docs[0].keys()))
            if "otherReceivePlaces" in docs[0]:
                raise RuntimeError(
                    "Solr HTTP 400: ERROR: [doc=1_meta] Error adding field "
                    "'otherReceivePlaces'='XXXX' msg=immense term"
                )

    solr = Solr()
    docs = [{"objectId": "1", "searchText": "A", "otherReceivePlaces": "B"}]
    ok, failed = add_docs_with_split(solr.add_docs, docs, 10000)
    assert ok == [0]
    assert failed == []
    assert solr.calls == [
        ["objectId", "otherReceivePlaces", "searchText"],
        ["objectId", "searchText"],
    ]


def test_is_solr_unreachable() -> None:
    assert is_solr_unreachable(RuntimeError("Solr connection failed x: Connection refused"))
    assert not is_solr_unreachable(RuntimeError("Solr HTTP 400 immense term"))


if __name__ == "__main__":
    test_clip_solr_term()
    test_clip_solr_doc()
    test_binary_split_isolates_bad_docs()
    test_unreachable_does_not_split()
    test_retry_drops_other_receive_places()
    test_compact_solr_msg_strips_huge_value()
    test_is_solr_unreachable()
    print("ok")
