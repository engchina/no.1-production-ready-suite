"""合成参照の版・未解決状態と、画面への権限漏れを確認する。"""

from functools import partial
from typing import Any

import pytest

from app.clients.graph_reference_store import GraphReferenceStore, _catalog_section_path
from app.rag.chunking import Chunk
from app.rag.reference_records import build_reference_records
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
