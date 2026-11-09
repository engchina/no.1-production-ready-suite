"""回答のモデルのカスケード（#4581）: 構造化出力が契約を満たさなかったときだけ、2 つ目のモデルでやり直す。

昇格の規則（信号の語彙・1 回だけ・明示のモデルでは使わない）は platform の ``pr_backend_core.model_cascade`` にある。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from pr_backend_core.model_cascade import ModelCascade, resolve_model_cascade

# 2 つ目のモデルでやり直す段（回答文の生成と根拠確認。段が特定できないときもこの段）。
ESCALATION_STAGE = "answer.generate"


class EscalationRequested(Exception):  # noqa: N818 - 失敗ではなく、2 つ目のモデルへの昇格の要求
    """1 つ目のモデルの生成の段の出力が形式の検査を通らなかった。"""

    def __init__(self, signals: tuple[str, ...]) -> None:
        super().__init__("model_cascade_escalation")
        self.signals = signals


@dataclass
class SharedSteps:
    """1 回の質問の中で、1 つ目と 2 つ目のモデルの実行が共有する段の結果（やり直さない段）。

    質問の対象の抽出は質問だけで決まるので、2 つ目の実行で呼び直さない。用語の照合結果は 1 回の回答で 1 回だけ記録する。
    """

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

    やり直しの中で再び昇格しないよう、カスケードを外す。
    """
    return replace(
        settings,
        default_answer_llm=settings.escalation_llm,
        answer_max_input_tokens=settings.escalation_max_input_tokens,
        escalation_llm="",
    )
