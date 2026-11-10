"""成功した重排の採用上限と、引用の依存関係・明示添付の保持。"""

from types import SimpleNamespace

import pytest

import rag_engine.generation.answering as answering
from rag_engine.generation.answering import AnswerRecord, rerank_records
from rag_engine.retrieval.rerank_selection import select_reranked_candidates


def child(index, *, source=None, attachment=False):
    return AnswerRecord(
        id=str(index),
        engine="text",
        engine_label="Text",
        page=1,
        seq_no=index,
        category="Chunk",
        text=f"合成の説明 {index}",
        source="sample.txt",
        chunk_level="child",
        chunk_id=str(index),
        chunk_uid=f"uid:{index}",
        source_run_id="run",
        metadata={
            **({"reference_expansion": {"from_chunk_uid": f"uid:{source}"}} if source is not None else {}),
            **({"is_attachment_chunk": True} if attachment else {}),
        },
    )


def test_reference_groups_are_atomic_and_oversized_groups_do_not_expand_limit():
    records = [child(0), child(1), child(2, source=0)]
    selected, detail = select_reranked_candidates(records, 2)
    assert [r.id for r in selected] == ["0", "2"]
    assert detail["ordinary_selected_count"] == 2
    selected, detail = select_reranked_candidates(records, 1)
    assert [r.id for r in selected] == ["1"]
    assert detail["groups_over_budget"] == 1


def test_attachment_and_its_reference_group_are_preserved_separately():
    records = [child(0), child(1, attachment=True), child(2, source=1), child(3)]
    selected, detail = select_reranked_candidates(records, 1)
    assert [r.id for r in selected] == ["0", "1", "2"]
    assert detail["ordinary_selected_count"] == 1
    assert detail["attachment_related_count"] == 2


def test_cycles_and_reference_before_source_do_not_split_groups():
    a, b, c = child(0, source=1), child(1, source=0), child(2)
    assert select_reranked_candidates([a, b, c], 1)[0] == [c]
    assert select_reranked_candidates([a, b, c], 2)[0] == [a, b]
    assert select_reranked_candidates([a, b, c], 0)[0] == [a, b, c]


@pytest.mark.parametrize("mode", ["off", "unconfigured", "failed", "unscored", "success", "unlimited"])
def test_selection_only_applies_after_successful_rerank(monkeypatch, mode):
    records = [child(i) for i in range(4)]
    settings = SimpleNamespace(
        rerank_model="test", rerank_top_n=0 if mode == "unlimited" else 2, rerank_min_relevance_score=0, document_selection_enabled=False
    )
    monkeypatch.setattr(answering, "_rerank_configured", lambda _: mode != "unconfigured")
    requests = []

    def rank(question, documents, settings, top_n=None):
        requests.append(top_n)
        if mode == "failed":
            raise RuntimeError("synthetic failure")
        return [SimpleNamespace(index=i, relevance_score=None if mode == "unscored" else 1 - i / 10) for i in range(len(documents))]

    monkeypatch.setattr(answering, "rerank_text_with_scores", rank)
    result = rerank_records("合成の質問", records, settings, enabled=mode != "off")
    assert len(result) == (2 if mode == "success" else 4)
    if requests:
        assert requests == [4]  # OCI へは全候補の採点を依頼する。


def test_large_answer_top_k_does_not_restore_excluded_search_anchors(monkeypatch):
    from dataclasses import replace

    from rag_engine.config import get_settings
    from tests.test_document_selection import child as context_child
    from tests.test_document_selection import parent

    records = [context_child(str(i), i + 1, f"合成本文 {i}", f"sample-{i}.txt", f"p{i}") for i in range(4)]
    parents = [parent(f"p{i}", i + 1, f"合成本文 {i}", f"sample-{i}.txt") for i in range(4)]
    settings = replace(get_settings(environ={}, dotenv_path=None), rerank_top_n=2, rerank_min_relevance_score=0)
    monkeypatch.setattr(answering, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(
        answering,
        "rerank_text_with_scores",
        lambda q, docs, s, top_n=None: [SimpleNamespace(index=i, relevance_score=1 - i / 10) for i in range(len(docs))],
    )
    selected = rerank_records("合成の質問", records, settings, enabled=True)
    bundle = answering._bundle_context(
        "合成の質問", selected, [*records, *parents], settings, top_k=50, neighbor_child_count=0, max_records=50, max_chars=5000
    )
    assert {record.id for record in bundle.records} == {"p0", "p1"}


def test_unscored_remainder_does_not_bypass_output_limit(monkeypatch):
    records = [child(i) for i in range(4)]
    settings = SimpleNamespace(rerank_model="test", rerank_top_n=1, rerank_min_relevance_score=0)
    monkeypatch.setattr(answering, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(answering, "rerank_text_with_scores", lambda *a, **k: [SimpleNamespace(index=0, relevance_score=0.9)])
    result = rerank_records("合成の質問", records, settings, enabled=True, candidate_limit=1)
    assert [record.id for record in result] == ["0"]


def test_each_followup_rerank_selects_from_its_own_candidates(monkeypatch):
    settings = SimpleNamespace(rerank_model="test", rerank_top_n=1, rerank_min_relevance_score=0)
    monkeypatch.setattr(answering, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(
        answering,
        "rerank_text_with_scores",
        lambda q, docs, s, **k: [SimpleNamespace(index=i, relevance_score=1 - i / 10) for i in range(len(docs))],
    )
    first = rerank_records("合成の質問", [child(0), child(1)], settings, enabled=True)
    next_round = rerank_records("合成の追加質問", [child(2), child(3)], settings, enabled=True)
    assert [record.id for record in first] == ["0"]
    assert [record.id for record in next_round] == ["2"]


def test_shared_reference_keeps_all_source_dependencies_in_one_group():
    from dataclasses import replace

    a, b, reference, other = child(0), child(1), child(2, source=0), child(3)
    reference = replace(reference, metadata={"reference_expansion": {"from_chunk_uid": "uid:0", "from_chunk_uids": ["uid:0", "uid:1"]}})
    assert select_reranked_candidates([a, b, reference, other], 2)[0] == [other]
    assert select_reranked_candidates([a, b, reference, other], 3)[0] == [a, b, reference]
