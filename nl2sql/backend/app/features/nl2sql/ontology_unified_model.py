"""13分類を正本とする候補統合・可読 Markdown・接地 graph の決定論的投影。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

from pydantic import TypeAdapter

from .object_identity import catalog_match_key, object_match_key, object_part_name
from .ontology_catalog import SchemaOntology
from .ontology_definition_validation import mapping_column_key, mapping_object_key
from .ontology_definitions import (
    BusinessDefinition,
    DefinitionEvidence,
    DefinitionMapping,
    MetricDefinitionV2,
    ObjectTypeDefinition,
    PropertyDefinition,
)
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
from .ontology_sql_validation import identifier_token
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
    "grain_ja": "業務上の粒度",
    "lifecycle_ja": "ライフサイクル",
    "title_property": "表示名の項目",
    "additivity": "加算特性",
    "constraints_ja": "制約",
    "interface": "インターフェース",
    "interface_property": "インターフェースの項目",
    "property_mapping": "項目の対応",
    "assignments": "更新する項目",
    "parameter": "入力項目",
    "label_ja": "表示名",
    "code": "値",
    "aliases": "別名",
    "mappings": "対応する表・列",
    "api_name": "識別名",
    "name_ja": "名称",
    "writable": "更新可能",
    "timezone": "タイムゾーン",
    "allowed_values": "選択可能な値",
    "inverse_name_ja": "逆方向の関係名",
    "optional": "任意の関係",
    "intermediary_object": "中間オブジェクト",
    "valid_time_ja": "有効期間",
    "error_policy_ja": "エラー時の扱い",
    "permission_requirement_ja": "必要な権限",
    "failure_policy_ja": "失敗時の扱い",
    "severity": "違反時の扱い",
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


def physical_node_index(nodes: Iterable[OntologyNode]) -> dict[str, OntologyNode]:
    """物理 node（schema / 表 / ビュー / 列）を `object_identity` の照合キーで引く索引。

    キーはカタログ上の owner / object / column 名から作る（`SALES."Mixed_Case".AMOUNT`）。
    technical_name を大文字化したキーでは `SALES."Mixed_Case"` と大文字の同名表
    `SALES.MIXED_CASE` を区別できない（#573）。引用が不要な名前のキーは従来と同じ値になる。
    """

    index: dict[str, OntologyNode] = {}
    for node in nodes:
        if node.kind.value not in PHYSICAL_KINDS:
            continue
        metadata = node.metadata
        parts = [
            str(metadata.get(key) or "")
            for key in ("owner", "object_name", "column_name")
            if str(metadata.get(key) or "")
        ]
        key = catalog_match_key(*parts) if parts else object_match_key(node.technical_name)
        index.setdefault(key, node)
    return index


def sql_column_key(column: Any) -> str:
    """SQL の列参照（`OWNER.OBJECT.COLUMN`、各部は引用可）を照合キーにする。"""

    return ".".join(identifier_token(part) for part in column.parts)


def metric_definition_projection(
    definition: MetricDefinitionV2,
    concept_ids: dict[str, str],
    physical: dict[str, OntologyNode],
) -> MetricDefinition:
    """13分類の指標を引導式 SQL の既存読み取り契約へ可逆な意味で投影する。"""
    import sqlglot
    from sqlglot import exp

    columns = set()
    for sql in (definition.expression_sql, definition.filter_sql):
        if not sql.strip():
            continue
        try:
            tree = sqlglot.parse_one(sql, read="oracle")
            for col in tree.find_all(exp.Column):
                node = physical.get(sql_column_key(col))
                if node and node.kind.value == "column":
                    columns.add(node.id)
        except sqlglot.errors.SqlglotError:
            pass  # 検証は公開前ゲート。ここでは原式・条件を保持する。
    return MetricDefinition(
        id=stable_ontology_id("metric_definition", definition.id),
        metric_node_id=definition.id,
        expression_sql=definition.expression_sql,
        filter_sql=definition.filter_sql,
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
        if node.kind.value == "enum_value" and node.metadata.get("derived_from_definition_id"):
            continue  # 新形式の列挙メンバーは親 Enumeration の values に保存されている。
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


def concept_name_key(name: str) -> str:
    return name.replace("_", "").casefold()


def resolve_concept_name(name: str, definitions: list[BusinessDefinition]) -> str:
    if any(d.api_name == name for d in definitions):
        return name
    matches = {
        d.api_name for d in definitions if concept_name_key(d.api_name) == concept_name_key(name)
    }
    return next(iter(matches)) if len(matches) == 1 else name


def is_reconciled_spelling_issue(
    message: str, original: list[BusinessDefinition], merged: list[BusinessDefinition]
) -> bool:
    """LLM の表記差診断は、機械的な統合結果で解消を確認できたものだけ除外する。"""
    target, separator, detail = message.partition(" / ")
    if not separator or len(original) <= len(merged):
        return False
    canonical = resolve_concept_name(target, merged)
    definition = next((d for d in merged if d.api_name == canonical), None)
    if definition is None:
        return False
    if definition.kind == "object_type" and re.fullmatch(
        r"プロパティ命名競合: (snake_case vs PascalCase|PascalCase vs snake_case)", detail
    ):
        properties = [d for d in original if d.kind == "property" and d.object_type == target]
        return bool(properties) and all(
            any(
                m.kind == "property"
                and m.object_type == canonical
                and m.api_name == resolve_concept_name(d.api_name, merged)
                for m in merged
            )
            for d in properties
        )
    return (
        definition.kind == "property"
        and definition.data_type == "number"
        and bool(re.fullmatch(r"データ型: (number vs integer|integer vs number)", detail))
        and {
            d.data_type
            for d in original
            if d.kind == "property" and resolve_concept_name(d.api_name, merged) == canonical
        }
        == {"integer", "number"}
    )


def _normalize_definition_spellings(
    definitions: list[BusinessDefinition],
) -> list[BusinessDefinition]:
    """同じ属性・物理列に限定して表記差を統合する。別の業務属性は併合しない。"""
    values = [d.model_copy(deep=True) for d in definitions]
    groups: dict[tuple[Any, ...], list[PropertyDefinition]] = {}
    for d in values:
        # 種類名の ID（AI が全定義に `object_type` / `property` などを付けた値）は同一性の根拠に
        # しない。そのままでは別の表・列の定義が 1 つに併合される（#1720）。
        if d.id.casefold() in CONCEPT_LABELS:
            d.id = ""
        for m in d.mappings:
            # 列そのものの式（列 `ADMIN.T.C` に式 `ADMIN.T.C`）は列の対応と同じ（#1720）。
            if m.column_name and object_match_key(m.expression_sql) == (
                f"{mapping_object_key(m)}.{mapping_column_key(m)}"
            ):
                m.expression_sql = ""
        if d.kind == "object_type":
            for m in d.mappings:
                # 表への対応を列 SQL として誤生成した場合だけ、構造化された表対応を正とする。
                if not m.column_name and object_match_key(m.expression_sql) == mapping_object_key(
                    m
                ):
                    m.expression_sql = ""
        if d.kind != "property" or not d.mappings:
            continue
        columns = tuple(
            sorted(
                {
                    mapping_column_key(m)
                    for m in d.mappings
                    if m.column_name and not m.expression_sql
                }
            )
        )
        if not columns or any(not m.column_name or m.expression_sql for m in d.mappings):
            continue
        key = (concept_name_key(d.api_name), concept_name_key(d.object_type), columns)
        groups.setdefault(key, []).append(d)
    for group in groups.values():
        first = group[0]
        identity = next((d.id for d in group if d.id), "")
        for d in group:
            d.api_name = first.api_name
            d.id = identity
        types = {getattr(d, "data_type", "") for d in group}
        if types == {"integer", "number"}:
            # integer は number の部分集合。根拠なしに整数制約を追加しない。
            for d in group:
                d.data_type = "number"
    for d_index, d in enumerate(values):
        raw = d.model_dump(mode="json")
        for field, _ in definition_references(d):
            if isinstance(raw.get(field), str):
                raw[field] = resolve_concept_name(raw[field], values)
            elif isinstance(raw.get(field), list) and all(isinstance(v, str) for v in raw[field]):
                raw[field] = [resolve_concept_name(v, values) for v in raw[field]]
        for field in ("source", "target"):
            if isinstance(raw.get(field), str):
                raw[field] = resolve_concept_name(raw[field], values)
        for impl in raw.get("implements", []):
            impl["interface"] = resolve_concept_name(impl["interface"], values)
            for binding in impl["property_mapping"]:
                binding["property"] = resolve_concept_name(binding["property"], values)
        for assignment in raw.get("assignments", []):
            assignment["property"] = resolve_concept_name(assignment["property"], values)
        normalized = DEFINITIONS.validate_python([raw])[0]
        # 未指定の既定値を明示的な変更と誤認し、統合競合を増やさない。
        normalized.__pydantic_fields_set__ = set(d.model_fields_set)
        values[d_index] = normalized
    return values


_UNION_REFERENCE_FIELDS = frozenset(
    {
        "properties",
        "dependencies",
        "applies_to",
        "affected_properties",
        "required_links",
        "required_actions",
        "extends",
    }
)


_UNKNOWN_IS_EMPTY_FIELDS = frozenset({"cardinality", "additivity"})


def rewrite_references(
    values: list[BusinessDefinition], remap: dict[str, str]
) -> list[BusinessDefinition]:
    """概念の参照（api_name）を remap に従って書き換える。"""

    if not remap:
        return values
    rewritten = []
    for d in values:
        value = d.model_dump(mode="json")
        fields = {field for field, _ in definition_references(d)} | {"source", "target"}
        for field in fields - {"implements", "affected_properties"}:
            field_value = value.get(field)
            if isinstance(field_value, str):
                value[field] = remap.get(field_value, field_value)
            elif isinstance(field_value, list):
                value[field] = [remap.get(v, v) if isinstance(v, str) else v for v in field_value]
        for impl in value.get("implements", []):
            impl["interface"] = remap.get(impl["interface"], impl["interface"])
            for binding in impl["property_mapping"]:
                binding["property"] = remap.get(binding["property"], binding["property"])
        for assignment in value.get("assignments", []):
            assignment["property"] = remap.get(assignment["property"], assignment["property"])
        if "affected_properties" in value:
            value["affected_properties"] = [remap.get(v, v) for v in value["affected_properties"]]
        rewritten.append(value)
    return DEFINITIONS.validate_python(rewritten)


def merge_definitions(
    profile_id: str, definitions: list[BusinessDefinition]
) -> tuple[list[BusinessDefinition], list[str]]:
    merged: dict[tuple[str, str], BusinessDefinition] = {}
    conflicts: list[str] = []
    remap: dict[str, str] = {}
    for original in _normalize_definition_spellings(definitions):
        item = original.model_copy(deep=True)
        # 単一バッチ内の重複も除去する。複数定義の併合時だけに限定しない。
        for field in ("aliases", "evidence", "missing_information_ja", "mappings"):
            unique = {
                canonical_json(v.model_dump(mode="json") if hasattr(v, "model_dump") else v): v
                for v in getattr(item, field)
            }
            setattr(item, field, list(unique.values()))
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
            elif field in _UNION_REFERENCE_FIELDS and isinstance(value, list):
                # 参照の一覧は和集合にする（抽出の回ごとに一部だけ挙げても競合にしない。#1720）。
                setattr(prior, field, list(dict.fromkeys([*current, *getattr(item, field)])))
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
            elif field in _UNKNOWN_IS_EMPTY_FIELDS and "unknown" in {current, value}:
                # unknown は未設定と同じ。分かった値を使い、競合にしない（#1720）。
                if current == "unknown":
                    setattr(prior, field, getattr(item, field))
            elif not current and value:
                setattr(prior, field, getattr(item, field))
            elif value and current != getattr(item, field):
                # 同一性が一致しても意味の違いは隠さない。
                conflicts.append(
                    f"{prior.api_name} / {FIELD_LABELS.get(field, field)}: "
                    f"{canonical_json(prior.model_dump(mode='json')[field])} ↔ "
                    f"{canonical_json(value)}"
                )
    values = rewrite_references(list(merged.values()), remap)
    return sorted(values, key=lambda d: (CONCEPT_ORDER.index(d.kind), d.api_name)), sorted(
        set(conflicts)
    )


def _schema_columns(schema: dict[str, Any] | None) -> dict[str, dict[str, dict[str, Any]]]:
    """schema context の object → 列の照合キー → 列の情報。"""

    result: dict[str, dict[str, dict[str, Any]]] = {}
    for obj in (schema or {}).get("objects", []):
        key = (
            f"{object_match_key(str(obj.get('owner', '')))}."
            f"{object_match_key(str(obj.get('object_name', '')))}"
        )
        columns = result.setdefault(key, {})
        for column in obj.get("columns", []):
            if isinstance(column, dict):
                name = str(column.get("column_name", column.get("column", column.get("name", ""))))
                columns[object_match_key(name)] = {**column, "column": name}
            else:
                columns[object_match_key(str(column))] = {"column": str(column)}
    return result


def _column_keys(prop: BusinessDefinition | None, name: str) -> list[str]:
    """参照名が指す列の照合キー。プロパティならその列、未定義なら名前を列名とみなす。"""

    if isinstance(prop, PropertyDefinition):
        keys = [mapping_column_key(m) for m in prop.mappings if m.column_name]
        if keys:
            return keys
    bare = name.rsplit(".", 1)[-1]
    return [object_match_key(bare)] if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", bare) else []


class _ObjectPropertyRepair:
    """1 つのオブジェクトの properties / 識別子 / 表示名の参照を、その表の列へ付け替える。"""

    def __init__(
        self,
        profile_id: str,
        obj: ObjectTypeDefinition,
        by_name: dict[str, BusinessDefinition],
        columns: dict[str, dict[str, dict[str, Any]]],
        added: list[BusinessDefinition],
    ) -> None:
        self.profile_id = profile_id
        self.obj = obj
        self.by_name = by_name
        self.columns = columns
        self.added = added
        self.tables = list(dict.fromkeys(mapping_object_key(m) for m in obj.mappings))
        self.own = [
            d
            for d in by_name.values()
            if isinstance(d, PropertyDefinition)
            and d.kind == "property"
            and d.object_type == obj.api_name
        ]
        self.cache: dict[str, str | None] = {}

    def _mapped(self, table: str, key: str, mappings: Iterable[Any]) -> bool:
        return any(
            mapping_object_key(m) == table and m.column_name and mapping_column_key(m) == key
            for m in mappings
        )

    def resolve(self, name: str) -> str | None:
        if name not in self.cache:
            self.cache[name] = self._resolve(name)
        return self.cache[name]

    def _resolve(self, name: str) -> str | None:
        obj = self.obj
        prop = self.by_name.get(name)
        if prop is not None and (
            prop.kind != "property"  # 種類の誤りは検証の診断に任せる
            or (isinstance(prop, PropertyDefinition) and prop.object_type == obj.api_name)
        ):
            return name
        if (
            isinstance(prop, PropertyDefinition)
            and not prop.object_type
            and all(mapping_object_key(m) in self.tables for m in prop.mappings)
        ):
            # 所属の無いプロパティは、参照したオブジェクトの表の列なら、そのオブジェクトに属させる。
            prop.object_type = obj.api_name
            self.own.append(prop)
            return name
        target = next(
            (
                (table, key)
                for key in _column_keys(prop, name)
                for table in self.tables
                if self._mapped(table, key, obj.mappings) or key in self.columns.get(table, {})
            ),
            None,
        )
        if target is None:
            return None
        table, key = target
        existing = next((d for d in self.own if self._mapped(table, key, d.mappings)), None)
        if existing is not None:
            return existing.api_name
        source = next(m for m in obj.mappings if mapping_object_key(m) == table)
        column_info = self.columns.get(table, {}).get(key, {})
        column_name = next(
            (m.column_name for m in obj.mappings if self._mapped(table, key, [m])),
            str(column_info.get("column") or key),
        )
        base = name.rsplit(".", 1)[-1]
        new_name = name if prop is None else f"{obj.api_name}_{base}"
        suffix = 2
        while new_name in self.by_name:
            new_name = f"{obj.api_name}_{base}_{suffix}"
            suffix += 1
        template = prop if isinstance(prop, PropertyDefinition) else None
        clone = PropertyDefinition(
            api_name=new_name,
            id=stable_ontology_id("profile_concept", self.profile_id, "property", new_name),
            name_ja=(
                template.name_ja
                if template
                else str(column_info.get("logical_name") or column_name)
            ),
            description_ja=template.description_ja if template else "",
            aliases=list(template.aliases) if template else [],
            object_type=obj.api_name,
            data_type=(template.data_type if template else str(column_info.get("data_type") or "")),
            unit=template.unit if template else "",
            currency=template.currency if template else "",
            allowed_values=list(template.allowed_values) if template else [],
            shared_property=template.shared_property if template else "",
            value_type=template.value_type if template else "",
            mappings=[
                DefinitionMapping(
                    owner=source.owner, object_name=source.object_name, column_name=column_name
                )
            ],
            evidence=[
                DefinitionEvidence(
                    source_id="schema",
                    locator=f"{table}.{key}",
                    source_kind="database",
                    assertion="mappings",
                )
            ],
            origin=template.origin if template else "ai",
        )
        self.by_name[new_name] = clone
        self.added.append(clone)
        self.own.append(clone)
        return new_name

    def names(self, names: list[str]) -> list[str]:
        return list(dict.fromkeys(r for n in names if (r := self.resolve(n))))


# AI が空の項目を書き写した不足情報（「description_ja: 記載なし」「主キーが不明」など）。
# 構造の検査（識別子・粒度・型・基数）が同じことを info で出すため、重ねない（#1720）。
_STRUCTURAL_NOTE = re.compile(
    r"(description_ja|primary_key|grain_ja|title_property|cardinality|data_type|主キー)"
)
_CONTENT_EXEMPT_FIELDS = frozenset(
    {
        "kind",
        "id",
        "api_name",
        "name_ja",
        "origin",
        "review_status",
        "evidence",
        "missing_information_ja",
        "aliases",
    }
)


def _is_ungrounded(d: BusinessDefinition) -> bool:
    """graph に置けない AI の出力（種類ごとの空の雛形・端点の無い参照）。

    資料から抽出した物理の対応の無いオブジェクトなど、中身のある定義は残す。
    """

    if d.api_name.casefold() in CONCEPT_LABELS and not d.mappings:
        return True  # api_name が種類名だけの雛形（object_type・property など）
    if d.kind == "link_type":
        return not (getattr(d, "source", "") and getattr(d, "target", ""))
    if d.kind in {"action_type", "business_event", "object_set"}:
        return not getattr(d, "object_type", "")
    if d.kind == "enumeration":
        return not getattr(d, "property", "")
    defaults = {
        name: info.get_default(call_default_factory=True)
        for name, info in type(d).model_fields.items()
    }
    return not d.description_ja.strip() and all(
        value == defaults.get(field)
        for field, value in d.model_dump().items()
        if field not in _CONTENT_EXEMPT_FIELDS
    )


def _prune_ai_output(values: list[BusinessDefinition]) -> list[BusinessDefinition]:
    """AI の出力から、graph に置けない雛形と、構造の検査と重なる不足情報を除く。"""

    kept = [d for d in values if not _is_ungrounded(d)]
    for d in kept:
        d.missing_information_ja = [
            note for note in d.missing_information_ja if not _STRUCTURAL_NOTE.search(note)
        ]
    # 同じオブジェクトの同じ列に対応するプロパティが名前違いで 2 つあれば 1 つにする。
    remap: dict[str, str] = {}
    seen: dict[tuple[str, str], str] = {}
    for d in kept:
        if not (isinstance(d, PropertyDefinition) and d.kind == "property"):
            continue
        columns = [
            m
            for m in d.mappings
            if m.column_name
            and (
                not m.expression_sql
                or object_match_key(m.expression_sql)
                == f"{mapping_object_key(m)}.{mapping_column_key(m)}"
            )
        ]
        if len(columns) != len(d.mappings) or len({mapping_column_key(m) for m in columns}) != 1:
            continue
        key = (
            d.object_type,
            f"{mapping_object_key(columns[0])}.{mapping_column_key(columns[0])}",
        )
        if key in seen:
            remap[d.api_name] = seen[key]
        else:
            seen[key] = d.api_name
    kept = [d for d in kept if d.api_name not in remap]
    # プロパティの対応は所属オブジェクトの表・ビューの列だけにする（他の表の列を 1 つの
    # プロパティに束ねない）。所属オブジェクトの表に 1 つも無ければ元のまま検証に任せる。
    tables = {
        d.api_name: {mapping_object_key(m) for m in d.mappings}
        for d in kept
        if isinstance(d, ObjectTypeDefinition) and d.mappings
    }
    for d in kept:
        if isinstance(d, PropertyDefinition) and d.kind == "property" and d.object_type in tables:
            own = [m for m in d.mappings if mapping_object_key(m) in tables[d.object_type]]
            if own:
                d.mappings = own
    return rewrite_references(kept, remap)


def repair_definitions(
    profile_id: str,
    definitions: list[BusinessDefinition],
    schema: dict[str, Any] | None = None,
    *,
    ai_output: bool = False,
) -> list[BusinessDefinition]:
    """AI の出力を graph にできる形へ直す（#1720）。

    オブジェクトの properties / 識別子が他のオブジェクトのプロパティや未定義の名前を指すときは、
    そのオブジェクトの表・列に対応するプロパティを作って付け替える。`ai_output` のときは、
    graph に置けない雛形（種類ごとの空の定義）・構造の検査と重なる不足情報・同じ列の重複した
    プロパティも除く。

    AI は列名が同じ（`DEPARTMENT_ID`・2 つのビューの `DEPARTMENT_NAME`）プロパティを 1 つだけ
    作り、複数のオブジェクトから参照しがちである。そのままでは graph が壊れ公開を止めるため、
    オブジェクトの表に同じ列があればオブジェクトごとのプロパティに分ける。列が無い参照は外す
    （best effort。graph を作れる範囲を最大にし、推測で列を足さない）。
    """

    values = [d.model_copy(deep=True) for d in definitions]
    if ai_output:
        # AI 構築の出力だけ。利用者が Markdown に書いた定義は消さず、検証の診断に任せる。
        values = _prune_ai_output(values)
    by_name: dict[str, BusinessDefinition] = {d.api_name: d for d in values}
    columns = _schema_columns(schema)
    added: list[BusinessDefinition] = []
    for obj in [d for d in values if isinstance(d, ObjectTypeDefinition)]:
        repair = _ObjectPropertyRepair(profile_id, obj, by_name, columns, added)
        obj.properties = repair.names(obj.properties)
        obj.primary_key = repair.names(obj.primary_key)
        if obj.title_property:
            obj.title_property = repair.resolve(obj.title_property) or ""
    return sorted([*values, *added], key=lambda d: (CONCEPT_ORDER.index(d.kind), d.api_name))


def _human(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("owner") and value.get("object_name"):
            physical = ".".join(
                str(value[k]) for k in ("owner", "object_name", "column_name") if value.get(k)
            )
            expression = str(value.get("expression_sql") or "")
            # 列そのものの式（`ADMIN.T.C`）は重複になるため書かない（#1720）。
            if not expression or object_match_key(expression) == object_match_key(physical):
                return physical
            return physical + f"（式: {expression}）"
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
    lines = ["## オントロジーの概念定義"]
    for kind, label in CONCEPT_LABELS.items():
        matches = [d for d in definitions if d.kind == kind]
        if not matches:
            continue
        lines.extend(["", f"#### {label}", ""])
        for d in matches:
            lines.append(f"##### {d.name_ja} (`{d.api_name}`)")
            defaults = {
                name: info.get_default(call_default_factory=True)
                for name, info in type(d).model_fields.items()
            }
            for field, value in d.model_dump(mode="json").items():
                # 既定値（「必須: いいえ」「多重度: unknown」など）は書かない（#1720）。
                if field not in {"kind"} and value == defaults.get(field, object()):
                    continue
                if field in {
                    "id",
                    "api_name",
                    "name_ja",
                    "kind",
                    "review_status",
                    "origin",
                    "implementation_key",
                    "evidence",
                    "missing_information_ja",
                } or value in ("", [], None):
                    continue
                lines.append(f"- {FIELD_LABELS.get(field, field)}: {_human(value)}")
            # 不足情報（missing_information_ja）は公開前の確認の「補うと精度が上がる項目」に出す。
            # Markdown の本文には書かない（#1720）。
            lines.append("")
    # 診断と根拠の構造は保存成果物に保持する。業務本文には混ぜない。
    return "\n".join(lines).rstrip() + "\n"


_CARDINALITY_JA = {
    "one_to_one": "一対一",
    "one_to_many": "一対多",
    "many_to_one": "多対一",
    "many_to_many": "多対多",
}


def _physical_label(mapping: Any) -> str:
    """`OWNER.OBJECT[.COLUMN]`。式が列そのものでないときだけ式を添える。"""

    path = ".".join(p for p in (mapping.owner, mapping.object_name, mapping.column_name) if p)
    expression = " ".join(str(mapping.expression_sql or "").split())
    if expression and object_match_key(expression) != object_match_key(path):
        return f"{path}（式: {expression}）" if mapping.column_name else expression
    return path


def _inline(text: str) -> str:
    return " ".join(str(text or "").split())


def _joined(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(_inline(str(v)) for v in value if str(v).strip())
    return _inline(str(value or ""))


def _details(pairs: Iterable[tuple[str, Any]]) -> str:
    return "、".join(f"{label}: {text}" for label, value in pairs if (text := _joined(value)))


def _description(text: str) -> str:
    """統合で行ごとに足された説明を、重複を除いて 1 行にする。"""

    return " / ".join(dict.fromkeys(line.strip() for line in text.splitlines() if line.strip()))


def _with_details(head: str, details: str, description: str = "") -> str:
    text = head + (f"（{details}）" if details else "")
    return text + (f" {_description(description)}" if _description(description) else "")


def render_llm_context(definitions: Iterable[BusinessDefinition], qa_markdown: str = "") -> str:
    """Select AI / Select AI Agent / LLM に業務の知識として渡すオントロジーの Markdown（#1720）。

    読み手は LLM。SQL の生成に効く業務の知識（オブジェクトと表、プロパティと列、関係の結合条件、
    指標の式、業務ルール、用語と同義語）だけを簡潔に書く。根拠・検証の状態・不足情報・書き方の
    説明・空の項目は入れない（それらは公開前の確認と画面のために定義に保存している）。
    """

    values = list(definitions)
    by_name = {d.api_name: d for d in values}

    def label(name: str) -> str:
        target = by_name.get(name)
        return target.name_ja if target is not None else name

    def physical(d: BusinessDefinition) -> str:
        return " / ".join(dict.fromkeys(_physical_label(m) for m in d.mappings))

    def aliases(d: BusinessDefinition) -> list[str]:
        return [a for a in dict.fromkeys(d.aliases) if a and a != d.name_ja]

    lines: list[str] = ["# 業務オントロジー"]
    objects = [d for d in values if isinstance(d, ObjectTypeDefinition)]
    properties = [d for d in values if isinstance(d, PropertyDefinition) and d.kind == "property"]
    owned: dict[str, list[PropertyDefinition]] = {}
    for prop in properties:
        owned.setdefault(prop.object_type, []).append(prop)
    enumerations = {getattr(d, "property", ""): d for d in values if d.kind == "enumeration"}

    def property_line(prop: PropertyDefinition, indent: str = "") -> str:
        enum = enumerations.get(prop.api_name)
        allowed = list(prop.allowed_values)
        if enum is not None:
            allowed = [
                f"{v.code}={v.label_ja}" if v.label_ja and v.label_ja != v.code else v.code
                for v in getattr(enum, "values", [])
            ]
        details = _details(
            [
                ("列", physical(prop)),
                ("型", prop.data_type),
                ("単位", prop.unit),
                ("通貨", prop.currency),
                ("許可値", allowed),
                ("別名", aliases(prop)),
            ]
        )
        return indent + "- " + _with_details(prop.name_ja, details, prop.description_ja)

    if objects:
        lines.extend(["", "## オブジェクト"])
        for obj in objects:
            head = f"### {obj.name_ja}"
            table = " / ".join(
                dict.fromkeys(f"{m.owner}.{m.object_name}" for m in obj.mappings if m.object_name)
            )
            lines.extend(["", head + (f"（{table}）" if table else "")])
            if _description(obj.description_ja):
                lines.append(_description(obj.description_ja))
            facts = [
                ("別名", aliases(obj)),
                ("識別子", [label(n) for n in obj.primary_key]),
                ("粒度", obj.grain_ja),
                ("表示名", label(obj.title_property) if obj.title_property else ""),
            ]
            lines.extend(f"- {name}: {value}" for name, raw in facts if (value := _joined(raw)))
            props = owned.get(obj.api_name, [])
            listed = [by_name[n] for n in obj.properties if n in by_name]
            ordered: list[PropertyDefinition] = []
            for prop in [p for p in listed if isinstance(p, PropertyDefinition)] + props:
                if all(prop is not seen for seen in ordered):
                    ordered.append(prop)
            if ordered:
                lines.append("- プロパティ:")
                lines.extend(property_line(p, "  ") for p in ordered)
    orphans = [p for p in properties if p.object_type not in {o.api_name for o in objects}]
    shared = [
        d for d in values if isinstance(d, PropertyDefinition) and d.kind == "shared_property"
    ]
    if orphans or shared:
        lines.extend(["", "## 共通のプロパティ"])
        lines.extend(property_line(p) for p in [*shared, *orphans])
    links = [d for d in values if d.kind == "link_type"]
    if links:
        lines.extend(["", "## 関係"])
        for link in links:
            intermediary = getattr(link, "intermediary_object", "")
            details = "、".join(
                part
                for part in (
                    _CARDINALITY_JA.get(getattr(link, "cardinality", ""), ""),
                    _details([("結合", getattr(link, "join_expression_sql", ""))]),
                    _details([("経由", label(intermediary) if intermediary else "")]),
                )
                if part
            )
            head = (
                f"{label(getattr(link, 'source', ''))} → {label(getattr(link, 'target', ''))}"
                f"「{link.name_ja}」"
            )
            lines.append("- " + _with_details(head, details, link.description_ja))
    metrics = [d for d in values if isinstance(d, MetricDefinitionV2)]
    if metrics:
        lines.extend(["", "## 指標"])
        for metric in metrics:
            details = _details(
                [
                    ("式", metric.expression_sql),
                    ("条件", metric.filter_sql),
                    ("集計", metric.aggregation),
                    ("粒度", [label(n) for n in metric.grain]),
                    ("重複排除", [label(n) for n in metric.distinct_keys]),
                    ("時間", label(metric.time_property) if metric.time_property else ""),
                    ("時間の扱い", metric.time_policy_ja),
                    ("単位", metric.unit),
                    ("通貨", metric.currency),
                    ("NULL", metric.null_policy_ja),
                    ("別名", aliases(metric)),
                ]
            )
            lines.append("- " + _with_details(metric.name_ja, details, metric.description_ja))
    rules = [d for d in values if d.kind == "business_rule"]
    if rules:
        lines.extend(["", "## 業務ルール"])
        for rule in rules:
            details = _details(
                [
                    ("条件", getattr(rule, "predicate_sql", "")),
                    ("対象", [label(n) for n in getattr(rule, "applies_to", [])]),
                ]
            )
            lines.append("- " + _with_details(rule.name_ja, details, rule.description_ja))
    others = [d for d in values if d.kind in {"function", "object_set", "business_event"}]
    if others:
        lines.extend(["", "## その他の定義"])
        for d in others:
            details = _details(
                [
                    ("式", getattr(d, "expression_sql", "")),
                    ("条件", getattr(d, "filter_sql", "")),
                    ("対象", label(target) if (target := getattr(d, "object_type", "")) else ""),
                    ("戻り値", getattr(d, "return_type", "")),
                    ("別名", aliases(d)),
                ]
            )
            lines.append("- " + _with_details(d.name_ja, details, d.description_ja))
    if qa_markdown.strip():
        lines.extend(["", qa_markdown.strip()])
    return "\n".join(lines).rstrip() + "\n" if len(lines) > 1 else ""


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
        pair = [physical.get(sql_column_key(col)) for col in (part.this, part.expression)]
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
    from .ontology_graph_semantics import enum_graph_members, reference_semantics

    rid = revision_id or base.revision.id
    nodes = [
        n.model_copy(update={"revision_id": rid}, deep=True)
        for n in base.nodes
        if n.kind.value in PHYSICAL_KINDS
    ]
    physical = physical_node_index(nodes)
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
            obj = physical.get(mapping_object_key(m))
            col = physical.get(f"{mapping_object_key(m)}.{mapping_column_key(m)}")
            mappings.append(
                PhysicalMapping(
                    object_ref=PhysicalObjectRef(
                        # 物理 node の object_ref と同じくカタログ上の名前（`"Mixed_Case"` →
                        # Mixed_Case、`orders` → ORDERS）。画面の SQL 接地が同じ規則で照合する。
                        owner=object_part_name(m.owner),
                        object_name=object_part_name(m.object_name),
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
            edge_kind, predicate = reference_semantics(d, field)
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
                        kind=edge_kind,
                        source_node_id=d.id,
                        target_node_id=target_id,
                        relationship_name_ja=FIELD_LABELS.get(field, field),
                        provenance=provenance,
                        review_status="approved",
                        metadata={
                            "reference_field": field,
                            "reference_api_name": ref,
                            "semantic_predicate": predicate,
                            "reference_definition_id": referenced.id,
                            "reference_definition_kind": referenced.kind,
                        },
                    )
                )
    enum_nodes, enum_edges = enum_graph_members(definitions, rid, provenance)
    nodes.extend(enum_nodes)
    edges.extend(enum_edges)
    return SchemaOntology(
        revision=base.revision.model_copy(update={"id": rid}),
        nodes=nodes,
        edges=list({e.id: e for e in edges}.values()),
    )
