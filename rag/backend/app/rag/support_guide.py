"""業務ガイド（SupportGuide。#1237）の検証と内容の指紋。

保存のたびと公開の前に、内容の整合（id の重複・依存の参照先と循環・分岐の行き先と網羅・道具）を
確かめる。公開の前だけ、参照する文書がプロファイルのナレッジベースにあり索引済みかも確かめる。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable, Sequence

from app.schemas.support_guide import (
    SUPPORT_GUIDE_TOOLS,
    SupportGuideContent,
    SupportGuideIssue,
)


def content_sha256(content: SupportGuideContent) -> str:
    """内容の指紋（キーの順を固定した JSON の sha256）。"""
    payload = json.dumps(
        content.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _error(code: str, path: str, message: str) -> SupportGuideIssue:
    return SupportGuideIssue(severity="error", code=code, path=path, message=message)


def _warning(code: str, path: str, message: str) -> SupportGuideIssue:
    return SupportGuideIssue(severity="warning", code=code, path=path, message=message)


def _duplicates(ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in ids:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _cycle(steps: dict[str, list[str]]) -> list[str] | None:
    """依存の循環（見つけた順の手順 id）。無ければ None。"""
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(step_id: str) -> list[str] | None:
        state[step_id] = 1
        stack.append(step_id)
        for dependency in steps.get(step_id, []):
            if dependency not in steps:
                continue
            if state.get(dependency) == 1:
                return [*stack[stack.index(dependency) :], dependency]
            if state.get(dependency) is None and (found := visit(dependency)):
                return found
        stack.pop()
        state[step_id] = 2
        return None

    for step_id in steps:
        if state.get(step_id) is None and (found := visit(step_id)):
            return found
    return None


def validate_content(content: SupportGuideContent) -> list[SupportGuideIssue]:
    """内容の整合を確かめる（文書の参照は確かめない）。"""
    issues: list[SupportGuideIssue] = []
    if not content.goal.intent_examples and not content.goal.match_terms:
        issues.append(
            _error(
                "goal_unmatchable",
                "goal",
                "質問の例か照合の語を 1 つ以上入れてください（どの質問に使うかを決められません）。",
            )
        )
    if not content.steps:
        issues.append(_error("steps_missing", "steps", "手順を 1 つ以上入れてください。"))
    for name, ids in (
        ("conditions", [condition.id for condition in content.conditions]),
        ("steps", [step.id for step in content.steps]),
        ("branches", [branch.id for branch in content.branches]),
        ("completion", [item.id for item in content.completion]),
    ):
        for duplicate in _duplicates(ids):
            issues.append(_error("duplicate_id", name, f"id「{duplicate}」が重複しています。"))

    step_ids = {step.id for step in content.steps}
    graph: dict[str, list[str]] = {}
    for index, step in enumerate(content.steps):
        graph[step.id] = list(step.depends_on)
        for dependency in step.depends_on:
            if dependency == step.id:
                issues.append(
                    _error(
                        "self_dependency",
                        f"steps[{index}].depends_on",
                        f"手順「{step.title}」が自分自身に依存しています。",
                    )
                )
            elif dependency not in step_ids:
                issues.append(
                    _error(
                        "unknown_dependency",
                        f"steps[{index}].depends_on",
                        f"手順「{step.title}」の依存先「{dependency}」がありません。",
                    )
                )
        unknown_tools = [tool for tool in step.allowed_tools if tool not in SUPPORT_GUIDE_TOOLS]
        if unknown_tools:
            issues.append(
                _error(
                    "unknown_tool",
                    f"steps[{index}].allowed_tools",
                    f"手順「{step.title}」の道具「{'、'.join(unknown_tools)}」は使えません。",
                )
            )
    if cycle := _cycle({key: [d for d in value if d != key] for key, value in graph.items()}):
        issues.append(
            _error("dependency_cycle", "steps", f"手順の依存が循環しています: {' → '.join(cycle)}")
        )

    conditions = {condition.id: condition for condition in content.conditions}
    covered: dict[str, set[str]] = {}
    has_unknown: set[str] = set()
    for index, branch in enumerate(content.branches):
        condition = conditions.get(branch.when.condition_id)
        if condition is None:
            issues.append(
                _error(
                    "unknown_condition",
                    f"branches[{index}].when",
                    f"分岐「{branch.id}」の条件「{branch.when.condition_id}」がありません。",
                )
            )
            continue
        if branch.goto_step not in step_ids:
            issues.append(
                _error(
                    "unknown_goto",
                    f"branches[{index}].goto_step",
                    f"分岐「{branch.id}」の行き先「{branch.goto_step}」の手順がありません。",
                )
            )
        if branch.when.operator == "unknown":
            has_unknown.add(condition.id)
            continue
        if condition.type == "enum":
            invalid = [
                value for value in branch.when.values if value not in condition.allowed_values
            ]
            if invalid:
                issues.append(
                    _error(
                        "unknown_value",
                        f"branches[{index}].when.values",
                        f"分岐「{branch.id}」の値「{'、'.join(invalid)}」は"
                        f"条件「{condition.label}」の選択肢にありません。",
                    )
                )
        covered.setdefault(condition.id, set()).update(branch.when.values)
    for index, condition in enumerate(content.conditions):
        if condition.unknown_handling == "branch" and condition.id not in has_unknown:
            issues.append(
                _error(
                    "unknown_branch_missing",
                    f"conditions[{index}]",
                    f"条件「{condition.label}」は不明のとき分岐しますが、"
                    "unknown の分岐がありません。",
                )
            )
        if condition.type == "enum" and condition.id in covered:
            missing = [
                value for value in condition.allowed_values if value not in covered[condition.id]
            ]
            if missing:
                issues.append(
                    _warning(
                        "branch_not_covered",
                        f"conditions[{index}]",
                        f"条件「{condition.label}」の「{'、'.join(missing)}」に当たる分岐がありません。",
                    )
                )
    if content.impact.scope != "individual" and not content.impact.approval_required:
        issues.append(
            _warning(
                "wide_impact_without_approval",
                "impact",
                "影響範囲がグループ・全体なのに、承認を求めていません。",
            )
        )
    return issues


DocumentLookup = Callable[[str], Awaitable[str | None]]


async def reference_issues(
    content: SupportGuideContent, lookup: DocumentLookup
) -> list[SupportGuideIssue]:
    """参照する文書が使えるか（公開の前だけ）。

    ``lookup`` は文書 ID から問題（見つからない・プロファイルの対象外・索引前など）の説明を返し、
    使えれば None を返す。
    """
    issues: list[SupportGuideIssue] = []
    for index, reference in enumerate(content.references):
        problem = await lookup(reference.document_id)
        if problem:
            name = reference.title or reference.document_id
            issues.append(
                _error(
                    "reference_unavailable", f"references[{index}]", f"資料「{name}」: {problem}"
                )
            )
    return issues


def has_errors(issues: Sequence[SupportGuideIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


__all__ = ["content_sha256", "has_errors", "reference_issues", "validate_content"]
