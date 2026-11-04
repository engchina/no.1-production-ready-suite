"""参照の解決のやり直しと題名の照合を、合成の値で確かめる（#4310。LLM・Oracle は呼ばない）。"""

import asyncio
from typing import Any

import pytest

from app.rag import reference_refresh
from app.rag.chunking import Chunk
from app.rag.cross_references import ReferenceSpec
from app.rag.reference_records import build_reference_records
from app.rag.reference_refresh import (
    ReferenceRefreshSweep,
    plan_reference_updates,
    run_reference_refresh_loop,
)
from app.rag.reference_resolution import (
    ReferenceDocument,
    document_title_from_paths,
    resolve_scoped_reference,
)

TITLE = "合成本体規程"


def _prefixed(number: str, title: str, *sections: str) -> ReferenceDocument:
    """番号の接頭辞のファイル名で、見出しの先頭の段に本文の題名がある文書。"""
    paths = tuple(f"{title} > {section}" for section in sections)
    return ReferenceDocument(
        f"doc-{number}",
        f"set-{number}",
        f"{number}_{title}.json",
        paths,
        document_title_from_paths(paths),
    )


def test_document_title_needs_majority_and_no_numbering() -> None:
    assert document_title_from_paths(("題名 > 第1章", "題名 > 第2章", "付録")) == "題名"
    assert document_title_from_paths(("第1章 > 第1条", "第1章 > 第2条")) is None
    assert document_title_from_paths(("甲", "乙")) is None
    assert document_title_from_paths(()) is None


def test_prefixed_file_name_resolves_by_body_title() -> None:
    target = _prefixed("A001", TITLE, "第2条")
    source = _prefixed("B002", "合成の起点", "第1条")
    spec = ReferenceSpec(label=f"{TITLE}第2条", kind="article", key="2", document_title=TITLE)
    result = resolve_scoped_reference(
        spec, source=source, citing_path=source.section_paths[0], documents=(source, target)
    )
    assert result.status == "resolved"
    assert result.target is not None and result.target.document_id == "doc-A001"


def test_abbreviation_expands_from_body_title_not_prefixed_name() -> None:
    """番号の接頭辞があっても、派生の文書の略称の参照が本体の文書に当たる。"""
    base = _prefixed("A001", TITLE, "第2条")
    derived = _prefixed("A002", f"{TITLE}施行令", "第1条")
    spec = ReferenceSpec(label="法第2条", kind="article", key="2", document_title="法")
    result = resolve_scoped_reference(
        spec, source=derived, citing_path=derived.section_paths[0], documents=(base, derived)
    )
    assert result.status == "resolved"
    assert result.target is not None and result.target.document_id == "doc-A001"


def test_same_named_documents_stay_ambiguous() -> None:
    """同名の文書をコードで選ばない。"""
    first = _prefixed("A001", TITLE, "第2条")
    second = ReferenceDocument("doc-copy", "set-copy", first.name, first.section_paths, first.title)
    source = _prefixed("B002", "合成の起点", "第1条")
    spec = ReferenceSpec(label=f"{TITLE}第2条", kind="article", key="2", document_title=TITLE)
    result = resolve_scoped_reference(
        spec, source=source, citing_path="", documents=(source, first, second)
    )
    assert (result.status, result.target) == ("ambiguous", None)


def test_answer_path_without_title_keeps_file_name_behavior() -> None:
    """題名を渡さない経路（回答の参照の補充）は、ファイル名だけで決まる。"""
    source = ReferenceDocument("s", "s1", "A001_合成の起点.json", ())
    assert source.title is None
    target = ReferenceDocument("t", "t1", f"{TITLE}.json", ("第2条",))
    spec = ReferenceSpec(label=f"{TITLE}第2条", kind="article", key="2", document_title=TITLE)
    result = resolve_scoped_reference(spec, source=source, citing_path="", documents=(target,))
    assert result.status == "resolved"
    other = ReferenceDocument("t", "t1", "A001_別の名前.json", (f"{TITLE} > 第2条",))
    missing = resolve_scoped_reference(spec, source=source, citing_path="", documents=(other,))
    assert missing.status == "not_found"


