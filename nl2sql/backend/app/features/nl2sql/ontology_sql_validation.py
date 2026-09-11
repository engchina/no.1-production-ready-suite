"""Oracle 式を Profile の表・列と SELECT の名前解決スコープで検証する。"""

from __future__ import annotations

from typing import Any, cast

from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope, traverse_scope


def physical_table(table: exp.Table, physical: dict[str, set[str]]) -> str:
    matches = [
        key
        for key in physical
        if key.split(".")[-1] == table.name.upper()
        and (not table.db or key == f"{table.db}.{table.name}".upper())
    ]
    if table.catalog or len(matches) != 1:
        raise ValueError(f"SQL 表 {table.sql()} を Profile 内で一意に解決できません。")
    return matches[0]


def validated_sql(tree: exp.Expression, physical: dict[str, set[str]]) -> exp.Expression:
    if isinstance(tree, exp.Query):
        schema: dict[str, Any] = {}
        for key, columns in physical.items():
            owner, table = key.split(".", 1)
            schema.setdefault(owner, {})[table] = dict.fromkeys(columns, "UNKNOWN")
        # CTE は物理表ではない。各スコープの実ソースだけを Profile と照合する。
        for scope in traverse_scope(tree):
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    physical_table(source, physical)
        return qualify(
            tree.copy(),
            dialect="oracle",
            schema=schema,
            identify=False,
            # CTE 列リスト等から生成された小文字 alias も再解析時に意味を保持する。
            quote_identifiers=True,
            infer_schema=False,
        )
    for column in tree.find_all(exp.Column):
        physical_column(column, physical)
    if any(tree.find_all(exp.Query)) or any(tree.find_all(exp.Table)):
        raise ValueError("副問い合わせを含む式は SELECT 全体として検証してください。")
    return tree


def expression_in_query(
    expression: exp.Expression, query: exp.Expression, physical: dict[str, set[str]]
) -> exp.Expression:
    """SELECT のソース・別名・CTE で独立したフィルタを解決する。SQL は実行しない。"""
    if not isinstance(query, exp.Select):
        raise ValueError("フィルタの所属 SELECT を一意に指定してください。")
    query = cast(exp.Select, validated_sql(query, physical))
    scope = build_scope(query)
    if scope is None:
        raise ValueError("フィルタの所属 SELECT を解決できません。")

    def bind_physical_reference(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and node.db:
            target = physical_column(node, physical)
            candidates = []
            for alias, (_, source) in scope.selected_sources.items():
                if isinstance(source, exp.Table):
                    columns = [exp.Column(this=node.this.copy())]
                elif isinstance(source, Scope) and isinstance(source.expression, exp.Select):
                    # 公開された投影だけを辿る。未投影列や計算結果を元の列と同一視しない。
                    columns = [
                        exp.column(p.alias_or_name, quoted=True) for p in source.expression.selects
                    ]
                else:
                    continue
                for column in columns:
                    column.set("table", exp.to_identifier(alias, quoted=True))
                    try:
                        resolved = physical_column(column, physical, scope)
                    except ValueError:
                        continue
                    if resolved == target:
                        candidates.append(column)
            if len(candidates) != 1:
                raise ValueError("フィルタの物理参照を SELECT 内で一意に解決できません。")
            return candidates[0]
        return node

    expression = expression.copy().transform(bind_physical_reference)
    name = "NL2SQL_ONTOLOGY_PREDICATE"
    while name in {p.alias_or_name.upper() for p in query.selects}:
        name += "_"
    augmented = query.select(exp.alias_(expression.copy(), name, quoted=True), append=True)
    qualified = cast(exp.Select, validated_sql(augmented, physical))
    return cast(exp.Expression, qualified.selects[-1].unalias())


def physical_column(
    column: exp.Column, physical: dict[str, set[str]], scope: Scope | None = None
) -> str:
    if scope is not None and column.table and not column.db:
        selected = scope.selected_sources.get(column.table)
        if selected is None:
            selected = scope.selected_sources.get(column.table.upper())
        if selected:
            source = selected[1]
            if isinstance(source, Scope):
                if not isinstance(source.expression, exp.Select):
                    raise ValueError("集合演算の粒度には明示的な mapping が必要です。")
                projections = [
                    p
                    for p in source.expression.selects
                    # qualify 済みの識別子は通常名が正規化され、引用名の大小文字は保持される。
                    if p.alias_or_name == column.name
                ]
                if len(projections) != 1:
                    raise ValueError("派生列の物理参照を一意に解決できません。")
                value = projections[0].unalias()
                if not isinstance(value, exp.Column):
                    raise ValueError("派生式の集約粒度には明示的な mapping が必要です。")
                return physical_column(value, physical, source)
            if isinstance(source, exp.Table):
                key = physical_table(source, physical)
                if column.name.upper() in physical[key]:
                    return f"{key}.{column.name.upper()}"
        raise ValueError(f"SQL 列 {column.sql()} の参照元を解決できません。")
    matches = [
        key
        for key, columns in physical.items()
        if column.name.upper() in columns
        and (not column.table or key.split(".")[-1] == column.table.upper())
        and (not column.db or key == f"{column.db}.{column.table}".upper())
    ]
    if column.catalog or len(matches) != 1:
        raise ValueError(f"SQL 列 {column.sql()} を Profile 内で一意に解決できません。")
    return f"{matches[0]}.{column.name.upper()}"


def canonical_expression(
    expression: exp.Expression, physical: dict[str, set[str]], scope: Scope | None = None
) -> str:
    def resolve(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column):
            owner, table, column = physical_column(node, physical, scope).split(".")
            return exp.column(column, table=table, db=owner)
        return node

    return expression.copy().transform(resolve).sql(dialect="oracle")
