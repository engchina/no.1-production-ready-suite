"""下限の実判定と原質問・採用スコアを、追加の採点なしで説明する。"""

from dataclasses import replace
from types import SimpleNamespace

from rag_engine.config import get_settings
from rag_engine.generation import answering
from rag_engine.generation.answer_records import _records_by_rerank_ranks
from rag_engine.models.llm import RerankTextRank
from rag_engine.retrieval.attachment_evidence import ATTACHMENT_EVIDENCE_KEY
from tests.test_document_selection import child


class Step:
    def __init__(self):
        self.fields, self.lines = {}, []

    def field(self, label, value):
        self.fields[label] = value

    def add(self, line):
        self.lines.append(line)


def test_threshold_trace_matches_actual_exclusion_and_preserves_unscored_and_attachment(monkeypatch):
    records = [child(str(i), i, f"合成本文 {i}", f"合成{i}.txt", f"p{i}") for i in range(5)]
    records[2].metadata[ATTACHMENT_EVIDENCE_KEY] = True
    settings = replace(get_settings(environ={}, dotenv_path=None), rerank_min_relevance_score=0.4)
    calls, stages = [], []

    def provider(question, documents, settings, top_n):
        calls.append(question)
        scores = [0.2, 0.3, 0.1, None] if question == "原質問" else [0.8, 0.399, 0.1, None]
        return [RerankTextRank(i, score) for i, score in enumerate(scores)]

    monkeypatch.setattr(answering, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(answering, "rerank_text_with_scores", provider)
    monkeypatch.setattr(answering, "record_retrieval_stage", stages.append)
    step = Step()
    kept = answering._rerank_records("原質問", records, settings, step, enabled=True, candidate_limit=None, extra_queries=["追加質問"])
    assert calls == ["原質問", "追加質問"]
    assert {record.id for record in kept} == {"0", "2", "3", "4"}
    trace = next(row for row in stages if row["stage"] == "rerank_threshold")
    assert trace["minimum"] == 0.4
    assert trace["excluded_count"] == 1
    assert trace["unscored_kept_count"] == 2
    assert trace["attachment_kept_count"] == 1
    decisions = {row["candidate_index"]: row for row in trace["decisions"]}
    assert decisions[0]["question_score"] == 0.2
    assert decisions[0]["adopted_score"] == 0.8
    assert decisions[1]["status"] == "excluded"
    assert decisions[2]["status"] == "kept_attachment"
    assert decisions[4]["status"] == "kept_unscored"
    assert step.fields["関連度スコアの下限"] == "0.4"
    assert "下限未満で除外 1 件" in step.fields["関連度による選別"]
    assert all("None" not in line for line in step.lines)
    assert any("未採点" in line for line in step.lines)


def test_missing_original_score_is_not_replaced_by_supplement_score(monkeypatch):
    record = child("a", 1, "合成本文", "合成.txt", "p")
    monkeypatch.setattr(
        answering, "rerank_text_with_scores", lambda question, *args, **kwargs: [RerankTextRank(0, None if question == "原質問" else 0.8)]
    )
    ranks = answering._best_rerank_ranks("原質問", ["追加質問"], [record.text], None, SimpleNamespace(add=lambda _: None))
    decisions = []
    settings = replace(get_settings(environ={}, dotenv_path=None), rerank_min_relevance_score=0.4)
    kept = _records_by_rerank_ranks([record], ranks, settings, decisions=decisions)
    assert len(kept) == 1
    assert decisions[0]["question_score"] is None
    assert decisions[0]["adopted_score"] == 0.8
    assert kept[0].metadata["rerank"]["question_relevance_score"] is None
    from rag_engine.generation.answer_payload import _rerank_scores_from_evidence

    exported = _rerank_scores_from_evidence(kept, ())
    assert exported[0]["question_relevance_score"] is None
    assert exported[0]["question_score_explicit"] is True


def test_missing_original_score_remains_unscored_in_consumers():
    from rag_engine.retrieval.rerank_scores import question_rerank_score, rerank_score

    record = child("a", 1, "合成本文", "合成.txt", "p")
    record.metadata["rerank"] = {"relevance_score": 0.8, "question_relevance_score": None, "question_score_explicit": True}
    assert question_rerank_score(record) is None
    assert rerank_score(record) == 0.8
    record.metadata["rerank"].pop("question_score_explicit")
    assert question_rerank_score(record) == 0.8
