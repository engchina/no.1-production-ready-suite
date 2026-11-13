"""MCP の生成修正記録を、識別名を変えず有界に保つ。"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_MAX_CHARS = 12_000
_MAX_ITEMS = 16


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _Projection(_Wire):
    ordinal: int = Field(ge=1, le=4096)
    output_name: str = Field(min_length=1, max_length=128)
    expression_kind: str = Field(min_length=1, max_length=64)


class _Change(_Wire):
    kind: Literal["expression_changed", "position_changed", "output_name_changed"]
    before: _Projection
    after: _Projection


class _Changes(_Wire):
    status: Literal["compared", "unavailable"]
    unavailable_reason: str | None = Field(default=None, max_length=64)
    removed: list[_Projection]
    added: list[_Projection]
    changed: list[_Change]
    truncated: bool


class _Reference(_Wire):
    qualifier: str = Field(min_length=1, max_length=128)
    column: str = Field(min_length=1, max_length=128)
    quoted: bool


class _Candidate(_Wire):
    owner: str = Field(min_length=1, max_length=128)
    table: str = Field(min_length=1, max_length=128)
    column: str = Field(min_length=1, max_length=128)
    sql_identifier: str = Field(min_length=1, max_length=512)


class _Binding(_Wire):
    source: Literal["schema_catalog"]
    reference: _Reference
    exact_candidates: list[_Candidate]
    truncated: bool


class _Repair(_Wire):
    rejected_attempt: Literal[1]
    accepted_attempt: Literal[2]
    reason_codes: list[Literal["SQL_COLUMN_BINDING_INVALID"]] = Field(max_length=1)
    binding_diagnostics: list[_Binding]
    projection_changes: _Changes

    @field_validator("rejected_attempt", "accepted_attempt", mode="before")
    @classmethod
    def _integer_attempt(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("attempt must be an integer")
        return value


class _Diagnostics(_Wire):
    schema_version: Literal[1]
    status: Literal["recorded", "unavailable"]
    unavailable_reason: str | None = Field(default=None, max_length=64)
    repairs: list[_Repair]
    truncated: bool

    @field_validator("schema_version", mode="before")
    @classmethod
    def _integer_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("schema version must be an integer")
        return value


def generation_diagnostics_summary(value: Any) -> dict[str, Any] | None:
    """未知／不正な記録は捨て、欠落や名前の切詰めを正常な記録に見せない。"""
    if value is None:
        return None
    # 検証前にも入力サイズを制限し、過大な入れ子を Pydantic に渡さない。
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        return None
    if len(raw) > _MAX_CHARS * 4:
        return None
    try:
        parsed = _Diagnostics.model_validate(value)
    except (ValidationError, RecursionError):
        return None
    if not parsed.repairs:
        return parsed.model_dump(mode="json") if parsed.status == "unavailable" else None
    if len(parsed.repairs) > 1:
        parsed.repairs = parsed.repairs[:1]
        parsed.truncated = True
    repair = parsed.repairs[0]
    if len(repair.binding_diagnostics) > _MAX_ITEMS:
        repair.binding_diagnostics = repair.binding_diagnostics[:_MAX_ITEMS]
        parsed.truncated = True
    for binding in repair.binding_diagnostics:
        if len(binding.exact_candidates) > _MAX_ITEMS:
            binding.exact_candidates = binding.exact_candidates[:_MAX_ITEMS]
            binding.truncated = parsed.truncated = True
    changes = repair.projection_changes
    for key in ("removed", "added", "changed"):
        items = getattr(changes, key)
        if len(items) > _MAX_ITEMS:
            setattr(changes, key, items[:_MAX_ITEMS])
            changes.truncated = parsed.truncated = True

    def size() -> int:
        return len(parsed.model_dump_json())

    # 削除された出力名を優先して保つ。候補を省く場合も名前自体は切らない。
    while size() > _MAX_CHARS:
        parsed.truncated = True
        tail_binding = next(
            (b for b in reversed(repair.binding_diagnostics) if b.exact_candidates), None
        )
        if tail_binding is not None:
            tail_binding.exact_candidates.pop()
            tail_binding.truncated = True
        elif repair.binding_diagnostics:
            repair.binding_diagnostics.pop()
        elif changes.added:
            changes.added.pop()
            changes.truncated = True
        elif changes.changed:
            changes.changed.pop()
            changes.truncated = True
        elif changes.removed:
            changes.removed.pop()
            changes.truncated = True
        else:
            return None
    return parsed.model_dump(mode="json")
