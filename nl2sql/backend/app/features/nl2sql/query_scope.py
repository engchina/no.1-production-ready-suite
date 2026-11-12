"""起点の解決条件と、関連先の結果集合を区別する構造化契約と AST 検査。"""

from __future__ import annotations

import json
from typing import Any, Literal

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, build_scope

from .object_identity import object_match_key
from .oracle_dialect import OracleConversion


class RelatedRecordScope(BaseModel):
    source_quote: str = Field(description="起点と関連先全体を求めている原質問の連続した写し。")
    anchor_predicate: str = Field(
        description=(
            "起点を特定する物理列とリテラルの等価条件（TABLE.COLUMN = 値）。"
            "結果全体の条件ではない。"
        )
    )
    result_key_columns: list[str] = Field(
        description="結果のレコードを識別する物理キー列。起点に限定してはいけない列。"
    )


class QueryScopeViolation(ValueError):
    pass


def _conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    if isinstance(node, exp.Paren):
        return _conjuncts(node.this)
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    return [node]


def equality_parts(node: exp.Expression) -> tuple[exp.Column, exp.Literal] | None:
    if isinstance(node, exp.EQ):
        for column, value in [(node.this, node.expression), (node.expression, node.this)]:
            if isinstance(column, exp.Column) and isinstance(value, exp.Literal):
                return column, value
    if (
        isinstance(node, exp.In)
        and isinstance(node.this, exp.Column)
        and len(node.expressions) == 1
        and isinstance(node.expressions[0], exp.Literal)
    ):
        return node.this, node.expressions[0]
    return None


def parse_anchor(predicate: str) -> tuple[exp.Column, exp.Literal]:
    statements = sqlglot.parse("SELECT 1 WHERE " + predicate, read=OracleConversion)
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("起点の条件は SQL の等価条件を一つだけ指定してください。")
    tree = statements[0]
    where = tree.args.get("where")
    parts = equality_parts(where.this) if where else None
    if parts is None:
        raise ValueError("起点の条件は物理キー列とリテラルの等価条件にしてください。")
    return parts


def _table_key(table: exp.Table, column: exp.Column, owner: str) -> str:
    identifiers = [table.args.get(key) for key in ("catalog", "db", "this")]
    names = [node.sql(dialect=OracleConversion) for node in identifiers if node is not None]
    if len(names) == 1 and owner:
        names.insert(0, owner)
    names.append(column.this.sql(dialect=OracleConversion))
    return object_match_key(".".join(names))


def _origins(
    column: exp.Column, scope: Scope, owner: str, visited: frozenset[int] = frozenset()
) -> set[str]:
    if id(scope) in visited:
        return set()
    sources = scope.selected_sources
    if column.table:
        candidates = [
            source
            for alias, (_, source) in sources.items()
            if object_match_key(alias) == object_match_key(column.table)
        ]
        # OWNER.TABLE.COLUMN で、別名の無い表を修飾した形。
        if not candidates:
            candidates = [
                source
                for _, source in sources.values()
                if isinstance(source, exp.Table)
                and source.name == column.table
                and (not column.db or source.db == column.db)
            ]
    else:
        candidates = [source for _, source in sources.values()] if len(sources) == 1 else []
    result: set[str] = set()
    for source in candidates:
        if isinstance(source, exp.Table):
            result.add(_table_key(source, column, owner))
        elif isinstance(source, Scope) and isinstance(source.expression, exp.Query):
            for projection in source.expression.selects:
                if projection.is_star:
                    unqualified = column.copy()
                    unqualified.set("table", None)
                    unqualified.set("db", None)
                    result.update(_origins(unqualified, source, owner, visited | {id(scope)}))
                elif object_match_key(projection.alias_or_name) == object_match_key(column.name):
                    value = projection.this if isinstance(projection, exp.Alias) else projection
                    if isinstance(value, exp.Column):
                        result.update(_origins(value, source, owner, visited | {id(scope)}))
    return result


def _result_scopes(scope: Scope, seen: set[int]) -> list[Scope]:
    if id(scope) in seen:
        return []
    seen.add(id(scope))
    result = [scope]
    for _, source in scope.selected_sources.values():
        if isinstance(source, Scope):
            result.extend(_result_scopes(source, seen))
    for child in scope.set_operation_scopes:
        result.extend(_result_scopes(child, seen))
    return result


