"""選択済みの業務ガイドの回答指示を適用する。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runtime_knowledge import RuntimeRule


@dataclass(frozen=True)
class RuleApplications:
    selected: tuple[RuntimeRule, ...]
    decisions: tuple[dict[str, Any], ...]


def rule_version(payload: dict[str, Any]) -> str:
    """実行時の設定本文を特定する版。更新時刻の有無に依存させない。"""
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def select_rule_applications(candidates: Sequence[RuntimeRule], limit: int) -> RuleApplications:
    """業務ガイドの回答指示を入力順で採用する。同じ本文は一度だけ、上限を超えた分は使わない。"""
    selected: list[RuntimeRule] = []
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in candidates:
        reason = "selected"
        if not rule.content:
            reason = "no_generation_instruction"
        elif rule.content in seen:
            reason = "duplicate_instruction"
        elif len(selected) >= max(1, limit):
            reason = "application_budget"
        else:
            selected.append(rule)
            seen.add(rule.content)
        decisions.append(
            {
                "id": rule.rule_id,
                "title": rule.title,
                "version": rule.version,
                "stage": "generation",
                "accepted": reason == "selected",
                "reason": reason,
            }
        )
    return RuleApplications(tuple(selected), tuple(decisions))
