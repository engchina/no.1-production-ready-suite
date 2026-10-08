"""業務ガイドを回答に使う（#1238。handoff §8.1・§9）。

公開した業務ガイドのうち質問に最も合う 1 つを決定的に選び（モデルは呼ばない）、条件の状態（既知 /
不明 / 矛盾）を決めて、回答の進め方を決める。

- answer: 必要な条件がそろっている。業務ガイドの手順を回答の材料に渡して答える。
- branch: 不明の条件はあるが、分岐で答える（条件ごとに分けた回答）。
- clarify: 不明・矛盾の条件を利用者に確かめる（検索・MCP は回答を作らずに確認の質問を返す）。
- handoff: 不明の条件は人へ引き継ぐ（窓口を示す）。

適用範囲（#1278。handoff §6.2）: 空の項目は「このプロファイルの中」で制限なし（schema・画面の説明の
とおり）。値のある項目（業務・対象の種類・版）は、質問・検索の絞り込みが名指しした値（手がかり）と
比べ、手がかりがあって合わなければそのガイドを使わない。手がかりが無い項目は ``unverified`` として
回答の記録・MCP に出す（推測で埋めない）。

業務ガイドの内容は、用語・ルールと同じ経路（runtime knowledge のルール。`pinned` で必ず使う）で
回答の材料に渡す。手順の検索の手がかりは検索文に足す。分かっている条件で決まる分岐だけを残し、
合わない分岐の行き先の手順は材料から外す（#1278）。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from app.schemas.classification import category_label
from app.schemas.support_guide import (
    SupportGuideBranch,
    SupportGuideCondition,
    SupportGuideContent,
    SupportGuideStep,
)

GuideDecision = Literal["answer", "branch", "clarify", "handoff"]
# 条件の状態（handoff §8.1）。conflicting = 質問に 1 つの条件の選択肢が 2 つ以上出た。
ConditionStatus = Literal["known", "unknown", "conflicting"]
# 適用範囲の項目の状態。matched = 手がかりと合った / unverified = 手がかりが無く確かめていない。
ApplicabilityStatus = Literal["matched", "unverified"]
ApplicabilityDimension = Literal["business_domains", "object_types", "versions"]
# 照合の点（照合の語 1 つで 2 点、質問の例に近ければ 3 点）。これ未満は使わない。
MATCH_THRESHOLD = 2
_EXAMPLE_SIMILARITY = 0.5
# 質問の中から名指しを探す値の最短の長さ（#553 の業務の名前と同じ）。
_MIN_NAME_CHARS = 2
_BOOLEAN_VALUES = {
    "true": "はい",
    "yes": "はい",
    "はい": "はい",
    "false": "いいえ",
    "no": "いいえ",
    "いいえ": "いいえ",
}
PINNED_RULE_TAG = "pinned"
# 公開の業務ガイドを読めなかったとき、回答の診断に残す key（回答は続ける。#1278）。
GUIDE_LOAD_FAILED_KEY = "guide_load_failed"
_DIMENSION_LABELS: dict[str, str] = {
    "business_domains": "業務",
    "object_types": "対象の種類",
    "versions": "資料・システムの版",
}
_SOURCE_NOTES = {"document": "資料で確かめる", "tool": "現場の記録・道具で確かめる"}


def _normalized(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _bigrams(text: str) -> set[str]:
    value = _normalized(text)
    return {value[index : index + 2] for index in range(max(0, len(value) - 1))}


def _similarity(left: str, right: str) -> float:
    a, b = _bigrams(left), _bigrams(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def _label(value: str) -> str:
    """適用範囲の値の比較用の名前（NFKC・大文字小文字・空白の連続をそろえる）。"""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _domain_label(value: str) -> str:
    """業務の比較用の名前（分類の番号の接頭辞 `10_` を外す。#547 / #553 と同じ）。"""
    return _label(category_label(value))


def _ascii_word(char: str) -> bool:
    return bool(char) and char.isascii() and char.isalnum()


