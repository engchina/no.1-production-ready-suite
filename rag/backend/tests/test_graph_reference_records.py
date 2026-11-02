"""合成参照の版・未解決状態と、画面への権限漏れを確認する。"""

from app.clients.graph_reference_store import GraphReferenceStore
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
    args = dict(knowledge_base_id="kb", source=source, chunks=chunks)
    records = build_reference_records(**args, documents=(target,))
    assert len(records) == 1
    assert records[0].status == "resolved"
    assert records[0].target_document_id == "target"
    assert records[0].target_chunk_set_id == "t1"
    assert records == build_reference_records(**args, documents=(target,))
    missing = build_reference_records(**args, documents=())
    assert missing[0].status == "not_found"
    assert missing[0].reference_id == records[0].reference_id
    ambiguous = ReferenceDocument("another", "a1", target.name, target.section_paths)
    assert build_reference_records(**args, documents=(target, ambiguous))[0].status == "ambiguous"


def test_invisible_target_and_replaced_version_never_leak_target_locator() -> None:
    row = dict(
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