def _row(source: ReferenceDocument, **values: Any) -> dict[str, Any]:
    return {
        "reference_id": "ref-1",
        "source_document_id": source.document_id,
        "source_chunk_set_id": source.chunk_set_id,
        "source_section_path": source.section_paths[0],
        "label": f"{TITLE}第2条",
        "kind": "article",
        "reference_key": "2",
        "document_title": TITLE,
        "resolution_status": "not_found",
        "target_document_id": None,
        "target_chunk_set_id": None,
        "target_section_path": None,
        **values,
    }


def test_plan_updates_only_changed_rows() -> None:
    source = _prefixed("B002", "合成の起点", "第1条")
    target = _prefixed("A001", TITLE, "第2条")
    updates, statuses = plan_reference_updates([_row(source)], (source, target))
    assert updates == [
        {
            "reference_id": "ref-1",
            "status": "resolved",
            "target_document_id": "doc-A001",
            "target_chunk_set_id": "set-A001",
            "target_section_path": f"{TITLE} > 第2条",
        }
    ]
    assert statuses == {"resolved": 1}
    resolved = _row(
        source,
        resolution_status="resolved",
        target_document_id="doc-A001",
        target_chunk_set_id="set-A001",
        target_section_path=f"{TITLE} > 第2条",
    )
    assert plan_reference_updates([resolved], (source, target))[0] == []
    # 参照先の文書が消えると未解決に戻す。
    removed, _ = plan_reference_updates([resolved], (source,))
    assert removed[0]["status"] == "not_found"
    assert removed[0]["target_document_id"] is None


def test_plan_keeps_rows_that_cannot_be_rechecked() -> None:
    source = _prefixed("B002", "合成の起点", "第1条")
    elided = _row(source, source_section_path="長い見出し…末尾", resolution_status="ambiguous")
    other = _row(source, source_chunk_set_id="old-set")
    updates, statuses = plan_reference_updates([elided, other], (source,))
    assert updates == []
    assert statuses == {"ambiguous": 1, "not_found": 1}


def test_records_use_body_title() -> None:
    target = _prefixed("A001", TITLE, "第2条")
    source = _prefixed("B002", "合成の起点", "第1条")
    chunk = Chunk(
        text=f"{TITLE}第2条を参照する。",
        index=0,
        start_offset=0,
        end_offset=10,
        metadata={"section_path": source.section_paths[0]},
    )
    records = build_reference_records(
        knowledge_base_id="kb", source=source, chunks=[chunk], documents=(source, target)
    )
    assert [(record.status, record.target_document_id) for record in records] == [
        ("resolved", "doc-A001")
    ]


class _Oracle:
    def __init__(self) -> None:
        self.members = [("kb-1", "d1", "s1")]

    async def _fetch_all(self, _sql: str, _binds: Any) -> list[dict[str, Any]]:
        return [
            {"knowledge_base_id": kb, "document_id": doc, "chunk_set_id": chunk_set}
            for kb, doc, chunk_set in self.members
        ]


async def test_sweep_waits_until_catalog_settles(monkeypatch: pytest.MonkeyPatch) -> None:
    """変わった次の回まで待ち、同じ指紋では解決し直さない。連続の変更は 1 回にまとめる。"""
    calls: list[str] = []

    async def refresh(_oracle: Any, kb_id: str) -> dict[str, Any]:
        calls.append(kb_id)
        return {"checked": 0, "updated": 0, "references": {}}

    monkeypatch.setattr(reference_refresh, "refresh_knowledge_base_references", refresh)
    oracle = _Oracle()
    sweep = ReferenceRefreshSweep(oracle)
    assert await sweep.run_once() == []
    assert await sweep.run_once() == ["kb-1"]
    assert await sweep.run_once() == []
    oracle.members = [("kb-1", "d1", "s1"), ("kb-1", "d2", "s2")]
    assert await sweep.run_once() == []
    oracle.members = [("kb-1", "d1", "s1"), ("kb-1", "d2", "s2"), ("kb-1", "d3", "s3")]
    assert await sweep.run_once() == []
    assert await sweep.run_once() == ["kb-1"]
    assert calls == ["kb-1", "kb-1"]


async def test_loop_survives_failure_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = asyncio.Event()
    attempts = 0

    async def run_once(_self: ReferenceRefreshSweep) -> list[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            stop.set()
        raise RuntimeError("合成の失敗")

    monkeypatch.setattr(ReferenceRefreshSweep, "run_once", run_once)
    await asyncio.wait_for(run_reference_refresh_loop(object(), stop, interval_seconds=0), 2)
    assert attempts == 2
