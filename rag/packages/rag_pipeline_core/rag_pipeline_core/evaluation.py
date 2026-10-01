"""評価の基準(閾値のプリセット)の決定論解決(backend / サービス共有)。

基準 → CI gate 用の閾値(指標名→最低値の dict)を決定論で解決する(#591)。
指標は「検索」「根拠」「回答」の 3 つの観点に整理した 9 つだけを持つ。

- 検索: context_recall(正解の文書を取れたか)/ mrr(正解の文書の順位)
- 根拠: claim_support_rate(画面の名前は Faithfulness。標準回答による評価で、LLM の主張の判定で
  根拠のない主張が無いか)/ citation_traceability_coverage(引用を原文の位置へたどれるか)/
  faithfulness(画面の名前は「根拠との語句の一致率」。参考値で、閾値は目安として表示するだけで
  合否に使わない。#711)
- 回答: answer_keyword_hit_rate(期待する語を含むか)/ refusal_accuracy(答えるべきでないときに
  答えず、答えるべきときに答えたか)/ requirement_coverage(標準回答の必要な項目を網羅したか)/
  answer_pass_rate(標準回答による評価の合格)

プリセットは「標準」と「厳格」の 2 つ。閾値は、そのケースの集合で測れた指標だけに適用する
(標準回答の無い golden set では、標準回答による評価の閾値を判定しない)。閾値は素の
dict[str, float] で受け渡し、backend が `EvaluationThresholds` へ写す。Settings 非依存。
"""

from __future__ import annotations

from dataclasses import dataclass

EVALUATION_SUITES: tuple[str, ...] = ("standard", "strict")
DEFAULT_EVALUATION_SUITE = "standard"
# 旧プリセット(#591 で削除)の名前。保存済みの設定値を後継へ寄せる。
LEGACY_EVALUATION_SUITES: dict[str, str] = {
    "request_only": "standard",
    "retrieval_focused": "standard",
    "balanced": "standard",
    "ragas_like": "standard",
    "strict_ci": "strict",
}


@dataclass(frozen=True)
class EvaluationSpec:
    name: str
    origin: str
    recommended_for: tuple[str, ...]
    thresholds: dict[str, float]


EVALUATION_SPECS: dict[str, EvaluationSpec] = {
    "standard": EvaluationSpec(
        "standard",
        "general_rag",
        ("general", "nightly"),
        {
            "context_recall": 0.8,
            "mrr": 0.6,
            "faithfulness": 0.7,
            "citation_traceability_coverage": 0.9,
            "claim_support_rate": 0.9,
            "answer_keyword_hit_rate": 0.8,
            "refusal_accuracy": 0.9,
            "requirement_coverage": 0.8,
            "answer_pass_rate": 0.7,
        },
    ),
    "strict": EvaluationSpec(
        "strict",
        "release_gate",
        ("release", "regression"),
        {
            "context_recall": 0.9,
            "mrr": 0.8,
            "faithfulness": 0.8,
            "citation_traceability_coverage": 0.95,
            "claim_support_rate": 1.0,
            "answer_keyword_hit_rate": 0.9,
            "refusal_accuracy": 1.0,
            "requirement_coverage": 0.9,
            "answer_pass_rate": 0.8,
        },
    ),
}


@dataclass(frozen=True)
class EvaluationResolved:
    suite: str
    thresholds: dict[str, float]


def normalize_evaluation_suite(value: object) -> str:
    """基準の名前を正規化する。旧プリセットは後継へ、未知の名前は既定(標準)へ寄せる。"""
    normalized = str(value).strip().casefold()
    normalized = LEGACY_EVALUATION_SUITES.get(normalized, normalized)
    return normalized if normalized in EVALUATION_SPECS else DEFAULT_EVALUATION_SUITE


def resolve_evaluation(suite: object) -> EvaluationResolved:
    """基準から CI gate 用の閾値 dict を解決する。"""
    name = normalize_evaluation_suite(suite)
    return EvaluationResolved(suite=name, thresholds=dict(EVALUATION_SPECS[name].thresholds))
