"""実体の 1 段の拡張で足した根拠（#1362）の扱い。

backend が metadata の ``entity_expansion`` に理由を付けた根拠（台帳の行・組織規程の略号の表など、質問の語と
重ならない橋渡しの根拠）は、rerank で位置を変えず、文書の選択で後回しにせず、context の親の枠を確保する。
印の無い根拠は今までと同じ。
"""

from dataclasses import replace
from types import SimpleNamespace

import rag_engine.generation.answering as answering
from rag_engine.config import get_settings
from rag_engine.generation.answer_records import is_entity_expansion_record
from rag_engine.generation.answering import AnswerRecord, rerank_records, select_documents
from rag_engine.retrieval.context_builder import ContextBuildRequest, build_chunk_context_bundle

EXPANSION = {"entity": "経理部", "hop": 1}


def _child(name, *, source="規程.pdf", parent=None, expansion=False, score=None):
    metadata = {"section_path": ["第 1 章"]}
    if expansion:
        metadata["entity_expansion"] = dict(EXPANSION)
    if score is not None:
        metadata["rerank"] = {"relevance_score": score}
    return AnswerRecord(id=name, engine="docling", engine_label="Docling", page=1, seq_no=1, category="Chunk",
                        text=f"{name} の本文", source=source, chunk_level="child", chunk_id=name,
                        chunk_uid=f"uid:{name}", parent_chunk_uid=f"uid:{parent or name}-p", source_run_id="run",
                        metadata=metadata)


def _parent(name, *, source="規程.pdf"):
    return AnswerRecord(id=f"{name}-p", engine="docling", engine_label="Docling", page=1, seq_no=1,
                        category="Chunk", text=f"{name} の親", source=source, chunk_level="parent",
                        chunk_id=f"{name}-p", chunk_uid=f"uid:{name}-p", source_run_id="run",
                        metadata={"section_path": ["第 1 章"]})


def _stub_reverse_rerank(monkeypatch):
    """rerank を、候補を逆順にする（末尾を 1 位にする）ものに差し替える。"""
    monkeypatch.setattr(answering, "_rerank_configured", lambda settings: True)

    def fake(question, documents, settings, top_n=None):
        order = list(reversed(range(len(documents))))
        return [SimpleNamespace(index=index, relevance_score=1.0 - position / 100)
                for position, index in enumerate(order)]

    monkeypatch.setattr(answering, "rerank_text_with_scores", fake)


def _settings():
    return SimpleNamespace(rerank_model="rerank-test", rerank_enabled=True, document_selection_enabled=False)


def test_marker_is_read_from_metadata():
    assert is_entity_expansion_record(_child("x", expansion=True))
    assert not is_entity_expansion_record(_child("y"))
    assert not is_entity_expansion_record(SimpleNamespace(metadata={"entity_expansion": "x"}))


def test_rerank_keeps_entity_expansion_at_its_position(monkeypatch):
    _stub_reverse_rerank(monkeypatch)
    records = [_child("a1"), _child("a2"), _child("e1", expansion=True), _child("a3"), _child("a4")]

    ranked = rerank_records("質問", records, _settings(), enabled=True, business_domains=(), screen_terms=())

    # 拡張の根拠は 3 番目のまま。ほかの候補は rerank の順（逆順）に並ぶ。
    assert [record.id for record in ranked] == ["a4", "a3", "e1", "a2", "a1"]
    assert ranked[2].metadata["rerank"]["skip_reason"] == "entity_expansion"


def test_rerank_without_marker_is_unchanged(monkeypatch):
    _stub_reverse_rerank(monkeypatch)
    records = [_child("a1"), _child("a2"), _child("a3")]

    ranked = rerank_records("質問", records, _settings(), enabled=True, business_domains=(), screen_terms=())

    assert [record.id for record in ranked] == ["a3", "a2", "a1"]


def test_document_selection_does_not_defer_entity_expansion():
    settings = replace(get_settings(environ={}, dotenv_path=None), document_selection_enabled=True,
                       document_selection_score_ratio=0.5, document_selection_min_hits=2)
    children = [_child("a1", source="a.pdf", score=0.9), _child("a2", source="a.pdf", score=0.8),
                _child("ledger", source="ledger.xlsx", expansion=True),
                _child("c1", source="c.pdf", score=0.1)]

    kept, deferred, _ = select_documents(children, settings)

    assert [record.id for record in kept] == ["a1", "a2", "ledger"]
    assert [record.id for record in deferred] == ["c1"]


def test_context_reserves_parent_slots_for_entity_expansion():
    """分数の順で親の枠（max_records）から落ちる拡張の根拠の親を、枠の後ろと入れ替えて確保する。"""
    names = [f"a{index}" for index in range(5)]
    children = [_child(name) for name in names] + [_child("e1", expansion=True), _child("e2", expansion=True)]
    parents = [_parent(name) for name in [*names, "e1", "e2"]]

    def build(records):
        return build_chunk_context_bundle(ContextBuildRequest(
            question="質問", ranked_children=records, active_records=[*records, *parents], top_k=10,
            neighbor_child_count=0, max_records=4, max_chars=5000))

    bundle = build(children)
    ids = [record.id for record in bundle.records]
    # 4 枠のうち半分（2）までを拡張の親に入れ替える。検索で当たった上位の親は残す。
    assert ids[:2] == ["a0-p", "a1-p"]
    assert set(ids) == {"a0-p", "a1-p", "e1-p", "e2-p"}

    plain = build([_child(name) for name in [*names, "e1", "e2"]])
    assert [record.id for record in plain.records] == ["a0-p", "a1-p", "a2-p", "a3-p"]
