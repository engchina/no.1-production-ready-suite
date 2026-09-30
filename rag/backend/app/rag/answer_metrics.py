"""標準回答による評価の指標と合否(評価の基準の指標と閾値。#680)。

評価の基準の 9 指標のうち、1 件の回答と標準回答で測れる 5 つを、評価の基準で選んだ suite
(standard / strict)の閾値で判定する。すべて閾値以上なら合格(品質評価の answer_pass_rate が
数える合否)。正解の文書が要る context_recall / mrr と、期待する語が要る answer_keyword_hit_rate
は測らない。

- faithfulness / citation_traceability_coverage: 品質評価と同じ決定的な計算(回答と引用)。
  faithfulness は語句の一致による近似で、1 件の回答では言い換えだけで閾値を下回る。同じ観点は
  LLM の主張の監査(claim_support_rate)が判定するので、1 件の回答では参考値にして合否に使わない
- claim_support_rate: LLM の主張の監査。根拠のない主張・矛盾する主張が無ければ 1
- requirement_coverage: LLM の照合。標準回答の required 項目の対応率
- refusal_accuracy: 標準回答がある質問は答えるべき質問。拒答なら 0
"""

from collections.abc import Mapping, Sequence
from typing import Any

from rag_engine.generation.grounded import NEUTRAL_SUMMARY, UNVERIFIED_NOTE, is_structural_line

from app.config import Settings
from app.rag.evaluation_adapter import resolve_evaluation_adapter
from app.rag.file_processing_evaluation import citation_traceability_coverage
from app.rag.guardrails import evaluate_groundedness
from app.schemas.search import RetrievedChunk

UNSUPPORTED_CLAIM_STATUSES = frozenset({"unsupported", "contradicted"})
# 1 件の回答では表示だけにして、合否に使わない指標。
REFERENCE_ONLY_METRICS = frozenset({"faithfulness"})


def grounding_text(answer: str) -> str:
    """忠実さの照合に使う回答の本文。回答生成が付けた見出し・出典行・定型文は主張ではないので外す。"""
    lines = answer.replace(UNVERIFIED_NOTE, "").splitlines()
    return "\n".join(
        line
        for line in lines
        if line.strip() and not is_structural_line(line) and line.strip() != NEUTRAL_SUMMARY
    )


def is_abstained_answer(
    answer: str, citations: Sequence[RetrievedChunk], insufficient_reason: str
) -> bool:
    """回答が「資料から答えられない」旨だけか(拒答)。

    本文か引用が無い回答は拒答。不足の理由があり、モデルが使った根拠が 1 つも無い回答も拒答と
    する(必要な根拠を原文のまま示すだけの回答を含む)。
    """
    if not answer.strip() or not citations:
        return True
    if not insufficient_reason.strip():
        return False
    return not any(chunk.metadata.get("evidence_model_used") for chunk in citations)


def claims_supported(claims: Sequence[Mapping[str, Any]]) -> bool:
    """根拠のない主張・根拠と矛盾する主張が無いか。"""
    return not any(str(claim.get("status")) in UNSUPPORTED_CLAIM_STATUSES for claim in claims)


def score_answer_evaluation(
    evaluation: Mapping[str, Any],
    *,
    answer: str,
    citations: Sequence[RetrievedChunk],
    insufficient_reason: str,
    settings: Settings,
) -> dict[str, Any]:
    """LLM の判定(照合・監査)に、評価の基準の指標・閾値・合否を足す。未完了の評価はそのまま。"""
    if evaluation.get("status") != "completed":
        return dict(evaluation)
    params = resolve_evaluation_adapter(settings)
    abstained = is_abstained_answer(answer, citations, insufficient_reason)
    claims = [claim for claim in evaluation.get("claim_checks") or [] if isinstance(claim, Mapping)]
    coverage = evaluation.get("requirement_coverage")
    values: dict[str, float | None] = {
        "faithfulness": (
            None
            if abstained
            else evaluate_groundedness(
                grounding_text(answer), "\n".join(chunk.text for chunk in citations)
            ).score
        ),
        "citation_traceability_coverage": (
            citation_traceability_coverage(citations) if citations else None
        ),
        "claim_support_rate": 1.0 if claims_supported(claims) else 0.0,
        "requirement_coverage": float(coverage) if isinstance(coverage, int | float) else None,
        "refusal_accuracy": 0.0 if abstained else 1.0,
    }
    metrics = []
    for name, value in values.items():
        threshold = getattr(params.thresholds, name)
        if value is None or threshold is None:
            continue
        metrics.append(
            {
                "name": name,
                "value": round(value, 4),
                "threshold": threshold,
                "passed": value >= threshold,
                "reference": name in REFERENCE_ONLY_METRICS,
            }
        )
    judged = [metric for metric in metrics if not metric["reference"]]
    return {
        **evaluation,
        "suite": params.suite,
        "metrics": metrics,
        "passed": bool(judged) and all(metric["passed"] for metric in judged),
    }
