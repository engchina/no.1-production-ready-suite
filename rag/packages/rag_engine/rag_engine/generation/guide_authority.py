"""業務ガイドの要求範囲を、資料の適用から独立して記録する。"""

from collections.abc import Sequence
from copy import deepcopy
from typing import Any


def authoritative_guide_inputs(
    task_scope: Sequence[dict[str, Any]],
    steps: Sequence[tuple[str, str, Sequence[str]]],
    contracts: Sequence[dict[str, Any]],
) -> tuple[tuple[tuple[str, str, Sequence[str]], ...], tuple[str, ...], tuple[dict[str, Any], ...]]:
    """要求を検索成否から独立させる。根拠IDは既存の検索結果だけから引き継ぐ。"""
    sources = {identity: evidence for identity, _title, evidence in steps}
    previous = {row["step_id"]: row for row in contracts}
    selected_steps = []
    optional = []
    selected_contracts = []
    for row in task_scope:
        identity = str(row.get("step_id") or "")
        if not identity:
            continue
        selected_steps.append((identity, str(row.get("title") or ""), sources.get(identity, ())))
        if row.get("required") is not True:
            optional.append(identity)
        contract = deepcopy(previous.get(identity, {}))
        contract.update(
            step_id=identity,
            purpose=row.get("purpose", contract.get("purpose", "")),
            requirements=deepcopy(row.get("requirements", contract.get("requirements", []))),
            depends_on=deepcopy(row.get("depends_on", contract.get("depends_on", []))),
        )
        selected_contracts.append(contract)
    return tuple(selected_steps), tuple(optional), tuple(selected_contracts)
