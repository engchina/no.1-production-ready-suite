"""回答の最終の検証の決定的な検査（#1276、handoff §12 の 1・4・5）。モデルは呼ばない。

主張の監査（モデル 1 回。`answer_validation`）と並べて、呼び出し側が任意で渡した材料から
次を確かめる。

- requests: AnswerEnvelope の要求ごとの充足（addressed / partial / missing / unknown）。
  partial・missing の要求を報告する。回答の本文に不足として示していれば warning、黙って落ちて
  いれば error。
- guide_steps: 業務ガイドの公開の版の手順。回答に出た手順の順序が依存（depends_on）と逆でないか、
  既知の条件に当たらない分岐の手順を含まないか、依存の手順・続きの手順を落としていないか。
- impact: 業務ガイドの影響範囲（グループ / 全体）と承認の要否を回答に書いているか。影響範囲・
  承認が係る手順（`impact.steps`）が、分かっている条件で外れた分岐の手順だけなら確かめない（#1320）。

手順・語の照合は、NFKC・大文字小文字・空白を揃えた文字列の部分一致で行う（言い換えは見つけられない）。
手順の名前が回答に 1 つも見つからなければ、順序は確かめられないことを warning で示す。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from app.schemas.support_guide import SupportGuideBranchWhen, SupportGuideContent

FindingCheck = Literal["requests", "guide", "guide_steps", "impact"]
FindingSeverity = Literal["error", "warning"]

# 回答の本文で不足を示す節の見出し（rag_engine の grounded.GAPS_SECTION_TITLE と、Agent が足す
# 見出し）。
GAP_SECTION_TITLES = ("資料からは確認できない点", "確かめられていない点")
# 影響範囲ごとに、回答に書いてあれば範囲を示したとみなす語。
IMPACT_SCOPE_TERMS: dict[str, tuple[str, ...]] = {
    "group": ("グループ",),
    "all": ("全体", "全員", "すべての", "全ての"),
}
IMPACT_SCOPE_LABELS = {"individual": "個別", "group": "グループ", "all": "全体"}
APPROVAL_TERMS = ("承認",)
_BOOLEAN_ALIASES = {"true": "はい", "yes": "はい", "false": "いいえ", "no": "いいえ"}
# 照合に使う手順の名前の最短の長さ（1 文字の名前は本文のどこにでも当たる）。
_MIN_TITLE_CHARS = 2


@dataclass(frozen=True)
class AnswerFinding:
    check: FindingCheck
    code: str
    severity: FindingSeverity
    message: str
    request_id: str | None = None
    step_id: str | None = None
    related_step_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


def _normalized(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _value(text: str) -> str:
    value = _normalized(text)
    return _normalized(_BOOLEAN_ALIASES.get(value, value))


def check_requests(
    answer: str, requests: Sequence[Mapping[str, str]], gaps: Sequence[str] = ()
) -> list[AnswerFinding]:
    """要求ごとの充足。partial / missing を報告し、本文で不足を示していなければ error にする。"""
    normalized = _normalized(answer)
    section = any(_normalized(title) in normalized for title in GAP_SECTION_TITLES)
    gap_shown = any(_normalized(gap) and _normalized(gap) in normalized for gap in gaps)
    findings: list[AnswerFinding] = []
    for request in requests:
        request_id = str(request.get("id") or "")
        text = " ".join(str(request.get("text") or "").split())
        status = request.get("status")
        label = f"要求「{text}」" if text else f"要求 {request_id}"
        if status == "unknown":
            findings.append(
                AnswerFinding(
                    "requests",
                    "request_unverified",
                    "warning",
                    f"{label}に答えたかは確かめられていません。",
                    request_id=request_id,
                )
            )
            continue
        if status not in {"partial", "missing"}:
            continue
        disclosed = section or gap_shown or bool(text and _normalized(text) in normalized)
        state = "一部しか答えていません" if status == "partial" else "答えていません"
        findings.append(
            AnswerFinding(
                "requests",
                f"request_{status}",
                "warning" if disclosed else "error",
                f"{label}に{state}"
                + (
                    "（回答に不足として示しています）。"
                    if disclosed
                    else "。不足も示していません。"
                ),
                request_id=request_id,
            )
        )
    return findings


def _branch_matches(when: SupportGuideBranchWhen, known: Mapping[str, str]) -> bool | None:
    """分岐の条件が今の条件で成り立つか（条件の値が分からなければ None）。"""
    value = known.get(when.condition_id)
    if when.operator == "unknown":
        return value is None
    if value is None:
        return None
    return value in {_value(item) for item in when.values}


def excluded_steps(content: SupportGuideContent, conditions: Mapping[str, str]) -> set[str]:
    """既知の条件に当たらない分岐の手順（と、それだけに依存する手順）。"""
    known = {key: _value(value) for key, value in conditions.items() if value and value.strip()}
    taken: set[str] = set()
    skipped: set[str] = set()
    decided = [(branch, _branch_matches(branch.when, known)) for branch in content.branches]
    # どの分岐にも当たらない値（選択肢に無い値など）は、分岐を決められない（外さない。#1320）。
    matched_conditions = {branch.when.condition_id for branch, matched in decided if matched}
    for branch, matched in decided:
        if matched is False and branch.when.condition_id in matched_conditions:
            skipped.add(branch.goto_step)
        else:
            taken.add(branch.goto_step)
    excluded = skipped - taken
    changed = True
    while changed:
        changed = False
        for step in content.steps:
            if step.id in excluded or step.id in taken or not step.depends_on:
                continue
            if all(dependency in excluded for dependency in step.depends_on):
                excluded.add(step.id)
                changed = True
    return excluded


def check_guide_steps(
    answer: str, content: SupportGuideContent, conditions: Mapping[str, str]
) -> list[AnswerFinding]:
    """回答に出た手順の順序・分岐・依存を業務ガイドの手順と照らす。"""
    if not content.steps:
        return []
    normalized = _normalized(answer)
    titles = {step.id: step.title for step in content.steps}
    positions: dict[str, int] = {}
    for step in content.steps:
        title = _normalized(step.title)
        if len(title) >= _MIN_TITLE_CHARS and (index := normalized.find(title)) >= 0:
            positions[step.id] = index
    if not positions:
        return [
            AnswerFinding(
                "guide_steps",
                "guide_steps_unmatched",
                "warning",
                "業務ガイドの手順の名前が回答に見つからず、手順の順序を確かめられません。",
            )
        ]
    excluded = excluded_steps(content, conditions)
    findings: list[AnswerFinding] = []
    for step in content.steps:
        if step.id not in positions:
            continue
        if step.id in excluded:
            findings.append(
                AnswerFinding(
                    "guide_steps",
                    "step_wrong_branch",
                    "error",
                    f"手順「{step.title}」は、分かっている条件に当たらない場合の手順です。",
                    step_id=step.id,
                )
            )
            continue
        for dependency in step.depends_on:
            if dependency in excluded or dependency not in titles:
                continue
            if dependency not in positions:
                findings.append(
                    AnswerFinding(
                        "guide_steps",
                        "step_dependency_missing",
                        "warning",
                        f"手順「{step.title}」の前の手順「{titles[dependency]}」が回答にありません。",
                        step_id=step.id,
                        related_step_id=dependency,
                    )
                )
            elif positions[dependency] > positions[step.id]:
                findings.append(
                    AnswerFinding(
                        "guide_steps",
                        "step_order",
                        "error",
                        f"手順「{step.title}」を、先に行う手順「{titles[dependency]}」より前に"
                        "書いています。",
                        step_id=step.id,
                        related_step_id=dependency,
                    )
                )
    for step in content.steps:
        if step.id in positions or step.id in excluded:
            continue
        before = next((item for item in step.depends_on if item in positions), None)
        if before is not None:
            findings.append(
                AnswerFinding(
                    "guide_steps",
                    "step_following_missing",
                    "warning",
                    f"手順「{titles[before]}」の後の手順「{step.title}」が回答にありません。",
                    step_id=step.id,
                    related_step_id=before,
                )
            )
    return findings


def impact_applies(content: SupportGuideContent, conditions: Mapping[str, str]) -> bool:
    """業務ガイドの影響範囲・承認が、分かっている条件の場合に係るか（#1320）。

    影響範囲・承認が係る手順（`impact.steps`）を決めていない業務ガイドは、すべての場合に係る。
    決めていれば、分かっている条件で外れた分岐の手順（`excluded_steps`）だけのときに係らない。
    条件が分からず分岐を決められないときは外れた手順が無いので、係る（安全側）。
    """
    steps = content.impact.steps
    if not steps:
        return True
    excluded = excluded_steps(content, conditions)
    return not all(step in excluded for step in steps)


def check_impact(
    answer: str, content: SupportGuideContent, conditions: Mapping[str, str] | None = None
) -> list[AnswerFinding]:
    """業務ガイドの影響範囲（グループ / 全体）と承認の要否を回答に書いているか。

    分かっている条件（`conditions`）で影響範囲・承認が係る手順が当たらなければ確かめない
    （例: 個別の利用者に付与する回答に、グループへの付与の承認を求めない。#1320）。
    """
    if not impact_applies(content, conditions or {}):
        return []
    normalized = _normalized(answer)
    impact = content.impact
    findings: list[AnswerFinding] = []
    terms = IMPACT_SCOPE_TERMS.get(impact.scope, ())
    if terms and not any(_normalized(term) in normalized for term in terms):
        findings.append(
            AnswerFinding(
                "impact",
                "impact_scope_missing",
                "error",
                f"影響範囲（{IMPACT_SCOPE_LABELS[impact.scope]}）を回答に書いていません。",
            )
        )
    if impact.approval_required and not any(term in normalized for term in APPROVAL_TERMS):
        findings.append(
            AnswerFinding(
                "impact",
                "approval_missing",
                "error",
                "実施の前に承認が要ることを回答に書いていません。",
            )
        )
    return findings


__all__ = [
    "AnswerFinding",
    "check_guide_steps",
    "check_impact",
    "check_requests",
    "excluded_steps",
    "impact_applies",
]