def _named(text: str, labels: Iterable[str]) -> set[str]:
    """質問が名指しした値（比較用の名前）。

    長い名前から探し、見つけた所は短い名前の照合に使わない。英数字の値は英数字の途中に当たる所を
    名指しとみなさない（「v1」を「v10」の中に見つけない）。
    """
    haystack = _label(text)
    found: set[str] = set()
    candidates = {label for label in labels if len(label) >= _MIN_NAME_CHARS}
    for label in sorted(candidates, key=lambda item: (-len(item), item)):
        index = 0
        while (start := haystack.find(label, index)) >= 0:
            end = start + len(label)
            before = haystack[start - 1] if start else ""
            after = haystack[end] if end < len(haystack) else ""
            if (_ascii_word(label[0]) and _ascii_word(before)) or (
                _ascii_word(label[-1]) and _ascii_word(after)
            ):
                index = start + 1
                continue
            found.add(label)
            haystack = haystack[:start] + "\0" + haystack[end:]
            index = start + 1
    return found


@dataclass(frozen=True)
class GuideContext:
    """適用範囲を確かめる手がかり（質問・検索の絞り込みが名指しした値の比較用の名前）。

    空の項目は手がかりなし（その項目は ``unverified``）。
    """

    business_domains: frozenset[str] = frozenset()
    object_types: frozenset[str] = frozenset()
    versions: frozenset[str] = frozenset()


def build_guide_context(
    text: str,
    guides: Iterable[tuple[str, int, SupportGuideContent]],
    *,
    filters: Mapping[str, str] | None = None,
    business_vocabulary: Iterable[str] = (),
) -> GuideContext:
    """質問と検索の絞り込みから、適用範囲の手がかりを読む（モデルは呼ばない）。

    - 業務: 絞り込み ``large_category`` と、質問が名指しした業務（``business_vocabulary`` =
      プロファイルの KB の大分類と、公開ガイドの業務の語）。
    - 対象の種類: 質問が名指しした、公開ガイドの対象の種類の語。
    - 版: 絞り込み ``document_version`` と、質問が名指しした、公開ガイドの版の語。
    """
    filters = filters or {}
    scopes = [content.applicability for _, _, content in guides]
    domains = _named(
        text,
        {
            _domain_label(value)
            for value in [*business_vocabulary, *(v for s in scopes for v in s.business_domains)]
        },
    )
    if value := filters.get("large_category", "").strip():
        domains.add(_domain_label(value))
    objects = _named(text, {_label(v) for s in scopes for v in s.object_types})
    versions = _named(text, {_label(v) for s in scopes for v in s.versions})
    if value := filters.get("document_version", "").strip():
        versions.add(_label(value))
    return GuideContext(frozenset(domains), frozenset(objects), frozenset(versions))


def applicability_status(
    content: SupportGuideContent, context: GuideContext
) -> dict[str, ApplicabilityStatus] | None:
    """値のある適用範囲の項目ごとの状態。手がかりと合わない項目があれば None（使わない）。

    空の項目は「このプロファイルの中」で制限なし（状態に出さない）。
    """
    scope = content.applicability
    result: dict[str, ApplicabilityStatus] = {}
    dimensions: tuple[tuple[str, list[str], Callable[[str], str], frozenset[str]], ...] = (
        ("business_domains", scope.business_domains, _domain_label, context.business_domains),
        ("object_types", scope.object_types, _label, context.object_types),
        ("versions", scope.versions, _label, context.versions),
    )
    for name, values, key, signal in dimensions:
        if not values:
            continue
        if not signal:
            result[name] = "unverified"
            continue
        if not {key(value) for value in values} & signal:
            return None
        result[name] = "matched"
    return result


@dataclass(frozen=True)
class ConditionState:
    condition: SupportGuideCondition
    value: str | None
    # user=利用者が答えた・渡した / question=質問の文から読んだ / None=不明
    source: str | None = None
    # 質問に出た選択肢（2 つ以上なら矛盾。どれかに決めつけない）。
    candidates: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return self.value is not None

    @property
    def conflicting(self) -> bool:
        return self.value is None and len(self.candidates) > 1

    @property
    def status(self) -> ConditionStatus:
        if self.known:
            return "known"
        return "conflicting" if self.conflicting else "unknown"


