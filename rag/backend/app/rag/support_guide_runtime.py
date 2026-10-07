"""業務ガイドを回答に使う（#1238。handoff §8.1・§9）。

公開した業務ガイドのうち質問に最も合う 1 つを決定的に選び（モデルは呼ばない）、条件の状態（既知 /
不明）を決めて、回答の進め方を決める。

- answer: 必要な条件がそろっている。業務ガイドの手順を回答の材料に渡して答える。
- branch: 不明の条件はあるが、分岐で答える（条件ごとに分けた回答）。
- clarify: 不明の条件を利用者に確かめる（検索・MCP は回答を作らずに確認の質問を返す）。
- handoff: 不明の条件は人へ引き継ぐ（窓口を示す）。

業務ガイドの内容は、用語・ルールと同じ経路（runtime knowledge のルール。`pinned` で必ず使う）で
回答の材料に渡す。手順の検索の手がかりは検索文に足す。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from app.schemas.support_guide import SupportGuideCondition, SupportGuideContent

GuideDecision = Literal["answer", "branch", "clarify", "handoff"]
# 照合の点（照合の語 1 つで 2 点、質問の例に近ければ 3 点）。これ未満は使わない。
MATCH_THRESHOLD = 2
_EXAMPLE_SIMILARITY = 0.5
_BOOLEAN_VALUES = {
    "true": "はい",
    "yes": "はい",
    "はい": "はい",
    "false": "いいえ",
    "no": "いいえ",
    "いいえ": "いいえ",
}
PINNED_RULE_TAG = "pinned"


def _normalized(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _bigrams(text: str) -> set[str]:
    value = _normalized(text)
    return {value[index : index + 2] for index in range(max(0, len(value) - 1))}


def _similarity(left: str, right: str) -> float:
    a, b = _bigrams(left), _bigrams(right)
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass(frozen=True)
class ConditionState:
    condition: SupportGuideCondition
    value: str | None
    # user=利用者が答えた・渡した / question=質問の文から読んだ / None=不明
    source: str | None = None

    @property
    def known(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class GuideMatch:
    guide_id: str
    revision: int
    content: SupportGuideContent
    score: int
    states: tuple[ConditionState, ...] = ()
    decision: GuideDecision = "answer"
    unknown: tuple[SupportGuideCondition, ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, Any]:
        """回答の記録・envelope・MCP に出す要約（既知 / 不明の条件）。"""
        return {
            "guide_id": self.guide_id,
            "revision": self.revision,
            "title": self.content.title,
            "decision": self.decision,
            "known_conditions": [
                {
                    "id": state.condition.id,
                    "label": state.condition.label,
                    "value": state.value,
                    "source": state.source,
                }
                for state in self.states
                if state.known
            ],
            "unknown_conditions": [
                {
                    "id": condition.id,
                    "label": condition.label,
                    "handling": condition.unknown_handling,
                }
                for condition in self.unknown
            ],
        }


def guide_score(content: SupportGuideContent, text: str) -> int:
    normalized = _normalized(text)
    score = sum(2 for term in content.goal.match_terms if _normalized(term) in normalized)
    if any(
        _similarity(example, text) >= _EXAMPLE_SIMILARITY
        for example in content.goal.intent_examples
    ):
        score += 3
    return score


def _in_period(content: SupportGuideContent, today: date) -> bool:
    period = content.applicability
    if period.effective_from and today < period.effective_from:
        return False
    return not (period.effective_to and today > period.effective_to)


def _provided_value(condition: SupportGuideCondition, raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if condition.type == "boolean":
        return _BOOLEAN_VALUES.get(value.casefold())
    if condition.type == "enum":
        return next(
            (
                option
                for option in condition.allowed_values
                if _normalized(option) == _normalized(value)
            ),
            None,
        )
    return value


def condition_states(
    content: SupportGuideContent, text: str, provided: Mapping[str, str]
) -> list[ConditionState]:
    """条件ごとの状態。

    利用者が渡した値を優先し、無ければ選択肢の語が質問に 1 つだけ出たときに既知とする。
    """
    normalized = _normalized(text)
    states: list[ConditionState] = []
    for condition in content.conditions:
        if condition.id in provided:
            value = _provided_value(condition, provided[condition.id])
            if value is not None:
                states.append(ConditionState(condition, value, "user"))
                continue
        if condition.type == "enum":
            found = [
                option for option in condition.allowed_values if _normalized(option) in normalized
            ]
            if len(found) == 1:
                states.append(ConditionState(condition, found[0], "question"))
                continue
        states.append(ConditionState(condition, None))
    return states


def decide(
    states: Sequence[ConditionState], *, interactive: bool
) -> tuple[GuideDecision, list[SupportGuideCondition]]:
    """不明で必須の条件の扱いから、回答の進め方を決める。

    ``interactive``（チャット）は送信の前に確認の質問を出すので、残った不明の条件は分岐で答える。
    """
    unknown = [state.condition for state in states if not state.known and state.condition.required]
    if not unknown:
        return "answer", []
    handlings = {condition.unknown_handling for condition in unknown}
    if "ask" in handlings and not interactive:
        return "clarify", [c for c in unknown if c.unknown_handling == "ask"]
    if "handoff" in handlings:
        return "handoff", [c for c in unknown if c.unknown_handling == "handoff"]
    return "branch", unknown


def match_guide(
    guides: Iterable[tuple[str, int, SupportGuideContent]],
    text: str,
    provided: Mapping[str, str] | None = None,
    *,
    interactive: bool = False,
    today: date | None = None,
) -> GuideMatch | None:
    """質問（と会話）に最も合う公開の業務ガイド。閾値未満・期間外なら None。"""
    today = today or date.today()
    candidates = [
        (guide_score(content, text), guide_id, revision, content)
        for guide_id, revision, content in guides
        if _in_period(content, today)
    ]
    candidates = [item for item in candidates if item[0] >= MATCH_THRESHOLD]
    if not candidates:
        return None
    # 点の高い順、同点は guide_id の順（決定的）。
    score, guide_id, revision, content = min(candidates, key=lambda item: (-item[0], item[1]))
    states = condition_states(content, text, provided or {})
    decision, unknown = decide(states, interactive=interactive)
    return GuideMatch(guide_id, revision, content, score, tuple(states), decision, tuple(unknown))


def rank_guides(
    guides: Iterable[tuple[str, int, SupportGuideContent]],
    text: str,
    provided: Mapping[str, str] | None = None,
    *,
    limit: int = 3,
    today: date | None = None,
) -> list[GuideMatch]:
    """質問に当たる公開の業務ガイドを点の高い順に（MCP の rag_lookup_guides）。"""
    today = today or date.today()
    scored = sorted(
        (
            (guide_score(content, text), guide_id, revision, content)
            for guide_id, revision, content in guides
            if _in_period(content, today)
        ),
        key=lambda item: (-item[0], item[1]),
    )
    result: list[GuideMatch] = []
    for score, guide_id, revision, content in scored:
        if score < MATCH_THRESHOLD or len(result) >= limit:
            break
        states = condition_states(content, text, provided or {})
        decision, unknown = decide(states, interactive=False)
        result.append(
            GuideMatch(guide_id, revision, content, score, tuple(states), decision, tuple(unknown))
        )
    return result


def _lines(match: GuideMatch) -> list[str]:
    content = match.content
    lines = [
        f"業務ガイド「{content.title}」（版 {match.revision}）に沿って答える。",
        f"期待する結果: {content.goal.expected_result}",
    ]
    known = [state for state in match.states if state.known]
    if known:
        lines.append(
            "分かっている条件: " + "、".join(f"{s.condition.label}={s.value}" for s in known)
        )
        # 既知の条件に当たる場合だけを答える（別の場合の手順を並べない。#1238 の実環境の確認）。
        lines.append(
            "分かっている条件に当たる場合の手順・注意だけを答え、"
            + "、".join(f"{s.condition.label}が「{s.value}」以外の場合" for s in known)
            + "の手順は答えに含めない。"
        )
    unknown = [state.condition for state in match.states if not state.known]
    if unknown:
        lines.append(
            "分かっていない条件（断定せず、条件ごとに分けて示す）: "
            + "、".join(
                condition.label
                + (f"（{'／'.join(condition.allowed_values)}）" if condition.allowed_values else "")
                for condition in unknown
            )
        )
    for index, step in enumerate(content.steps, start=1):
        after = f"（{'・'.join(step.depends_on)} の後）" if step.depends_on else ""
        purpose = f": {step.purpose}" if step.purpose else ""
        done = f" 終わりの条件: {step.done_when}" if step.done_when else ""
        lines.append(f"手順 {index}. {step.title}{after}{purpose}{done}")
    states = {state.condition.id: state for state in match.states}
    for branch in content.branches:
        condition = states.get(branch.when.condition_id)
        label = condition.condition.label if condition else branch.when.condition_id
        when = "不明" if branch.when.operator == "unknown" else "／".join(branch.when.values)
        target = next(
            (step.title for step in content.steps if step.id == branch.goto_step), branch.goto_step
        )
        lines.append(f"分岐: {label} が {when} のときは「{target}」へ進む。")
    for item in content.completion:
        lines.append(
            f"完了の条件: {item.description}"
            + (f"（確かめ方: {item.check_method}）" if item.check_method else "")
        )
    impact = content.impact
    scope = {"individual": "個別", "group": "グループ", "all": "全体"}[impact.scope]
    lines.append(
        f"影響範囲: {scope}" + ("。実施の前に承認が要る" if impact.approval_required else "")
    )
    if impact.approval_note:
        lines.append(f"承認について: {impact.approval_note}")
    if content.handoff.conditions:
        lines.append("人へ引き継ぐ条件: " + "、".join(content.handoff.conditions))
    lines.append(
        "資料で確かめられない手順・値は答えに含めず、確かめられない点として示す。"
        "案内した操作が実際に終わったとは書かない。"
    )
    return lines


def guide_rule(match: GuideMatch) -> dict[str, Any]:
    """業務ガイドを runtime knowledge のルール（pinned）にする。"""
    hints = [hint for step in match.content.steps for hint in step.retrieval_hints]
    return {
        "id": f"guide-{match.guide_id}",
        "title": match.content.title,
        "triggers": list(dict.fromkeys(hints)),
        "content": "\n".join(_lines(match)),
        "source": f"業務ガイド {match.guide_id}@{match.revision}",
        "tags": [PINNED_RULE_TAG, "support_guide"],
        "status": "approved",
    }


def with_guide_rule(payload: Mapping[str, Any] | None, match: GuideMatch) -> dict[str, Any]:
    """用語・ルールの payload に業務ガイドのルールを先頭に足した写し。"""
    base = dict(payload or {})
    rules = [rule for rule in base.get("rules", []) if isinstance(rule, dict)]
    return {
        "schema_version": base.get("schema_version", 1),
        "terms": list(base.get("terms", [])),
        "rules": [guide_rule(match), *rules],
    }


def clarification_questions(match: GuideMatch) -> list[dict[str, Any]]:
    """確かめる条件と問い・選択肢（検索・MCP の needs_clarification）。"""
    return [
        {
            "condition_id": condition.id,
            "label": condition.label,
            "question": condition.question or f"{condition.label}を教えてください。",
            "options": list(condition.allowed_values)
            or (["はい", "いいえ"] if condition.type == "boolean" else []),
        }
        for condition in match.unknown
    ]


def short_circuit_answer(match: GuideMatch) -> str:
    """回答を作らずに返す本文（確認の質問・人への引き継ぎ）。"""
    content = match.content
    if match.decision == "clarify":
        lines = [f"「{content.title}」の進め方は条件によって変わるため、次を教えてください。", ""]
        for item in clarification_questions(match):
            options = f"（{'／'.join(item['options'])}）" if item["options"] else ""
            lines.append(f"・{item['question']}{options}")
        return "\n".join(lines)
    contact = content.handoff.contact or "担当の窓口"
    labels = "、".join(condition.label for condition in match.unknown)
    return (
        f"「{content.title}」は、{labels}が分からないと資料だけでは案内できないため、"
        f"{contact}へお問い合わせください。"
    )


_GUIDE_SUMMARY_KEYS = (
    "guide_id",
    "revision",
    "title",
    "decision",
    "known_conditions",
    "unknown_conditions",
)


def _guide_summary(guide: Mapping[str, Any]) -> dict[str, Any]:
    return {key: guide.get(key) for key in _GUIDE_SUMMARY_KEYS if key in guide}


def guide_short_circuit_outcome(guide: Mapping[str, Any]) -> Any:
    """確認の質問・人への引き継ぎを、回答（AnswerOutcome）の形にする（引用なし・モデルを呼ばない）。"""
    from app.rag.answer_engine import AnswerOutcome

    clarify = guide.get("decision") == "clarify"
    outcome = "needs_clarification" if clarify else "needs_human"
    summary = _guide_summary(guide)
    envelope = {
        "schema_version": 1,
        "outcome": outcome,
        "requests": [],
        "items": [],
        "conditions": [],
        "gaps": [],
        "confirmations": [],
        "reference_materials": [],
        "handoff_reasons": ["clarification" if clarify else "handoff"],
        "clarifications": list(guide.get("clarifications") or []),
        "guide": summary,
    }
    return AnswerOutcome(
        answer=str(guide.get("short_answer") or ""),
        citations=[],
        diagnostics={
            "answer_flow": "support_guide",
            "outcome": outcome,
            "envelope": envelope,
            "guide": summary,
            "needs_human_review": not clarify,
            "insufficient_reason": "",
        },
        context_text="",
    )


def apply_guide_to_diagnostics(diagnostics: dict[str, Any], guide: Mapping[str, Any]) -> None:
    """回答の記録に業務ガイドの版・条件を残す。分岐で答えたときは「答えた」を条件付きにする。"""
    summary = _guide_summary(guide)
    diagnostics["guide"] = summary
    envelope = diagnostics.get("envelope")
    if isinstance(envelope, dict):
        envelope["guide"] = summary
        if guide.get("decision") == "branch" and envelope.get("outcome") == "answered":
            envelope["outcome"] = "conditional"
            diagnostics["outcome"] = "conditional"
            unknown = [str(item.get("label")) for item in guide.get("unknown_conditions") or []]
            envelope["conditions"] = list(
                dict.fromkeys([*envelope.get("conditions", []), *unknown])
            )


GUIDE_CLARIFICATION_PREFIX = "guide:"


def _condition_options(condition: SupportGuideCondition) -> list[str]:
    if condition.type == "boolean":
        return ["はい", "いいえ"]
    return list(condition.allowed_values)


def guide_clarification(match: GuideMatch) -> tuple[str, str, Any] | None:
    """チャットの確認の質問（ルールの確認と同じ形）。選択肢で答えられる最初の不明な条件だけ。

    戻り値は (rule_id, 題名, RuleClarification)。rule_id は ``guide:<guide_id>:<condition_id>``。
    """
    from app.schemas.search_answer_profile_knowledge import (
        ClarificationOption,
        RuleClarification,
    )

    if match.decision != "clarify":
        return None
    for condition in match.unknown:
        values = _condition_options(condition)
        if not 2 <= len(values) <= 8:
            continue
        clarification = RuleClarification(
            question=(condition.question or f"{condition.label}を教えてください。")[:200],
            multiple=False,
            allow_other=False,
            options=[
                ClarificationOption(
                    id=f"o{index}",
                    label=value[:80],
                    search_terms=[value[:50]],
                    premise=f"{condition.label}は「{value}」"[:300],
                )
                for index, value in enumerate(values, start=1)
            ],
        )
        rule_id = f"{GUIDE_CLARIFICATION_PREFIX}{match.guide_id}:{condition.id}"
        return rule_id, match.content.title, clarification
    return None


def resolve_guide_clarification(
    guides: Iterable[tuple[str, int, SupportGuideContent]], rule_id: str, option_ids: Sequence[str]
) -> tuple[dict[str, str], str] | None:
    """チャットの確認の答えを、業務ガイドの条件の値と回答の前提の文にする（保存済みの公開の版で引き直す）。"""
    if not rule_id.startswith(GUIDE_CLARIFICATION_PREFIX):
        return None
    guide_id, _, condition_id = rule_id.removeprefix(GUIDE_CLARIFICATION_PREFIX).partition(":")
    content = next((item for gid, _, item in guides if gid == guide_id), None)
    if content is None or len(option_ids) != 1:
        return None
    condition = next((item for item in content.conditions if item.id == condition_id), None)
    if condition is None:
        return None
    values = _condition_options(condition)
    index = int(option_ids[0][1:]) if option_ids[0][1:].isdigit() else 0
    if not 1 <= index <= len(values):
        return None
    value = values[index - 1]
    context = "\n".join(
        [
            f"確認の質問: {condition.question or condition.label}",
            f"選んだ答え: {value}",
            f"前提: {condition.label}は「{value}」",
        ]
    )
    return {condition.id: value}, context


__all__ = [
    "GUIDE_CLARIFICATION_PREFIX",
    "apply_guide_to_diagnostics",
    "guide_clarification",
    "resolve_guide_clarification",
    "guide_short_circuit_outcome",
    "MATCH_THRESHOLD",
    "ConditionState",
    "GuideMatch",
    "clarification_questions",
    "condition_states",
    "decide",
    "guide_rule",
    "guide_score",
    "match_guide",
    "rank_guides",
    "short_circuit_answer",
    "with_guide_rule",
]
