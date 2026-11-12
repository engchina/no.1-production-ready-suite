"""単件回答の正確性（標準回答との比較）の閾値判定と、拒答の判定。"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.config import Settings
from app.rag.evaluation_adapter import resolve_evaluation_adapter
from app.schemas.evaluation import EVALUATION_METRICS_VERSION
from app.schemas.search import RetrievedChunk


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


def score_answer_evaluation(evaluation: Mapping[str, Any], *, settings: Settings) -> dict[str, Any]:
    """LLM の判定(正確性)に、評価の基準の閾値と合否を足す。未完了の評価はそのまま。"""
    if evaluation.get("status") != "completed":
        return dict(evaluation)
    params = resolve_evaluation_adapter(settings)
    correctness = evaluation.get("answer_correctness")
    value = float(correctness) if isinstance(correctness, int | float) else None
    threshold = params.thresholds.answer_correctness
    passed = value >= threshold if value is not None and threshold is not None else None
    return {
        **evaluation,
        "metrics_version": EVALUATION_METRICS_VERSION,
        "suite": params.suite,
        "metrics": [
            {
                "name": "answer_correctness",
                "value": value,
                "threshold": threshold,
                "passed": passed,
                "reference": False,
            }
        ],
        "passed": passed,
    }
