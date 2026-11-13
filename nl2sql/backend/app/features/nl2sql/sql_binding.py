"""SQL の scope と公開 schema から列の束縛を検証する。SQL 本文は変更しない。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

from .generation_diagnostics import BindingDiagnostic, ExactColumnCandidate, MissingPhysicalColumn
from .models import SchemaTable
from .oracle_dialect import OracleConversion


class SqlGenerationValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ColumnBindingFinding:
    message: str
    diagnostics: list[BindingDiagnostic] = field(default_factory=list)


_MAX_EXACT_CANDIDATES = 16
_MAX_BINDING_DIAGNOSTICS = 16


def column_binding_violations(sql: str, tables: Sequence[SchemaTable], *, owner: str) -> list[str]:
    """既存の違反文字列を保つ。候補用の権限が不明な呼出しには候補を付けない。"""
    return [finding.message for finding in column_binding_findings(sql, tables, owner=owner)]


def column_binding_findings(
    sql: str,
    tables: Sequence[SchemaTable],
    *,
    owner: str,
    candidate_tables: Sequence[SchemaTable] = (),
) -> list[ColumnBindingFinding]:
    """候補だけに、呼出元が object・column の可視性を確認した同回の snapshot を使う。"""
    schema = MappingSchema(dialect=OracleConversion, normalize=False)
    for table in tables:
        schema.add_table(
            exp.Table(
                this=exp.Identifier(this=table.table_name, quoted=True),
                db=exp.Identifier(this=table.owner, quoted=True),
            ),
            {column.column_name: "UNKNOWN" for column in table.columns},
            normalize=False,
        )
    # DUAL は利用者の schema に無い Oracle の1行の表。
    for dual_owner in {owner, "SYS"}:
        if dual_owner:
            schema.add_table(
                exp.Table(this=exp.to_identifier("DUAL"), db=exp.to_identifier(dual_owner)),
                {"DUMMY": "VARCHAR"},
                normalize=False,
            )
    try:
        tree = sqlglot.parse_one(sql, read=OracleConversion)
        original_tree = tree.copy()
        # 自動の表別名が引用名を大文字に変えないよう、検証用 AST だけに同名の別名を置く。
        for source in tree.find_all(exp.Table):
            if not source.args.get("alias") and isinstance(source.this, exp.Identifier):
                source.set("alias", exp.TableAlias(this=source.this.copy()))
        qualify(
            tree,
            dialect=OracleConversion,
            db=owner or None,
            schema=schema if tables else None,
            validate_qualify_columns=True,
        )
        return [
            ColumnBindingFinding(message=message)
            for message in (_set_operation_violations(tree) or _derived_column_violations(tree))
        ]
    except OptimizeError as exc:
        if not tables:
            return []
        try:
            diagnostics = _missing_physical_columns(
                original_tree, tables, candidate_tables=candidate_tables, owner=owner
            )
        except Exception:
            # 補足候補の作成失敗で、元の検査結果や SQL 本文を変更しない。
            diagnostics = []
        return [ColumnBindingFinding("SQL_COLUMN_BINDING_INVALID: " + str(exc), diagnostics)]
    except SqlglotError:
        return []  # 構文の検査は parse_oracle_sql が担当する。
    return []


def _missing_physical_columns(
    tree: exp.Expr,
    tables: Sequence[SchemaTable],
    *,
    candidate_tables: Sequence[SchemaTable],
    owner: str,
) -> list[BindingDiagnostic]:
    """例外文を解析せず、ローカル scope の確定した物理列だけを観測する。"""
    if not candidate_tables:
        return []
    normalized = normalize_identifiers(tree.copy(), dialect=OracleConversion)
    visible = {(table.owner, table.table_name): table for table in candidate_tables}
    physical = {(table.owner, table.table_name): table for table in tables}
    diagnostics: list[BindingDiagnostic] = []
    seen: set[tuple[str, str, str]] = set()
    for scope in traverse_scope(normalized):
        for column in scope.columns:
            if column.find_ancestor(exp.Select) is not scope.expression:
                continue
            # CTE/派生列、無修飾列、外側の scope はここで推測しない。
            source = scope.sources.get(column.table) if column.table else None
            if not isinstance(source, exp.Table) or source.catalog or source.args.get("pivots"):
                continue
            table = visible.get((source.db or owner, source.name))
            if table is None or column.is_star:
                continue
            original = physical.get((table.owner, table.table_name))
            if original is None or column.name in {item.column_name for item in original.columns}:
                continue
            if not isinstance(column.this, exp.Identifier):
                continue
            key = (table.owner, table.table_name, column.name)
            if key in seen:
                continue
            seen.add(key)
            candidates = [
                ExactColumnCandidate(
                    owner=candidate.owner,
                    table=candidate.table_name,
                    column=item.column_name,
                    sql_identifier=".".join(
                        exp.Identifier(this=name, quoted=True).sql(dialect=OracleConversion)
                        for name in (candidate.owner, candidate.table_name, item.column_name)
                    ),
                )
                for candidate in candidate_tables
                for item in candidate.columns
                if item.column_name == column.name
            ]
            # 同名候補のどれが意味上正しいかは決めず、安定した順序で有界に返す。
            candidates.sort(key=lambda item: (item["owner"], item["table"], item["column"]))
            diagnostics.append(
                BindingDiagnostic(
                    source="schema_catalog",
                    reference=MissingPhysicalColumn(
                        qualifier=column.table,
                        column=column.name,
                        quoted=bool(column.this.args.get("quoted")),
                    ),
                    exact_candidates=candidates[:_MAX_EXACT_CANDIDATES],
                    truncated=len(candidates) > _MAX_EXACT_CANDIDATES,
                )
            )
            if len(diagnostics) == _MAX_BINDING_DIAGNOSTICS:
                return diagnostics
    return diagnostics


def _derived_column_violations(tree: exp.Expr) -> list[str]:
    """正規化・列リスト適用・star展開後の参照先の多重度を検査する。"""
    for scope in traverse_scope(tree):
        if not isinstance(scope.expression, exp.Selectable):
            continue
        for column in scope.columns:
            source = scope.sources.get(column.table)
            # 相関参照の表別名は、最も近い外側のscopeで解決する。
            parent = scope.parent
            while source is None and parent is not None:
                source = parent.sources.get(column.table)
                parent = parent.parent
            if not isinstance(source, Scope) or not isinstance(source.expression, exp.Selectable):
                continue
            if Counter(source.expression.named_selects)[column.name] > 1:
                return [f"SQL_COLUMN_BINDING_INVALID: Ambiguous derived column: {column.sql()}"]
        # 重複した派生出力のstarはSQLGlotが展開せず残すため、別に参照先を検査する。
        for selection in scope.expression.selects:
            if not selection.is_star:
                continue
            sources = (
                [scope.sources.get(selection.table)]
                if isinstance(selection, exp.Column)
                else scope.selected_sources.values()
            )
            for candidate in sources:
                source = candidate[1] if isinstance(candidate, tuple) else candidate
                if (
                    isinstance(source, Scope)
                    and isinstance(source.expression, exp.Selectable)
                    and any(
                        count > 1 for count in Counter(source.expression.named_selects).values()
                    )
                ):
                    return [
                        "SQL_COLUMN_BINDING_INVALID: Ambiguous derived column: " + selection.sql()
                    ]
    return []


def _projection_arity(query: exp.Expr) -> int | None:
    """列束縛後もstarが残る投影は数えず、入れ子の確定した出力数だけを返す。"""
    if isinstance(query, (exp.Subquery, exp.Paren)):
        return _projection_arity(query.this)
    if isinstance(query, exp.SetOperation):
        left = _projection_arity(query.this)
        right = _projection_arity(query.expression)
        return left if left is not None and left == right else None
    if isinstance(query, exp.Select):
        return None if any(column.is_star for column in query.selects) else len(query.selects)
    return None


def _set_operation_violations(tree: exp.Expr) -> list[str]:
    """UNION等の列数を、別名・CTE列リスト・star解決済みの検証用ASTで照合する。"""
    for operation in tree.find_all(exp.SetOperation):
        left = _projection_arity(operation.this)
        right = _projection_arity(operation.expression)
        if left is not None and right is not None and left != right:
            return [
                f"SQL_COLUMN_BINDING_INVALID: Set operation {operation.key.upper()} "
                f"has incompatible projection counts: {left} and {right}"
            ]
    return []
