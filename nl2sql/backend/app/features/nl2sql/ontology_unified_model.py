"""13分類を正本とする候補統合・可読 Markdown・接地 graph の決定論的投影。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import TypeAdapter

from .ontology_catalog import SchemaOntology
from .ontology_definitions import BusinessDefinition, MetricDefinitionV2
from .ontology_models import (
    BusinessRuleDefinition,
    BusinessRuleExpression,
    JoinCondition,
    MetricAggregation,
    MetricDefinition,
    OntologyEdge,
    OntologyNode,
    OntologyProvenance,
    PhysicalMapping,
    PhysicalObjectRef,
)
from .ontology_store import canonical_json, stable_ontology_id

CONCEPT_LABELS = {
    "object_type": "オブジェクト型（Object Type）",
    "property": "プロパティ（Property）",
    "link_type": "リンク型（Link Type）",
    "interface": "インターフェース（Interface）",
    "function": "関数（Function）",
    "action_type": "アクション型（Action Type）",
    "shared_property": "共有プロパティ（Shared Property）",
    "value_type": "値型（Value Type）",
    "enumeration": "列挙型（Enumeration）",
    "metric": "指標（Metric）",
    "business_rule": "業務ルール（Business Rule）",
    "business_event": "業務イベント（Business Event）",
    "object_set": "オブジェクト集合（Object Set）",
}
CONCEPT_ORDER = tuple(CONCEPT_LABELS)
DEFINITIONS = TypeAdapter(list[BusinessDefinition])
PHYSICAL_KINDS = {"schema", "table", "view", "column"}
FIELD_LABELS = {
    "description_ja": "説明",
    "aliases": "別名",
    "mappings": "物理マッピング",
    "primary_key": "主識別子",
    "properties": "プロパティ",
    "implements": "実装するインターフェース",
    "object_type": "対象オブジェクト",
    "data_type": "データ型",
    "required": "必須",
    "source": "関係の起点",
    "target": "関係の終点",
    "cardinality": "多重度",
    "join_expression_sql": "結合条件",
    "expression_sql": "定義 SQL",
    "filter_sql": "絞り込み条件",
    "aggregation": "集計方法",
    "grain": "集計粒度",
    "distinct_keys": "重複排除キー",
    "dependencies": "依存する定義",
    "parameters": "パラメーター",
    "return_type": "戻り値",
    "applies_to": "適用対象",
    "predicate_sql": "業務条件",
    "values": "列挙値",
    "property": "対象プロパティ",
    "shared_property": "共有プロパティ",
    "value_type": "値型",
    "extends": "継承元",
    "required_links": "必須の関係",
    "required_actions": "必須の操作",
    "preconditions_ja": "前提条件",
    "affected_properties": "影響するプロパティ",
    "evidence": "根拠",
    "missing_information_ja": "要確認事項",
    "unit": "単位",
    "currency": "通貨",
    "null_policy_ja": "NULL の扱い",
    "time_policy_ja": "時間の扱い",
    "time_property": "時間プロパティ",
    "timestamp_property": "発生日時プロパティ",
}


def api_name(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.]", "_", value)
    if result != value or len(result) > 128:
        result = result[:100] + "_" + hashlib.sha256(value.encode()).hexdigest()[:16]
    return (result if result and result[0].isalpha() else "concept_" + result)[:128]


def metric_definition_projection(
    definition: MetricDefinitionV2,
    concept_ids: dict[str, str],
    physical: dict[str, OntologyNode],
) -> MetricDefinition:
    """13分類の指標を引導式 SQL の既存読み取り契約へ可逆な意味で投影する。"""
    import sqlglot
    from sqlglot import exp

    columns = set()
    try:
        tree = sqlglot.parse_one(definition.expression_sql, read="oracle")
        for col in tree.find_all(exp.Column):
            node = physical.get(".".join(part.name for part in col.parts).upper())
            if node and node.kind.value == "column":
                columns.add(node.id)
    except sqlglot.errors.SqlglotError:
        pass  # 検証は公開前ゲート。ここでは原式を保持する。
    return MetricDefinition(
        id=stable_ontology_id("metric_definition", definition.id),
        metric_node_id=definition.id,
        expression_sql=definition.expression_sql,
        aggregation=(
            definition.aggregation.lower()
            if definition.aggregation.lower() in MetricAggregation._value2member_map_
            else "none"
        ),
        base_column_node_ids=sorted(columns),
        grain_node_ids=[concept_ids[name] for name in definition.grain if name in concept_ids],
        distinct_key_node_ids=[
            concept_ids[name] for name in definition.distinct_keys if name in concept_ids
        ],
        unit=definition.unit,
        currency=definition.currency,
        additivity=definition.additivity,
        null_policy_ja=definition.null_policy_ja,
        description_ja=definition.description_ja,
    )


def _legacy_rule_sql(expression: BusinessRuleExpression, nodes: dict[str, OntologyNode]) -> Any:
    """固定ルール AST を物理列の SQL 条件へ変換する。解決不能は呼出元に返す。"""
    from sqlglot import exp

    op = expression.operator
    if op in {"all", "any", "not"}:
        children = [_legacy_rule_sql(child, nodes) for child in expression.children]
        if op == "not":
            return exp.not_(children[0])
        return exp.and_(*children) if op == "all" else exp.or_(*children)
    node = nodes.get(expression.property_node_id)
    if node is None or len(node.physical_mappings) != 1:
        raise ValueError("ルールの参照プロパティを一意に解決できません。")
    mapping = node.physical_mappings[0]
    if mapping.expression_sql or len(mapping.column_refs) != 1:
        raise ValueError("ルールの物理列を一意に解決できません。")
    col = mapping.column_refs[0]
    left = exp.column(col.column_name, table=col.object_name, db=col.owner or None, quoted=True)
    if op in {"is_null", "not_null"}:
        condition = exp.Is(this=left, expression=exp.Null())
        return exp.not_(condition) if op == "not_null" else condition
    if op in {"in", "not_in"}:
        membership = exp.In(this=left, expressions=[exp.convert(v) for v in expression.values])
        return exp.not_(membership) if op == "not_in" else membership
    comparison = {
        "eq": exp.EQ,
        "ne": exp.NEQ,
        "lt": exp.LT,
        "lte": exp.LTE,
        "gt": exp.GT,
        "gte": exp.GTE,
    }[op]
    return comparison(this=left, expression=exp.convert(expression.value))


def legacy_definitions(graph: SchemaOntology) -> list[BusinessDefinition]:
    """旧 graph の明示的な identity を保った変換。物理名だけで業務概念を併合しない。"""
    names = {
        n.id: (
            str(n.metadata["definition"]["api_name"])
            if isinstance(n.metadata.get("definition"), dict)
            else api_name(n.technical_name or n.id)
        )
        for n in graph.nodes
    }
    business_nodes = [n for n in graph.nodes if n.kind.value not in PHYSICAL_KINDS]
    original_names = dict(names)
    for node in business_nodes:
        # 旧 technical_name が同じ表名でも、異なる stable ID は別概念である。
        if not isinstance(node.metadata.get("definition"), dict) and any(
            other.id != node.id and original_names[other.id] == original_names[node.id]
            for other in business_nodes
        ):
            names[node.id] = api_name(
                f"{node.technical_name or 'concept'}_"
                f"{hashlib.sha256(node.id.encode()).hexdigest()[:12]}"
            )
    legacy_kinds = {
        "business_entity": "object_type",
        "business_term": "property",
        "enum_value": "enumeration",
    }
    result: list[dict[str, Any]] = []
    for node in graph.nodes:
        if node.kind.value in PHYSICAL_KINDS:
            continue
        if isinstance(node.metadata.get("definition"), dict):
            result.append(node.metadata["definition"])
            continue
        kind = legacy_kinds.get(node.kind.value, node.kind.value)
        if kind not in CONCEPT_LABELS or kind == "link_type":
            continue
        mappings = [
            {
                "owner": m.object_ref.owner,
                "object_name": m.object_ref.object_name,
                "column_name": c.column_name if c else "",
                "expression_sql": m.expression_sql,
            }
            for m in node.physical_mappings
            for c in (m.column_refs if m.column_refs else [None])
        ]
        value: dict[str, Any] = dict(
            kind=kind,
            id=node.id,
            api_name=names[node.id],
            name_ja=node.business_name_ja,
            description_ja=node.description_ja,
            aliases=node.aliases,
            mappings=mappings,
            origin="legacy",
            evidence=[
                dict(
                    source_id=e.source_document_id,
                    locator=e.locator,
                    excerpt_ja=e.excerpt_ja,
                    source_sha256=e.source_sha256,
                    source_kind="inference",
                )
                for e in node.provenance.evidence
            ],
        )
        if kind == "metric":
            metric = node.metadata.get("metric_definition", {})
            for field in ("expression_sql", "aggregation", "unit", "filter_sql", "null_policy_ja"):
                if metric.get(field):
                    value[field] = metric[field]
        if kind == "business_rule" and node.business_rule_definition:
            rule = node.business_rule_definition
            value["applies_to"] = [names[i] for i in rule.applies_to_node_ids if i in names]
            # statement と元の固定 AST は変換可否に関わらず残す。LLM に渡す
            # Markdown 自体に元の条件を含め、解釈不能な条件を消さない。
            value["description_ja"] = "\n".join(
                dict.fromkeys(
                    filter(
                        None,
                        [
                            node.description_ja,
                            rule.statement_ja,
                            "旧ルール定義: " + canonical_json(rule.model_dump(mode="json")),
                        ],
                    )
                )
            )
            value["severity"] = "warning" if rule.severity.value in {"info", "warning"} else "error"
            if rule.expression:
                try:
                    value["predicate_sql"] = _legacy_rule_sql(
                        rule.expression, {n.id: n for n in graph.nodes}
                    ).sql(dialect="oracle")
                except (ValueError, TypeError, KeyError):
                    value["missing_information_ja"] = [
                        "旧ルールの条件を SQL に変換できません。保持した元定義を確認してください。"
                    ]
        if kind in {"business_event", "action_type", "object_set"}:
            targets = [
                e.target_node_id
                for e in graph.edges
                if e.source_node_id == node.id and e.target_node_id in names
            ]
            value["object_type"] = names[targets[0]] if targets else ""
        if kind == "enumeration" and node.enum_value_definition:
            enum = node.enum_value_definition
            value["property"] = names.get(enum.property_node_id, "")
            value["values"] = [{"code": enum.code, "label_ja": enum.label_ja}]
        result.append(value)
    business_ids = {v["id"] for v in result}
    for edge in graph.edges:
        if isinstance(edge.metadata.get("definition"), dict):
            result.append(edge.metadata["definition"])
        elif (
            edge.kind.value == "business_relationship"
            and {edge.source_node_id, edge.target_node_id} <= business_ids
        ):
            joins = [
                f"{c.left.owner}.{c.left.object_name}.{c.left.column_name} {c.operator} "
                f"{c.right.owner}.{c.right.object_name}.{c.right.column_name}"
                for c in edge.join_conditions
            ]
            result.append(
                dict(
                    kind="link_type",
                    id=edge.id,
                    api_name=api_name(edge.id),
                    name_ja=edge.relationship_name_ja,
                    description_ja=edge.description_ja,
                    source=names[edge.source_node_id],
                    target=names[edge.target_node_id],
                    cardinality=edge.cardinality.value,
                    join_expression_sql=" AND ".join(joins),
                    origin="legacy",
                )
            )
    return DEFINITIONS.validate_python(result)


def merge_definitions(
    profile_id: str, definitions: list[BusinessDefinition]
) -> tuple[list[BusinessDefinition], list[str]]:
    merged: dict[tuple[str, str], BusinessDefinition] = {}
    conflicts: list[str] = []
    remap: dict[str, str] = {}
    for original in definitions:
        item = original.model_copy(deep=True)
        identity = (item.kind, item.api_name)
        # 安定 ID の明示的一致も同一性の根拠にする。名前や同じ表だけでは併合しない。
        prior = merged.get(identity) or next(
            (d for d in merged.values() if item.id and d.id == item.id and d.kind == item.kind),
            None,
        )
        if prior is None:
            item.id = item.id or stable_ontology_id(
                "profile_concept", profile_id, item.kind, item.api_name
            )
            merged[identity] = item
            continue
        remap[item.api_name] = prior.api_name
        for field, value in item.model_dump(mode="json").items():
            if field in {"id", "api_name", "kind", "origin", "review_status", "implementation_key"}:
                continue
            current = getattr(prior, field)
            if field in {"aliases", "evidence", "missing_information_ja", "mappings"}:
                if field == "mappings":
                    for left in current:
                        for right in item.mappings:
                            if (
                                (left.owner, left.object_name, left.column_name)
                                == (right.owner, right.object_name, right.column_name)
                                and left.expression_sql
                                and right.expression_sql
                                and left.expression_sql != right.expression_sql
                            ):
                                conflicts.append(
                                    f"{prior.api_name} / 物理マッピング: "
                                    f"{left.expression_sql} ↔ {right.expression_sql}"
                                )
                unique = {
                    canonical_json(v.model_dump(mode="json") if hasattr(v, "model_dump") else v): v
                    for v in [*current, *getattr(item, field)]
                }
                setattr(prior, field, list(unique.values()))
            elif field == "description_ja" and value and value != current:
                prior.description_ja = "\n".join(dict.fromkeys([current, str(value)]))
            elif field == "name_ja" and value and value != current:
                prior.aliases = list(dict.fromkeys([*prior.aliases, str(value)]))
            elif (
                isinstance(value, bool)
                and current != value
                and field in item.model_fields_set
                and field in prior.model_fields_set
            ):
                conflicts.append(
                    f"{prior.api_name} / {FIELD_LABELS.get(field, field)}: {current} ↔ {value}"
                )
            elif not current and value:
                setattr(prior, field, getattr(item, field))
            elif value and current != getattr(item, field):
                # 同一性が一致しても意味の違いは隠さない。
                conflicts.append(
                    f"{prior.api_name} / {FIELD_LABELS.get(field, field)}: "
                    f"{canonical_json(prior.model_dump(mode='json')[field])} ↔ "
                    f"{canonical_json(value)}"
                )
    values = list(merged.values())
    if remap:

        rewritten = []
        for d in values:
            value = d.model_dump(mode="json")
            fields = {field for field, _ in definition_references(d)} | {"source", "target"}
            for field in fields - {"implements", "affected_properties"}:
                field_value = value.get(field)
                if isinstance(field_value, str):
                    value[field] = remap.get(field_value, field_value)
                elif isinstance(field_value, list):
                    value[field] = [
                        remap.get(v, v) if isinstance(v, str) else v for v in field_value
                    ]
            for impl in value.get("implements", []):
                impl["interface"] = remap.get(impl["interface"], impl["interface"])
                for binding in impl["property_mapping"]:
                    binding["property"] = remap.get(binding["property"], binding["property"])
            for assignment in value.get("assignments", []):
                assignment["property"] = remap.get(assignment["property"], assignment["property"])
            if "affected_properties" in value:
                value["affected_properties"] = [
                    remap.get(v, v) for v in value["affected_properties"]
                ]
            rewritten.append(value)
        values = DEFINITIONS.validate_python(rewritten)
    return sorted(values, key=lambda d: (CONCEPT_ORDER.index(d.kind), d.api_name)), sorted(
        set(conflicts)
    )


def _human(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("owner") and value.get("object_name"):
            physical = ".".join(
                str(value[k]) for k in ("owner", "object_name", "column_name") if value.get(k)
            )
            return physical + (
                f"（式: {value['expression_sql']}）" if value.get("expression_sql") else ""
            )
        return "、".join(
            f"{FIELD_LABELS.get(k, k)}: {_human(v)}"
            for k, v in value.items()
            if v not in ("", [], None)
        )
    if isinstance(value, list):
        return " / ".join(_human(v) for v in value)
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    return str(value).replace("\n", " / ")


def render_concepts(
    definitions: list[BusinessDefinition],
    conflicts: list[str] | None = None,
    coverage: list[Any] | None = None,
) -> str:
    lines = [
        "## オントロジーの概念定義",
        "この章の業務定義・関係・条件を SQL 生成と接地確認に使用します。",
    ]
    reasons = {c.kind: c.reason_ja for c in coverage or []}
    for i, (kind, label) in enumerate(CONCEPT_LABELS.items()):
        if i in (0, 6):
            lines.append("### 主要概念（6種類）" if i == 0 else "### 補助概念（7種類）")
        lines.append(f"#### {label}")
        matches = [d for d in definitions if d.kind == kind]
        if not matches:
            lines.append(
                "- " + (reasons.get(kind) or "根拠となる定義がありません。資料を確認してください。")
            )
        for d in matches:
            lines.extend([f"##### {d.name_ja} (`{d.api_name}`)", f"- 概念 ID: `{d.id}`"])
            for field, value in d.model_dump(mode="json").items():
                if field in {
                    "id",
                    "api_name",
                    "name_ja",
                    "kind",
                    "review_status",
                    "origin",
                    "implementation_key",
                } or value in ("", [], None):
                    continue
                lines.append(f"- {FIELD_LABELS.get(field, field)}: {_human(value)}")
    if conflicts:
        lines.extend(["### 解決が必要な定義の競合", *[f"- {c}" for c in conflicts]])
    return "\n\n".join(lines)


def definition_references(d: BusinessDefinition) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for field in (
        "object_type",
        "property",
        "shared_property",
        "value_type",
        "time_property",
        "timestamp_property",
        "title_property",
        "intermediary_object",
    ):
        if getattr(d, field, ""):
            refs.append((field, getattr(d, field)))
    for field in (
        "dependencies",
        "applies_to",
        "extends",
        "required_links",
        "required_actions",
        "grain",
        "distinct_keys",
        "affected_properties",
        "primary_key",
    ):
        refs.extend((field, value) for value in getattr(d, field, []))
    refs.extend(
        ("properties", value) for value in getattr(d, "properties", []) if isinstance(value, str)
    )
    refs.extend(("implements", value.interface) for value in getattr(d, "implements", []))
    refs.extend(
        ("properties", p.property) for i in getattr(d, "implements", []) for p in i.property_mapping
    )
    refs.extend(("affected_properties", a.property) for a in getattr(d, "assignments", []))
    return list(dict.fromkeys(refs))


def _join_conditions(sql: str, physical: dict[str, OntologyNode]) -> list[JoinCondition]:
    """単純な物理列比較を SQL 接地の既存 join 契約へ投影する。原式は定義に保持。"""
    import sqlglot
    from sqlglot import exp

    if not sql:
        return []
    try:
        expression = sqlglot.parse_one(sql, read="oracle")
    except sqlglot.errors.SqlglotError:
        return []
    comparisons: dict[type[Any], Any] = {
        exp.EQ: "=",
        exp.NEQ: "!=",
        exp.LT: "<",
        exp.LTE: "<=",
        exp.GT: ">",
        exp.GTE: ">=",
    }
    result: list[JoinCondition] = []
    for part in expression.flatten() if isinstance(expression, exp.And) else [expression]:
        if (
            type(part) not in comparisons
            or not isinstance(part.this, exp.Column)
            or not isinstance(part.expression, exp.Column)
        ):
            continue
        pair = [
            physical.get(".".join(p.name for p in col.parts).upper())
            for col in (part.this, part.expression)
        ]
        left, right = pair
        if (
            left is not None
            and right is not None
            and left.kind.value == "column"
            and right.kind.value == "column"
        ):
            result.append(
                JoinCondition(
                    left=left.physical_mappings[0].column_refs[0],
                    right=right.physical_mappings[0].column_refs[0],
                    operator=comparisons[type(part)],
                    ordinal=len(result) + 1,
                )
            )
    return result


def project_graph(
    base: SchemaOntology, definitions: list[BusinessDefinition], revision_id: str | None = None
) -> SchemaOntology:
    rid = revision_id or base.revision.id
    nodes = [
        n.model_copy(update={"revision_id": rid}, deep=True)
        for n in base.nodes
        if n.kind.value in PHYSICAL_KINDS
    ]
    physical = {n.technical_name.upper(): n for n in nodes}
    by_name = {d.api_name: d for d in definitions}
    edges = [
        e.model_copy(update={"revision_id": rid}, deep=True)
        for e in base.edges
        if e.source_node_id in {n.id for n in nodes} and e.target_node_id in {n.id for n in nodes}
    ]
    provenance = OntologyProvenance(
        source_kind="manual", source_detail="確認済み Markdown の派生定義"
    )
    old_nodes = {n.id: n for n in base.nodes}
    old_edges = {e.id: e for e in base.edges}
    for d in definitions:
        if d.kind == "link_type":
            if d.source not in by_name or d.target not in by_name:
                continue
            edges.append(
                OntologyEdge(
                    id=d.id,
                    revision_id=rid,
                    kind="link_type",
                    source_node_id=by_name[d.source].id,
                    target_node_id=by_name[d.target].id,
                    relationship_name_ja=d.name_ja,
                    description_ja=d.description_ja,
                    cardinality=d.cardinality,
                    join_conditions=_join_conditions(d.join_expression_sql, physical),
                    provenance=old_edges[d.id].provenance if d.id in old_edges else provenance,
                    review_status="approved",
                    metadata={"definition": d.model_dump(mode="json"), "api_name": d.api_name},
                )
            )
            continue
        mappings = []
        for m in d.mappings:
            obj = physical.get(f"{m.owner}.{m.object_name}".upper())
            col = physical.get(f"{m.owner}.{m.object_name}.{m.column_name}".upper())
            mappings.append(
                PhysicalMapping(
                    object_ref=PhysicalObjectRef(
                        owner=m.owner,
                        object_name=m.object_name,
                        node_id=obj.id if obj else "",
                        object_type="view" if obj and obj.kind.value == "view" else "table",
                    ),
                    column_refs=(
                        [col.physical_mappings[0].column_refs[0].model_copy(deep=True)]
                        if col
                        else []
                    ),
                    expression_sql=m.expression_sql,
                )
            )
            target = col or obj
            if target:
                edges.append(
                    OntologyEdge(
                        id=stable_ontology_id("mapping", d.id, target.id),
                        revision_id=rid,
                        kind="maps_to",
                        source_node_id=d.id,
                        target_node_id=target.id,
                        relationship_name_ja="物理マッピング",
                        provenance=provenance,
                        review_status="approved",
                    )
                )
        kwargs: dict[str, Any] = {}
        if d.kind == "business_rule":
            targets = [by_name[r].id for r in d.applies_to if r in by_name]
            kwargs["business_rule_definition"] = BusinessRuleDefinition(
                rule_kind="constraint",
                statement_ja=d.description_ja or d.name_ja,
                applies_to_node_ids=targets or [d.id],
            )
        nodes.append(
            OntologyNode(
                id=d.id,
                revision_id=rid,
                kind=d.kind,
                technical_name=d.api_name,
                business_name_ja=d.name_ja,
                description_ja=d.description_ja,
                aliases=d.aliases,
                physical_mappings=mappings,
                provenance=old_nodes[d.id].provenance if d.id in old_nodes else provenance,
                review_status="approved",
                metadata={
                    "definition": d.model_dump(mode="json"),
                    "concept_kind": d.kind,
                    **(
                        {
                            "metric_definition": metric_definition_projection(
                                d, {name: item.id for name, item in by_name.items()}, physical
                            ).model_dump(mode="json")
                        }
                        if d.kind == "metric" and d.expression_sql
                        else {}
                    ),
                },
                **kwargs,
            )
        )
    for d in definitions:
        if d.kind == "link_type":
            continue
        for field, ref in definition_references(d):
            referenced = by_name.get(ref)
            if not referenced:
                continue
            # Link Type 自体は辺。参照はその両端へ投影し、元の参照名も残す。
            targets = (
                [by_name[n].id for n in (referenced.source, referenced.target) if n in by_name]
                if referenced.kind == "link_type"
                else [referenced.id]
            )
            for target_id in targets:
                edges.append(
                    OntologyEdge(
                        id=stable_ontology_id("concept_ref", d.id, field, ref, target_id),
                        revision_id=rid,
                        kind="is_a" if field in {"implements", "extends"} else "uses",
                        source_node_id=d.id,
                        target_node_id=target_id,
                        relationship_name_ja=FIELD_LABELS.get(field, field),
                        provenance=provenance,
                        review_status="approved",
                        metadata={"reference_field": field, "reference_api_name": ref},
                    )
                )
    return SchemaOntology(
        revision=base.revision.model_copy(update={"id": rid}),
        nodes=nodes,
        edges=list({e.id: e for e in edges}.values()),
    )