def related_scope_violations(
    sql: str, contracts: list[RelatedRecordScope], *, owner: str = ""
) -> list[str]:
    """結果を起点だけに狭める必須条件を検出する。起点検索のサブクエリは対象外。"""
    if not contracts:
        return []
    try:
        tree = sqlglot.parse_one(sql, read=OracleConversion)
        root = build_scope(tree)
    except (sqlglot.errors.SqlglotError, ValueError, TypeError):
        return []  # 構文の可否は既存の安全検査が判定する。
    if root is None:
        return []
    findings = []
    for contract in contracts:
        anchor_column, anchor_value = parse_anchor(contract.anchor_predicate)
        keys = {object_match_key(key) for key in contract.result_key_columns}
        keys.add(object_match_key(anchor_column.sql(dialect=OracleConversion)))
        for scope in _result_scopes(root, set()):
            expression = scope.expression
            if not isinstance(expression, exp.Query):
                continue
            if scope is not root and not any(
                projection.is_star
                or any(
                    _origins(column, scope, owner) & keys
                    for column in projection.find_all(exp.Column)
                )
                for projection in expression.selects
            ):
                continue
            clauses = []
            equal_columns: list[tuple[set[str], set[str]]] = []
            where = scope.expression.args.get("where")
            if where is not None:
                clauses.extend(_conjuncts(where.this))
            for join in scope.expression.args.get("joins") or []:
                if not join.side and join.kind not in {"CROSS"}:
                    clauses.extend(_conjuncts(join.args.get("on")))
            for clause in clauses:
                if (
                    isinstance(clause, exp.EQ)
                    and isinstance(clause.this, exp.Column)
                    and isinstance(clause.expression, exp.Column)
                ):
                    equal_columns.append(
                        (
                            _origins(clause.this, scope, owner),
                            _origins(clause.expression, scope, owner),
                        )
                    )
            equivalent_keys = set(keys)
            while True:
                expanded = set(equivalent_keys)
                for left, right in equal_columns:
                    if left & equivalent_keys or right & equivalent_keys:
                        expanded.update(left | right)
                if expanded == equivalent_keys:
                    break
                equivalent_keys = expanded
            for clause in clauses:
                parts = equality_parts(clause)
                if parts is None:
                    continue
                column, value = parts
                if value != anchor_value or not (_origins(column, scope, owner) & equivalent_keys):
                    continue
                findings.append(
                    "RELATED_SCOPE_ANCHOR_RESTRICTION: "
                    + clause.sql(dialect=OracleConversion)
                    + " は関連先全体を起点だけに限定します。"
                    "起点の特定条件と結果集合の条件を分けてください。"
                )
    return list(dict.fromkeys(findings))


class ScopeInterpretation(BaseModel):
    kind: Literal["direct", "related"]
    source_quote: str
    anchor_predicate: str
    result_key_columns: list[str]


SCOPE_SYSTEM_PROMPT = (
    "## Step 1: 質問の結果集合を判断してください。起点のレコードから関係をたどった"
    "他のレコード全体を求める場合は related、それ以外は direct とします。"
    "起点自身を含む・除くの違いはこの判断を変えません。\n"
    "## Step 2: related の場合、source_quote にその関係を求める原質問の連続した写し、"
    "anchor_predicate に起点の物理キー列と値の SQL 等価条件、"
    "result_key_columns に結果レコードを識別する物理列を入れてください。"
    "列名はスキーマの完全修飾名を使用してください。"
    "起点条件は関連先全体の制約ではありません。\n"
    "## Step 3: direct の場合、source_quote と anchor_predicate は空文字列、"
    "result_key_columns は空配列にしてください。指定された JSON schema で返してください。"
)


def interpret_query_scope(
    client: Any,
    *,
    question: str,
    context: Any,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> list[RelatedRecordScope]:
    from pr_backend_core.observability.model_purpose import call_with_model_purpose

    from .structured_outputs import response_format

    schema = [
        {
            "table": table.key,
            "columns": [
                column.key
                for column in table.columns.values()
                if not column.masked and column.filterable
            ],
        }
        for table in context.tables.values()
    ]
    kwargs: dict[str, Any] = dict(
        prompt=question,
        context=json.dumps(schema, ensure_ascii=False),
        system_prompt=SCOPE_SYSTEM_PROMPT,
        response_format=response_format(ScopeInterpretation),
    )
    if timeout_seconds is not None:
        kwargs["timeout_seconds"] = timeout_seconds
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    raw = call_with_model_purpose("sql.scope", client.generate, **kwargs)
    try:
        interpretation = ScopeInterpretation.model_validate_json(raw.strip())
    except ValueError as exc:
        raise QueryScopeViolation("SQL_SCOPE_MISMATCH: 対象範囲の応答形式が不正です。") from exc
    if interpretation.kind == "direct":
        if interpretation.anchor_predicate or interpretation.result_key_columns:
            raise QueryScopeViolation("SQL_SCOPE_MISMATCH: 直接の対象範囲に起点条件があります。")
        return []
    if not interpretation.source_quote or interpretation.source_quote not in question:
        raise QueryScopeViolation("SQL_SCOPE_MISMATCH: 対象範囲の根拠が質問にありません。")
    column, value = parse_anchor(interpretation.anchor_predicate)
    names = [column.sql(dialect=OracleConversion), *interpretation.result_key_columns]
    if not interpretation.result_key_columns or str(value.this) not in question:
        raise QueryScopeViolation("SQL_SCOPE_MISMATCH: 対象範囲のキーまたは起点値が不明です。")
    for name in names:
        key = context.resolve_column(name)
        info = context.column(key) if key else None
        if info is None or info.masked or not info.filterable:
            raise QueryScopeViolation("SQL_SCOPE_MISMATCH: 対象範囲に利用できない列があります。")
    return [
        RelatedRecordScope(
            source_quote=interpretation.source_quote,
            anchor_predicate=interpretation.anchor_predicate,
            result_key_columns=interpretation.result_key_columns,
        )
    ]
