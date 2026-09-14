"""型付き Q/A 関係を入力 SQL の物理列へ接地する。SQL は実行しない。"""

from typing import Any, cast

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from .ontology_definition_validation import checked_expression, schema_objects
from .ontology_definitions import BusinessDefinition, ConceptCoverage
from .ontology_models import OntologyBuildExtraction
from .ontology_sql_validation import physical_column, validated_sql


def _query_columns(sql: str, physical: dict[str, set[str]]) -> set[str]:
    """alias・CTE の投影を解決し、物理列の完全名を集める。"""
    statements = sqlglot.parse(sql, read="oracle")
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        return set()
    query = validated_sql(cast(exp.Expression, statements[0]), physical)
    columns: set[str] = set()
    for scope in traverse_scope(query):
        for column in scope.columns:
            try:
                columns.add(physical_column(column, physical, scope))
            except ValueError:
                # 計算された派生列自体は物理列ではない。元列は内側の scope で収集する。
                continue
    return columns


def ground_qa_links(
    extraction: OntologyBuildExtraction,
    qa_sql_texts: list[str] | None,
    schema: dict[str, Any],
) -> OntologyBuildExtraction:
    """同一 Q/A に列の裏付けがない関係だけを除外し、対象付きの理由を保持する。"""
    if qa_sql_texts is None or not any(d.kind == "link_type" for d in extraction.definitions):
        return extraction
    physical = schema_objects(schema)
    queries = []
    for sql in qa_sql_texts:
        try:
            queries.append(_query_columns(sql, physical))
        except (ValueError, sqlglot.errors.SqlglotError):
            # 解釈不能な SQL を根拠として採用しない。他の Q/A の成功分は利用する。
            continue
    kept: list[BusinessDefinition] = []
    warnings = list(extraction.warnings_ja)
    for definition in extraction.definitions:
        if definition.kind != "link_type":
            kept.append(definition)
            continue
        try:
            condition = checked_expression(definition.join_expression_sql)
            if isinstance(condition, exp.Query):
                raise ValueError("単独の JOIN 条件式が必要です。")
            condition = validated_sql(condition, physical)
            columns = {
                physical_column(column, physical) for column in condition.find_all(exp.Column)
            }
            if not columns or not any(columns <= query for query in queries):
                raise ValueError("JOIN 列を同一 Q/A の SQL 内に確認できません。")
        except (ValueError, sqlglot.errors.SqlglotError) as exc:
            warnings.append(
                f"Q/A 関係 {definition.api_name} / join_expression_sql を採用しません: {exc}"
            )
            continue
        kept.append(definition)
    coverage = list(extraction.coverage)
    if not any(d.kind == "link_type" for d in kept):
        coverage = [c for c in coverage if c.kind != "link_type"]
        coverage.append(
            ConceptCoverage(
                kind="link_type",
                status="insufficient_evidence",
                reason_ja="Q/A の SQL で裏付けられた関係がありません。",
            )
        )
    return extraction.model_copy(
        update={"definitions": kept, "warnings_ja": warnings, "coverage": coverage}
    )
