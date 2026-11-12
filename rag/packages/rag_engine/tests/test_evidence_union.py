"""集合和で原文・順番・保存版と通常検索の採用数が保たれることを確認する。"""

from dataclasses import replace

from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.evidence_union import union_sources


def record(uid: str, *, version: str = "version-a") -> AnswerRecord:
    return AnswerRecord(
        id=uid,
        chunk_uid=uid,
        engine="synthetic",
        engine_label="合成",
        page=1,
        seq_no=1,
        category="ChildChunk",
        text="架空の原文。" * 2000,
        metadata={"document_id": "document-a", "chunk_set_id": version},
    )


def test_union_keeps_native_order_and_all_original_text_without_score_fusion() -> None:
    native = [record(str(i)) for i in range(20)]
    additional = [record("0"), record("wiki"), record("0", version="version-b")]
    context = AnswerContext(records=native, text="元の表示")
    result = union_sources(context, additional)
    assert [r.id for r in result.records] == [r.id for r in native] + ["wiki", "0"]
    assert result.records[0].metadata["wiki_source"] is True
    assert result.records[1:] == native[1:] + additional[1:]
    assert len(result.records[-1].text) == len(additional[-1].text)
    assert context.records == native


def test_same_text_in_different_documents_and_ranges_is_not_deduplicated() -> None:
    first = record("same")
    second = replace(first, metadata={**first.metadata, "document_id": "document-b"})
    third = replace(first, source_seq_ranges=({"start": 2, "end": 3},))
    result = union_sources(AnswerContext(records=[first], text=first.text), [second, third])
    assert len(result.records) == 3


def test_no_additions_preserves_context_object() -> None:
    context = AnswerContext(records=[], text="")
    assert union_sources(context, []) is context
