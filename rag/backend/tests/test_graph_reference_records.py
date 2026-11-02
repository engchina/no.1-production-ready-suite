"""合成参照の版・未解決状態と、画面への権限漏れを確認する。"""

import json
from functools import partial
from typing import Any

import pytest

from app.clients.graph_reference_store import GraphReferenceStore, _catalog_section_path
from app.rag.chunking import Chunk
from app.rag.reference_records import GraphReferenceRecord, build_reference_records
from app.rag.reference_resolution import ReferenceDocument


def test_records_keep_cross_document_target_version_and_unresolved_state() -> None:
    source = ReferenceDocument("source", "s1", "合成の起点.txt", ("第1条",))
    chunks = [
        Chunk(
            text="合成規程の第2条を参照する。",
            index=0,
            start_offset=0,
            end_offset=20,
            metadata={"section_path": "第1条"},
        )
    ]
    target = ReferenceDocument("target", "t1", "合成規程.txt", ("第2条",))
    build = partial(build_reference_records, knowledge_base_id="kb", source=source, chunks=chunks)
    records = build(documents=(target,))
    assert len(records) == 1
    assert records[0].status == "resolved"
    assert records[0].target_document_id == "target"
    assert records[0].target_chunk_set_id == "t1"
    assert records == build(documents=(target,))
    missing = build(documents=())
    assert missing[0].status == "not_found"
    assert missing[0].reference_id == records[0].reference_id
    ambiguous = ReferenceDocument("another", "a1", target.name, target.section_paths)
    assert build(documents=(target, ambiguous))[0].status == "ambiguous"


def test_invisible_target_and_replaced_version_never_leak_target_locator() -> None:
    row: dict[str, Any] = dict(
        reference_id="ref",
        label="第2条",
        resolution_status="resolved",
        source_document_id="source",
        source_chunk_set_id="s1",
        source_section_path="第1条",
        source_name="source.txt",
        target_document_id="hidden",
        target_chunk_set_id="old",
        target_section_path="第2条",
        target_name=None,
        active_target_set=None,
    )
    view = GraphReferenceStore._view("kb", row)
    assert view["status"] == "not_found"
    assert view["target"] is None
    row["target_name"] = "visible.txt"
    view = GraphReferenceStore._view("kb", row)
    assert view["status"] == "version_unconfirmed" and view["target"] is None
    row["active_target_set"] = "old"
    assert GraphReferenceStore._view("kb", row)["target"]["chunk_set_id"] == "old"


def test_unknown_source_version_is_never_promoted_to_a_resolved_reference() -> None:
    source = ReferenceDocument("source", None, "source.txt", ("第1条", "第2条"))
    chunks = [
        Chunk(
            text="第2条を参照する。",
            index=0,
            start_offset=0,
            end_offset=10,
            metadata={"section_path": "第1条"},
        )
    ]
    records = build_reference_records(
        knowledge_base_id="kb", source=source, chunks=chunks, documents=()
    )
    assert records[0].status == "version_unconfirmed"
    assert records[0].target_document_id is None


async def test_unindexed_target_is_loaded_only_from_a_visible_versioned_reference() -> None:
    from app.rag.graph_index import _graph_id

    row: dict[str, Any] = dict(
        reference_id="ref",
        label="第2条",
        resolution_status="resolved",
        source_document_id="source",
        source_chunk_set_id="s1",
        source_section_path="第1条",
        source_name="source.txt",
        target_document_id="target",
        target_chunk_set_id="t1",
        target_section_path="第2条",
        target_name="target.txt",
        active_target_set="t1",
    )

    class Oracle:
        async def _fetch_all(self, sql: str, binds: object) -> list[dict[str, Any]]:
            if "FROM rag_graph_entities" in sql:
                return []
            assert "rag_graph_references" in sql and "r.reference_id=:reference_id" in sql
            assert "JOIN rag_document_knowledge_bases sm" in sql
            assert "ts.is_active=1" in sql
            return [row]

    store = GraphReferenceStore(Oracle())
    target_id = _graph_id("entity", "t1", "kb", "第2条")
    node = await store.node("kb", target_id, "ref")
    assert node is not None and node["chunk_set_id"] == "t1"
    assert await store.node("kb", "different", "ref") is None
    row["target_name"] = None
    assert await store.node("kb", target_id, "ref") is None


