"""回答のモデルのカスケード（#4581）: 機械で判定できる確認の不合格を集め、2 つ目のモデルへ昇格するかを決める。

昇格の規則（信号の語彙・1 回だけ・明示のモデルでは使わない）は platform の ``pr_backend_core.model_cascade`` の
1 か所にある。ここは回答の生成の段の結果（``grounded.Round``）から信号を集めることと、2 つ目のモデルで生成の段を
やり直すときの設定を作ることだけを持つ。

- 集める信号: 引用の ID・原文が一致しない（原文と照合できなかった説明・範囲の指定の誤り）、計算の再計算の不一致、
  要約・結論の門での保留、監査が有効なときの「資料と矛盾」。構造化出力の不正（再試行の後）は生成の例外で分かるので
  呼び出し側が足す。
- 集めない: 根拠 0 件・確認の質問・人への引き継ぎ・未確認や警告の印だけの段落・接続や 429・5xx の失敗。表の行が
  質問から決まらないための結論の保留と、結論の判定の呼び出しが失敗したための保留も集めない（2 つ目のモデルで
  生成をやり直しても変わらない）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from pr_backend_core.model_cascade import (
    SIGNAL_ARITHMETIC_MISMATCH,
    SIGNAL_AUDIT_CONTRADICTED,
    SIGNAL_CITATION_MISMATCH,
    SIGNAL_CONCLUSION_WITHHELD,
    ModelCascade,
    escalation_reasons,
    resolve_model_cascade,
)

from rag_engine.generation.summary_projection import APPLICATION_SUMMARY_WITHHELD

# 2 つ目のモデルでやり直す段（回答文の生成と根拠確認。段が特定できないときもこの段）。
ESCALATION_STAGE = "answer.generate"
# 結論の判定の診断の状態のうち、2 つ目のモデルで生成をやり直しても変わらない保留。
_UNCHANGED_BY_REGENERATION = frozenset({"withheld_table_not_determined"})


class EscalationRequested(Exception):  # noqa: N818 - 失敗ではなく、2 つ目のモデルへの昇格の要求
    """1 つ目のモデルの生成の段が確認を通らなかった（修正の回を 1 つ目のモデルで続けない）。"""

    def __init__(self, signals: tuple[str, ...]) -> None:
        super().__init__("model_cascade_escalation")
        self.signals = signals


@dataclass
class SharedSteps:
    """1 回の質問の中で、1 つ目と 2 つ目のモデルの実行が共有する段の結果（やり直さない段）。

    質問の対象の抽出は質問だけで決まり、表の行の判定は段のモデルで送った根拠だけから決まるので、2 つ目のモデルの
    実行で呼び直さない。用語の照合結果は 1 回の回答で 1 回だけ記録する。
    """

    question_subjects: Callable[[], Any] | None = None
    row_conditions: dict[tuple[str, ...], Any] = field(default_factory=dict)
    term_usage_recorded: bool = False


def answer_cascade(settings: Any, provider_id: str | None) -> ModelCascade:
    """回答のモデル（``provider_id``）と、確認を通らなかったときのモデルのカスケード。設定が無ければ使わない。"""
    providers = getattr(settings, "llm_providers", None) or {}
    escalation_id = str(getattr(settings, "escalation_llm", "") or "")
    primary_id = provider_id or str(getattr(settings, "default_answer_llm", "") or "")
    primary = getattr(providers.get(primary_id), "model", "") if primary_id else ""
    escalation = getattr(providers.get(escalation_id), "model", "") if escalation_id else ""
    if escalation_id and escalation_id == primary_id:
        escalation = ""
    return resolve_model_cascade(primary, escalation)


def escalated_settings(settings: Any) -> Any:
    """2 つ目のモデルで生成の段をやり直す設定（回答のモデルをそのモデルにし、入力の上限もそのモデルの値にする）。

    段ごとに固定のモデル（``STEP_LLM_PROVIDERS``）はそのまま。固定のモデルの無い段は回答のモデルに従う。
    やり直しの中で再び昇格しないよう、カスケードを外す。
    """
    return replace(
        settings,
        default_answer_llm=settings.escalation_llm,
        answer_max_input_tokens=settings.escalation_max_input_tokens,
        escalation_llm="",
    )


def round_signals(current: Any, *, audit_mode: str) -> tuple[str, ...]:
    """生成の段の 1 回の結果（``grounded.Round``）から、昇格の信号を集める。"""
    from rag_engine.generation import grounded

    signals: list[str] = []
    citations = [own for entry in current.checked for own in grounded.item_citations(entry)]
    range_errors = any(own.citation_resolution.get("reason_code") in grounded.CITATION_RANGE_ERROR_CODES for own in citations) or any(
        isinstance(note.get("citation_resolution"), dict)
        and note["citation_resolution"].get("reason_code") in grounded.CITATION_RANGE_ERROR_CODES
        and not note.get("ignored")
        for note in current.dropped
    )
    if range_errors or any(own.unmatched for own in citations):
        signals.append(SIGNAL_CITATION_MISMATCH)
    if any(getattr(application, "arithmetic_issues", None) for application in current.applications):
        signals.append(SIGNAL_ARITHMETIC_MISMATCH)
    if conclusion_withheld(current):
        signals.append(SIGNAL_CONCLUSION_WITHHELD)
    # 監査が「資料と矛盾」と判定して外した説明（表の対応の取り違えで外した説明は決定的な検査なので含めない）。
    contradicted = any(entry.removed and entry.withdrawn and not entry.table_conflict for entry in current.checked)
    if audit_mode != grounded.AUDIT_MODE_NONE and contradicted:
        signals.append(SIGNAL_AUDIT_CONTRADICTED)
    return escalation_reasons(signals)


def conclusion_withheld(current: Any) -> bool:
    """要約・結論を門で保留したか（2 つ目のモデルで生成をやり直しても変わらない保留を除く）。"""
    reason = str(getattr(current, "summary_reason", "") or "")
    if not reason:
        return False
    if reason != APPLICATION_SUMMARY_WITHHELD:
        return True
    review = (getattr(current, "timing", None) or {}).get("application_review") or {}
    return not (review.get("status") in _UNCHANGED_BY_REGENERATION or review.get("error_type"))
