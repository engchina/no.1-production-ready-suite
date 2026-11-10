"""目的逸脱、監査漏れ、原文ID、修正回数と出典保存の反例を検証する。"""

from rag_engine.generation.answering import (
    AnswerRecord,
)
from rag_engine.retrieval.evidence_selection import evidence_spans
from rag_engine.retrieval.task_contract import filter_queries


def record(uid, text="入口を押すと編集画面が開く。保存ボタンで確定する。", source="guide.pdf", metadata=None):
    return AnswerRecord(
        id="local",
        chunk_id="local",
        chunk_uid=uid,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source=source,
        metadata=dict(metadata or {}),
    )


def test_duplicate_parse_is_consolidated_without_merging_different_versions():
    same_document = {"document_id": "doc-a"}
    a = record("run-a:1", metadata=same_document)
    b = record("run-b:1", metadata=same_document)
    changed = record("run-c:1", "取消ボタンで戻る。", metadata=same_document)
    spans = evidence_spans("保存操作", [a, b, changed])
    assert len(spans) == 2
    assert spans[0]["source_aliases"] == ["run-a:1", "run-b:1"]
    assert spans[0]["evidence_id"] != spans[1]["evidence_id"]
    assert a.text[spans[0]["start"] : spans[0]["end"]] == spans[0]["text"]
    assert len(evidence_spans("保存操作", [record("x", source=""), record("y", source="")])) == 2
    # 文書の識別子が無い根拠は、表示名が同じでも同じ文書と確かめられないのでまとめない（#2603）。
    assert len(evidence_spans("保存操作", [record("run-a:1"), record("run-b:1")])) == 2


def test_filter_queries_rejects_only_business_ids_not_in_the_question():
    question = "申請を変更する方法を教えて"
    accepted, rejected = filter_queries(question, ["申請の変更手順", "XY99 の変更手順"])
    assert accepted == ("申請の変更手順",)
    assert rejected == ({"query": "XY99 の変更手順", "reason": "原質問・明示別名にない業務識別子"},)
    assert filter_queries(question, ["XY99 の変更手順"], grounded_text="XY99")[0] == ("XY99 の変更手順",)
