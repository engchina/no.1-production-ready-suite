"""呼び出し元が選んだ業務ガイドの指示を、入力順と上限で選ぶ。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rag_engine.matching.analysis import ANALYSIS_VERSION

if TYPE_CHECKING:
    from .runtime_knowledge import RuntimeRule


@dataclass(frozen=True)
class RuleMatches:
    selected: tuple[RuntimeRule, ...]
    decisions: tuple[dict[str, Any], ...]


def select_guide_rules(rules: Sequence[RuntimeRule], limit: int) -> RuleMatches:
    """有効で pinned の指示だけを選び、質問との語の一致は推測しない。"""
    eligible = tuple(rule for rule in rules if rule.active and "pinned" in rule.tags)
    selected = eligible[: max(1, limit)]
    decisions = tuple(
        {
            "source": "rule",
            "key": rule.rule_id,
            "matched_labels": [],
            "accepted": index < len(selected),
            "selected": index < len(selected),
            "reason": "caller_selected" if index < len(selected) else "limit",
            "score_kind": "ranking_only",
            "score": 1000.0,
            "rank": index + 1,
            "contributions": [],
            "conditions": [],
            "unmet": [],
            "analysis_version": ANALYSIS_VERSION,
        }
        for index, rule in enumerate(eligible)
    )
    return RuleMatches(selected, decisions)
