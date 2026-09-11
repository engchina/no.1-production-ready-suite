"""Profile 業務定義の静的検査。Oracle SQL AST と契約を検査し、SQL は実行しない。"""

from __future__ import annotations

from typing import Any, cast

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import build_scope

from .ontology_definition_quality import inspect_definition_quality
from .ontology_definitions import (
    ActionTypeDefinition,
    BusinessDefinition,
    BusinessEventDefinition,
    DefinitionFinding,
    InterfaceDefinition,
    ObjectSetDefinition,
    ObjectTypeDefinition,
    ProfileOntologyBundle,
    PropertyDefinition,
)
from .ontology_sql_validation import canonical_expression, validated_sql


def schema_objects(payload: dict[str, Any]) -> dict[str, set[str]]:
    return {
        f"{obj.get('owner', '')}.{obj.get('object_name', '')}".upper(): {
            str(
                col.get("column_name", col.get("column", col.get("name", "")))
                if isinstance(col, dict)
                else col
            ).upper()
            for col in obj.get("columns", [])
        }
        for obj in payload.get("objects", [])
    }


def checked_expression(sql: str, *, query: bool = False) -> exp.Expression:
    statements = sqlglot.parse(sql, read="oracle")
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("単一の Oracle SQL 式が必要です。")
    tree = statements[0]
    if query and not isinstance(tree, exp.Select):
        raise ValueError("読み取り専用 SELECT が必要です。")
    if any(
        isinstance(node, (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Lock))
        for node in tree.walk()
    ):
        raise ValueError("更新・DDL・ロックは許可されません。")
    # UDF/package/network functions require registered implementations, never AI SQL execution.
    if any(isinstance(node, exp.Anonymous) for node in tree.walk()):
        raise ValueError("未登録の SQL 関数は使用できません。")
    return cast(exp.Expression, tree)


def interface_contracts(
    definitions: dict[str, BusinessDefinition], name: str
) -> list[InterfaceDefinition]:
    pending, seen, result = [name], set(), []
    while pending:
        current = pending.pop()
        definition = definitions.get(current)
        if current in seen or not isinstance(definition, InterfaceDefinition):
            continue
        seen.add(current)
        result.append(definition)
        pending.extend(definition.extends)
    return result


