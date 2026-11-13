"""実際の修正の形式的な事実を保存・公開する。SQL の意味や権限は判定しない。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Annotated, Literal, TypedDict

import sqlglot
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from .oracle_dialect import OracleConversion

MAX_GENERATION_DIAGNOSTIC_CHARS = 12_000
_MAX_ITEMS = 16
Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]
ShortCode = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64)]


class ExactColumnCandidate(TypedDict):
    owner: Identifier
    table: Identifier
    column: Identifier
    sql_identifier: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=512)]


class MissingPhysicalColumn(TypedDict):
    qualifier: Identifier
    column: Identifier
    quoted: bool


class BindingDiagnostic(TypedDict):
    source: Literal["schema_catalog"]
    reference: MissingPhysicalColumn
    exact_candidates: Annotated[list[ExactColumnCandidate], Field(max_length=_MAX_ITEMS)]
    truncated: bool


class _DiagnosticModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class ProjectionReference(_DiagnosticModel):
    ordinal: int = Field(ge=1, le=4096)
    output_name: Identifier
    expression_kind: ShortCode


class ProjectionChange(_DiagnosticModel):
    kind: Literal["expression_changed", "position_changed", "output_name_changed"]
    before: ProjectionReference
    after: ProjectionReference


class ProjectionChanges(_DiagnosticModel):
    status: Literal["compared", "unavailable"]
    unavailable_reason: (
        Literal["unsupported_projection_scope", "projection_comparison_failed"] | None
    ) = None
    removed: list[ProjectionReference] = Field(default_factory=list, max_length=_MAX_ITEMS)
    added: list[ProjectionReference] = Field(default_factory=list, max_length=_MAX_ITEMS)
    changed: list[ProjectionChange] = Field(default_factory=list, max_length=_MAX_ITEMS)
    truncated: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> ProjectionChanges:
        if self.status == "unavailable":
            if self.unavailable_reason is None or self.removed or self.added or self.changed:
                raise ValueError("比較不能の投影診断が不正です。")
        elif self.unavailable_reason is not None:
            raise ValueError("比較済みの投影診断が不正です。")
        return self


class GenerationRepair(_DiagnosticModel):
    rejected_attempt: Literal[1] = 1
    accepted_attempt: Literal[2] = 2
    reason_codes: list[Literal["SQL_COLUMN_BINDING_INVALID"]] = Field(
        default=["SQL_COLUMN_BINDING_INVALID"], max_length=1
    )
    binding_diagnostics: list[BindingDiagnostic] = Field(
        default_factory=list, max_length=_MAX_ITEMS
    )
    projection_changes: ProjectionChanges

    @field_validator("rejected_attempt", "accepted_attempt", mode="before")
    @classmethod
    def _integer_attempt(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("修正試行の番号は整数が必要です。")
        return value


class GenerationDiagnostics(_DiagnosticModel):
    schema_version: Literal[1] = 1
    status: Literal["recorded", "unavailable"] = "recorded"
    unavailable_reason: (
        Literal["invalid_saved_diagnostics", "diagnostic_build_failed", "diagnostic_size_exceeded"]
        | None
    ) = None
    repairs: list[GenerationRepair] = Field(default_factory=list, max_length=1)
    truncated: bool = False

    @field_validator("schema_version", mode="before")
    @classmethod
    def _integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("修正記録の版は整数が必要です。")
        return value

    @model_validator(mode="after")
    def _bounded(self) -> GenerationDiagnostics:
        if self.status == "recorded":
            if len(self.repairs) != 1 or self.unavailable_reason is not None:
                raise ValueError("修正記録の外枠が不正です。")
        elif self.repairs or self.unavailable_reason is None:
            raise ValueError("取得不能の修正記録が不正です。")
        if _size(self) > MAX_GENERATION_DIAGNOSTIC_CHARS:
            raise ValueError("SQL 修正診断が文字数上限を超えています。")
        return self


def _size(value: GenerationDiagnostics) -> int:
    return len(json.dumps(value.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")))


def unavailable_generation_diagnostics(
    reason: Literal[
        "invalid_saved_diagnostics", "diagnostic_build_failed", "diagnostic_size_exceeded"
    ],
) -> GenerationDiagnostics:
    return GenerationDiagnostics(status="unavailable", unavailable_reason=reason)


def recorded_generation_diagnostics(value: object) -> GenerationDiagnostics | None:
    """旧 job は None。不正な保存値は本文を漏らさず、取得不能と明示する。"""
    if value is None:
        return None
    try:
        return GenerationDiagnostics.model_validate(value)
    except (ValueError, TypeError):
        return unavailable_generation_diagnostics("invalid_saved_diagnostics")


def _projection_facts(sql: str) -> tuple[list[ProjectionReference], list[str]] | None:
    tree = normalize_identifiers(
        sqlglot.parse_one(sql, read=OracleConversion), dialect=OracleConversion
    )
    # scope を跨ぐ対応や star の実列数を推測しない。
    if not isinstance(tree, exp.Select) or tree.args.get("with_"):
        return None
    references: list[ProjectionReference] = []
    expressions: list[str] = []
    for ordinal, selected in enumerate(tree.expressions, 1):
        expression = selected.this if isinstance(selected, exp.Alias) else selected
        if selected.is_star:
            return None
        # alias のない literal/式の値を output_name として外へ出さない。
        name = (
            selected.alias
            if isinstance(selected, exp.Alias)
            else (selected.name if isinstance(selected, exp.Column) else "")
        )
        if not name:
            return None
        references.append(
            ProjectionReference(
                ordinal=ordinal,
                output_name=name,
                expression_kind=type(expression).__name__,
            )
        )
        # 式は同一性を内部で比較するだけで、本文・literal・hash は結果に出さない。
        expressions.append(expression.sql(dialect=OracleConversion, comments=False))
    if len({reference.output_name for reference in references}) != len(references):
        return None
    return references, expressions


def projection_changes(before_sql: str, after_sql: str) -> ProjectionChanges:
    try:
        before = _projection_facts(before_sql)
        after = _projection_facts(after_sql)
        if before is None or after is None:
            return ProjectionChanges(
                status="unavailable", unavailable_reason="unsupported_projection_scope"
            )
        before_refs, before_exprs = before
        after_refs, after_exprs = after
        left = {
            item.output_name: (item, expression)
            for item, expression in zip(before_refs, before_exprs, strict=True)
        }
        right = {
            item.output_name: (item, expression)
            for item, expression in zip(after_refs, after_exprs, strict=True)
        }
        changed: list[ProjectionChange] = []
        for name in left.keys() & right.keys():
            old, old_expression = left[name]
            new, new_expression = right[name]
            if old_expression != new_expression:
                changed.append(ProjectionChange(kind="expression_changed", before=old, after=new))
            elif old.ordinal != new.ordinal:
                changed.append(ProjectionChange(kind="position_changed", before=old, after=new))
        removed = [item for item in before_refs if item.output_name not in right]
        added = [item for item in after_refs if item.output_name not in left]
        # 一意な同一式だけは alias 変更と分かる。多重度があるときは対応を推測しない。
        for old in list(removed):
            expression = left[old.output_name][1]
            matches = [new for new in added if right[new.output_name][1] == expression]
            if (
                len(matches) == 1
                and sum(left[item.output_name][1] == expression for item in removed) == 1
            ):
                new = matches[0]
                changed.append(ProjectionChange(kind="output_name_changed", before=old, after=new))
                removed.remove(old)
                added.remove(new)
        changed.sort(key=lambda item: (item.before.ordinal, item.after.ordinal))
        return ProjectionChanges(
            status="compared",
            removed=removed[:_MAX_ITEMS],
            added=added[:_MAX_ITEMS],
            changed=changed[:_MAX_ITEMS],
            truncated=any(len(items) > _MAX_ITEMS for items in (removed, added, changed)),
        )
    except Exception:
        return ProjectionChanges(
            status="unavailable", unavailable_reason="projection_comparison_failed"
        )


def build_generation_diagnostics(
    before_sql: str,
    after_sql: str,
    bindings: Sequence[BindingDiagnostic],
) -> GenerationDiagnostics:
    """補足を作れなくても元の成功/失敗を変えない。技術上限で省く場合は明示する。"""
    try:
        projection = projection_changes(before_sql, after_sql)
        accepted: list[BindingDiagnostic] = []
        truncated = len(bindings) > _MAX_ITEMS
        for binding in bindings[:_MAX_ITEMS]:
            try:
                validated = GenerationRepair.model_validate(
                    {
                        "binding_diagnostics": [binding],
                        "projection_changes": {
                            "status": "unavailable",
                            "unavailable_reason": "unsupported_projection_scope",
                        },
                    }
                )
                accepted.extend(validated.binding_diagnostics)
            except (ValueError, TypeError):
                truncated = True
        repair = GenerationRepair(binding_diagnostics=accepted, projection_changes=projection)
        # 最終全体サイズ検査の前だけ未検証の外枠を使い、有界になるまで完全な項目単位で省く。
        result = GenerationDiagnostics.model_construct(repairs=[repair], truncated=truncated)
        while _size(result) > MAX_GENERATION_DIAGNOSTIC_CHARS:
            result.truncated = True
            candidates = [
                item for item in repair.binding_diagnostics if len(item["exact_candidates"]) > 1
            ]
            if candidates:
                item = max(candidates, key=lambda entry: len(entry["exact_candidates"]))
                item["exact_candidates"].pop()
                item["truncated"] = True
            elif len(repair.binding_diagnostics) > 1:
                repair.binding_diagnostics.pop()
            elif projection.changed:
                projection.changed.pop()
                projection.truncated = True
            elif len(projection.removed) > 1 or projection.added:
                (projection.removed if len(projection.removed) > 1 else projection.added).pop()
                projection.truncated = True
            elif repair.binding_diagnostics:
                repair.binding_diagnostics.pop()
            elif projection.removed:
                projection.removed.pop()
                projection.truncated = True
            else:
                return unavailable_generation_diagnostics("diagnostic_size_exceeded")
        return GenerationDiagnostics.model_validate(result.model_dump(mode="json"))
    except Exception:
        return unavailable_generation_diagnostics("diagnostic_build_failed")
