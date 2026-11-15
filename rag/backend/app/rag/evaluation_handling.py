"""品質評価の回答の対応の記録。

回答の対応は、回答の記録の `diagnostics.answer.outcome` があればそれを使う。無いときは、拒答・
現場のデータが要るか（`external_data_required`）・確認の質問・人の確認が要るか（`needs_human_review`）
から推定する（推定であることを `outcome_source` に残す）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import get_args

from app.schemas.evaluation import EvaluationOutcome

EVALUATION_OUTCOMES: frozenset[str] = frozenset(get_args(EvaluationOutcome))


@dataclass(frozen=True)
class ObservedOutcome:
    outcome: EvaluationOutcome
    # explicit: 回答の記録が対応を持っていた / inferred: 診断から推定した
    source: str


def observed_outcome(
    answer_details: Mapping[str, object] | None, *, abstained: bool
) -> ObservedOutcome:
    """回答の対応（answered / conditional / needs_clarification / … ）。"""
    details = answer_details or {}
    explicit = details.get("outcome")
    if isinstance(explicit, str) and explicit in EVALUATION_OUTCOMES:
        return ObservedOutcome(explicit, "explicit")  # type: ignore[arg-type]
    if abstained:
        return ObservedOutcome("insufficient_evidence", "inferred")
    if details.get("clarification") or details.get("clarification_question"):
        return ObservedOutcome("needs_clarification", "inferred")
    if details.get("external_data_required"):
        return ObservedOutcome("needs_environment_data", "inferred")
    if details.get("needs_human_review"):
        return ObservedOutcome("conditional", "inferred")
    return ObservedOutcome("answered", "inferred")


__all__ = [
    "EVALUATION_OUTCOMES",
    "ObservedOutcome",
    "observed_outcome",
]
