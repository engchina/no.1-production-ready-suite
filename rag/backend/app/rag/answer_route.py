"""回答の経路（固定の RAG と Agent の振り分け。#1283、handoff §5 / §13）。

振り分けのモデル:

- 経路は利用者が製品で選ぶ（RAG のチャット・検索なら固定の RAG、Agent のチャットなら
  Agent）。質問ごとにモデルで経路を選ぶ自動の振り分け（LLM router）は置かない。
  モデルの呼び出しと待ち時間が増え、Agent の効果（評価の経路 D）がまだ測れていないため。
- 固定の RAG は、回答の対応（AnswerEnvelope の outcome）から経路の理由を決定的に記録する
  （モデルは呼ばない）。
- 固定の RAG では完了できないと outcome が示すとき（今は ``needs_environment_data`` =
  現場の実データの確認が要る）だけ、Agent で続けることを提案する
  （``escalation_suggested``）。Agent は許可された読み取りの道具（NL2SQL など）で現場の
  値を確かめられる。
- 確認の質問（needs_clarification）は RAG のチャットで答えれば続けられる。人への引き継ぎ
  （needs_human）と資料の不足（insufficient_evidence）は Agent でも埋められないので
  提案しない（handoff §1）。

記録は回答の診断（``diagnostics.answer.route``）に入り、回答の記録（trace）に残る。
Prometheus には理由と提案の有無だけを数える（業務の原文は送らない）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ROUTE_SCHEMA_VERSION = 1
# 経路の理由（回答の対応）。古い回答・回答を作らない検索など、対応が無いときは unknown。
ROUTE_REASONS = frozenset(
    {
        "answered",
        "conditional",
        "needs_clarification",
        "needs_environment_data",
        "needs_human",
        "insufficient_evidence",
        "unknown",
    }
)
# 固定の RAG では完了できず、Agent で続けることを提案する対応。
ESCALATION_OUTCOMES = frozenset({"needs_environment_data"})


def answer_route(answer_diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    """回答の診断から経路の記録を作る（決定的。モデルは呼ばない）。"""
    envelope = answer_diagnostics.get("envelope")
    envelope = envelope if isinstance(envelope, Mapping) else {}
    outcome = str(envelope.get("outcome") or answer_diagnostics.get("outcome") or "")
    reason = outcome if outcome in ROUTE_REASONS else "unknown"
    signals = [
        str(value) for value in envelope.get("handoff_reasons") or () if isinstance(value, str)
    ]
    escalate = reason in ESCALATION_OUTCOMES
    return {
        "schema_version": ROUTE_SCHEMA_VERSION,
        "path": "rag",
        "reason": reason,
        # outcome を決めた手がかり（AnswerEnvelope の handoff_reasons）。振り分けの評価に使う。
        "signals": signals,
        "escalation_suggested": escalate,
        "escalation_reason": reason if escalate else "",
    }


__all__ = ["ESCALATION_OUTCOMES", "ROUTE_REASONS", "ROUTE_SCHEMA_VERSION", "answer_route"]
