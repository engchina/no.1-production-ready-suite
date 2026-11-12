"""等価条件の値を、許可列と単一主キーの実在から照合する。意味を推測して列を置換しない。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import sqlglot
from sqlglot.optimizer.scope import build_scope

from .models import AllowedObjects, SchemaTable
from .object_identity import format_object_part, object_match_key
from .oracle_dialect import OracleConversion
from .query_scope import QueryScopeViolation, _conjuncts, _origins, equality_parts


class IdentifierBindingConflict(QueryScopeViolation):
    pass


@dataclass(frozen=True)
class IdentifierCheck:
    table: str
    column: str
    primary_key: str
    value: str

    @property
    def sql(self) -> str:
        return (
            "SELECT CASE WHEN EXISTS (SELECT 1 FROM "
            + self.table
            + " WHERE "
            + self.column
            + " = :lookup_value) THEN 1 ELSE 0 END AS COLUMN_HIT, "
            "CASE WHEN EXISTS (SELECT 1 FROM "
            + self.table
            + " WHERE "
            + self.primary_key
            + " = :lookup_value) THEN 1 ELSE 0 END AS KEY_HIT FROM DUAL"
        )


def identifier_checks(
    sql: str,
    tables: Sequence[SchemaTable],
    *,
    question: str,
    owner: str,
    allowed: AllowedObjects,
    explicit_columns: Sequence[str] = (),
) -> list[IdentifierCheck]:
    tree = sqlglot.parse_one(sql, read=OracleConversion)
    root = build_scope(tree)
    if root is None:
        return []
    columns = {}
    for table in tables:
        keys = [
            constraint.columns
            for constraint in table.constraint_details
            if constraint.constraint_type == "P"
        ]
        if len(keys) != 1 or len(keys[0]) != 1:
            continue
        qualified = format_object_part(table.owner) + "." + format_object_part(table.table_name)
        primary = next(
            (column for column in table.columns if column.column_name == keys[0][0]), None
        )
        if primary is None or not primary.data_type.upper().startswith(
            ("VARCHAR", "NVARCHAR", "CHAR", "NCHAR")
        ):
            continue
        restrictions = next(
            (
                values
                for name, values in allowed.columns.items()
                if object_match_key(name) == object_match_key(qualified)
            ),
            [],
        )
        if restrictions and object_match_key(primary.column_name) not in {
            object_match_key(v) for v in restrictions
        }:
            continue
        for column in table.columns:
            if column.column_name == primary.column_name:
                continue
            if not column.data_type.upper().startswith(("VARCHAR", "NVARCHAR", "CHAR", "NCHAR")):
                continue
            if restrictions and object_match_key(column.column_name) not in {
                object_match_key(v) for v in restrictions
            }:
                continue
            # 明示した物理名・論理名の条件はそのまま保つ。語の意味による推定はしない。
            if any(
                label and label.casefold() in question.casefold()
                for label in (column.column_name, column.logical_name)
            ):
                continue
            identities = {
                object_match_key(qualified + "." + format_object_part(column.column_name)),
                object_match_key(
                    format_object_part(table.table_name)
                    + "."
                    + format_object_part(column.column_name)
                ),
            }
            if any(object_match_key(value) in identities for value in explicit_columns):
                continue
            columns[object_match_key(qualified + "." + format_object_part(column.column_name))] = (
                qualified,
                format_object_part(column.column_name),
                format_object_part(primary.column_name),
            )
    checks = []
    for scope in root.traverse():
        where = scope.expression.args.get("where")
        for clause in _conjuncts(where.this if where else None):
            parts = equality_parts(clause)
            if parts is None:
                continue
            reference_column, value = parts
            if not value.is_string or not str(value.this) or str(value.this) not in question:
                continue
            origins = _origins(reference_column, scope, owner)
            if len(origins) != 1:
                continue
            binding = columns.get(next(iter(origins)))
            if binding is not None:
                checks.append(IdentifierCheck(*binding, str(value.this)))
    return list(dict.fromkeys(checks))


def binding_conflicts(
    checks: Sequence[IdentifierCheck], probe: Callable[[IdentifierCheck], tuple[bool, bool]]
) -> list[str]:
    findings = []
    for check in checks:
        selected, primary = probe(check)
        if not selected and primary:
            findings.append(
                "IDENTIFIER_BINDING_UNCONFIRMED: "
                + check.table
                + "."
                + check.column
                + " に指定値がなく、同じ表の主キー "
                + check.primary_key
                + " に存在します。対象列は未確認です。"
                "原質問に基づき列を確認し、空の結果を確定しないでください。"
            )
    return findings