@dataclass(frozen=True)
class GuideMatch:
    guide_id: str
    revision: int
    content: SupportGuideContent
    score: int
    states: tuple[ConditionState, ...] = ()
    decision: GuideDecision = "answer"
    unknown: tuple[SupportGuideCondition, ...] = field(default_factory=tuple)
    # 値のある適用範囲の項目ごとの状態（matched / unverified）。
    applicability: Mapping[str, ApplicabilityStatus] = field(default_factory=dict)

    def state_of(self, condition_id: str) -> ConditionState | None:
        return next((state for state in self.states if state.condition.id == condition_id), None)

    def summary(self) -> dict[str, Any]:
        """回答の記録・envelope・MCP に出す要約（既知 / 不明・矛盾の条件と適用範囲）。"""
        unknown_conditions: list[dict[str, Any]] = []
        for condition in self.unknown:
            state = self.state_of(condition.id)
            unknown_conditions.append(
                {
                    "id": condition.id,
                    "label": condition.label,
                    "handling": condition.unknown_handling,
                    "state": state.status if state else "unknown",
                    "candidates": list(state.candidates) if state and state.conflicting else [],
                }
            )
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
                    "state": "known",
                }
                for state in self.states
                if state.known
            ],
            "unknown_conditions": unknown_conditions,
            "applicability": dict(self.applicability),
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

    利用者が渡した値を優先し、無ければ選択肢の語が質問に 1 つだけ出たときに既知とする。2 つ以上
    出たら矛盾（conflicting）。質問の文・渡した値で既知にするのは出所が利用者（``source=user``）の
    条件だけで、資料・道具の条件は利用者の申告では決めない（handoff §8.1。#1278）。
    """
    normalized = _normalized(text)
    states: list[ConditionState] = []
    for condition in content.conditions:
        if condition.source != "user":
            states.append(ConditionState(condition, None))
            continue
        if condition.id in provided:
            value = _provided_value(condition, provided[condition.id])
            if value is not None:
                states.append(ConditionState(condition, value, "user"))
                continue
        if condition.type == "enum":
            # 選択肢そのものか、その言い換え（value_aliases。#1237）が質問に出た選択肢。
            found = [
                option
                for option in condition.allowed_values
                if any(
                    _normalized(word) in normalized
                    for word in (option, *condition.value_aliases.get(option, []))
                    if _normalized(word)
                )
            ]
            if len(found) == 1:
                states.append(ConditionState(condition, found[0], "question"))
                continue
            if len(found) > 1:
                states.append(ConditionState(condition, None, candidates=tuple(found)))
                continue
        states.append(ConditionState(condition, None))
    return states


def _askable(state: ConditionState) -> bool:
    """利用者に聞けば決まる条件か（矛盾、または利用者が出所で「確かめる」扱いの不明）。"""
    condition = state.condition
    return state.conflicting or (condition.unknown_handling == "ask" and condition.source == "user")


def decide(
    states: Sequence[ConditionState], *, interactive: bool
) -> tuple[GuideDecision, list[SupportGuideCondition]]:
    """不明・矛盾で必須の条件の扱いから、回答の進め方を決める。

    ``interactive``（チャット）は送信の前に確認の質問を出すので、残った条件は分岐で答える。
    資料・道具が出所の条件は利用者に聞いても決まらないので、確かめずに分岐で答える。
    """
    pending = [state for state in states if not state.known and state.condition.required]
    if not pending:
        return "answer", []
    askable = [state.condition for state in pending if _askable(state)]
    if askable and not interactive:
        return "clarify", askable
    handoff = [
        state.condition
        for state in pending
        if not state.conflicting and state.condition.unknown_handling == "handoff"
    ]
    if handoff:
        return "handoff", handoff
    return "branch", [state.condition for state in pending]


GuideTriple = tuple[str, int, SupportGuideContent]


def _candidates(
    guides: Iterable[GuideTriple],
    text: str,
    today: date | None,
    context: GuideContext | None,
) -> list[tuple[int, str, int, SupportGuideContent, dict[str, ApplicabilityStatus]]]:
    """期間・照合の点・適用範囲に通る候補（点の高い順、同点は guide_id の順。決定的）。"""
    items = list(guides)
    today = today or date.today()
    context = context if context is not None else build_guide_context(text, items)
    result: list[tuple[int, str, int, SupportGuideContent, dict[str, ApplicabilityStatus]]] = []
    for guide_id, revision, content in items:
        if not _in_period(content, today):
            continue
        score = guide_score(content, text)
        if score < MATCH_THRESHOLD:
            continue
        applicability = applicability_status(content, context)
        if applicability is None:
            continue
        result.append((score, guide_id, revision, content, applicability))
    result.sort(key=lambda item: (-item[0], item[1]))
    return result


def _match(
    item: tuple[int, str, int, SupportGuideContent, dict[str, ApplicabilityStatus]],
    text: str,
    provided: Mapping[str, str],
    *,
    interactive: bool,
) -> GuideMatch:
    score, guide_id, revision, content, applicability = item
    states = condition_states(content, text, provided)
    decision, unknown = decide(states, interactive=interactive)
    return GuideMatch(
        guide_id,
        revision,
        content,
        score,
        tuple(states),
        decision,
        tuple(unknown),
        applicability,
    )


def match_guide(
    guides: Iterable[GuideTriple],
    text: str,
    provided: Mapping[str, str] | None = None,
    *,
    interactive: bool = False,
    today: date | None = None,
    context: GuideContext | None = None,
) -> GuideMatch | None:
    """質問（と会話）に最も合う公開の業務ガイド。閾値未満・期間外・適用範囲の外なら None。

    ``context`` を渡さなければ、質問の文と公開ガイドの語だけから手がかりを読む。
    """
    candidates = _candidates(guides, text, today, context)
    if not candidates:
        return None
    return _match(candidates[0], text, provided or {}, interactive=interactive)


def rank_guides(
    guides: Iterable[GuideTriple],
    text: str,
    provided: Mapping[str, str] | None = None,
    *,
    limit: int = 3,
    today: date | None = None,
    context: GuideContext | None = None,
) -> list[GuideMatch]:
    """質問に当たる公開の業務ガイドを点の高い順に（MCP の rag_lookup_guides）。"""
    return [
        _match(item, text, provided or {}, interactive=False)
        for item in _candidates(guides, text, today, context)[:limit]
    ]


def _branch_applies(branch: SupportGuideBranch, state: ConditionState | None) -> bool | None:
    """分岐が今の条件に当たるか。条件が決まっていなければ None（どちらとも言えない）。"""
    if state is None or state.conflicting:
        return None
    if branch.when.operator == "unknown":
        return not state.known
    if not state.known:
        return None
    values = {_provided_value(state.condition, value) or value for value in branch.when.values}
    return state.value in values


def visible_plan(
    match: GuideMatch,
) -> tuple[list[SupportGuideStep], list[tuple[SupportGuideBranch, bool | None]]]:
    """回答の材料に渡す手順と分岐（分かっている条件で外れた分岐の行き先を外す。#1278）。

    当たる・決まらない分岐の行き先とその依存先は残す。外れた分岐の行き先と、外した手順だけに依存する
    手順は外す。どの分岐の行き先でもない手順（共通の手順）は残す。
    """
    content = match.content
    steps = {step.id: step for step in content.steps}
    decided = [
        (branch, _branch_applies(branch, match.state_of(branch.when.condition_id)))
        for branch in content.branches
    ]
    keep: set[str] = set()
    stack = [branch.goto_step for branch, applies in decided if applies is not False]
    while stack:
        step_id = stack.pop()
        if step_id in keep or step_id not in steps:
            continue
        keep.add(step_id)
        stack.extend(steps[step_id].depends_on)
    excluded = {branch.goto_step for branch, applies in decided if applies is False} - keep
    changed = bool(excluded)
    while changed:
        changed = False
        for step in content.steps:
            if step.id in excluded or step.id in keep or not step.depends_on:
                continue
            if all(dependency in excluded for dependency in step.depends_on):
                excluded.add(step.id)
                changed = True
    return (
        [step for step in content.steps if step.id not in excluded],
        [(branch, applies) for branch, applies in decided if applies is not False],
    )


def _condition_text(condition: SupportGuideCondition) -> str:
    options = f"（{'／'.join(condition.allowed_values)}）" if condition.allowed_values else ""
    note = f"。{_SOURCE_NOTES[condition.source]}" if condition.source in _SOURCE_NOTES else ""
    return condition.label + options + note


def _lines(match: GuideMatch) -> list[str]:
    content = match.content
    lines = [
        f"業務ガイド「{content.title}」（版 {match.revision}）に沿って答える。",
        f"期待する結果: {content.goal.expected_result}",
    ]
    unverified = [
        _DIMENSION_LABELS[name] + f"（{'／'.join(getattr(content.applicability, name))}）"
        for name, status in match.applicability.items()
        if status == "unverified"
    ]
    if unverified:
        lines.append(
            "適用範囲のうち質問から確かめられていない項目: "
            + "、".join(unverified)
            + "。この範囲に当たる場合の案内であることを示す。"
        )
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
    conflicting = [state for state in match.states if state.conflicting]
    if conflicting:
        lines.append(
            "質問に複数の値が出ている条件（どれかに決めつけず、値ごとに分けて示す）: "
            + "、".join(f"{s.condition.label}（{'／'.join(s.candidates)}）" for s in conflicting)
        )
    unknown = [
        state.condition for state in match.states if not state.known and not state.conflicting
    ]
    if unknown:
        lines.append(
            "分かっていない条件（断定せず、条件ごとに分けて示す）: "
            + "、".join(_condition_text(condition) for condition in unknown)
        )
    steps, branches = visible_plan(match)
    for index, step in enumerate(steps, start=1):
        after = f"（{'・'.join(step.depends_on)} の後）" if step.depends_on else ""
        purpose = f": {step.purpose}" if step.purpose else ""
        done = f" 終わりの条件: {step.done_when}" if step.done_when else ""
        lines.append(f"手順 {index}. {step.title}{after}{purpose}{done}")
    for branch, applies in branches:
        state = match.state_of(branch.when.condition_id)
        label = state.condition.label if state else branch.when.condition_id
        when = "不明" if branch.when.operator == "unknown" else "／".join(branch.when.values)
        target = next(
            (step.title for step in content.steps if step.id == branch.goto_step), branch.goto_step
        )
        if applies:
            lines.append(f"分岐: {label} が {when} に当たるため「{target}」へ進む。")
        else:
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
    steps, _ = visible_plan(match)
    hints = [hint for step in steps for hint in step.retrieval_hints]
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


def _condition_options(condition: SupportGuideCondition) -> list[str]:
    if condition.type == "boolean":
        return ["はい", "いいえ"]
    return list(condition.allowed_values)


def _clarification(match: GuideMatch, condition: SupportGuideCondition) -> tuple[str, list[str]]:
    """確かめる問いと選択肢。矛盾した条件は、質問に出た値を問いと選択肢に並べる。"""
    base = condition.question or f"{condition.label}を教えてください。"
    state = match.state_of(condition.id)
    if state is not None and state.conflicting:
        values = "」と「".join(state.candidates)
        return f"質問に{condition.label}の「{values}」が出ています。{base}", list(state.candidates)
    return base, _condition_options(condition)


def clarification_questions(match: GuideMatch) -> list[dict[str, Any]]:
    """確かめる条件と問い・選択肢（検索・MCP の needs_clarification）。"""
    questions: list[dict[str, Any]] = []
    for condition in match.unknown:
        question, options = _clarification(match, condition)
        questions.append(
            {
                "condition_id": condition.id,
                "label": condition.label,
                "question": question,
                "options": options,
            }
        )
    return questions


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
    "applicability",
    # 下書きで試した回答で、下書きの版を使ったとき True（#1288）。
    "draft",
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
    """回答の記録に業務ガイドの版・条件を残す。分岐で答えたときは「答えた」を条件付きにする。

    公開の業務ガイドを読めなかったときは ``guide_load_failed`` を残す（ガイドを使わずに答えた
    ことを、ガイドが無かったときと区別する。#1278）。
    """
    if guide.get(GUIDE_LOAD_FAILED_KEY):
        diagnostics[GUIDE_LOAD_FAILED_KEY] = True
        return
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

# 下書きで試す（#1288）。回答の記録・診断の key と、trace_id の接頭辞（利用者の回答の履歴・
# フィードバック・評価から外すのに使う。uuid の hex 32 字と合わせて 64 字に収まる）。
GUIDE_PREVIEW_KEY = "guide_preview"
GUIDE_PREVIEW_TRACE_PREFIX = "guide-preview-"


@dataclass(frozen=True)
class GuidePreview:
    """公開の版の代わりに、1 回の回答だけに使う下書き（#1288）。公開の版・保存は変えない。"""

    guide_id: str
    draft_revision: int
    content: SupportGuideContent
    published_revision: int | None = None

    def marker(self) -> dict[str, Any]:
        """回答の記録・診断に残す印（どのガイドのどの下書きの版で試したか）。"""
        return {
            "guide_id": self.guide_id,
            "draft_revision": self.draft_revision,
            "published_revision": self.published_revision,
        }