def validate_definitions(
    bundle: ProfileOntologyBundle, schema: dict[str, Any]
) -> list[DefinitionFinding]:
    findings = inspect_definition_quality(bundle.definitions, bundle.sources)
    definitions = {item.api_name: item for item in bundle.definitions}
    physical = schema_objects(schema)
    for item in bundle.definitions:

        def error(code: str, field: str, message: str, definition_id: str = item.id) -> None:
            findings.append(
                DefinitionFinding(
                    code=code,
                    field=field,
                    message_ja=message,
                    severity="error",
                    definition_id=definition_id,
                )
            )

        if sum(d.api_name == item.api_name for d in bundle.definitions) > 1:
            error("DUPLICATE_NAME", "api_name", "概念名が重複しています。")
        if not item.id or sum(d.id == item.id for d in bundle.definitions) > 1:
            error("DUPLICATE_DEFINITION_ID", "id", "概念 ID は空でない一意な値が必要です。")
        refs: list[tuple[str, str, set[str]]] = []
        if isinstance(item, ObjectTypeDefinition):
            refs.extend(
                ("properties", name, {"property"}) for name in {*item.properties, *item.primary_key}
            )
            if item.title_property:
                refs.append(("title_property", item.title_property, {"property"}))
            if not item.mappings:
                error(
                    "OBJECT_MAPPING_REQUIRED",
                    "mappings",
                    "オブジェクトの物理マッピングが必要です。",
                )
            for name in {*item.properties, *item.primary_key}:
                prop = definitions.get(name)
                if isinstance(prop, PropertyDefinition) and prop.object_type != item.api_name:
                    error(
                        "PROPERTY_OWNER_MISMATCH",
                        "properties",
                        f"{name} は別オブジェクトのプロパティです。",
                    )
            for implementation in item.implements:
                refs.append(("implements", implementation.interface, {"interface"}))
                interface = definitions.get(implementation.interface)
                if interface is None or interface.kind != "interface":
                    continue
                bindings = {
                    binding.interface_property: binding.property
                    for binding in implementation.property_mapping
                }
                contracts = interface_contracts(definitions, interface.api_name)
                for requirement in [p for contract in contracts for p in contract.properties]:
                    prop = definitions.get(bindings.get(requirement.api_name, ""))
                    if (
                        not isinstance(prop, PropertyDefinition)
                        or prop.object_type != item.api_name
                        or prop.data_type != requirement.data_type
                        or (requirement.required and not prop.required)
                    ):
                        error(
                            "INTERFACE_PROPERTY_MISMATCH",
                            "implements",
                            f"{interface.api_name}.{requirement.api_name} "
                            "の実装契約を満たしません。",
                        )
                for name in {name for contract in contracts for name in contract.required_links}:
                    link = definitions.get(name)
                    if (
                        link is None
                        or link.kind != "link_type"
                        or item.api_name not in {link.source, link.target}
                    ):
                        error(
                            "INTERFACE_LINK_MISMATCH",
                            "implements",
                            f"必須リンク {name} が実装されていません。",
                        )
                for name in {name for contract in contracts for name in contract.required_actions}:
                    action = definitions.get(name)
                    if (
                        action is None
                        or action.kind != "action_type"
                        or action.object_type != item.api_name
                    ):
                        error(
                            "INTERFACE_ACTION_MISMATCH",
                            "implements",
                            f"必須アクション {name} が実装されていません。",
                        )
        elif isinstance(item, PropertyDefinition):
            if item.kind == "property":
                refs.append(("object_type", item.object_type, {"object_type"}))
                if not item.mappings:
                    error(
                        "PROPERTY_MAPPING_REQUIRED",
                        "mappings",
                        "プロパティの物理マッピングが必要です。",
                    )
            if item.shared_property:
                refs.append(("shared_property", item.shared_property, {"shared_property"}))
            if item.value_type:
                refs.append(("value_type", item.value_type, {"value_type"}))
        elif item.kind == "link_type":
            refs.extend(
                (field, getattr(item, field), {"object_type", "interface"})
                for field in ("source", "target")
            )
        elif item.kind == "interface":
            refs.extend(("extends", name, {"interface"}) for name in item.extends)
            refs.extend(("required_links", name, {"link_type"}) for name in item.required_links)
            refs.extend(
                ("required_actions", name, {"action_type"}) for name in item.required_actions
            )
            pending, visited = list(item.extends), {item.api_name}
            while pending:
                name = pending.pop()
                if name == item.api_name:
                    error("INTERFACE_CYCLE", "extends", "インターフェース継承が循環しています。")
                    break
                parent = definitions.get(name)
                if name not in visited and parent is not None and parent.kind == "interface":
                    visited.add(name)
                    pending.extend(parent.extends)
        elif isinstance(item, (ActionTypeDefinition, BusinessEventDefinition, ObjectSetDefinition)):
            refs.append(("object_type", str(item.object_type), {"object_type"}))
        elif item.kind == "enumeration":
            refs.append(("property", item.property, {"property", "shared_property"}))
        for field in (
            "dependencies",
            "applies_to",
            "grain",
            "distinct_keys",
            "affected_properties",
        ):
            refs.extend((field, name, set()) for name in getattr(item, field, []))
        for field, name, kinds in refs:
            target = definitions.get(name)
            if target is None or (kinds and target.kind not in kinds):
                error("REFERENCE_INVALID", field, f"概念参照 {name or '未設定'} を解決できません。")
        for field in ("time_property", "timestamp_property"):
            name = getattr(item, field, "")
            if name and (name not in definitions or definitions[name].kind != "property"):
                error("REFERENCE_INVALID", field, f"時刻プロパティ {name} を解決できません。")
        for mapping in item.mappings:
            key = f"{mapping.owner}.{mapping.object_name}".upper()
            if key not in physical or (
                mapping.column_name and mapping.column_name.upper() not in physical[key]
            ):
                error(
                    "MAPPING_OUTSIDE_PROFILE",
                    "mappings",
                    f"{key}.{mapping.column_name} は現在の Profile 範囲にありません。",
                )
        expressions = [
            (field, getattr(item, field, ""), physical)
            for field in ("expression_sql", "filter_sql", "predicate_sql", "join_expression_sql")
        ]
        expressions.extend(
            (
                f"mappings.{index}.expression_sql",
                mapping.expression_sql,
                {
                    f"{mapping.owner}.{mapping.object_name}".upper(): physical.get(
                        f"{mapping.owner}.{mapping.object_name}".upper(), set()
                    )
                },
            )
            for index, mapping in enumerate(item.mappings)
            if mapping.expression_sql
        )
        for field, sql, expression_scope in expressions:
            if not sql:
                continue
            try:
                parsed = checked_expression(sql)
                tree = validated_sql(
                    parsed, physical if isinstance(parsed, exp.Query) else expression_scope
                )
                if item.kind == "metric" and field == "expression_sql":
                    aggregates = list(tree.find_all(exp.AggFunc))
                    if item.aggregation and not any(
                        node.key.lower() == item.aggregation.lower() for node in aggregates
                    ):
                        error(
                            "METRIC_AGGREGATION_MISMATCH",
                            field,
                            "宣言した集約関数と SQL が一致しません。",
                        )
                    scope = build_scope(tree) if isinstance(tree, exp.Query) else None
                    if item.filter_sql:
                        predicate = checked_expression(item.filter_sql)
                        try:
                            expected_filter = canonical_expression(predicate, physical, scope)
                        except ValueError:
                            expected_filter = ""
                        # SELECT に表示するだけ、または OR の片側だけでは必須フィルタにならない。
                        clauses = [tree.args.get(name) for name in ("where", "having")]
                        pending_conditions: list[exp.Expression] = [
                            clause.this for clause in clauses if clause is not None
                        ]
                        guaranteed = set()
                        while pending_conditions:
                            condition = cast(exp.Expression, pending_conditions.pop().unnest())
                            guaranteed.add(canonical_expression(condition, physical, scope))
                            if isinstance(condition, exp.And):
                                pending_conditions.extend([condition.this, condition.expression])
                        if expected_filter not in guaranteed:
                            error(
                                "METRIC_FILTER_MISMATCH",
                                "filter_sql",
                                "業務フィルタが指標 SQL の必須条件に含まれていません。",
                            )

                    def declared_keys(names: list[str]) -> set[str]:
                        result = set()
                        for name in names:
                            prop = definitions.get(name)
                            if not isinstance(prop, PropertyDefinition) or len(prop.mappings) != 1:
                                raise ValueError(
                                    f"{name} の集約キー mapping が一意ではありません。"
                                )
                            mapping = prop.mappings[0]
                            value = (
                                checked_expression(mapping.expression_sql)
                                if mapping.expression_sql
                                else exp.column(
                                    mapping.column_name, table=mapping.object_name, db=mapping.owner
                                )
                            )
                            result.add(canonical_expression(value, physical))
                        return result

                    if item.distinct_keys:
                        expected = declared_keys(item.distinct_keys)
                        distincts = [
                            node.this
                            for node in aggregates
                            if isinstance(node.this, exp.Distinct)
                            and (
                                not item.aggregation or node.key.lower() == item.aggregation.lower()
                            )
                        ]
                        if not distincts or any(
                            {canonical_expression(e, physical, scope) for e in distinct.expressions}
                            != expected
                            for distinct in distincts
                        ):
                            error(
                                "METRIC_DISTINCT_MISMATCH",
                                "distinct_keys",
                                "SQL の重複排除列が宣言したキーと一致しません。",
                            )
                    if item.grain and isinstance(tree, exp.Query):
                        group = tree.args.get("group")
                        actual = (
                            {canonical_expression(e, physical, scope) for e in group.expressions}
                            if group
                            else set()
                        )
                        if actual != declared_keys(item.grain):
                            error(
                                "METRIC_GRAIN_MISMATCH",
                                "grain",
                                "SQL の GROUP BY が宣言した集約粒度と一致しません。",
                            )
                    if any(True for _ in tree.find_all(exp.Join)) and aggregates:
                        error(
                            "JOIN_GRAIN_REVIEW_REQUIRED",
                            "expression_sql",
                            "結合後集計の重複を防ぐ事前集計または粒度検証が必要です。",
                        )
            except (ValueError, sqlglot.errors.SqlglotError) as exc:
                error(
                    "SQL_EXPRESSION_INVALID",
                    field,
                    f"Oracle SQL 式を検証できません: {str(exc)[:160]}",
                )
    return findings
