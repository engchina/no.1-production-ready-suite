"""会話の添付の根拠は、起点・親の数の外に足し、文書ごとの上限に数えない（#3100・#3212）。"""

from dataclasses import replace

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.retrieval.attachment_evidence import ATTACHMENT_EVIDENCE_KEY
from tests.record_fixtures import child


def test_explicit_attachment_is_not_counted_by_the_document_cap():
    attached = child("attachment", 1, "入力の行", "input.csv", "attached", 0.1)
    attached.metadata[ATTACHMENT_EVIDENCE_KEY] = True
    other = child("a", 2, "規則", "a.pdf", "pa", 0.9)
    settings = replace(get_settings(environ={}, dotenv_path=None), max_anchors_per_document=1)
    assert a.cap_anchors_per_document([attached, attached, other], settings) == [attached, attached, other]


def _context_record(name, level):
    from rag_engine.generation.answering import AnswerRecord

    return AnswerRecord(
        id=name if level == "child" else f"{name}-p",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=name,
        source="架空手引き.pdf",
        chunk_level=level,
        chunk_id=name if level == "child" else f"{name}-p",
        chunk_uid=f"uid:{name}" if level == "child" else f"uid:{name}-p",
        parent_chunk_uid=f"uid:{name}-p" if level == "child" else "",
        source_run_id="run",
        metadata={"section_path": ["第 1 章"]},
    )


def _bundle(children, *, top_k, max_records):
    from rag_engine.retrieval.context_builder import ContextBuildRequest, build_chunk_context_bundle

    parents = [_context_record(r.id, "parent") for r in children]
    return build_chunk_context_bundle(
        ContextBuildRequest(
            question="質問",
            ranked_children=children,
            active_records=[*children, *parents],
            top_k=top_k,
            neighbor_child_count=0,
            max_records=max_records,
            max_chars=5000,
        )
    )


def _attachment_context_record(name):
    from rag_engine.retrieval.attachment_evidence import ATTACHMENT_EVIDENCE_KEY

    record = _context_record(name, "child")
    record.metadata[ATTACHMENT_EVIDENCE_KEY] = True
    return record


def test_attachment_below_the_anchor_limit_is_added_outside_it():
    """rerank で top_k より下の会話の添付も、起点・親の数の外に足して根拠に入れる（#3212）。"""
    children = [_context_record(f"m{index}", "child") for index in range(5)]
    attached = _attachment_context_record("att1")

    bundle = _bundle([*children, attached], top_k=3, max_records=3)

    # ナレッジベースの起点・親は添付が無いときと同じ 3 つで、添付の親を後ろに足す。
    assert [r.id for r in bundle.records] == ["m0-p", "m1-p", "m2-p", "att1-p"]
    assert bundle.evidence_tree[-1].reason == "conversation_attachment"
    assert [child.role for child in bundle.evidence_tree[-1].children] == ["retrieved_anchor"]


def test_attachment_inside_the_anchor_limit_does_not_take_a_knowledge_base_anchor():
    """top_k の内に並んだ添付も起点に数えず、ナレッジベースの起点を減らさない（#3212）。"""
    children = [_context_record(f"m{index}", "child") for index in range(5)]
    attached = _attachment_context_record("att1")

    bundle = _bundle([attached, *children], top_k=3, max_records=3)
    plain = _bundle(children, top_k=3, max_records=3)

    assert [r.id for r in plain.records] == ["m0-p", "m1-p", "m2-p"]
    assert [r.id for r in bundle.records] == ["m0-p", "m1-p", "m2-p", "att1-p"]
