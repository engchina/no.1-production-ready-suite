"""異機能合成の防止と最終監査からの一度限りの回復を検証する。"""

from rag_engine.generation.answering import AnswerContext, AnswerRecord, AnswerResponse
from rag_engine.retrieval.evidence_scope import evidence_scope_error


def record(name, seq, text, level="child", parent="p1", operation="管理-(1)登録", run="run"):
    return AnswerRecord(
        id=name,
        engine="docling",
        engine_label="Docling",
        page=seq,
        seq_no=seq,
        category="Chunk",
        text=text,
        source="manual.pdf",
        source_run_id="source",
        chunk_id=name,
        chunk_uid=f"{run}:{name}",
        chunk_level=level,
        chunk_seq=seq,
        parent_chunk_uid=f"{run}:{parent}" if parent else "",
        metadata={"section_path": [operation]},
    )


def span(name, text="", **extra):
    return dict(section_path=["管理-(1)" + name], text=text, **extra)


def test_document_versions_are_checked_without_classification_restrictions():
    assert evidence_scope_error([span("登録", document_scope=["doc", "1"]), span("登録", document_scope=["doc", "2"])])
    assert not evidence_scope_error([span("登録", business_scope="分類A"), span("登録", business_scope="分類B")])
    assert not evidence_scope_error([span("登録", document_scope=["doc", "1"]), span("登録", document_scope=["doc", "1"])])
    assert not evidence_scope_error([span("登録"), span("登録")])


def response(status="partial", supported=()):
    return AnswerResponse(
        answer_text="支持された部分回答",
        generation_trace={
            "reviews": [
                {
                    "status": "completed",
                    "complete": True,
                    "reviews": [],
                    "request_reviews": [dict(request_id="Q1", status=status), *[dict(request_id=i, status="addressed") for i in supported]],
                }
            ]
        },
    )


def context():
    a = record("p1", 1, "画面を開く。", "parent", "")
    b = record("p2", 2, "条件を指定して実行する。", "parent", "")
    return AnswerContext(records=[a], text=a.text, expansion_records=(a, b))
