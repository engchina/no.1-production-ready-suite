"""回答の構造（AnswerEnvelope。#1235、handoff §12）。

根拠付き回答（grounded）の確定の段で、すでにある草稿・監査・公開した説明から、回答の対応・要求ごとの
充足・適用の条件・不足・実データの確認を決定的に組み立てる。モデルは呼ばない。本文（`grounded.render`）と
同じ材料から作るので、本文と食い違わない。

対応（outcome）:

- answered: 公開した説明があり、未回答の要求・不足・条件付きの説明が無い。
- conditional: 公開した説明はあるが、未回答の要求・不足・適用の条件（未確認を含む）がある。または原文の
  提示だけ（適用は未確認）で答えた。
- needs_environment_data: 回答の確定に現場の実データ（設定値・記録・ログ）の確認が要る。
- insufficient_evidence: 公開できる説明も原文も無く、資料から答えられない。
- needs_clarification / needs_human: 利用者への確認・人への引き継ぎ（業務ガイドと会話の段で決める。
  ここでは作らない）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

ENVELOPE_SCHEMA_VERSION = 1
AnswerOutcome = Literal[
    "answered",
    "conditional",
    "needs_clarification",
    "needs_environment_data",
    "needs_human",
    "insufficient_evidence",
]
# 要求の状態。unknown は監査が無く充足を確かめていない要求。
RequestStatus = Literal["addressed", "partial", "missing", "unknown"]


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _request_text(unit: Mapping[str, Any]) -> str:
    text = str(unit.get("text") or "")
    text = text.split("原文: ", 1)[1] if "原文: " in text else text
    return " ".join(text.split())


def envelope_requests(
    requests: Sequence[Mapping[str, Any]], reviews: Sequence[Mapping[str, Any]] | None
) -> list[dict[str, str]]:
    """要求単位ごとの充足。背景（kind=context）の要求は含めない。"""
    by_id = {str(review.get("request_id")): review for review in reviews or ()}
    result: list[dict[str, str]] = []
    for unit in requests:
        if unit.get("kind") == "context":
            continue
        request_id = str(unit.get("id") or "")
        review = by_id.get(request_id)
        if reviews is None:
            status = "unknown"
        elif review is None:
            # 監査が返さなかった id は、欠落と決めつけない（grounded の unanswered と同じ扱い）。
            status = "addressed"
        else:
            status = str(review.get("status") or "unknown")
            if status == "context":
                continue
        result.append({"id": request_id, "text": _request_text(unit), "status": status})
    return result


def envelope_item(entry: Any) -> dict[str, Any]:
    """公開した説明 1 件（grounded の CheckedItem）。"""
    item = entry.item
    span = entry.span or {}
    applies = "quote_only" if entry.quote_only else str(item.applies)
    return {
        "kind": str(item.kind),
        "text": str(item.quote if entry.quote_only else item.text),
        "evidence_id": str(span.get("evidence_id") or ""),
        "source": str(span.get("source") or ""),
        "location": str(span.get("location") or (f"p.{span['page']}" if span.get("page") else "")),
        "applies": applies,
        "condition": str(item.condition or ""),
    }


def build_envelope(
    *,
    requests: Sequence[Mapping[str, Any]],
    reviews: Sequence[Mapping[str, Any]] | None,
    items: Sequence[Mapping[str, Any]],
    gaps: Sequence[str],
    confirmations: Sequence[str],
    external_data_required: bool,
    reference_materials: Sequence[str] = (),
    off_goal: bool = False,
) -> dict[str, Any]:
    """回答の構造を組み立てる。"""
    request_entries = envelope_requests(requests, reviews)
    actionable = [item for item in items if item.get("applies") != "quote_only"]
    unsettled = [item for item in actionable if item.get("applies") in {"conditional", "unverified"}]
    unanswered = [entry for entry in request_entries if entry["status"] in {"partial", "missing"}]
    conditions = _unique(str(item.get("condition") or "") for item in unsettled)
    handoff: list[str] = []
    if unanswered:
        handoff.append("unanswered_requests")
    if gaps:
        handoff.append("gaps")
    if external_data_required or confirmations:
        handoff.append("environment_data")
    if any(item.get("applies") == "unverified" for item in unsettled):
        handoff.append("unverified_applicability")
    if reference_materials:
        handoff.append("reference_materials")
    if off_goal:
        handoff.append("off_goal")

    outcome: AnswerOutcome
    if external_data_required or (confirmations and not actionable):
        outcome = "needs_environment_data"
    elif not items:
        outcome = "insufficient_evidence"
    elif not actionable or unanswered or gaps or unsettled or off_goal:
        outcome = "conditional"
    else:
        outcome = "answered"
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "outcome": outcome,
        "requests": request_entries,
        "items": [dict(item) for item in items],
        "conditions": conditions,
        "gaps": _unique(gaps),
        "confirmations": _unique(confirmations),
        "reference_materials": _unique(reference_materials),
        "handoff_reasons": handoff,
    }


def fallback_envelope(
    *,
    answered: bool,
    insufficient_reason: str = "",
    needs_human_review: bool | None = None,
    external_data_required: bool | None = None,
    external_data_items: Sequence[str] = (),
) -> dict[str, Any]:
    """grounded の確定を経ない回答（根拠が無く打ち切った回答・承認済み FAQ など）の構造。

    説明の構造が無いので、回答の有無と不足・人の確認・実データの印から対応だけを決める。
    """
    envelope = build_envelope(
        requests=(),
        reviews=None,
        items=(),
        gaps=[insufficient_reason] if insufficient_reason else [],
        confirmations=list(external_data_items),
        external_data_required=bool(external_data_required),
    )
    if answered and envelope["outcome"] == "insufficient_evidence":
        envelope["outcome"] = "conditional" if (insufficient_reason or needs_human_review) else "answered"
    return envelope


__all__ = [
    "ENVELOPE_SCHEMA_VERSION",
    "AnswerOutcome",
    "build_envelope",
    "envelope_item",
    "envelope_requests",
    "fallback_envelope",
]