def with_draft(guides: Iterable[GuideTriple], preview: GuidePreview) -> list[GuideTriple]:
    """公開のガイドのうち、試すガイドだけを下書きに置き換えた一覧（未公開なら足す）。

    照合はほかの公開のガイドと同じ規則で行い、公開したときと同じガイドが選ばれるかを確かめる。
    """
    return [
        *(item for item in guides if item[0] != preview.guide_id),
        (preview.guide_id, preview.draft_revision, preview.content),
    ]


def is_guide_preview_trace(trace_id: str) -> bool:
    """下書きで試した回答の trace_id か。"""
    return trace_id.startswith(GUIDE_PREVIEW_TRACE_PREFIX)


def is_guide_preview_record(diagnostics: object) -> bool:
    """回答の記録の診断が、下書きで試した回答のものか。"""
    return isinstance(diagnostics, Mapping) and bool(diagnostics.get(GUIDE_PREVIEW_KEY))


def guide_clarification(match: GuideMatch) -> tuple[str, str, Any] | None:
    """チャットの確認の質問（ルールの確認と同じ形）。選択肢で答えられる最初の不明・矛盾の条件だけ。

    戻り値は (rule_id, 題名, RuleClarification)。rule_id は ``guide:<guide_id>:<condition_id>``。
    選択肢の id は条件の全選択肢の中の位置（``o<1 始まり>``）で、矛盾した条件は質問に出た値だけを
    出す（答えは ``resolve_guide_clarification`` が同じ位置で引き直す）。
    """
    from app.schemas.search_answer_profile_knowledge import (
        ClarificationOption,
        RuleClarification,
    )

    if match.decision != "clarify":
        return None
    for condition in match.unknown:
        values = _condition_options(condition)
        question, shown = _clarification(match, condition)
        if not 2 <= len(shown) <= 8:
            continue
        clarification = RuleClarification(
            question=question[:200],
            multiple=False,
            allow_other=False,
            options=[
                ClarificationOption(
                    id=f"o{values.index(value) + 1}",
                    label=value[:80],
                    search_terms=[value[:50]],
                    premise=f"{condition.label}は「{value}」"[:300],
                )
                for value in shown
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
    "GUIDE_LOAD_FAILED_KEY",
    "GUIDE_PREVIEW_KEY",
    "GUIDE_PREVIEW_TRACE_PREFIX",
    "MATCH_THRESHOLD",
    "ConditionState",
    "GuideContext",
    "GuideMatch",
    "GuidePreview",
    "applicability_status",
    "apply_guide_to_diagnostics",
    "build_guide_context",
    "clarification_questions",
    "condition_states",
    "decide",
    "guide_clarification",
    "guide_rule",
    "guide_score",
    "guide_short_circuit_outcome",
    "is_guide_preview_record",
    "is_guide_preview_trace",
    "match_guide",
    "rank_guides",
    "resolve_guide_clarification",
    "short_circuit_answer",
    "visible_plan",
    "with_draft",
    "with_guide_rule",
]
