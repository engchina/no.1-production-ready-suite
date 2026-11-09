"""モデルのカスケード: 確認を通らなかったときだけ 2 つ目のモデルで回答し直す（#4581）。

1 つ目のモデル（低コスト）の出力が、機械で判定できる確認を通らなかったときだけ、2 つ目のモデル
（高性能）で失敗した段をやり直す（FrugalGPT などの LLM cascade と同じ考え方）。3 製品で同じ規則に
するため、判定の信号の語彙・昇格の判断・診断の記録の形をここに 1 つだけ置く。どの段で信号を集めるか
と、やり直す段の実行は製品が持つ（最初は RAG の回答。NL2SQL・Agent は後で同じ部品を使う）。

- 信号は機械で判定できるものだけ（モデルの自己評価は使わない）。``ESCALATION_SIGNALS`` に無い語
  （根拠 0 件・確認の質問・人への引き継ぎ・未確認や警告の印だけ・接続や 429・5xx などの障害）は
  昇格の理由にしない。障害の切り替えと品質の昇格を混ぜない。
- 1 回の質問で昇格は最多 1 回。
- 利用者が明示したモデル（モデルの比較・要求のモデル）ではカスケードしない。2 つ目のモデルが空、
  または 1 つ目と同じなら使わない。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# 昇格する信号（機械で判定できる確認の不合格）。並びは表示の順。
SIGNAL_STRUCTURED_OUTPUT_INVALID = "structured_output_invalid"  # 構造化出力が再試行の後も不正
ESCALATION_SIGNALS: tuple[str, ...] = (SIGNAL_STRUCTURED_OUTPUT_INVALID,)


@dataclass(frozen=True)
class ModelCascade:
    """1 回の実行のカスケードの設定（1 つ目のモデルと、確認を通らなかったときのモデル）。"""

    primary_model: str
    # 確認を通らなかったときのモデル。空はカスケードしない。
    escalation_model: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.escalation_model)


def resolve_model_cascade(
    primary_model: str | None,
    escalation_model: str | None,
    *,
    explicit_model: bool = False,
) -> ModelCascade:
    """設定からカスケードを決める。

    ``explicit_model`` は利用者がモデルを明示した実行（モデルの比較・要求のモデル）で、そのときは
    使わない。2 つ目のモデルが空・1 つ目と同じときも使わない。
    """
    primary = (primary_model or "").strip()
    escalation = (escalation_model or "").strip()
    if explicit_model or not escalation or escalation == primary:
        escalation = ""
    return ModelCascade(primary_model=primary, escalation_model=escalation)


@dataclass(frozen=True)
class EscalationDecision:
    """昇格の判断。``reasons`` は昇格する信号（``ESCALATION_SIGNALS`` の順・重複なし）。"""

    escalate: bool
    reasons: tuple[str, ...] = ()


def escalation_reasons(signals: Iterable[str]) -> tuple[str, ...]:
    """信号のうち昇格の理由になるものだけ（``ESCALATION_SIGNALS`` の順・重複なし）。"""
    seen = set(signals)
    return tuple(signal for signal in ESCALATION_SIGNALS if signal in seen)


def decide_escalation(
    signals: Iterable[str], *, cascade: ModelCascade, escalated: bool
) -> EscalationDecision:
    """集めた信号で 2 つ目のモデルへ昇格するかを決める（1 回の質問で最多 1 回）。"""
    reasons = escalation_reasons(signals)
    return EscalationDecision(
        escalate=cascade.enabled and not escalated and bool(reasons), reasons=reasons
    )


def cascade_record(
    cascade: ModelCascade,
    *,
    escalated: bool,
    reasons: Iterable[str] = (),
    stage: str = "",
) -> dict[str, Any]:
    """診断・実行履歴に残すカスケードの記録（カスケードしない実行は空）。

    ``stage`` はやり直した段（製品の工程の名前。昇格しなかったときは空）。モデルの呼び出しと token
    は実行の trace（`model_usage` のモデルごとの内訳）にあり、ここには持たない。
    """
    if not cascade.enabled:
        return {}
    return {
        "primary_model": cascade.primary_model,
        "escalation_model": cascade.escalation_model,
        "escalated": bool(escalated),
        "reasons": list(escalation_reasons(reasons)) if escalated else [],
        **({"stage": stage} if escalated and stage else {}),
    }


__all__ = [
    "ESCALATION_SIGNALS",
    "SIGNAL_STRUCTURED_OUTPUT_INVALID",
    "EscalationDecision",
    "ModelCascade",
    "cascade_record",
    "decide_escalation",
    "escalation_reasons",
    "resolve_model_cascade",
]