def _long_path_record(source_path: str, target_path: str | None) -> GraphReferenceRecord:
    return GraphReferenceRecord(
        reference_id="ref",
        knowledge_base_id="kb",
        source_document_id="source",
        source_chunk_set_id="s1",
        source_chunk_id="source:s1:0",
        source_section_path=source_path,
        label="第2条",
        kind="article",
        key="2",
        document_title=None,
        status="resolved" if target_path else "not_found",
        target_document_id="target" if target_path else None,
        target_chunk_set_id="t1" if target_path else None,
        target_section_path=target_path,
    )


@pytest.mark.parametrize(
    ("path", "stored_whole"),
    [
        ("a" * 4000, True),
        ("合" * 1333, True),
        ("a" * 4001, False),
        ("合" * 1334, False),
        ("合" * 4000, False),
    ],
    ids=["4000_bytes", "3999_bytes_multibyte", "4001_bytes", "4002_bytes_multibyte", "12000_bytes"],
)
def test_write_keeps_section_paths_within_the_column(
    monkeypatch: pytest.MonkeyPatch, path: str, stored_whole: bool
) -> None:
    """#4126: 列に入るパスは変えず、入らないパスは図のノードと同じ表示名で保存する。"""
    from app.clients import oracle as oracle_module
    from app.clients.graph_reference_store import write_graph_references
    from app.rag.graph_index import _clean_label

    written: list[dict[str, Any]] = []
    monkeypatch.setattr(oracle_module, "_execute", lambda *_args: None)
    monkeypatch.setattr(oracle_module, "_executemany", lambda _c, _s, rows: written.extend(rows))
    write_graph_references(
        object(),
        knowledge_base_id="kb",
        chunk_set_id="s1",
        records=[_long_path_record(path, path), _long_path_record(path, None)],
    )
    expected = path if stored_whole else _clean_label(path)
    assert [row["source_section_path"] for row in written] == [expected, expected]
    assert [row["target_section_path"] for row in written] == [expected, None]
    assert all(len(row["source_section_path"].encode()) <= 4000 for row in written)
    # 保存した値の表示名は変わらない（SQL の表示名の式で図のノードへ結べる）。
    assert _clean_label(expected) == _clean_label(path)


async def test_catalog_reads_clobs_only_when_a_path_overflows() -> None:
    """#4126: 長いパスがあるときだけ CLOB で読み直し、空のパスとして目録に残さない。"""
    long_path = "合成の長い親階層" * 170 + " > 第1条"
    calls: list[str] = []
    first_page = [
        {
            "document_id": "d1",
            "chunk_set_id": "s1",
            "file_name": "one.pdf",
            "path": "第2条",
            "path_json": None,
            "path_overflow": 0,
        },
        {
            "document_id": "d1",
            "chunk_set_id": "s1",
            "file_name": "one.pdf",
            "path": None,
            "path_json": None,
            "path_overflow": 1,
        },
        {
            "document_id": "d2",
            "chunk_set_id": "s2",
            "file_name": "two.pdf",
            "path": None,
            "path_json": None,
            "path_overflow": 1,
        },
    ]
    overflow = [
        {
            "document_id": "d1",
            "chunk_set_id": "s1",
            "file_name": "one.pdf",
            "path": long_path,
            "path_json": None,
        },
        {
            "document_id": "d1",
            "chunk_set_id": "s1",
            "file_name": "one.pdf",
            "path": long_path,
            "path_json": None,
        },
        {
            "document_id": "d2",
            "chunk_set_id": "s2",
            "file_name": "two.pdf",
            "path": None,
            "path_json": json.dumps(["合成の長い親階層" * 170, "第3条"], ensure_ascii=False),
        },
    ]

    class Oracle:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self.rows = rows

        async def _fetch_all(self, sql: str, _binds: object) -> list[dict[str, Any]]:
            calls.append(sql)
            if "RETURNING CLOB" in sql:
                # DISTINCT・ORDER BY は CLOB に使えない。
                assert "DISTINCT" not in sql and "ORDER BY" not in sql
                return overflow
            return self.rows

    catalog = await GraphReferenceStore(Oracle(first_page)).documents("kb")
    assert len(calls) == 2
    assert {item.document_id: item.section_paths for item in catalog} == {
        "d1": ("第2条", long_path),
        "d2": ("合成の長い親階層" * 170 + " > 第3条",),
    }

    calls.clear()
    short = [{**first_page[0]}]
    catalog = await GraphReferenceStore(Oracle(short)).documents("kb")
    assert len(calls) == 1
    assert [item.section_paths for item in catalog] == [("第2条",)]


