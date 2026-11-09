"""単件回答の意味上の正確性・網羅性・根拠への忠実性と、測定できた指標の閾値判定。

標準回答がない場合は正確性・網羅性を未評価とし、主張の支持は回答と引用だけで測る。"""

from collections.abc import Mapping, Sequence
from typing import Any

from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.grounded import (
    generated_section_lines,
    is_structural_line,
)

from app.config import Settings
from app.rag.evaluation_adapter import resolve_evaluation_adapter
from app.schemas.search import RetrievedChunk

UNSUPPORTED_CLAIM_STATUSES = frozenset({"unsupported", "contradicted"})
# 1 件の回答では表示だけにして、合否に使わない指標。
REFERENCE_ONLY_METRICS: frozenset[str] = frozenset()


def grounding_text(answer: str) -> str:
    """忠実さの照合に使う回答の本文。

    回答生成が付けた見出し・出典行・定型文（「参考情報」の節の直下の説明を含む。#2040）と、
    Agent の回答の節（旧形式を含む）の扱いは suite の
    Agent の adapter（`suite/evaluation`。#3240）にあり、RAG は Agent の回答の形式を持たない。
    """
    # 読めなかった章節・時間の上限で省いた処理の行もシステムの案内なので外す（#2040）。
    generated = generated_section_lines(answer)
    return "\n".join(
        line
        for line in answer.splitlines()
        if line.strip()
        and not is_structural_line(line)
        and line.strip() != answer_labels().neutral_summary
        and line.strip() not in generated
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
    # 親（まとまり）だけを参照した根拠（#2710）も、回答が資料を使った印にする。
    return not any(
        chunk.metadata.get("evidence_model_used") or chunk.metadata.get("evidence_parent_used")
        for chunk in citations
    )


def claims_supported(claims: Sequence[Mapping[str, Any]]) -> bool | None:
    """根拠のない主張・根拠と矛盾する主張が無いか。"""
    if any(str(claim.get("status")) in UNSUPPORTED_CLAIM_STATUSES for claim in claims):
        return False
    if any(claim.get("status") in {"unassessed", "citation_error"} for claim in claims):
        return None
    return True


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
    claims = [claim for claim in evaluation.get("claim_checks") or [] if isinstance(claim, Mapping)]
    assessed = [
        claim for claim in claims if claim.get("status") not in {"not_a_claim", "data_confirmation"}
    ]
    support = (
        sum(claim.get("status") == "supported" for claim in assessed) / len(assessed)
        if assessed
        and citations
        and all(
            claim.get("status") in {"supported", "unsupported", "contradicted"}
            for claim in assessed
        )
        else None
    )
    coverage = evaluation.get("requirement_coverage")
    correctness = evaluation.get("answer_correctness")
    values: dict[str, float | None] = {
        "answer_correctness": float(correctness) if isinstance(correctness, int | float) else None,
        "requirement_coverage": float(coverage) if isinstance(coverage, int | float) else None,
        "claim_support_rate": support,
    }
    metrics = []
    for name, value in values.items():
        threshold = getattr(params.thresholds, name)
        metrics.append(
            {
                "name": name,
                "value": round(value, 4) if value is not None else None,
                "threshold": threshold,
                "passed": value >= threshold
                if value is not None and threshold is not None
                else None,
                "reference": name in REFERENCE_ONLY_METRICS,
            }
        )
    judged = [metric for metric in metrics if metric["passed"] is not None]
    incomplete = any(claim.get("status") in {"unassessed", "citation_error"} for claim in assessed)
    passed = (
        False
        if any(metric["passed"] is False for metric in judged)
        else None
        if incomplete or not judged
        else True
    )
    return {
        **evaluation,
        "metrics_version": "six_metrics_v1",
        "claim_support_rate": support,
        "suite": params.suite,
        "metrics": metrics,
        "passed": passed,
    }
