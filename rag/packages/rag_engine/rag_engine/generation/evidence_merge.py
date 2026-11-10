"""取得済みの根拠の統合。LLM と検索は呼ばない。"""

from __future__ import annotations

from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.retrieval.context_builder import context_bundle_from_parent_evidence, record_context_key
from rag_engine.retrieval.evidence_selection import evidence_packet, evidence_relevance
from rag_engine.retrieval.rerank_scores import best_rerank_score, rerank_first_key


def merge_evidence(previous: AnswerContext, current: AnswerContext, question: str, *, max_records: int, max_chars: int) -> AnswerContext:
    """既取得の出典を最大2回分保持する。満杯時は rerank の点数の高い順に選ぶ（#3504）。

    親の点数は、その親の起点の child の rerank の点数の最高値（記録の metadata か根拠の木の起点）。点数の無い
    親は点数のある親の後ろに置き、語の一致の点数（質問の語の重なり）は同点の順と点数の無い親の順だけに使う。
    """
    if not previous.records:
        return current
    by_key = {record_context_key(r) or r.id: r for r in previous.records}
    by_key.update({record_context_key(r) or r.id: r for r in current.records})
    records = list(by_key.values())
    limit = max_records * 2
    if len(records) > limit:
        parents = (*previous.evidence_tree, *current.evidence_tree)
        tree_scores = {
            record_context_key(p.record) or p.record.id: best_rerank_score(
                [p.record, *(child.record for child in p.children if child.role == "retrieved_anchor")]
            )
            for p in parents
        }

        def score(record: AnswerRecord) -> float | None:
            own = best_rerank_score([record])
            return own if own is not None else tree_scores.get(record_context_key(record) or record.id)

        records = sorted(records, key=lambda r: (*rerank_first_key(score(r)), -evidence_relevance(question, r.text)))[:limit]
    keys = {record_context_key(r) or r.id for r in records}
    tree = {record_context_key(p.record) or p.record.id: p for p in previous.evidence_tree}
    tree.update({record_context_key(p.record) or p.record.id: p for p in current.evidence_tree})
    if tree and all(key in tree for key in keys):
        bundle = context_bundle_from_parent_evidence(tuple(tree[record_context_key(r) or r.id] for r in records), max_chars=max_chars)
        return AnswerContext(records=list(bundle.records), text=bundle.text, evidence=bundle.evidence, evidence_tree=bundle.evidence_tree)
    return AnswerContext(records=records, text=evidence_packet(question, records))