def test_view_points_a_shortened_path_at_the_graph_node() -> None:
    """#4126: 表示名で保存した端点の ID は図のノード。図が未構築なら保存した値の位置の ID。"""
    from app.rag.graph_index import _clean_label, graph_section_node_id

    stored = _clean_label("合成の長い親階層" * 170 + " > 第2条")
    row: dict[str, Any] = dict(
        reference_id="ref",
        label="第2条",
        resolution_status="resolved",
        source_document_id="source",
        source_chunk_set_id="s1",
        source_section_path="第1条",
        source_name="source.txt",
        target_document_id="target",
        target_chunk_set_id="t1",
        target_section_path=stored,
        target_name="target.txt",
        active_target_set="t1",
        target_entity_id="f" * 64,
    )
    view = GraphReferenceStore._view("kb", row)
    assert view["target"]["id"] == "f" * 64
    assert view["target"]["name"] == stored
    assert view["source"]["id"] == graph_section_node_id("s1", "kb", "第1条")
    row["target_entity_id"] = None
    assert GraphReferenceStore._view("kb", row)["target"]["id"] == graph_section_node_id(
        "t1", "kb", stored
    )


def test_long_reference_locator_keeps_the_full_path_and_a_distinct_stable_id() -> None:
    from app.rag.graph_index import _graph_id

    common = "合成の長い親階層" * 80
    row: dict[str, Any] = dict(
        reference_id="ref",
        label="第2条",
        resolution_status="resolved",
        source_document_id="source",
        source_chunk_set_id="s1",
        source_section_path="第1条",
        source_name="source.txt",
        target_document_id="target",
        target_chunk_set_id="t1",
        target_section_path=common + " > 第2条",
        target_name="target.txt",
        active_target_set="t1",
    )
    first = GraphReferenceStore._view("kb", row)["target"]
    row["target_section_path"] = common + " > 第3条"
    second = GraphReferenceStore._view("kb", row)["target"]
    assert first["id"] != second["id"]
    assert first["section_path"] == common + " > 第2条"
    assert first["id"] != _graph_id("entity", "t1", "kb", first["name"][:512])


@pytest.mark.parametrize(
    ("path", "path_json", "expected"),
    [
        ("第1条", None, "第1条"),
        (None, '["第1章", "第1条"]', "第1章 > 第1条"),
        (None, '["第1章", 1]', "第1章 > 1"),
        (None, "null", ""),
        (None, '{"section": "第1条"}', ""),
        (None, None, ""),
    ],
)
def test_catalog_section_path_accepts_every_stored_json_shape(
    path: str | None, path_json: str | None, expected: str
) -> None:
    assert _catalog_section_path({"path": path, "path_json": path_json}) == expected
