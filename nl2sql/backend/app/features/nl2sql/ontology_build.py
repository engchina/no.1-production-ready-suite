"""AI オントロジー構築(業務エンティティ命名・Q/A 学習・自然言語補強)。

OCI Enterprise AI の入力 schema は Profile + DB schema catalog から直接作る。
出力は Pydantic(:class:`OntologyBuildExtraction`)で検証し、profile スコープ外の
owner/object/column を参照する候補は Markdown 下書きへ入れず warnings に落とす。
生成物は承認済み draft revision と Markdown 下書き artifact として保存され、
publish で Published Markdown へコピーされるまで SQL 生成には使われない。

job と実行入力は Oracle store に永続化する。local は thread、production は独立 worker が
同じ処理を実行し、成果物は Markdown 下書きの確認ゲートを通る。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.features.nl2sql.models import Nl2SqlProfile, SchemaCatalog, SchemaTable
from app.features.nl2sql.ontology_catalog import SchemaOntology, catalog_schema_fingerprint
from app.features.nl2sql.ontology_models import (
    JoinCondition,
    MetricDefinition,
    OntologyBuildEvent,
    OntologyBuildExtraction,
    OntologyBuildJob,
    OntologyBuildStatus,
    OntologyBuildStep,
    OntologyBuildStepName,
    OntologyBuildStepStatus,
    OntologyEdge,
    OntologyEdgeKind,
    OntologyEvidence,
    OntologyEvidenceLocatorKind,
    OntologyMetricCandidate,
    OntologyNode,
    OntologyNodeKind,
    OntologyProposalKind,
    OntologyProposalPayload,
    OntologyProvenance,
    OntologyRelationshipCandidate,
    OntologyReviewStatus,
    OntologySourceDocument,
    OntologySourceKind,
    OntologySourceProgress,
    OntologySourceStatus,
    ProfileOntologyView,
    QaPair,
    RelationshipCardinality,
    utc_now,
)
from app.features.nl2sql.ontology_observability import record_job, record_source_extraction
from app.features.nl2sql.ontology_service import (
    OntologyNotFoundError,
    OntologyStateConflictError,
    OntologyVersionConflictError,
)
from app.features.nl2sql.ontology_sources import (
    ExtractedSourceChunk,
    OntologySourceStorage,
    extract_ontology_source,
)
from app.features.nl2sql.ontology_store import (
    OntologyVersionConflict,
    canonical_json,
    stable_ontology_id,
)
from app.features.nl2sql.sql_semantics import parse_oracle_sql
from app.settings import get_settings

logger = logging.getLogger(__name__)

_DANGEROUS_EXPRESSION_TOKENS = (";", "--", "/*")
_ONTOLOGY_BUILD_LLM_CONTEXT_MAX_CHARS = 100_000
_LLM_CONTEXT_HEADROOM_CHARS = 512
_ORACLE_PSEUDO_COLUMNS = {"LEVEL", "ORA_ROWSCN", "ROWID", "ROWNUM"}
_QA_SQL_EXAMPLE_SECTION_TITLE = "## Q/A SQL 例"
_QA_SQL_PATTERN_SECTION_TITLE = "## Q/A SQL 構造パターン"
_QA_SQL_EXAMPLE_BLOCK_RE = re.compile(
    rf"^{re.escape(_QA_SQL_EXAMPLE_SECTION_TITLE)}\s*\n```jsonl?\s*\n(?P<body>.*?)\n```",
    flags=re.MULTILINE | re.DOTALL,
)
_QA_SQL_EXAMPLE_MAX_PROMPT_COUNT = 3
_QA_SQL_PATTERN_MAX_PROMPT_COUNT = 3


# --- DB schema scope / profile view スコープの解決 -------------------------------------------


@dataclass(frozen=True)
class OntologyBuildSchemaContext:
    schema_context: str
    object_count: int
    column_count: int
    schema_fingerprint: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _normalize_oracle_identifier(value: str) -> str:
    return value.replace('"', "").strip().upper()


def _schema_object_kind(table: SchemaTable) -> str:
    return "view" if "view" in table.table_type.casefold() else "table"


def _schema_object_label(table: SchemaTable) -> str:
    owner = _normalize_oracle_identifier(table.owner or "APP")
    name = _normalize_oracle_identifier(table.table_name)
    return f"{owner}.{name}" if owner else name


def _resolve_catalog_object(
    raw_name: str,
    *,
    object_kind: str,
    candidates: list[SchemaTable],
) -> tuple[SchemaTable | None, str | None]:
    parts = [part for part in _normalize_oracle_identifier(raw_name).split(".") if part]
    owner = parts[-2] if len(parts) >= 2 else ""
    object_name = parts[-1] if parts else ""
    matches = [
        table
        for table in candidates
        if _schema_object_kind(table) == object_kind
        and _normalize_oracle_identifier(table.table_name) == object_name
        and (not owner or _normalize_oracle_identifier(table.owner or "APP") == owner)
    ]
    if len(matches) > 1:
        qualified = ", ".join(sorted(_schema_object_label(table) for table in matches))
        return (
            None,
            f"「{raw_name}」は DB schema catalog 内で複数 object に一致します: {qualified}。"
            "owner 付きの object 名を Profile に設定してください。",
        )
    if not matches:
        return (
            None,
            f"「{raw_name}」を DB schema catalog の {object_kind} として解決できません。"
            "DB 構造を再取得するか、Profile の対象 object 名(owner 付き)を確認してください。",
        )
    return matches[0], None


def _selected_schema_objects(
    profile: Nl2SqlProfile,
    catalog: SchemaCatalog,
) -> tuple[list[SchemaTable], list[str], list[str]]:
    if not profile.allowed_tables and not profile.allowed_views:
        return sorted(catalog.tables, key=_schema_object_label), [], []

    selected: dict[tuple[str, str, str], SchemaTable] = {}
    warnings: list[str] = []
    errors: list[str] = []
    for raw_name, object_kind in [
        *((name, "table") for name in profile.allowed_tables),
        *((name, "view") for name in profile.allowed_views),
    ]:
        table, message = _resolve_catalog_object(
            raw_name,
            object_kind=object_kind,
            candidates=catalog.tables,
        )
        if message:
            if "複数 object" in message:
                errors.append(message)
            else:
                warnings.append(message)
            continue
        if table is None:
            continue
        key = (
            _normalize_oracle_identifier(table.owner or "APP"),
            _normalize_oracle_identifier(table.table_name),
            _schema_object_kind(table),
        )
        selected[key] = table
    return sorted(selected.values(), key=_schema_object_label), warnings, errors


def _constraint_cardinality(detail: Any, source_table: SchemaTable) -> str:
    source_columns = tuple(_normalize_oracle_identifier(value) for value in detail.columns)
    unique_column_sets = {
        tuple(_normalize_oracle_identifier(value) for value in constraint.columns)
        for constraint in source_table.constraint_details
        if constraint.constraint_type in {"P", "U"}
    }
    return "one_to_one" if source_columns in unique_column_sets else "many_to_one"


def build_schema_context_from_catalog(
    profile: Nl2SqlProfile,
    catalog: SchemaCatalog,
) -> OntologyBuildSchemaContext:
    """AI 構築 input 用に、Profile scope の DB schema 情報を catalog から直接作る。"""

    selected_objects, warnings, errors = _selected_schema_objects(profile, catalog)
    selected_keys = {
        (
            _normalize_oracle_identifier(table.owner or "APP"),
            _normalize_oracle_identifier(table.table_name),
        )
        for table in selected_objects
    }
    objects: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    for table in selected_objects:
        owner = _normalize_oracle_identifier(table.owner or "APP")
        object_name = _normalize_oracle_identifier(table.table_name)
        qualified_name = f"{owner}.{object_name}" if owner else object_name
        objects.append(
            {
                "object": qualified_name,
                "owner": owner,
                "object_name": object_name,
                "object_type": _schema_object_kind(table),
                "logical_name": table.logical_name,
                "comment": table.comment,
                "row_count": table.row_count,
                "columns": [
                    {
                        "column": _normalize_oracle_identifier(column.column_name),
                        "qualified_column": (
                            f"{qualified_name}."
                            f"{_normalize_oracle_identifier(column.column_name)}"
                        ),
                        "data_type": column.data_type,
                        "nullable": column.nullable,
                        "ordinal": ordinal,
                        "logical_name": column.logical_name,
                        "comment": column.comment,
                    }
                    for ordinal, column in enumerate(table.columns, start=1)
                ],
                "constraints": [
                    {
                        "constraint_name": detail.constraint_name,
                        "constraint_type": detail.constraint_type,
                        "columns": [
                            _normalize_oracle_identifier(column) for column in detail.columns
                        ],
                    }
                    for detail in table.constraint_details
                    if detail.constraint_type in {"P", "U", "C"}
                ],
            }
        )
        for detail in table.constraint_details:
            if detail.constraint_type != "R" or not detail.referenced_table:
                continue
            target_owner = _normalize_oracle_identifier(detail.referenced_owner or owner)
            target_name = _normalize_oracle_identifier(detail.referenced_table)
            if (target_owner, target_name) not in selected_keys:
                continue
            relationships.append(
                {
                    "id": detail.constraint_name,
                    "kind": "foreign_key",
                    "source_object": qualified_name,
                    "target_object": f"{target_owner}.{target_name}",
                    "relationship_name_ja": f"{qualified_name} → {target_owner}.{target_name}",
                    "description_ja": f"Oracle 外部キー {detail.constraint_name}",
                    "cardinality": _constraint_cardinality(detail, table),
                    "review_status": "approved" if detail.status == "ENABLED" else "proposed",
                    "allowed_path": True,
                    "join_conditions": [
                        {
                            "left": (f"{qualified_name}.{_normalize_oracle_identifier(left)}"),
                            "right": (
                                f"{target_owner}.{target_name}."
                                f"{_normalize_oracle_identifier(right)}"
                            ),
                            "operator": "=",
                            "ordinal": ordinal,
                            "expression": (
                                f"{qualified_name}.{_normalize_oracle_identifier(left)} = "
                                f"{target_owner}.{target_name}."
                                f"{_normalize_oracle_identifier(right)}"
                            ),
                        }
                        for ordinal, (left, right) in enumerate(
                            zip(detail.columns, detail.referenced_columns, strict=False),
                            start=1,
                        )
                    ],
                }
            )

    for dependency in catalog.view_dependencies:
        source_owner = _normalize_oracle_identifier(dependency.owner or "APP")
        source_name = _normalize_oracle_identifier(dependency.view_name)
        target_owner = _normalize_oracle_identifier(dependency.referenced_owner or source_owner)
        target_name = _normalize_oracle_identifier(dependency.referenced_name)
        if (source_owner, source_name) not in selected_keys:
            continue
        if (target_owner, target_name) not in selected_keys:
            continue
        relationships.append(
            {
                "id": f"view_dependency:{source_owner}.{source_name}:{target_owner}.{target_name}",
                "kind": "view_dependency",
                "source_object": f"{source_owner}.{source_name}",
                "target_object": f"{target_owner}.{target_name}",
                "relationship_name_ja": "参照",
                "description_ja": "Oracle ビュー依存関係",
                "cardinality": "unknown",
                "review_status": "approved",
                "allowed_path": True,
                "join_conditions": [],
            }
        )

    schema_context = json.dumps(
        {
            "profile": {"id": profile.id, "name": profile.name},
            "objects": sorted(objects, key=lambda item: str(item["object"])),
            "relationships": sorted(
                relationships,
                key=lambda item: (
                    str(item["kind"]),
                    str(item["source_object"]),
                    str(item["target_object"]),
                    str(item["id"]),
                ),
            ),
            "warnings": warnings,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return OntologyBuildSchemaContext(
        schema_context=schema_context,
        object_count=len(objects),
        column_count=sum(len(item["columns"]) for item in objects),
        schema_fingerprint=catalog_schema_fingerprint(catalog),
        warnings=warnings,
        errors=errors,
    )


class _ScopeResolver:
    """profile view 内の物理 object/column に限定した参照解決。"""

    def __init__(self, ontology: SchemaOntology, view: ProfileOntologyView) -> None:
        scoped = set(view.node_ids)
        self.objects: dict[str, OntologyNode] = {}
        self.objects_by_name: dict[str, list[OntologyNode]] = {}
        self.columns: dict[str, OntologyNode] = {}
        self.columns_by_name: dict[str, list[OntologyNode]] = {}
        self.sql_aliases: dict[str, set[str]] = {}
        for node in ontology.nodes:
            if node.id not in scoped:
                continue
            if node.kind in {OntologyNodeKind.TABLE, OntologyNodeKind.VIEW}:
                owner = str(node.metadata.get("owner", "")).upper()
                name = str(node.metadata.get("object_name", "")).upper()
                self.objects[f"{owner}.{name}"] = node
                self.objects_by_name.setdefault(name, []).append(node)
            elif node.kind == OntologyNodeKind.COLUMN:
                owner = str(node.metadata.get("owner", "")).upper()
                name = str(node.metadata.get("object_name", "")).upper()
                column = str(node.metadata.get("column_name", "")).upper()
                self.columns[f"{owner}.{name}.{column}"] = node
                self.columns_by_name.setdefault(column, []).append(node)

    def _resolve_object_key(self, reference: str) -> str | None:
        key = reference.replace('"', "").strip().upper()
        if not key:
            return None
        if key in self.objects:
            return key
        alias_targets = self.sql_aliases.get(key, set())
        if len(alias_targets) == 1:
            return next(iter(alias_targets))
        parts = [part for part in key.split(".") if part]
        lookup_name = parts[-1] if parts else key
        candidates = self.objects_by_name.get(lookup_name, [])
        if len(candidates) == 1:
            node = candidates[0]
            owner = str(node.metadata.get("owner", "")).upper()
            name = str(node.metadata.get("object_name", "")).upper()
            return f"{owner}.{name}"
        return None

    def resolve_object(self, reference: str) -> OntologyNode | None:
        key = self._resolve_object_key(reference)
        return self.objects.get(key) if key is not None else None

    def register_sql_aliases(self, sql_texts: Sequence[str]) -> None:
        """Q/A SQL 内の table alias を profile scope の物理 object に結び直す。"""

        for sql in sql_texts:
            graph = parse_oracle_sql(sql).graph
            if graph is None:
                continue
            for table in graph.tables:
                if table.is_cte:
                    continue
                owner = str(table.owner).strip().upper()
                name = str(table.name).strip().upper()
                object_key = self._resolve_object_key(f"{owner}.{name}" if owner else name)
                if object_key is None:
                    continue
                for token in {name, str(table.alias).strip().upper()}:
                    if token:
                        self.sql_aliases.setdefault(token, set()).add(object_key)

    def resolve_column(self, reference: str) -> OntologyNode | None:
        key = reference.replace('"', "").strip().upper()
        parts = [part for part in key.split(".") if part]
        if len(parts) >= 3:
            exact = self.columns.get(".".join(parts[-3:]))
            if exact is not None:
                return exact
            object_column = ".".join(parts[-2:])
            matches = [
                node
                for node_key, node in self.columns.items()
                if node_key.endswith(f".{object_column}")
            ]
            return matches[0] if len(matches) == 1 else None
        if len(parts) == 2:
            alias_targets = self.sql_aliases.get(parts[0], set())
            alias_matches = [
                node
                for object_key in sorted(alias_targets)
                if (node := self.columns.get(f"{object_key}.{parts[1]}")) is not None
            ]
            if len(alias_matches) == 1:
                return alias_matches[0]
            # OBJECT.COLUMN 形式は owner が一意に決まる場合だけ解決する
            matches = [
                node
                for node_key, node in self.columns.items()
                if node_key.endswith("." + ".".join(parts))
            ]
            return matches[0] if len(matches) == 1 else None
        if len(parts) == 1:
            matches = self.columns_by_name.get(parts[0], [])
            return matches[0] if len(matches) == 1 else None
        return None


def _physical_object_label(node: OntologyNode) -> str:
    if node.physical_mappings:
        ref = node.physical_mappings[0].object_ref
        return f"{ref.owner}.{ref.object_name}" if ref.owner else ref.object_name
    owner = str(node.metadata.get("owner", "")).strip()
    object_name = str(node.metadata.get("object_name", "")).strip()
    if object_name:
        return f"{owner}.{object_name}" if owner else object_name
    return node.technical_name or node.id


def _column_ref_label(ref: Any) -> str:
    owner = str(getattr(ref, "owner", "") or "").strip()
    object_name = str(getattr(ref, "object_name", "") or "").strip()
    column_name = str(getattr(ref, "column_name", "") or "").strip()
    if owner and object_name and column_name:
        return f"{owner}.{object_name}.{column_name}"
    if object_name and column_name:
        return f"{object_name}.{column_name}"
    return column_name or object_name or owner


def _join_condition_label(condition: JoinCondition) -> str:
    return (
        f"{_column_ref_label(condition.left)} {condition.operator} "
        f"{_column_ref_label(condition.right)}"
    )


def build_schema_context(ontology: SchemaOntology, view: ProfileOntologyView) -> str:
    """LLM に渡す profile スコープの schema 情報(JSON 文字列、決定論)。"""

    scoped = set(view.node_ids)
    scoped_edges = set(view.edge_ids)
    objects: dict[str, dict[str, Any]] = {}
    for node in sorted(ontology.nodes, key=lambda item: item.id):
        if node.id not in scoped:
            continue
        if node.kind in {OntologyNodeKind.TABLE, OntologyNodeKind.VIEW}:
            objects[node.technical_name] = {
                "object": node.technical_name,
                "object_type": node.kind.value,
                "logical_name": node.business_name_ja,
                "comment": node.description_ja,
                "table_usage_ja": view.table_usages_ja.get(node.id, ""),
                "columns": [],
            }
    for node in sorted(ontology.nodes, key=lambda item: item.id):
        if node.id not in scoped or node.kind != OntologyNodeKind.COLUMN:
            continue
        owner = str(node.metadata.get("owner", ""))
        object_name = str(node.metadata.get("object_name", ""))
        entry = objects.get(f"{owner}.{object_name}")
        if entry is None:
            continue
        entry["columns"].append(
            {
                "column": str(node.metadata.get("column_name", "")),
                "data_type": str(node.metadata.get("data_type", "")),
                "nullable": bool(node.metadata.get("nullable", True)),
                "ordinal": node.metadata.get("ordinal"),
                "logical_name": node.business_name_ja,
                "comment": node.description_ja,
            }
        )
    node_by_id = {node.id: node for node in ontology.nodes}
    relationships: list[dict[str, Any]] = []
    for edge in sorted(ontology.edges, key=lambda item: item.id):
        if edge.id not in scoped_edges:
            continue
        if edge.kind not in {
            OntologyEdgeKind.FOREIGN_KEY,
            OntologyEdgeKind.BUSINESS_RELATIONSHIP,
            OntologyEdgeKind.JOINS,
        }:
            continue
        source = node_by_id.get(edge.source_node_id)
        target = node_by_id.get(edge.target_node_id)
        if source is None or target is None:
            continue
        relationships.append(
            {
                "id": edge.id,
                "kind": edge.kind.value,
                "source_object": _physical_object_label(source),
                "target_object": _physical_object_label(target),
                "relationship_name_ja": edge.relationship_name_ja,
                "description_ja": edge.description_ja,
                "cardinality": edge.cardinality.value,
                "review_status": edge.review_status.value,
                "allowed_path": edge.id in set(view.allowed_path_ids),
                "join_conditions": [
                    {
                        "left": _column_ref_label(condition.left),
                        "right": _column_ref_label(condition.right),
                        "operator": condition.operator,
                        "ordinal": condition.ordinal,
                        "expression": _join_condition_label(condition),
                    }
                    for condition in sorted(edge.join_conditions, key=lambda item: item.ordinal)
                ],
            }
        )
    return json.dumps(
        {
            "objects": sorted(objects.values(), key=lambda item: str(item["object"])),
            "relationships": relationships,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


# --- 候補 → proposal 変換 ---------------------------------------------------------------------


@dataclass
class ProposalDraft:
    kind: OntologyProposalKind
    title_ja: str
    description_ja: str
    payload: OntologyProposalPayload


@dataclass
class _ConversionResult:
    drafts: list[ProposalDraft] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _provenance(
    job_id: str,
    inferred_by: str,
    evidence_ja: str,
    source_evidence: list[Any] | None = None,
) -> OntologyProvenance:
    return OntologyProvenance(
        source_kind=OntologySourceKind.INFERRED,
        source_id=f"ontology_build:{job_id}",
        source_detail=evidence_ja,
        inferred_by=inferred_by,
        evidence=list(source_evidence or []),
    )


def _business_entity_node(
    object_node: OntologyNode,
    *,
    revision_id: str,
    business_name_ja: str,
    description_ja: str,
    aliases: list[str],
    confidence: float,
    provenance: OntologyProvenance,
    synthetic: bool,
) -> OntologyNode:
    owner = str(object_node.metadata.get("owner", ""))
    object_name = str(object_node.metadata.get("object_name", ""))
    metadata: dict[str, Any] = {"owner": owner, "object_name": object_name}
    if synthetic:
        # 関係提案の endpoint 用に自動生成した最小ノード。accept 時に既存ノードがあれば
        # 上書きせずスキップされる。
        metadata["synthetic_endpoint"] = True
    return OntologyNode(
        id=stable_ontology_id("business_entity", owner, object_name),
        revision_id=revision_id,
        kind=OntologyNodeKind.BUSINESS_ENTITY,
        technical_name=object_node.technical_name,
        business_name_ja=business_name_ja,
        description_ja=description_ja,
        aliases=aliases,
        # 検証(BUSINESS_OBJECT_MAPPING_SPOOFED)を通すため、物理ノードの安定参照を複製する
        physical_mappings=[object_node.physical_mappings[0].model_copy(deep=True)],
        provenance=provenance,
        confidence=confidence,
        review_status=OntologyReviewStatus.PROPOSED,
        metadata=metadata,
    )


def _maps_to_edge(
    business_node: OntologyNode,
    physical_node: OntologyNode,
    *,
    revision_id: str,
    provenance: OntologyProvenance,
) -> OntologyEdge:
    return OntologyEdge(
        id=stable_ontology_id("maps_to", business_node.id, physical_node.id),
        revision_id=revision_id,
        kind=OntologyEdgeKind.MAPS_TO,
        source_node_id=business_node.id,
        target_node_id=physical_node.id,
        relationship_name_ja="物理マッピング",
        provenance=provenance,
        review_status=OntologyReviewStatus.PROPOSED,
    )


def _upserts_payload(
    kind: OntologyProposalKind,
    nodes: list[OntologyNode],
    edges: list[OntologyEdge],
) -> OntologyProposalPayload:
    return OntologyProposalPayload(
        kind=kind,
        values={
            "node_upserts": [node.model_dump(mode="json") for node in nodes],
            "edge_upserts": [edge.model_dump(mode="json") for edge in edges],
        },
    )


def _convert_relationship(
    candidate: OntologyRelationshipCandidate,
    resolver: _ScopeResolver,
    *,
    revision_id: str,
    provenance: OntologyProvenance,
    qa_sql_texts: list[str] | None,
    result: _ConversionResult,
) -> None:
    source = resolver.resolve_object(candidate.source_object)
    target = resolver.resolve_object(candidate.target_object)
    if source is None or target is None:
        result.warnings.append(
            f"関係候補 {candidate.source_object} → {candidate.target_object} は "
            "profile 範囲外のため提案化しません。"
        )
        return
    if not candidate.join_conditions:
        result.warnings.append(
            f"関係候補 {candidate.relationship_name_ja} に Join 条件がないため提案化しません。"
        )
        return
    if candidate.cardinality is RelationshipCardinality.UNKNOWN:
        result.warnings.append(
            f"関係候補 {candidate.relationship_name_ja} の cardinality が未確定(unknown)です。"
            "承認前に確認してください。"
        )
    join_conditions: list[JoinCondition] = []
    for ordinal, item in enumerate(candidate.join_conditions, start=1):
        left = resolver.resolve_column(item.left)
        right = resolver.resolve_column(item.right)
        if left is None or right is None:
            result.warnings.append(
                f"関係候補 {candidate.relationship_name_ja} の Join 列 "
                f"({item.left} {item.operator} {item.right}) を profile 範囲内に解決できません。"
            )
            return
        if qa_sql_texts is not None:
            left_column = str(left.metadata.get("column_name", "")).upper()
            right_column = str(right.metadata.get("column_name", "")).upper()
            if not any(left_column in sql and right_column in sql for sql in qa_sql_texts):
                result.warnings.append(
                    f"関係候補 {candidate.relationship_name_ja} の Join 列が Q/A の SQL に "
                    "現れないため提案化しません。"
                )
                return
        join_conditions.append(
            JoinCondition(
                # 検証(BUSINESS_COLUMN_MAPPING_SPOOFED)を通すため列の安定参照を複製する
                left=left.physical_mappings[0].column_refs[0].model_copy(deep=True),
                right=right.physical_mappings[0].column_refs[0].model_copy(deep=True),
                operator=item.operator,
                ordinal=ordinal,
            )
        )
    nodes: list[OntologyNode] = []
    edges: list[OntologyEdge] = []
    endpoints: list[OntologyNode] = []
    for object_node in (source, target):
        business = _business_entity_node(
            object_node,
            revision_id=revision_id,
            business_name_ja=object_node.business_name_ja,
            description_ja=object_node.description_ja,
            aliases=list(object_node.aliases),
            confidence=candidate.confidence,
            provenance=provenance,
            synthetic=True,
        )
        endpoints.append(business)
        nodes.append(business)
        edges.append(
            _maps_to_edge(business, object_node, revision_id=revision_id, provenance=provenance)
        )
    edges.append(
        OntologyEdge(
            id=stable_ontology_id(
                "business_relationship",
                endpoints[0].id,
                endpoints[1].id,
                [f"{item.left}={item.right}" for item in candidate.join_conditions],
            ),
            revision_id=revision_id,
            kind=OntologyEdgeKind.BUSINESS_RELATIONSHIP,
            source_node_id=endpoints[0].id,
            target_node_id=endpoints[1].id,
            relationship_name_ja=candidate.relationship_name_ja,
            description_ja=candidate.evidence_ja,
            cardinality=candidate.cardinality,
            join_conditions=join_conditions,
            provenance=provenance,
            confidence=candidate.confidence,
            review_status=OntologyReviewStatus.PROPOSED,
        )
    )
    result.drafts.append(
        ProposalDraft(
            kind=OntologyProposalKind.RELATIONSHIP,
            title_ja=f"業務関係の提案: {candidate.relationship_name_ja}",
            description_ja=candidate.evidence_ja
            or f"{source.technical_name} と {target.technical_name} の関係候補",
            payload=_upserts_payload(OntologyProposalKind.RELATIONSHIP, nodes, edges),
        )
    )


def _convert_metric(
    candidate: OntologyMetricCandidate,
    resolver: _ScopeResolver,
    *,
    revision_id: str,
    provenance: OntologyProvenance,
    result: _ConversionResult,
) -> None:
    expression_upper = f" {candidate.expression_sql.upper()} "
    if any(token in expression_upper for token in _DANGEROUS_EXPRESSION_TOKENS):
        result.warnings.append(
            f"指標候補 {candidate.metric_name_ja} の式に危険な token が含まれるため提案化しません。"
        )
        return
    column_nodes: list[OntologyNode] = []
    for reference in candidate.base_columns:
        column = resolver.resolve_column(reference)
        if column is None:
            result.warnings.append(
                f"指標候補 {candidate.metric_name_ja} の列 {reference} を "
                "profile 範囲内に解決できません。"
            )
            return
        column_nodes.append(column)
    if not column_nodes:
        result.warnings.append(
            f"指標候補 {candidate.metric_name_ja} に基準列がないため提案化しません。"
        )
        return
    metric_node_id = stable_ontology_id("metric", candidate.metric_name_ja)
    definition = MetricDefinition(
        id=stable_ontology_id("metric_definition", metric_node_id),
        metric_node_id=metric_node_id,
        expression_sql=candidate.expression_sql,
        aggregation=candidate.aggregation,
        base_column_node_ids=[column.id for column in column_nodes],
        unit=candidate.unit,
        description_ja=candidate.description_ja,
    )
    node = OntologyNode(
        id=metric_node_id,
        revision_id=revision_id,
        kind=OntologyNodeKind.METRIC,
        technical_name=candidate.metric_name_ja,
        business_name_ja=candidate.metric_name_ja,
        description_ja=candidate.description_ja,
        physical_mappings=[
            column.physical_mappings[0].model_copy(deep=True) for column in column_nodes
        ],
        provenance=provenance,
        confidence=candidate.confidence,
        review_status=OntologyReviewStatus.PROPOSED,
        metadata={"metric_definition": definition.model_dump(mode="json")},
    )
    result.drafts.append(
        ProposalDraft(
            kind=OntologyProposalKind.METRIC_DEFINITION,
            title_ja=f"指標定義の提案: {candidate.metric_name_ja}",
            description_ja=candidate.evidence_ja or candidate.description_ja,
            payload=_upserts_payload(OntologyProposalKind.METRIC_DEFINITION, [node], []),
        )
    )


def convert_extraction_to_proposals(
    extraction: OntologyBuildExtraction,
    *,
    ontology: SchemaOntology,
    view: ProfileOntologyView,
    job_id: str,
    inferred_by: str,
    qa_sql_texts: list[str] | None = None,
    source_evidence: list[Any] | None = None,
) -> tuple[list[ProposalDraft], list[str]]:
    """検証済み LLM 出力を承認フロー用の proposal 下書きへ決定論変換する。"""

    resolver = _ScopeResolver(ontology, view)
    if qa_sql_texts is not None:
        resolver.register_sql_aliases(qa_sql_texts)
    revision_id = ontology.revision.id
    result = _ConversionResult(warnings=list(extraction.warnings_ja))
    normalized_qa = [sql.upper() for sql in qa_sql_texts] if qa_sql_texts is not None else None

    # 同義語は entity 候補の aliases に合流させる(対象 object が同じもの)。
    alias_by_object: dict[str, list[str]] = {}
    for synonym in extraction.synonyms:
        target_object = resolver.resolve_object(synonym.target)
        if target_object is not None:
            alias_by_object.setdefault(target_object.id, []).extend(synonym.aliases)
            continue
        target_column = resolver.resolve_column(synonym.target)
        if target_column is None:
            result.warnings.append(
                f"同義語候補 {synonym.target} を profile 範囲内に解決できません。"
            )
            continue
        term_node = OntologyNode(
            id=stable_ontology_id("business_term", target_column.id),
            revision_id=revision_id,
            kind=OntologyNodeKind.BUSINESS_TERM,
            technical_name=target_column.technical_name,
            business_name_ja=synonym.aliases[0],
            description_ja=synonym.evidence_ja,
            aliases=synonym.aliases,
            physical_mappings=[target_column.physical_mappings[0].model_copy(deep=True)],
            provenance=_provenance(job_id, inferred_by, synonym.evidence_ja, source_evidence),
            review_status=OntologyReviewStatus.PROPOSED,
        )
        term_edge = _maps_to_edge(
            term_node,
            target_column,
            revision_id=revision_id,
            provenance=_provenance(job_id, inferred_by, synonym.evidence_ja, source_evidence),
        )
        result.drafts.append(
            ProposalDraft(
                kind=OntologyProposalKind.ALIAS,
                title_ja=f"同義語の提案: {synonym.target}",
                description_ja=synonym.evidence_ja or "、".join(synonym.aliases),
                payload=_upserts_payload(OntologyProposalKind.ALIAS, [term_node], [term_edge]),
            )
        )

    for candidate in extraction.entities:
        object_node = resolver.resolve_object(candidate.object_name)
        if object_node is None:
            result.warnings.append(
                f"命名候補 {candidate.object_name} を profile 範囲内に解決できません。"
            )
            continue
        provenance = _provenance(job_id, inferred_by, candidate.description_ja, source_evidence)
        aliases = [*dict.fromkeys([*candidate.aliases, *alias_by_object.pop(object_node.id, [])])]
        business = _business_entity_node(
            object_node,
            revision_id=revision_id,
            business_name_ja=candidate.business_name_ja,
            description_ja=candidate.description_ja,
            aliases=aliases,
            confidence=candidate.confidence,
            provenance=provenance,
            synthetic=False,
        )
        edge = _maps_to_edge(business, object_node, revision_id=revision_id, provenance=provenance)
        result.drafts.append(
            ProposalDraft(
                kind=OntologyProposalKind.MAPPING,
                title_ja=f"業務エンティティ命名: {candidate.business_name_ja}",
                description_ja=candidate.description_ja
                or f"{object_node.technical_name} の業務名候補",
                payload=_upserts_payload(OntologyProposalKind.MAPPING, [business], [edge]),
            )
        )

    # entity 候補に合流できなかった同義語(object 対象)は alias 専用の提案にする。
    for object_id, aliases in alias_by_object.items():
        object_node = next(
            (node for node in resolver.objects.values() if node.id == object_id), None
        )
        if object_node is None:
            continue
        provenance = _provenance(job_id, inferred_by, "同義語の提案", source_evidence)
        business = _business_entity_node(
            object_node,
            revision_id=revision_id,
            business_name_ja=object_node.business_name_ja,
            description_ja=object_node.description_ja,
            aliases=[*dict.fromkeys(aliases)],
            confidence=0.6,
            provenance=provenance,
            synthetic=False,
        )
        edge = _maps_to_edge(business, object_node, revision_id=revision_id, provenance=provenance)
        result.drafts.append(
            ProposalDraft(
                kind=OntologyProposalKind.ALIAS,
                title_ja=f"同義語の提案: {object_node.technical_name}",
                description_ja="、".join(dict.fromkeys(aliases)),
                payload=_upserts_payload(OntologyProposalKind.ALIAS, [business], [edge]),
            )
        )

    for relationship in extraction.relationships:
        _convert_relationship(
            relationship,
            resolver,
            revision_id=revision_id,
            provenance=_provenance(job_id, inferred_by, relationship.evidence_ja, source_evidence),
            qa_sql_texts=normalized_qa,
            result=result,
        )

    for metric in extraction.metrics:
        _convert_metric(
            metric,
            resolver,
            revision_id=revision_id,
            provenance=_provenance(job_id, inferred_by, metric.evidence_ja, source_evidence),
            result=result,
        )

    return result.drafts, result.warnings


# --- Markdown output --------------------------------------------------------------------------


def _md_text(value: Any, fallback: str = "未設定") -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def _md_code(value: Any, fallback: str = "未設定") -> str:
    text = _md_text(value, fallback).replace("`", "'")
    return f"`{text}`"


def _qa_pair_from_markdown_payload(payload: object) -> QaPair | None:
    if not isinstance(payload, Mapping):
        return None
    question = str(payload.get("question") or "").strip()
    sql = str(payload.get("sql") or "").strip()
    note_ja = str(payload.get("note_ja") or "").strip()
    if not question or not sql:
        return None
    if sql.split(None, 1)[0].upper() not in {"SELECT", "WITH"}:
        return None
    return QaPair(question=question, sql=sql, note_ja=note_ja)


def _qa_sql_example_markdown_lines(qa_pairs: Sequence[QaPair]) -> list[str]:
    deduped: list[QaPair] = []
    seen: set[tuple[str, str]] = set()
    for pair in qa_pairs:
        question = pair.question.strip()
        sql = pair.sql.strip()
        if not question or not sql:
            continue
        key = (question, sql)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(QaPair(question=question, sql=sql, note_ja=pair.note_ja.strip()))
    if not deduped:
        return ["- なし"]
    lines = ["```jsonl"]
    for pair in deduped:
        lines.append(
            json.dumps(
                pair.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    lines.append("```")
    return lines


def qa_sql_examples_from_markdown(markdown: str) -> list[QaPair]:
    """Published Markdown に保存された Q/A SQL 例を、指示ではなくデータとして復元する。"""

    examples: list[QaPair] = []
    seen: set[tuple[str, str]] = set()
    for match in _QA_SQL_EXAMPLE_BLOCK_RE.finditer(markdown):
        body = match.group("body").strip()
        if not body:
            continue
        payloads: list[object]
        if body.startswith("["):
            try:
                loaded = json.loads(body)
            except json.JSONDecodeError:
                continue
            payloads = loaded if isinstance(loaded, list) else []
        else:
            payloads = []
            for line in body.splitlines():
                cleaned = line.strip()
                if not cleaned:
                    continue
                try:
                    payloads.append(json.loads(cleaned))
                except json.JSONDecodeError:
                    continue
        for payload in payloads:
            pair = _qa_pair_from_markdown_payload(payload)
            if pair is None:
                continue
            key = (pair.question, pair.sql)
            if key in seen:
                continue
            seen.add(key)
            examples.append(pair)
    return examples


def _normalize_question_for_example_match(value: str) -> str:
    return "".join(character.lower() for character in value if not character.isspace())


def _question_ngrams(value: str) -> set[str]:
    normalized = _normalize_question_for_example_match(value)
    if not normalized:
        return set()
    if len(normalized) == 1:
        return {normalized}
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _question_example_score(question: str, example_question: str) -> float:
    left = _normalize_question_for_example_match(question)
    right = _normalize_question_for_example_match(example_question)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.92
    left_terms = _question_ngrams(left)
    right_terms = _question_ngrams(right)
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def select_qa_sql_examples_from_markdown(
    markdown: str,
    question: str,
    *,
    limit: int = _QA_SQL_EXAMPLE_MAX_PROMPT_COUNT,
    min_score: float = 0.2,
) -> list[QaPair]:
    """現在の質問に近い Q/A SQL 例だけを SQL 生成 context 用に選ぶ。"""

    ranked: list[tuple[float, int, QaPair]] = []
    for index, pair in enumerate(qa_sql_examples_from_markdown(markdown)):
        score = _question_example_score(question, pair.question)
        if score >= min_score:
            ranked.append((score, index, pair))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [pair for _score, _index, pair in ranked[: max(0, limit)]]


def _unique_values(values: Sequence[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _qa_sql_pattern_from_pair(pair: QaPair) -> dict[str, Any]:
    try:
        analysis = parse_oracle_sql(
            pair.sql,
            intent_version=1,
            ontology_revision_id="qa_sql_pattern",
        )
    except Exception as exc:
        return {
            "question": pair.question,
            "parse_complete": False,
            "warnings_ja": [f"SQL 構造を解析できませんでした: {type(exc).__name__}"],
        }
    graph = analysis.graph
    if graph is None:
        return {
            "question": pair.question,
            "parse_complete": False,
            "warnings_ja": [finding.message_ja for finding in analysis.validation.findings[:5]],
        }
    return {
        "question": pair.question,
        "statement_type": graph.statement_type,
        "physical_tables": _unique_values(
            [
                table.qualified_name or table.name
                for table in graph.tables
                if not table.is_cte and (table.qualified_name or table.name)
            ],
            limit=20,
        ),
        "cte_names": _unique_values([cte.name for cte in graph.ctes], limit=20),
        "set_operations": _unique_values(
            [operation.operator for operation in graph.set_operations],
            limit=20,
        ),
        "join_conditions": _unique_values(
            [join.condition_sql for join in graph.joins if join.condition_sql],
            limit=40,
        ),
        "filters": _unique_values(
            [predicate.expression_sql for predicate in [*graph.filters, *graph.having]],
            limit=40,
        ),
        "projections": _unique_values(
            [projection.expression_sql for projection in graph.projections],
            limit=40,
        ),
        "aggregates": _unique_values(
            [aggregate.expression_sql for aggregate in graph.aggregates],
            limit=20,
        ),
        "group_by": _unique_values([group.expression_sql for group in graph.groups], limit=20),
        "order_by": _unique_values(
            [f"{order.expression_sql} {order.direction}".strip() for order in graph.orders],
            limit=20,
        ),
    }


def qa_sql_patterns_from_pairs(
    pairs: Sequence[QaPair],
    *,
    limit: int = _QA_SQL_PATTERN_MAX_PROMPT_COUNT,
) -> list[dict[str, Any]]:
    return [_qa_sql_pattern_from_pair(pair) for pair in pairs[: max(0, limit)]]


def _qa_sql_pattern_markdown_lines(qa_pairs: Sequence[QaPair]) -> list[str]:
    patterns = qa_sql_patterns_from_pairs(qa_pairs, limit=20)
    if not patterns:
        return ["- なし"]
    lines = ["```jsonl"]
    for pattern in patterns:
        lines.append(json.dumps(pattern, ensure_ascii=False, separators=(",", ":")))
    lines.append("```")
    return lines


def _draft_nodes(draft: ProposalDraft) -> list[OntologyNode]:
    nodes: list[OntologyNode] = []
    for value in draft.payload.values.get("node_upserts") or []:
        try:
            nodes.append(OntologyNode.model_validate(value))
        except Exception:  # nosec B112
            continue
    return nodes


def _draft_edges(draft: ProposalDraft) -> list[OntologyEdge]:
    edges: list[OntologyEdge] = []
    for value in draft.payload.values.get("edge_upserts") or []:
        try:
            edges.append(OntologyEdge.model_validate(value))
        except Exception:  # nosec B112
            continue
    return edges


def _physical_mapping_label(node: OntologyNode) -> str:
    if not node.physical_mappings:
        return node.technical_name or node.id
    mapping = node.physical_mappings[0]
    if mapping.column_refs:
        return _column_ref_label(mapping.column_refs[0])
    ref = mapping.object_ref
    return f"{ref.owner}.{ref.object_name}" if ref.owner else ref.object_name


def _profile_node_override_map(view: ProfileOntologyView | None) -> dict[str, dict[str, Any]]:
    if view is None:
        return {}
    return {
        str(item.get("node_id") or ""): dict(item)
        for item in view.draft_node_overrides
        if str(item.get("node_id") or "")
    }


def _profile_edge_override_map(view: ProfileOntologyView | None) -> dict[str, dict[str, Any]]:
    if view is None:
        return {}
    return {
        str(item.get("edge_id") or ""): dict(item)
        for item in view.draft_edge_overrides
        if str(item.get("edge_id") or "")
    }


def _effective_business_name(node: OntologyNode, view: ProfileOntologyView | None) -> str:
    override = _profile_node_override_map(view).get(node.id, {})
    return _md_text(override.get("business_name_ja") or node.business_name_ja, node.id)


def _effective_table_usage(node: OntologyNode, view: ProfileOntologyView | None) -> str:
    if view is None:
        return ""
    override = _profile_node_override_map(view).get(node.id, {})
    usage = str(override.get("table_usage") or view.table_usages_ja.get(node.id, "") or "")
    return usage.strip()


def _effective_edge_cardinality(edge: OntologyEdge, view: ProfileOntologyView | None) -> str:
    override = _profile_edge_override_map(view).get(edge.id, {})
    cardinality = override.get("cardinality") or edge.cardinality.value
    return str(cardinality or "unknown")


def _effective_edge_allowed(edge: OntologyEdge, view: ProfileOntologyView | None) -> bool:
    # OntologyEdge に enabled 属性は存在しない(extra=forbid のため参照すると
    # AttributeError)。view 無し = 共有 Ontology 全体の export なので常に許可、
    # view 有りは override > allowed_path_ids の順で判定する。
    if view is None:
        return True
    override = _profile_edge_override_map(view).get(edge.id, {})
    if "allowed_path" in override:
        return bool(override.get("allowed_path"))
    return edge.id in set(view.allowed_path_ids)


def _node_markdown_lines(node: OntologyNode, view: ProfileOntologyView | None) -> list[str]:
    aliases = ", ".join(sorted(set(node.aliases))) or "なし"
    usage = _effective_table_usage(node, view)
    lines = [
        f"- {_effective_business_name(node, view)} ({_md_code(_physical_mapping_label(node))})",
        f"  - 種別: {node.kind.value}",
        f"  - 説明: {_md_text(node.description_ja)}",
        f"  - 別名: {aliases}",
    ]
    if usage:
        lines.append(f"  - 用途: {_md_text(usage)}")
    if node.provenance.evidence:
        lines.append("  - 証拠:")
        for evidence in node.provenance.evidence[:5]:
            # OntologyEvidence の実フィールドは excerpt_ja / locator / source_document_id。
            # (旧 label/location/source_id は存在せず AttributeError になっていた)
            label = str(
                evidence.excerpt_ja or evidence.locator or evidence.source_document_id or ""
            ).strip()
            if label:
                lines.append(f"    - {_md_text(label)}")
    return lines


def _edge_markdown_lines(
    edge: OntologyEdge,
    *,
    node_by_id: dict[str, OntologyNode],
    view: ProfileOntologyView | None,
) -> list[str]:
    source = node_by_id.get(edge.source_node_id)
    target = node_by_id.get(edge.target_node_id)
    source_label = (
        f"{_effective_business_name(source, view)} ({_md_code(_physical_mapping_label(source))})"
        if source is not None
        else _md_code(edge.source_node_id)
    )
    target_label = (
        f"{_effective_business_name(target, view)} ({_md_code(_physical_mapping_label(target))})"
        if target is not None
        else _md_code(edge.target_node_id)
    )
    lines = [
        f"- {source_label} → {target_label}: {_md_text(edge.relationship_name_ja)}",
        f"  - 種別: {edge.kind.value}",
        f"  - 多重度: {_effective_edge_cardinality(edge, view)}",
        f"  - 検索利用: {'利用可' if _effective_edge_allowed(edge, view) else '利用不可'}",
        f"  - 証拠: {_md_text(edge.description_ja)}",
    ]
    if edge.join_conditions:
        lines.append("  - Join 条件:")
        lines.extend(
            f"    - {_md_code(_join_condition_label(condition))}"
            for condition in sorted(edge.join_conditions, key=lambda item: item.ordinal)
        )
    return lines


def _physical_object_lines(
    *,
    schema_objects: list[Any],
    ontology: SchemaOntology | None,
    view: ProfileOntologyView | None,
) -> list[str]:
    if ontology is not None and view is not None and view.physical_objects:
        node_by_id = {node.id: node for node in ontology.nodes}
        lines: list[str] = []
        for ref in sorted(
            view.physical_objects,
            key=lambda item: (item.owner, item.object_name, item.object_type),
        ):
            node = node_by_id.get(ref.node_id)
            business_name = _effective_business_name(node, view) if node else ref.object_name
            description = _md_text(node.description_ja if node else "")
            usage = _effective_table_usage(node, view) if node else ""
            lines.extend(
                [
                    f"- {_md_code(f'{ref.owner}.{ref.object_name}')} ({ref.object_type})",
                    f"  - 業務名: {_md_text(business_name)}",
                    f"  - 説明: {description}",
                    f"  - 用途: {_md_text(usage, '未設定')}",
                ]
            )
        return lines

    lines = []
    for item in schema_objects:
        if not isinstance(item, dict):
            continue
        object_name = str(item.get("object") or item.get("object_name") or "")
        object_type = str(item.get("object_type") or "")
        logical_name = str(item.get("logical_name") or "")
        comment = str(item.get("comment") or "")
        raw_columns = item.get("columns")
        columns: list[Any] = raw_columns if isinstance(raw_columns, list) else []
        lines.extend(
            [
                f"- {_md_code(object_name)} ({_md_text(object_type)})",
                f"  - 業務名: {_md_text(logical_name)}",
                f"  - 説明: {_md_text(comment)}",
                f"  - 列数: {len(columns)}",
            ]
        )
    return lines


def _profile_entity_lines(
    ontology: SchemaOntology | None,
    view: ProfileOntologyView | None,
) -> list[str]:
    if ontology is None or view is None:
        return []
    scoped = set(view.node_ids)
    return [
        line
        for node in sorted(ontology.nodes, key=lambda item: (item.kind.value, item.id))
        if node.id in scoped
        and node.kind
        in {
            OntologyNodeKind.TABLE,
            OntologyNodeKind.VIEW,
            OntologyNodeKind.BUSINESS_ENTITY,
            OntologyNodeKind.BUSINESS_TERM,
        }
        for line in _node_markdown_lines(node, view)
    ]


def _profile_relationship_lines(
    ontology: SchemaOntology | None,
    view: ProfileOntologyView | None,
) -> list[str]:
    if ontology is None or view is None:
        return []
    scoped = set(view.edge_ids)
    node_by_id = {node.id: node for node in ontology.nodes}
    return [
        line
        for edge in sorted(ontology.edges, key=lambda item: (item.kind.value, item.id))
        if edge.id in scoped
        and edge.kind
        in {
            OntologyEdgeKind.FOREIGN_KEY,
            OntologyEdgeKind.BUSINESS_RELATIONSHIP,
            OntologyEdgeKind.JOINS,
        }
        for line in _edge_markdown_lines(edge, node_by_id=node_by_id, view=view)
    ]


def _profile_metric_lines(
    ontology: SchemaOntology | None,
    view: ProfileOntologyView | None,
) -> list[str]:
    if ontology is None or view is None:
        return []
    scoped = set(view.node_ids)
    lines: list[str] = []
    for node in sorted(ontology.nodes, key=lambda item: (item.kind.value, item.id)):
        if node.id not in scoped:
            continue
        if node.kind == OntologyNodeKind.METRIC:
            lines.extend(_node_markdown_lines(node, view))
            definition = node.metadata.get("metric_definition")
            if isinstance(definition, dict):
                lines.append(f"  - 定義 SQL: {_md_code(definition.get('expression_sql'))}")
                lines.append(f"  - 集計: {_md_text(definition.get('aggregation'))}")
                lines.append(f"  - 単位: {_md_text(definition.get('unit'), 'なし')}")
    return lines


def _profile_rule_enum_lines(
    ontology: SchemaOntology | None,
    view: ProfileOntologyView | None,
) -> list[str]:
    if ontology is None or view is None:
        return []
    scoped = set(view.node_ids)
    node_by_id = {node.id: node for node in ontology.nodes}
    lines: list[str] = []
    for node in sorted(ontology.nodes, key=lambda item: (item.kind.value, item.id)):
        if node.id not in scoped:
            continue
        if node.business_rule_definition is not None:
            rule = node.business_rule_definition
            applies_to = ", ".join(
                _effective_business_name(target, view)
                for target_id in rule.applies_to_node_ids
                if (target := node_by_id.get(target_id)) is not None
            )
            lines.extend(
                [
                    f"- {_effective_business_name(node, view)}",
                    "  - 種別: 業務ルール",
                    f"  - ルール文: {_md_text(rule.statement_ja)}",
                    f"  - 適用対象: {_md_text(applies_to, '未設定')}",
                    f"  - 重大度: {rule.severity.value}",
                    f"  - 実行方式: {rule.execution_mode.value}",
                ]
            )
        elif node.enum_value_definition is not None:
            enum_value = node.enum_value_definition
            property_node = node_by_id.get(enum_value.property_node_id)
            property_label = (
                _effective_business_name(property_node, view)
                if property_node is not None
                else enum_value.property_node_id
            )
            lines.extend(
                [
                    f"- {_effective_business_name(node, view)}",
                    "  - 種別: 列挙値",
                    f"  - コード: {_md_text(enum_value.code)}",
                    f"  - 物理値: {_md_code(enum_value.physical_literal)}",
                    f"  - 表示名: {_md_text(enum_value.label_ja)}",
                    f"  - 属性: {_md_text(property_label)}",
                    f"  - 別名: {', '.join(enum_value.aliases) or 'なし'}",
                ]
            )
    return lines


def render_ontology_build_markdown(
    *,
    profile_id: str,
    schema_context: str,
    drafts: list[ProposalDraft],
    warnings: list[str],
    source_count: int,
    qa_pair_count: int,
    business_text_present: bool,
    qa_pairs: list[QaPair] | None = None,
    ontology: SchemaOntology | None = None,
    profile_view: ProfileOntologyView | None = None,
) -> str:
    """AI 構築結果から、確認・編集用 Markdown 下書きを決定論生成する。"""

    try:
        schema = json.loads(schema_context)
    except Exception:
        schema = {}
    objects = schema.get("objects") if isinstance(schema, dict) else []
    relationships = schema.get("relationships") if isinstance(schema, dict) else []
    object_count = len(objects) if isinstance(objects, list) else 0
    column_count = (
        sum(len(item.get("columns") or []) for item in objects if isinstance(item, dict))
        if isinstance(objects, list)
        else 0
    )
    relationship_count = len(relationships) if isinstance(relationships, list) else 0
    physical_lines = _physical_object_lines(
        schema_objects=objects if isinstance(objects, list) else [],
        ontology=ontology,
        view=profile_view,
    )
    profile_entity_lines = _profile_entity_lines(ontology, profile_view)
    profile_relationship_lines = _profile_relationship_lines(ontology, profile_view)
    profile_metric_lines = _profile_metric_lines(ontology, profile_view)
    profile_rule_enum_lines = _profile_rule_enum_lines(ontology, profile_view)

    lines = [
        "# オントロジー下書き",
        "",
        "## 入力サマリー",
        f"- プロファイル: {_md_code(profile_id)}",
        f"- 業務説明: {'あり' if business_text_present else 'なし'}",
        f"- Q/A 件数: {qa_pair_count}",
        f"- 構築資料: {source_count}",
        f"- DB スキーマオブジェクト: {object_count}",
        f"- DB スキーマ列: {column_count}",
        f"- 既存スキーマ関係: {relationship_count}",
        "",
        _QA_SQL_EXAMPLE_SECTION_TITLE,
    ]
    lines.extend(_qa_sql_example_markdown_lines(qa_pairs or []))
    lines.extend(["", _QA_SQL_PATTERN_SECTION_TITLE])
    lines.extend(_qa_sql_pattern_markdown_lines(qa_pairs or []))
    lines.extend(["", "## 物理オブジェクト"])
    lines.extend(physical_lines or ["- なし"])
    lines.extend(
        [
            "",
            "## 業務エンティティ",
        ]
    )

    entity_lines: list[str] = []
    relationship_lines: list[str] = []
    metric_lines: list[str] = []
    synonym_lines: list[str] = []

    for draft in drafts:
        nodes = _draft_nodes(draft)
        edges = _draft_edges(draft)
        if draft.kind == OntologyProposalKind.MAPPING:
            for node in nodes:
                if node.kind != OntologyNodeKind.BUSINESS_ENTITY:
                    continue
                aliases = ", ".join(sorted(set(node.aliases))) or "なし"
                entity_lines.extend(
                    [
                        f"- {node.business_name_ja} ({_md_code(_physical_mapping_label(node))})",
                        f"  - 説明: {_md_text(node.description_ja)}",
                        f"  - 別名: {aliases}",
                        f"  - 信頼度: {node.confidence:.2f}",
                    ]
                )
                if node.aliases:
                    synonym_lines.extend(
                        [
                            f"- 対象: {_md_code(_physical_mapping_label(node))}",
                            f"  - 別名: {aliases}",
                            f"  - 証拠: {_md_text(node.description_ja)}",
                        ]
                    )
        elif draft.kind == OntologyProposalKind.RELATIONSHIP:
            node_by_id = {node.id: node for node in nodes}
            for edge in edges:
                if edge.kind != OntologyEdgeKind.BUSINESS_RELATIONSHIP:
                    continue
                source = node_by_id.get(edge.source_node_id)
                target = node_by_id.get(edge.target_node_id)
                source_label = (
                    f"{source.business_name_ja} ({_md_code(_physical_mapping_label(source))})"
                    if source is not None
                    else _md_code(edge.source_node_id)
                )
                target_label = (
                    f"{target.business_name_ja} ({_md_code(_physical_mapping_label(target))})"
                    if target is not None
                    else _md_code(edge.target_node_id)
                )
                relationship_lines.extend(
                    [
                        f"- {source_label} → {target_label}: {edge.relationship_name_ja}",
                        f"  - 多重度: {edge.cardinality.value}",
                        f"  - 証拠: {_md_text(edge.description_ja)}",
                        f"  - 信頼度: {edge.confidence:.2f}",
                    ]
                )
                if edge.join_conditions:
                    relationship_lines.append("  - Join 条件:")
                    relationship_lines.extend(
                        f"    - {_md_code(_join_condition_label(condition))}"
                        for condition in sorted(edge.join_conditions, key=lambda item: item.ordinal)
                    )
        elif draft.kind == OntologyProposalKind.METRIC_DEFINITION:
            for node in nodes:
                if node.kind != OntologyNodeKind.METRIC:
                    continue
                metric_definition = node.metadata.get("metric_definition")
                expression = ""
                aggregation = ""
                unit = ""
                if isinstance(metric_definition, dict):
                    expression = str(metric_definition.get("expression_sql") or "")
                    aggregation = str(metric_definition.get("aggregation") or "")
                    unit = str(metric_definition.get("unit") or "")
                base_columns = ", ".join(
                    _md_code(_column_ref_label(column_ref))
                    for mapping in node.physical_mappings
                    for column_ref in mapping.column_refs
                )
                metric_lines.extend(
                    [
                        f"- {node.business_name_ja}",
                        f"  - 定義 SQL: {_md_code(expression)}",
                        f"  - 元列: {base_columns or 'なし'}",
                        f"  - 集計: {_md_text(aggregation)}",
                        f"  - 単位: {_md_text(unit, 'なし')}",
                        f"  - 説明: {_md_text(node.description_ja)}",
                        f"  - 信頼度: {node.confidence:.2f}",
                    ]
                )
        elif draft.kind == OntologyProposalKind.ALIAS:
            for node in nodes:
                aliases = ", ".join(sorted(set(node.aliases))) or "なし"
                synonym_lines.extend(
                    [
                        f"- 対象: {_md_code(_physical_mapping_label(node))}",
                        f"  - 別名: {aliases}",
                        f"  - 証拠: {_md_text(node.description_ja)}",
                    ]
                )

    merged_entity_lines = [*profile_entity_lines, *entity_lines]
    merged_relationship_lines = [*profile_relationship_lines, *relationship_lines]
    lines.extend(merged_entity_lines or ["- なし"])
    lines.extend(["", "## 関係 / Join"])
    lines.extend(merged_relationship_lines or ["- なし"])
    lines.extend(["", "## 指標"])
    lines.extend([*profile_metric_lines, *metric_lines] or ["- なし"])
    lines.extend(["", "## 業務ルール / 列挙値"])
    lines.extend(profile_rule_enum_lines or ["- なし"])
    lines.extend(["", "## 同義語"])
    lines.extend(synonym_lines or ["- なし"])
    lines.extend(["", "## 証拠 / 警告"])
    unique_warnings = list(dict.fromkeys(warning for warning in warnings if warning.strip()))
    lines.extend(f"- {warning}" for warning in unique_warnings)
    if not unique_warnings:
        lines.append("- なし")
    return "\n".join(lines).rstrip() + "\n"


# --- LLM 呼び出し -----------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = (
    "あなたは NL2SQL 用オントロジーの構築支援器です。JSON オブジェクトだけを返し、"
    "説明文や Markdown を付けないでください。返す JSON は次の形式です: "
    '{"entities": [{"object_name": "OWNER.OBJECT", "business_name_ja": "...", '
    '"description_ja": "...", "aliases": ["..."], "confidence": 0.0}], '
    '"relationships": [{"source_object": "OWNER.OBJECT", "target_object": "OWNER.OBJECT", '
    '"relationship_name_ja": "...", "cardinality": "many_to_one", '
    '"join_conditions": [{"left": "OWNER.OBJECT.COLUMN", "right": "OWNER.OBJECT.COLUMN", '
    '"operator": "="}], "evidence_ja": "...", "confidence": 0.0}], '
    '"metrics": [{"metric_name_ja": "...", "expression_sql": "SUM(OWNER.OBJECT.COLUMN)", '
    '"aggregation": "sum", "base_columns": ["OWNER.OBJECT.COLUMN"], "unit": "", '
    '"description_ja": "...", "evidence_ja": "...", "confidence": 0.0}], '
    '"synonyms": [{"target": "OWNER.OBJECT", "aliases": ["..."], "evidence_ja": "..."}], '
    '"warnings_ja": ["..."]} '
    "。schema_context に存在しない owner/object/column を参照しないでください。"
    "qa_pairs に schema_resolved_columns / schema_resolved_join_conditions がある場合は、"
    "SQL の alias 表記ではなく、その正規化済み参照を使ってください。"
    "抽出ルール: (1) 業務文中の名詞をエンティティ候補、動詞・述語を関係候補として抽出する。"
    "(2) 各関係の cardinality は one_to_one / one_to_many / many_to_one / many_to_many から"
    "必ず選ぶ。判断できない場合のみ unknown とし、理由を warnings_ja に 1 行残す。"
    "(3) 各エンティティの主識別子(主キーに相当する列)を description_ja に明記する。"
    "(4) 各エンティティ・同義語には、利用者が質問で使いそうな言い回し"
    "(短縮形・ひらがな表記・別表記・現場用語)を 2〜5 個 aliases として提案する。"
    "確信が持てない候補は confidence を下げるか warnings_ja に残してください。"
    "出力に含める業務名・説明・証拠・警告・同義語の文言はすべて日本語にしてください。"
    "汎用的な英語ラベルや説明文をそのまま出さないでください。"
    # schema-guided few-shot(1 例)。研究では few-shot 付き schema 誘導が最高精度。
    "\n\n例 — schema_context: "
    '{"objects":[{"owner":"APP","object_name":"ORDERS",'
    '"columns":["ORDER_ID","CUSTOMER_ID","AMOUNT"],'
    '"constraints":[{"type":"P","columns":["ORDER_ID"]},'
    '{"type":"R","columns":["CUSTOMER_ID"],"references":"APP.CUSTOMERS"}]},'
    '{"owner":"APP","object_name":"CUSTOMERS",'
    '"columns":["CUSTOMER_ID","CUSTOMER_NAME"],'
    '"constraints":[{"type":"P","columns":["CUSTOMER_ID"]}]}]} '
    "/ 業務文:「受注は顧客に紐づく。売上は確定済み受注の受注金額の合計。」"
    "に対する期待出力: "
    '{"entities":[{"object_name":"APP.ORDERS","business_name_ja":"受注",'
    '"description_ja":"顧客からの受注。主識別子は ORDER_ID。",'
    '"aliases":["注文","オーダー","じゅちゅう"],"confidence":0.9}],'
    '"relationships":[{"source_object":"APP.ORDERS","target_object":"APP.CUSTOMERS",'
    '"relationship_name_ja":"顧客に紐づく","cardinality":"many_to_one",'
    '"join_conditions":[{"left":"APP.ORDERS.CUSTOMER_ID",'
    '"right":"APP.CUSTOMERS.CUSTOMER_ID","operator":"="}],'
    '"evidence_ja":"受注は顧客に紐づく","confidence":0.85}],'
    '"metrics":[{"metric_name_ja":"売上","expression_sql":"SUM(APP.ORDERS.AMOUNT)",'
    '"aggregation":"sum","base_columns":["APP.ORDERS.AMOUNT"],"unit":"円",'
    '"description_ja":"確定済み受注の受注金額の合計",'
    '"evidence_ja":"売上は確定済み受注の受注金額の合計","confidence":0.8}],'
    '"synonyms":[{"target":"APP.CUSTOMERS","aliases":["得意先","客先","顧客"],'
    '"evidence_ja":"業務慣用表現"}],"warnings_ja":[]}'
)


def _proposal_payload_key(kind_value: str, values: dict[str, Any]) -> str:
    """提案の同一性判定キー(kind + 安定 node/edge ID)。実行を跨いだ dedup に使う。"""

    return json.dumps(
        {
            "kind": kind_value,
            "nodes": sorted(str(node["id"]) for node in values.get("node_upserts") or []),
            "edges": sorted(str(edge["id"]) for edge in values.get("edge_upserts") or []),
        },
        sort_keys=True,
    )


def merge_build_extractions(
    base: OntologyBuildExtraction,
    addition: OntologyBuildExtraction,
) -> tuple[OntologyBuildExtraction, int]:
    """gleaning パスの追加抽出を key ベースで重複排除しつつ結合する。

    戻り値は (結合結果, 新規に追加された候補数)。
    """

    added = 0
    entities = list(base.entities)
    entity_keys = {candidate.object_name.strip().upper() for candidate in entities}
    for candidate in addition.entities:
        key = candidate.object_name.strip().upper()
        if key in entity_keys:
            continue
        entity_keys.add(key)
        entities.append(candidate)
        added += 1

    relationships = list(base.relationships)
    relationship_keys = {
        (
            relationship.source_object.strip().upper(),
            relationship.target_object.strip().upper(),
            relationship.relationship_name_ja.strip(),
        )
        for relationship in relationships
    }
    for relationship in addition.relationships:
        relationship_key = (
            relationship.source_object.strip().upper(),
            relationship.target_object.strip().upper(),
            relationship.relationship_name_ja.strip(),
        )
        if relationship_key in relationship_keys:
            continue
        relationship_keys.add(relationship_key)
        relationships.append(relationship)
        added += 1

    metrics = list(base.metrics)
    metric_keys = {metric.metric_name_ja.strip() for metric in metrics}
    for metric in addition.metrics:
        metric_key = metric.metric_name_ja.strip()
        if metric_key in metric_keys:
            continue
        metric_keys.add(metric_key)
        metrics.append(metric)
        added += 1

    synonyms = list(base.synonyms)
    synonym_keys = {synonym.target.strip().upper() for synonym in synonyms}
    for synonym in addition.synonyms:
        synonym_key = synonym.target.strip().upper()
        if synonym_key in synonym_keys:
            continue
        synonym_keys.add(synonym_key)
        synonyms.append(synonym)
        added += 1

    warnings_ja = [*base.warnings_ja]
    for warning in addition.warnings_ja:
        if warning not in warnings_ja:
            warnings_ja.append(warning)

    return (
        OntologyBuildExtraction(
            entities=entities,
            relationships=relationships,
            metrics=metrics,
            synonyms=synonyms,
            warnings_ja=warnings_ja,
        ),
        added,
    )


def parse_extraction(raw: str) -> OntologyBuildExtraction:
    """LLM 応答から JSON 部分を抽出し、契約 schema で検証する。"""

    cleaned = str(raw).strip()
    if "{" in cleaned and "}" in cleaned:
        cleaned = cleaned[cleaned.find("{") : cleaned.rfind("}") + 1]
    return OntologyBuildExtraction.model_validate(json.loads(cleaned))


class OntologyBuildContextBudgetError(ValueError):
    """LLM 入力を省略せずに分割しても context 予算へ収まらない。"""


@dataclass(frozen=True)
class _BuildTextUnit:
    source_document: OntologySourceDocument | None
    source_label: str
    locator_kind: OntologyEvidenceLocatorKind
    locator: str
    text: str

    def context_payload(self) -> dict[str, str]:
        return {
            "source": self.source_label,
            "locator_kind": self.locator_kind.value,
            "locator": self.locator,
            "text": self.text,
        }

    def evidence(self) -> OntologyEvidence | None:
        if self.source_document is None:
            return None
        return ExtractedSourceChunk(
            self.text,
            self.locator_kind,
            self.locator,
        ).evidence(self.source_document)


@dataclass(frozen=True)
class _OntologyBuildLlmTask:
    name: OntologyBuildStepName
    prompt: str
    context: str
    progress_ja: str
    cross_check_sql: list[str] | None = None
    source_evidence: list[OntologyEvidence] = field(default_factory=list)
    # 二分割リトライ用の元 batch(QA/TEXT のみ)。schema_naming は分割不可。
    qa_batch: list[QaPair] | None = None
    text_batch: list[_BuildTextUnit] | None = None
    schema_payload: dict[str, Any] | None = None

    def split(self) -> list[_OntologyBuildLlmTask] | None:
        """batch を二分割した子タスクを返す(分割不能・要素 1 件以下は None)。"""

        if self.schema_payload is None:
            return None
        if self.qa_batch is not None and len(self.qa_batch) > 1:
            middle = len(self.qa_batch) // 2
            qa_halves = [self.qa_batch[:middle], self.qa_batch[middle:]]
            return [
                _OntologyBuildLlmTask(
                    name=self.name,
                    prompt=self.prompt,
                    context=_dump_qa_context(self.schema_payload, half),
                    progress_ja=self.progress_ja,
                    cross_check_sql=[pair.sql for pair in half],
                    qa_batch=half,
                    schema_payload=self.schema_payload,
                )
                for half in qa_halves
            ]
        if self.text_batch is not None and len(self.text_batch) > 1:
            middle = len(self.text_batch) // 2
            text_halves = [self.text_batch[:middle], self.text_batch[middle:]]
            return [
                _OntologyBuildLlmTask(
                    name=self.name,
                    prompt=self.prompt,
                    context=_dump_text_context(self.schema_payload, text_half),
                    progress_ja=self.progress_ja,
                    source_evidence=[
                        evidence for unit in text_half if (evidence := unit.evidence()) is not None
                    ],
                    text_batch=text_half,
                    schema_payload=self.schema_payload,
                )
                for text_half in text_halves
            ]
        return None


@dataclass(frozen=True)
class _ValidatedBuildExtraction:
    name: OntologyBuildStepName
    label_ja: str
    extraction: OntologyBuildExtraction
    cross_check_sql: list[str] | None
    source_evidence: list[OntologyEvidence]


def _llm_call_chars(prompt: str, context: str) -> int:
    return len(_EXTRACTION_SYSTEM_PROMPT) + len(prompt) + len(context) + _LLM_CONTEXT_HEADROOM_CHARS


def _ensure_llm_call_fits(prompt: str, context: str) -> None:
    if _llm_call_chars(prompt, context) <= _ONTOLOGY_BUILD_LLM_CONTEXT_MAX_CHARS:
        return
    raise OntologyBuildContextBudgetError(
        "LLM 入力が上限を超えています。Profile の対象 object を減らすか、"
        "単一行・単一ページ・単一セルの内容を分割して再実行してください。"
    )


def _schema_context_payload(schema_context: str) -> dict[str, Any]:
    try:
        loaded = json.loads(schema_context)
    except Exception as exc:
        raise OntologyBuildContextBudgetError("schema_context を解析できません。") from exc
    if not isinstance(loaded, dict):
        raise OntologyBuildContextBudgetError("schema_context が不正です。")
    return loaded


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


@dataclass(frozen=True)
class _SchemaContextLookup:
    objects: dict[str, str]
    objects_by_name: dict[str, tuple[str, ...]]
    columns: dict[str, str]
    columns_by_name: dict[str, tuple[str, ...]]

    @classmethod
    def from_payload(cls, schema_context: Mapping[str, Any]) -> _SchemaContextLookup:
        objects: dict[str, str] = {}
        objects_by_name: dict[str, list[str]] = {}
        columns: dict[str, str] = {}
        columns_by_name: dict[str, list[str]] = {}
        for item in schema_context.get("objects") or []:
            if not isinstance(item, Mapping):
                continue
            object_key = _schema_context_object_key(item)
            if not object_key:
                continue
            objects[object_key] = object_key
            object_name = object_key.split(".")[-1]
            objects_by_name.setdefault(object_name, []).append(object_key)
            for raw_column in item.get("columns") or []:
                column_name = ""
                qualified_column = ""
                if isinstance(raw_column, Mapping):
                    column_name = _normalize_oracle_identifier(str(raw_column.get("column") or ""))
                    qualified_column = _normalize_oracle_identifier(
                        str(raw_column.get("qualified_column") or "")
                    )
                else:
                    column_name = _normalize_oracle_identifier(str(raw_column))
                if not column_name:
                    continue
                qualified_column = qualified_column or f"{object_key}.{column_name}"
                columns[qualified_column] = qualified_column
                columns_by_name.setdefault(column_name, []).append(qualified_column)
        return cls(
            objects=objects,
            objects_by_name={
                key: tuple(sorted(set(value))) for key, value in objects_by_name.items()
            },
            columns=columns,
            columns_by_name={
                key: tuple(sorted(set(value))) for key, value in columns_by_name.items()
            },
        )

    def resolve_object(self, owner: str, object_name: str) -> str | None:
        normalized_owner = _normalize_oracle_identifier(owner)
        normalized_object = _normalize_oracle_identifier(object_name)
        if not normalized_object:
            return None
        if normalized_owner:
            key = f"{normalized_owner}.{normalized_object}"
            if key in self.objects:
                return key
        candidates = self.objects_by_name.get(normalized_object, ())
        return candidates[0] if len(candidates) == 1 else None

    def resolve_column(
        self,
        column_name: str,
        *,
        object_keys: Sequence[str] = (),
    ) -> str | None:
        normalized_column = _normalize_oracle_identifier(column_name)
        if not normalized_column:
            return None
        if object_keys:
            matches = [
                column
                for object_key in object_keys
                if (column := self.columns.get(f"{object_key}.{normalized_column}")) is not None
            ]
            matches = sorted(set(matches))
            if len(matches) == 1:
                return matches[0]
        candidates = self.columns_by_name.get(normalized_column, ())
        return candidates[0] if len(candidates) == 1 else None

    def resolve_qualified_column(
        self,
        reference: str,
        *,
        aliases: Mapping[str, set[str]],
        statement_objects: Sequence[str],
    ) -> str | None:
        parts = [
            _normalize_oracle_identifier(part)
            for part in reference.replace('"', "").split(".")
            if part.strip()
        ]
        if not parts:
            return None
        column_name = parts[-1]
        if len(parts) >= 3:
            object_key = self.resolve_object(parts[-3], parts[-2])
            return self.resolve_column(column_name, object_keys=[object_key] if object_key else [])
        if len(parts) == 2:
            qualifier = parts[0]
            alias_targets = aliases.get(qualifier, set())
            if alias_targets:
                return self.resolve_column(column_name, object_keys=sorted(alias_targets))
            object_key = self.resolve_object("", qualifier)
            return self.resolve_column(column_name, object_keys=[object_key] if object_key else [])
        return self.resolve_column(column_name, object_keys=statement_objects)


def _schema_context_object_key(item: Mapping[str, Any]) -> str:
    owner = _normalize_oracle_identifier(str(item.get("owner") or ""))
    object_name = _normalize_oracle_identifier(str(item.get("object_name") or ""))
    if owner and object_name:
        return f"{owner}.{object_name}"
    raw_object = _normalize_oracle_identifier(str(item.get("object") or ""))
    parts = [part for part in raw_object.split(".") if part]
    if len(parts) >= 2:
        return f"{parts[-2]}.{parts[-1]}"
    return parts[0] if parts else object_name


def _explicit_projection_aliases(graph: Any) -> set[str]:
    aliases: set[str] = set()
    for projection in graph.projections:
        output_name = _normalize_oracle_identifier(str(projection.output_name))
        if not output_name:
            continue
        expression = _normalize_oracle_identifier(str(projection.expression_sql))
        if f" AS {output_name}" in f" {expression} ":
            aliases.add(output_name)
    return aliases


def _derived_sql_qualifiers(graph: Any) -> set[str]:
    qualifiers = {
        _normalize_oracle_identifier(str(cte.name)) for cte in graph.ctes if str(cte.name).strip()
    }
    qualifiers.update(
        _normalize_oracle_identifier(str(subquery.alias))
        for subquery in graph.subqueries
        if str(subquery.alias).strip()
    )
    return qualifiers


def _ignore_sql_column_reference(
    column: Any,
    *,
    projection_aliases: set[str],
    derived_qualifiers: set[str],
) -> bool:
    name = _normalize_oracle_identifier(str(column.name))
    qualifier = _normalize_oracle_identifier(str(column.table))
    if name in _ORACLE_PSEUDO_COLUMNS:
        return True
    if not qualifier and name in projection_aliases:
        return True
    return bool(qualifier and qualifier in derived_qualifiers)


def _qa_pair_context_payload(schema_context: Mapping[str, Any], pair: QaPair) -> dict[str, Any]:
    payload = pair.model_dump(mode="json")
    lookup = _SchemaContextLookup.from_payload(schema_context)
    analysis = parse_oracle_sql(pair.sql)
    graph = analysis.graph
    if graph is None:
        first_finding = analysis.validation.findings[0] if analysis.validation.findings else None
        if first_finding is not None:
            payload["sql_parse_warning_ja"] = first_finding.message_ja
        return payload

    aliases: dict[str, set[str]] = {}
    alias_rows: list[dict[str, str]] = []
    resolved_objects: list[dict[str, str]] = []
    unresolved: list[str] = []
    for table in graph.tables:
        if table.is_cte:
            continue
        object_key = lookup.resolve_object(str(table.owner), str(table.name))
        sql_table = str(table.source_sql or table.qualified_name or table.name)
        if object_key is None:
            unresolved.append(sql_table)
            continue
        resolved_objects.append(
            {
                "sql_table": sql_table,
                "object": object_key,
                "alias": str(table.alias or ""),
            }
        )
        statement_tokens = {
            str(table.name),
            str(table.qualified_name),
            f"{table.owner}.{table.name}" if str(table.owner).strip() else "",
        }
        for token in statement_tokens:
            normalized = _normalize_oracle_identifier(token)
            if normalized:
                aliases.setdefault(normalized, set()).add(object_key)
        alias = _normalize_oracle_identifier(str(table.alias or ""))
        if alias:
            aliases.setdefault(alias, set()).add(object_key)
            alias_rows.append({"alias": alias, "object": object_key})

    statement_objects = _dedupe([item["object"] for item in resolved_objects])
    projection_aliases = _explicit_projection_aliases(graph)
    derived_qualifiers = _derived_sql_qualifiers(graph)
    resolved_columns: list[dict[str, str]] = []
    ignored_aliases: list[str] = []
    for column in graph.columns:
        if _ignore_sql_column_reference(
            column,
            projection_aliases=projection_aliases,
            derived_qualifiers=derived_qualifiers,
        ):
            ignored_aliases.append(str(column.expression_sql))
            continue
        resolved = lookup.resolve_qualified_column(
            str(column.expression_sql),
            aliases=aliases,
            statement_objects=statement_objects,
        )
        if resolved is None:
            unresolved.append(str(column.expression_sql))
            continue
        resolved_columns.append(
            {
                "sql": str(column.expression_sql),
                "column": resolved,
                "clause": str(column.clause),
            }
        )

    resolved_join_conditions: list[dict[str, Any]] = []
    for join in graph.joins:
        columns = [
            resolved
            for reference in join.referenced_columns
            if (
                resolved := lookup.resolve_qualified_column(
                    reference,
                    aliases=aliases,
                    statement_objects=statement_objects,
                )
            )
            is not None
        ]
        if columns:
            resolved_join_conditions.append(
                {
                    "condition_sql": join.condition_sql,
                    "resolved_columns": sorted(set(columns)),
                }
            )

    payload["schema_resolved_objects"] = resolved_objects
    payload["schema_sql_aliases"] = alias_rows
    payload["schema_projection_aliases"] = sorted(projection_aliases)
    payload["schema_ignored_sql_aliases"] = _dedupe(ignored_aliases)
    payload["schema_resolved_columns"] = [
        item for index, item in enumerate(resolved_columns) if item not in resolved_columns[:index]
    ]
    payload["schema_resolved_join_conditions"] = resolved_join_conditions
    payload["schema_unresolved_references"] = sorted(set(unresolved))
    return payload


def _dump_text_context(
    schema_context: dict[str, Any],
    units: list[_BuildTextUnit],
) -> str:
    return json.dumps(
        {
            "schema_context": schema_context,
            "business_text_chunks": [unit.context_payload() for unit in units],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _dump_qa_context(schema_context: dict[str, Any], pairs: list[QaPair]) -> str:
    return json.dumps(
        {
            "schema_context": schema_context,
            "qa_pairs": [_qa_pair_context_payload(schema_context, pair) for pair in pairs],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _extraction_batch_max_chars() -> int:
    """1 回の抽出呼び出しに載せる資料本文の上限。チャンクが大きいほど抽出漏れが
    増える(GraphRAG の知見)ため、入力上限とは別に本文量を絞る。"""

    return max(1000, int(get_settings().nl2sql_ontology_extraction_batch_max_chars))


def _split_text_unit_for_budget(
    schema_context: dict[str, Any],
    unit: _BuildTextUnit,
    prompt: str,
) -> list[_BuildTextUnit]:
    empty_unit = _BuildTextUnit(
        source_document=unit.source_document,
        source_label=unit.source_label,
        locator_kind=unit.locator_kind,
        locator=unit.locator,
        text="",
    )
    empty_context = _dump_text_context(schema_context, [empty_unit])
    available = (
        _ONTOLOGY_BUILD_LLM_CONTEXT_MAX_CHARS
        - _llm_call_chars(prompt, empty_context)
        - _LLM_CONTEXT_HEADROOM_CHARS
    )
    if available <= 0:
        raise OntologyBuildContextBudgetError(
            "schema_context が大きすぎるため、資料本文を 1 chunk も LLM 入力へ追加できません。"
            "Profile の対象 object を減らして再実行してください。"
        )
    available = min(available, _extraction_batch_max_chars())
    if len(unit.text) <= available:
        return [unit]
    parts: list[_BuildTextUnit] = []
    total_parts = (len(unit.text) + available - 1) // available
    for index, start in enumerate(range(0, len(unit.text), available), start=1):
        part_text = unit.text[start : start + available]
        parts.append(
            _BuildTextUnit(
                source_document=unit.source_document,
                source_label=unit.source_label,
                locator_kind=unit.locator_kind,
                locator=f"{unit.locator};part:{index}/{total_parts}",
                text=part_text,
            )
        )
    return parts


def _batch_text_units(
    schema_context: dict[str, Any],
    units: list[_BuildTextUnit],
    prompt: str,
) -> list[list[_BuildTextUnit]]:
    expanded: list[_BuildTextUnit] = []
    for unit in units:
        if unit.text.strip():
            expanded.extend(_split_text_unit_for_budget(schema_context, unit, prompt))
    batch_max_chars = _extraction_batch_max_chars()
    batches: list[list[_BuildTextUnit]] = []
    current: list[_BuildTextUnit] = []
    for unit in expanded:
        candidate = [*current, unit]
        candidate_context = _dump_text_context(schema_context, candidate)
        candidate_content_chars = sum(len(item.text) for item in candidate)
        if (
            _llm_call_chars(prompt, candidate_context) <= _ONTOLOGY_BUILD_LLM_CONTEXT_MAX_CHARS
            and candidate_content_chars <= batch_max_chars
        ):
            current = candidate
            continue
        if not current:
            _ensure_llm_call_fits(prompt, candidate_context)
        batches.append(current)
        current = [unit]
        _ensure_llm_call_fits(prompt, _dump_text_context(schema_context, current))
    if current:
        batches.append(current)
    return batches


def _batch_qa_pairs(
    schema_context: dict[str, Any],
    pairs: list[QaPair],
    prompt: str,
) -> list[list[QaPair]]:
    batch_max_chars = _extraction_batch_max_chars()
    batches: list[list[QaPair]] = []
    current: list[QaPair] = []
    for pair in pairs:
        candidate = [*current, pair]
        candidate_context = _dump_qa_context(schema_context, candidate)
        candidate_content_chars = sum(len(item.question) + len(item.sql) for item in candidate)
        if _llm_call_chars(prompt, candidate_context) <= _ONTOLOGY_BUILD_LLM_CONTEXT_MAX_CHARS and (
            len(candidate) == 1 or candidate_content_chars <= batch_max_chars
        ):
            current = candidate
            continue
        if not current:
            raise OntologyBuildContextBudgetError(
                "1 件の Q/A が LLM 入力上限を超えています。"
                "質問または SQL を分割して再実行してください。"
            )
        batches.append(current)
        current = [pair]
        _ensure_llm_call_fits(prompt, _dump_qa_context(schema_context, current))
    if current:
        batches.append(current)
    return batches


# --- job service ------------------------------------------------------------------------------

_STEP_LABELS_JA: dict[OntologyBuildStepName, str] = {
    OntologyBuildStepName.SOURCE_EXTRACTION: "資料の抽出",
    OntologyBuildStepName.SCHEMA_CONTEXT: "スキーマ情報の準備",
    OntologyBuildStepName.SCHEMA_NAMING: "業務エンティティ命名",
    OntologyBuildStepName.QA_EXTRACTION: "Q/A からの抽出",
    OntologyBuildStepName.TEXT_EXTRACTION: "業務説明からの抽出",
    OntologyBuildStepName.PROPOSAL_REGISTRATION: "Markdown 下書き生成",
}
_MAX_JOB_EVENTS = 100
# 完了(succeeded/failed/cancelled)job の in-memory 保持上限。超過分は start 時に古い順へ破棄する
_MAX_FINISHED_JOBS = 20
_TERMINAL_STATUSES = {
    OntologyBuildStatus.SUCCEEDED,
    OntologyBuildStatus.SUCCEEDED_WITH_WARNINGS,
    OntologyBuildStatus.FAILED,
    OntologyBuildStatus.CANCELLED,
}
_PROFILE_ACTIVE_LOCK_OPERATION = "build_ontology_profile_active"
_PROFILE_ACTIVE_LOCK_STALE_SECONDS = 300.0
_TERMINAL_STEP_STATUSES = {
    OntologyBuildStepStatus.SUCCEEDED,
    OntologyBuildStepStatus.FAILED,
    OntologyBuildStepStatus.SKIPPED,
}


class OntologyBuildService:
    """永続 job。local は thread、production は独立 worker から同じ run を呼ぶ。"""

    def __init__(
        self,
        runtime: Any,
        *,
        source_storage: OntologySourceStorage | None = None,
    ) -> None:
        self._runtime = runtime
        self._source_storage = source_storage or OntologySourceStorage()
        self._jobs: dict[str, OntologyBuildJob] = {}
        self._inputs: dict[str, dict[str, Any]] = {}
        # _persist_job の read-modify-write を直列化する(worker thread と API thread の競合防止)
        self._persist_lock = threading.RLock()
        self._lock = threading.Lock()

    def start(
        self,
        profile_id: str,
        *,
        business_text: str = "",
        qa_pairs: list[QaPair] | None = None,
        run_schema_naming: bool = True,
        run_qa_extraction: bool = True,
        run_text_extraction: bool = True,
        initial_warnings: list[str] | None = None,
        source_documents: list[OntologySourceDocument] | None = None,
        idempotency_key: str | None = None,
    ) -> OntologyBuildJob:
        # 未知 profile を非同期 error に隠さない。重いオントロジー同期は worker 側の
        # 「スキーマ情報の準備」ステップで行い、POST は即時に job を返す。
        self._runtime.ensure_profile(profile_id)
        pairs = qa_pairs or []
        sources = source_documents or []
        request_hash = hashlib.sha256(
            canonical_json(
                {
                    "profile_id": profile_id,
                    "business_text": business_text,
                    "qa_pairs": pairs,
                    "source_sha256": [source.sha256 for source in sources],
                    "source_roles": [source.source_role.value for source in sources],
                    "run_schema_naming": run_schema_naming,
                    "run_qa_extraction": run_qa_extraction,
                    "run_text_extraction": run_text_extraction,
                }
            ).encode("utf-8")
        ).hexdigest()
        if idempotency_key:
            existing = self._runtime.store.get_idempotency("build_ontology", idempotency_key)
            if existing is not None:
                if existing.get("request_hash") != request_hash:
                    raise OntologyVersionConflictError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "同じ Idempotency-Key が別の構築リクエストに使用されています。",
                    )
                restored = self.get(str(existing.get("resource_id") or ""))
                if restored is not None:
                    return restored
        steps: list[OntologyBuildStep] = []
        if sources:
            steps.append(OntologyBuildStep(name=OntologyBuildStepName.SOURCE_EXTRACTION))
        steps.append(OntologyBuildStep(name=OntologyBuildStepName.SCHEMA_CONTEXT))
        if run_schema_naming:
            steps.append(OntologyBuildStep(name=OntologyBuildStepName.SCHEMA_NAMING))
        if run_qa_extraction and (pairs or sources):
            steps.append(OntologyBuildStep(name=OntologyBuildStepName.QA_EXTRACTION))
        if run_text_extraction and (business_text.strip() or sources):
            steps.append(OntologyBuildStep(name=OntologyBuildStepName.TEXT_EXTRACTION))
        steps.append(OntologyBuildStep(name=OntologyBuildStepName.PROPOSAL_REGISTRATION))
        job = OntologyBuildJob(
            id=f"ontology_build_{uuid4().hex}",
            profile_id=profile_id,
            steps=steps,
            # POST 応答に最初のフィードバックを含める(worker 開始を待たない)
            events=[
                OntologyBuildEvent(message_ja="構築リクエストを受け付けました。処理を開始します。")
            ],
            warnings_ja=list(initial_warnings or []),
            source_document_ids=[source.id for source in sources],
            sources=[
                OntologySourceProgress(
                    source_document_id=source.id,
                    filename=source.filename,
                    status=source.status,
                )
                for source in sources
            ],
        )
        self._acquire_profile_job_lock(profile_id, job.id)
        try:
            for source in sources:
                self._save_source_document(source)
            with self._lock:
                self._prune_finished_jobs_locked()
                self._jobs[job.id] = job
                self._inputs[job.id] = {
                    "business_text": business_text,
                    "qa_pairs": [pair.model_dump(mode="json") for pair in pairs],
                    # retry がステップ構成を忠実に再現できるようトグルも永続化する
                    "run_schema_naming": run_schema_naming,
                    "run_qa_extraction": run_qa_extraction,
                    "run_text_extraction": run_text_extraction,
                }
            self._persist_job(job)
            if idempotency_key:
                self._runtime.store.save_idempotency(
                    {
                        "operation": "build_ontology",
                        "idempotency_key": idempotency_key,
                        "request_hash": request_hash,
                        "resource_id": job.id,
                        "status": "accepted",
                    }
                )
            if get_settings().nl2sql_ontology_worker_mode == "inprocess":
                thread = threading.Thread(
                    target=self._run_safely,
                    args=(job.id, business_text, pairs),
                    daemon=True,
                )
                thread.start()
            return job.model_copy(deep=True)
        except Exception:
            self._release_profile_job_lock(profile_id, job.id)
            raise

    def get(self, job_id: str) -> OntologyBuildJob | None:
        # external worker モードでは worker だけが進捗を書くため store が正
        # (in-memory は queued のまま止まって見える)。inprocess では実行スレッドが
        # 先に in-memory を更新するため従来どおり in-memory が正。
        if get_settings().nl2sql_ontology_worker_mode == "external":
            restored = self._restore_from_store(job_id)
            if restored is not None:
                return self._normalize_job_for_response(restored)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return self._normalize_job_for_response(job)
        restored = self._restore_from_store(job_id)
        return self._normalize_job_for_response(restored) if restored is not None else None

    def _restore_from_store(self, job_id: str) -> OntologyBuildJob | None:
        """store の job を in-memory キャッシュへ再水和して返す(未登録は None)。"""

        document = self._runtime.store.get_document("jobs", {"job_id": job_id})
        if document is None or document.get("job_type") != "build":
            return None
        restored = OntologyBuildJob.model_validate(document["payload"])
        with self._lock:
            self._jobs[job_id] = restored
            input_payload = document.get("input_payload")
            if isinstance(input_payload, dict):
                self._inputs[job_id] = dict(input_payload)
        return restored

    def run_persisted(self, job_id: str) -> OntologyBuildJob:
        """独立 worker から、Oracle に保存した入力だけで job を再開する。"""

        job = self.get(job_id)
        if job is None:
            raise RuntimeError("Ontology build job が見つかりません。")
        with self._lock:
            payload = dict(self._inputs.get(job_id, {}))
        if not payload:
            document = self._runtime.store.get_document("jobs", {"job_id": job_id})
            raw_payload = document.get("input_payload") if document is not None else None
            payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        business_text = str(payload.get("business_text") or "")
        qa_pairs = [QaPair.model_validate(item) for item in payload.get("qa_pairs", [])]
        self._run_safely(job_id, business_text, qa_pairs)
        result = self.get(job_id)
        if result is None:
            raise RuntimeError("Ontology build job の実行結果を取得できません。")
        return result

    def purge_profile_source_documents(self, profile_id: str) -> int:
        """Profile 削除時に source_documents 行とアップロード実体を削除する(残置リーク防止)。"""

        documents = self._runtime.store.list_documents(
            "source_documents", {"profile_id": profile_id}
        )
        for document in documents:
            try:
                source = OntologySourceDocument.model_validate(document["payload"])
                self._source_storage.delete(source)
            except Exception:
                # 実体削除の失敗で行削除・Profile 削除全体を止めない(cleanup は best-effort)
                logger.warning(
                    "ontology_source_blob_cleanup_failed",
                    exc_info=True,
                    extra={"profile_id": profile_id},
                )
        return int(
            self._runtime.store.delete_documents("source_documents", {"profile_id": profile_id})
        )

    def discard_source_documents(self, sources: Sequence[OntologySourceDocument]) -> int:
        """Job 投入失敗時に、今回保存した source_documents だけを best-effort で破棄する。"""

        deleted_rows = 0
        for source in sources:
            try:
                self._source_storage.delete(source)
            except Exception:
                logger.warning(
                    "ontology_source_blob_cleanup_failed",
                    exc_info=True,
                    extra={
                        "profile_id": source.profile_id,
                        "source_document_id": source.id,
                    },
                )
            try:
                deleted_rows += int(
                    self._runtime.store.delete_documents(
                        "source_documents",
                        {"source_document_id": source.id},
                    )
                )
            except Exception:
                logger.warning(
                    "ontology_source_row_cleanup_failed",
                    exc_info=True,
                    extra={
                        "profile_id": source.profile_id,
                        "source_document_id": source.id,
                    },
                )
        return deleted_rows

    def cancel_profile_jobs(self, profile_id: str) -> int:
        """削除対象 Profile の queued/running build を永続的に取消す。"""

        cancelled = 0
        documents = self._runtime.store.list_documents("jobs", {"profile_id": profile_id})
        for document in documents:
            if document.get("job_type") != "build":
                continue
            job = OntologyBuildJob.model_validate(document["payload"])
            if job.status in _TERMINAL_STATUSES:
                continue
            self._save_cancelled(document, job, "業務 Profile が削除されたため構築を中止しました。")
            cancelled += 1
        return cancelled

    def _save_cancelled(
        self,
        document: dict[str, Any],
        job: OntologyBuildJob,
        message_ja: str,
    ) -> None:
        """job を CANCELLED として ETag 付きで保存し、in-memory も同期する。"""

        now = utc_now()
        job.status = OntologyBuildStatus.CANCELLED
        job.error_message_ja = message_ja
        job.finished_at = now
        for step in job.steps:
            if step.status in {
                OntologyBuildStepStatus.PENDING,
                OntologyBuildStepStatus.RUNNING,
            }:
                step.status = OntologyBuildStepStatus.SKIPPED
                step.finished_at = now
        job.events.append(OntologyBuildEvent(at=now, message_ja=message_ja))
        del job.events[:-_MAX_JOB_EVENTS]
        self._runtime.store.save_document(
            "jobs",
            {
                **{
                    key: value
                    for key, value in document.items()
                    if key not in {"etag", "created_at", "updated_at"}
                },
                "status": OntologyBuildStatus.CANCELLED.value,
                "payload": job.model_dump(mode="json"),
            },
            expected_etag=str(document["etag"]),
        )
        self._release_profile_job_lock(job.profile_id, job.id)
        with self._lock:
            self._jobs[job.id] = job.model_copy(deep=True)

    def list_profile_jobs(self, profile_id: str, *, limit: int = 5) -> list[OntologyBuildJob]:
        """profile の build job を新しい順に返す(store が正、リロード復旧/履歴用)。"""

        documents = self._runtime.store.list_documents("jobs", {"profile_id": profile_id})
        jobs = [
            OntologyBuildJob.model_validate(document["payload"])
            for document in documents
            if document.get("job_type") == "build"
        ]
        jobs.sort(key=lambda job: (job.created_at, job.id), reverse=True)
        return [self._normalize_job_for_response(job) for job in jobs[: max(1, limit)]]

    def list_profile_source_documents(
        self, profile_id: str, *, limit: int = 20
    ) -> list[OntologySourceDocument]:
        """profile に保存済みの Ontology 構築資料を新しい順に返す。"""

        documents = self._runtime.store.list_documents(
            "source_documents", {"profile_id": profile_id}
        )
        sources: list[OntologySourceDocument] = []
        for document in documents:
            try:
                sources.append(OntologySourceDocument.model_validate(document["payload"]))
            except Exception:
                logger.warning(
                    "ontology_source_document_decode_failed",
                    exc_info=True,
                    extra={"profile_id": profile_id},
                )
        sources.sort(key=lambda source: (source.created_at, source.id), reverse=True)
        return sources[: max(1, limit)]

    def cancel(self, job_id: str) -> OntologyBuildJob:
        """ユーザー起点の単一 job キャンセル。

        queued/running → CANCELLED(実行ループは既存の cancelled チェックで停止)。
        CANCELLED → no-op 成功。SUCCEEDED/FAILED → 409。
        """

        for _attempt in range(3):
            document = self._runtime.store.get_document("jobs", {"job_id": job_id})
            if document is None or document.get("job_type") != "build":
                raise OntologyNotFoundError(
                    "ONTOLOGY_BUILD_JOB_NOT_FOUND",
                    "AI オントロジー構築 job が見つかりません。",
                )
            job = OntologyBuildJob.model_validate(document["payload"])
            if job.status == OntologyBuildStatus.CANCELLED:
                return job.model_copy(deep=True)
            if job.status in _TERMINAL_STATUSES:
                raise OntologyStateConflictError(
                    "ONTOLOGY_BUILD_JOB_FINISHED",
                    "この構築 job は既に完了しているため中止できません。",
                )
            try:
                self._save_cancelled(document, job, "利用者の操作で構築を中止しました。")
                record_job(job_type="build", status="cancelled", error_code="user_cancelled")
                return job.model_copy(deep=True)
            except Exception:  # 完了/進捗書込と競合(ETag 不一致)→ 再読して再判定
                logger.info("ontology_build_cancel_conflict job_id=%s", job_id)
                continue
        raise OntologyStateConflictError(
            "ONTOLOGY_BUILD_CANCEL_CONFLICT",
            "構築 job の状態が変化し続けているため中止できませんでした。再試行してください。",
        )

    def retry(self, job_id: str) -> OntologyBuildJob:
        """failed/cancelled job を保存済み入力から新規 job として再実行する。"""

        document = self._runtime.store.get_document("jobs", {"job_id": job_id})
        if document is None or document.get("job_type") != "build":
            raise OntologyNotFoundError(
                "ONTOLOGY_BUILD_JOB_NOT_FOUND",
                "AI オントロジー構築 job が見つかりません。",
            )
        job = OntologyBuildJob.model_validate(document["payload"])
        if job.status not in {OntologyBuildStatus.FAILED, OntologyBuildStatus.CANCELLED}:
            raise OntologyStateConflictError(
                "ONTOLOGY_BUILD_JOB_NOT_RETRYABLE",
                "失敗または中止された構築 job だけを再実行できます。",
            )
        raw_input = document.get("input_payload")
        payload = dict(raw_input) if isinstance(raw_input, dict) else {}
        step_names = {step.name for step in job.steps}

        def toggle(key: str, step_name: OntologyBuildStepName) -> bool:
            if key in payload:
                return bool(payload[key])
            # 旧 job(トグル未永続化)はステップ構成から復元する
            return step_name in step_names

        sources: list[OntologySourceDocument] = []
        for source_id in job.source_document_ids:
            try:
                source = self._get_source_document(source_id)
            except Exception as exc:
                raise OntologyStateConflictError(
                    "ONTOLOGY_BUILD_SOURCE_MISSING",
                    "元の構築に使った資料が見つからないため再実行できません。"
                    "資料を選び直して新規に実行してください。",
                ) from exc
            sources.append(
                source.model_copy(
                    update={"status": OntologySourceStatus.STORED, "updated_at": utc_now()},
                    deep=True,
                )
            )
        return self.start(
            job.profile_id,
            business_text=str(payload.get("business_text") or ""),
            qa_pairs=[QaPair.model_validate(item) for item in payload.get("qa_pairs", [])],
            run_schema_naming=toggle("run_schema_naming", OntologyBuildStepName.SCHEMA_NAMING),
            run_qa_extraction=toggle("run_qa_extraction", OntologyBuildStepName.QA_EXTRACTION),
            run_text_extraction=toggle(
                "run_text_extraction", OntologyBuildStepName.TEXT_EXTRACTION
            ),
            source_documents=sources,
            idempotency_key=f"retry:{job_id}:{uuid4().hex}",
        )

    def _acquire_profile_job_lock(self, profile_id: str, job_id: str) -> None:
        active = self._find_active_profile_job(profile_id)
        if active is not None:
            self._raise_active_profile_job_conflict(active)
        existing = self._runtime.store.get_idempotency(
            _PROFILE_ACTIVE_LOCK_OPERATION,
            profile_id,
        )
        if existing is not None:
            active = self._active_job_from_lock(existing)
            if active is not None:
                self._raise_active_profile_job_conflict(active)
            self._delete_profile_job_lock(profile_id, str(existing.get("resource_id") or ""))
        try:
            self._runtime.store.save_idempotency(
                {
                    "operation": _PROFILE_ACTIVE_LOCK_OPERATION,
                    "idempotency_key": profile_id,
                    "request_hash": hashlib.sha256(profile_id.encode("utf-8")).hexdigest(),
                    "resource_id": job_id,
                    "status": "active",
                    "payload": {
                        "operation": _PROFILE_ACTIVE_LOCK_OPERATION,
                        "profile_id": profile_id,
                        "job_id": job_id,
                        "created_at_epoch": time.time(),
                    },
                },
                expected_etag=None,
            )
        except OntologyVersionConflict as exc:
            lock = self._runtime.store.get_idempotency(
                _PROFILE_ACTIVE_LOCK_OPERATION,
                profile_id,
            )
            active = self._active_job_from_lock(lock) if lock is not None else None
            if active is None:
                active = self._find_active_profile_job(profile_id)
            if active is not None:
                self._raise_active_profile_job_conflict(active)
            raise OntologyStateConflictError(
                "ONTOLOGY_BUILD_START_CONFLICT",
                "構築 job の開始状態が競合しました。再試行してください。",
            ) from exc

    def _release_profile_job_lock(self, profile_id: str, job_id: str) -> None:
        try:
            self._delete_profile_job_lock(profile_id, job_id)
        except Exception:
            logger.warning(
                "ontology_build_profile_lock_release_failed",
                exc_info=True,
                extra={"profile_id": profile_id, "job_id": job_id},
            )

    def _delete_profile_job_lock(self, profile_id: str, job_id: str) -> None:
        existing = self._runtime.store.get_idempotency(
            _PROFILE_ACTIVE_LOCK_OPERATION,
            profile_id,
        )
        if existing is None or str(existing.get("resource_id") or "") != job_id:
            return
        self._runtime.store.delete_documents(
            "idempotency",
            {
                "operation": _PROFILE_ACTIVE_LOCK_OPERATION,
                "idempotency_key": profile_id,
            },
        )

    def _active_job_from_lock(self, lock: Mapping[str, Any]) -> OntologyBuildJob | None:
        job_id = str(lock.get("resource_id") or "")
        if not job_id:
            return None
        document = self._runtime.store.get_document("jobs", {"job_id": job_id})
        job = self._job_from_document(document)
        if job is None:
            payload = lock.get("payload")
            created_at_epoch = (
                payload.get("created_at_epoch") if isinstance(payload, Mapping) else 0
            )
            lock_age = _PROFILE_ACTIVE_LOCK_STALE_SECONDS + 1
            if isinstance(created_at_epoch, int | float | str):
                try:
                    lock_age = time.time() - float(created_at_epoch)
                except ValueError:
                    lock_age = _PROFILE_ACTIVE_LOCK_STALE_SECONDS + 1
            if 0 <= lock_age <= _PROFILE_ACTIVE_LOCK_STALE_SECONDS:
                profile_id = (
                    str(payload.get("profile_id") or "")
                    if isinstance(payload, Mapping)
                    else str(lock.get("idempotency_key") or "")
                )
                return OntologyBuildJob(
                    id=job_id,
                    profile_id=profile_id,
                    status=OntologyBuildStatus.QUEUED,
                )
            return None
        return job if job.status not in _TERMINAL_STATUSES else None

    def _find_active_profile_job(self, profile_id: str) -> OntologyBuildJob | None:
        jobs: list[OntologyBuildJob] = []
        for document in self._runtime.store.list_documents("jobs", {"profile_id": profile_id}):
            job = self._job_from_document(document)
            if job is not None:
                jobs.append(job)
        with self._lock:
            jobs.extend(
                job.model_copy(deep=True)
                for job in self._jobs.values()
                if job.profile_id == profile_id
            )
        active = [job for job in jobs if job.status not in _TERMINAL_STATUSES]
        if not active:
            return None
        active.sort(key=lambda job: (job.created_at, job.id))
        return active[0]

    @staticmethod
    def _job_from_document(document: Mapping[str, Any] | None) -> OntologyBuildJob | None:
        if document is None or document.get("job_type") != "build":
            return None
        try:
            return OntologyBuildJob.model_validate(document["payload"])
        except Exception:
            logger.warning("ontology_build_job_decode_failed", exc_info=True)
            return None

    @staticmethod
    def _raise_active_profile_job_conflict(job: OntologyBuildJob) -> None:
        raise OntologyStateConflictError(
            "ONTOLOGY_BUILD_JOB_ALREADY_RUNNING",
            f"この profile では構築 job {job.id} が実行中です。完了後に再実行してください。",
        )

    # --- internal ---------------------------------------------------------------------------

    def _prune_finished_jobs_locked(self) -> None:
        """lock 保持中に呼ぶ。完了 job が上限を超えたら古い順に破棄する(queued/running は保護)。"""
        finished = [job for job in self._jobs.values() if job.status in _TERMINAL_STATUSES]
        overflow = len(finished) - _MAX_FINISHED_JOBS
        if overflow <= 0:
            return
        finished.sort(key=lambda job: (job.finished_at or job.created_at, job.id))
        for job in finished[:overflow]:
            del self._jobs[job.id]
            # business_text / Q/A 全文を保持する入力もあわせて破棄する(メモリリーク防止)
            self._inputs.pop(job.id, None)

    def _update(self, job_id: str, mutate: Any) -> None:
        persisted = self._runtime.store.get_document("jobs", {"job_id": job_id})
        if persisted is not None and persisted.get("status") == OntologyBuildStatus.CANCELLED.value:
            cancelled = OntologyBuildJob.model_validate(persisted["payload"])
            with self._lock:
                self._jobs[job_id] = cancelled
            return
        updated: OntologyBuildJob | None = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                mutate(job)
                updated = job.model_copy(deep=True)
        if updated is not None:
            self._persist_job(updated)

    def _update_persisted_copy(self, job_id: str, mutate: Any) -> None:
        persisted = self._runtime.store.get_document("jobs", {"job_id": job_id})
        if persisted is not None and persisted.get("status") == OntologyBuildStatus.CANCELLED.value:
            cancelled = OntologyBuildJob.model_validate(persisted["payload"])
            with self._lock:
                self._jobs[job_id] = cancelled
            return
        updated: OntologyBuildJob | None = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                updated = job.model_copy(deep=True)
        if updated is None:
            return
        mutate(updated)
        self._persist_job(updated)
        with self._lock:
            self._jobs[job_id] = updated

    def _normalize_job_for_response(self, job: OntologyBuildJob) -> OntologyBuildJob:
        normalized = job.model_copy(deep=True)
        if normalized.status not in {
            OntologyBuildStatus.SUCCEEDED,
            OntologyBuildStatus.SUCCEEDED_WITH_WARNINGS,
        } or not (
            normalized.draft_revision_id or normalized.draft_etag or normalized.markdown_output
        ):
            return normalized
        non_terminal = [
            step for step in normalized.steps if step.status not in _TERMINAL_STEP_STATUSES
        ]
        if (
            len(non_terminal) != 1
            or non_terminal[0].name != OntologyBuildStepName.PROPOSAL_REGISTRATION
        ):
            return normalized
        step = non_terminal[0]
        step.status = OntologyBuildStepStatus.SUCCEEDED
        # code=FINALIZING は「完了状態の保存中」を表す機械可読コード。文言一致は旧データ互換。
        legacy_finalizing = "構築 job の完了状態" in step.detail_ja
        if not step.detail_ja or step.code == "FINALIZING" or legacy_finalizing:
            step.detail_ja = "Markdown 下書きを生成しました。"
            step.code = ""
        step.finished_at = normalized.finished_at or step.finished_at or step.started_at
        return normalized

    def _persist_job(self, job: OntologyBuildJob) -> None:
        # read(etag) → save の間に別 thread(worker の進捗更新 vs API の cancel)が
        # 書き込むと etag conflict で状態が分岐する。専用 lock で直列化し、conflict 時は
        # etag を読み直して再試行する。persisted が CANCELLED になっていたら cancel を優先する。
        with self._persist_lock:
            for attempt in range(3):
                current = self._runtime.store.get_document("jobs", {"job_id": job.id})
                if (
                    current is not None
                    and current.get("status") == OntologyBuildStatus.CANCELLED.value
                    and job.status != OntologyBuildStatus.CANCELLED
                ):
                    cancelled = OntologyBuildJob.model_validate(current["payload"])
                    with self._lock:
                        self._jobs[job.id] = cancelled
                    return
                with self._lock:
                    input_payload = self._inputs.get(job.id)
                if input_payload is None and current is not None:
                    current_input = current.get("input_payload")
                    input_payload = dict(current_input) if isinstance(current_input, dict) else {}
                try:
                    self._runtime.store.save_document(
                        "jobs",
                        {
                            "job_id": job.id,
                            "job_type": "build",
                            "profile_id": job.profile_id,
                            "status": job.status.value,
                            "payload": job.model_dump(mode="json"),
                            "input_payload": input_payload or {},
                            **(
                                {
                                    "claimed_by": current.get("claimed_by"),
                                    "claimed_at": time.time(),
                                }
                                if current is not None and current.get("claimed_by")
                                else {}
                            ),
                        },
                        expected_etag=str(current["etag"]) if current is not None else None,
                    )
                    if job.status in _TERMINAL_STATUSES:
                        self._release_profile_job_lock(job.profile_id, job.id)
                    return
                except OntologyVersionConflict:
                    if attempt == 2:
                        raise

    def _save_source_document(self, source: OntologySourceDocument) -> None:
        current = self._runtime.store.get_document(
            "source_documents", {"source_document_id": source.id}
        )
        self._runtime.store.save_document(
            "source_documents",
            {
                "source_document_id": source.id,
                "profile_id": source.profile_id,
                "status": source.status.value,
                "sha256": source.sha256,
                "payload": source.model_dump(mode="json"),
            },
            expected_etag=str(current["etag"]) if current is not None else None,
        )

    def _get_source_document(self, source_id: str) -> OntologySourceDocument:
        document = self._runtime.store.get_document(
            "source_documents", {"source_document_id": source_id}
        )
        if document is None:
            raise RuntimeError(f"Ontology source document が見つかりません: {source_id}")
        return OntologySourceDocument.model_validate(document["payload"])

    def _update_source_document(
        self,
        source: OntologySourceDocument,
        **updates: Any,
    ) -> OntologySourceDocument:
        updated = source.model_copy(update={**updates, "updated_at": utc_now()}, deep=True)
        self._save_source_document(updated)
        return updated

    def _update_source_progress(
        self,
        job_id: str,
        source_id: str,
        **updates: Any,
    ) -> None:
        def mutate(job: OntologyBuildJob) -> None:
            for index, progress in enumerate(job.sources):
                if progress.source_document_id == source_id:
                    job.sources[index] = progress.model_copy(update=updates, deep=True)
                    break

        self._update(job_id, mutate)

    def _set_step(
        self,
        job_id: str,
        name: OntologyBuildStepName,
        status: OntologyBuildStepStatus,
        detail_ja: str = "",
        *,
        code: str = "",
    ) -> None:
        now = utc_now()

        def mutate(job: OntologyBuildJob) -> None:
            for step in job.steps:
                if step.name == name:
                    step.status = status
                    if detail_ja:
                        step.detail_ja = detail_ja
                    if code:
                        step.code = code
                    if status == OntologyBuildStepStatus.RUNNING and step.started_at is None:
                        step.started_at = now
                    if status in {
                        OntologyBuildStepStatus.SUCCEEDED,
                        OntologyBuildStepStatus.FAILED,
                        OntologyBuildStepStatus.SKIPPED,
                    }:
                        step.finished_at = now

        self._update(job_id, mutate)

    def _emit(
        self,
        job_id: str,
        message_ja: str,
        *,
        code: str = "",
        step: OntologyBuildStepName | None = None,
    ) -> None:
        """アクティビティタイムラインへ 1 行追記する(上限超過は古い順に間引く)。"""

        event = OntologyBuildEvent(message_ja=message_ja, code=code, step=step)

        def mutate(job: OntologyBuildJob) -> None:
            job.events.append(event)
            if len(job.events) > _MAX_JOB_EVENTS:
                del job.events[: len(job.events) - _MAX_JOB_EVENTS]

        self._update(job_id, mutate)

    def _add_warnings(self, job_id: str, warnings: list[str]) -> None:
        if not warnings:
            return

        def mutate(job: OntologyBuildJob) -> None:
            job.warnings_ja = [*job.warnings_ja, *warnings]

        self._update(job_id, mutate)

    def _generate_extraction(self, client: Any, task: _OntologyBuildLlmTask) -> str:
        """抽出系呼び出し。出力トークン予算に対応するクライアントには予算を渡す。"""

        budget = int(get_settings().nl2sql_ontology_extraction_max_output_tokens)
        try:
            supports_budget = "max_output_tokens" in inspect.signature(client.generate).parameters
        except (TypeError, ValueError):
            supports_budget = False
        if supports_budget and budget > 0:
            return str(
                client.generate(
                    prompt=task.prompt,
                    context=task.context,
                    system_prompt=_EXTRACTION_SYSTEM_PROMPT,
                    max_output_tokens=budget,
                )
            )
        return str(
            client.generate(
                prompt=task.prompt,
                context=task.context,
                system_prompt=_EXTRACTION_SYSTEM_PROMPT,
            )
        )

    def _glean_extraction(
        self,
        job_id: str,
        client: Any,
        task: _OntologyBuildLlmTask,
        extraction: OntologyBuildExtraction,
        *,
        label: str,
    ) -> OntologyBuildExtraction:
        """GraphRAG 流 gleaning: 抽出済み候補の一覧を提示し、見逃した候補だけを
        追加パスで回収する。失敗・追加なし・入力上限超過は静かに打ち切る。"""

        passes = max(0, int(get_settings().nl2sql_ontology_extraction_gleaning_passes))
        if passes == 0 or task.name not in {
            OntologyBuildStepName.SCHEMA_NAMING,
            OntologyBuildStepName.TEXT_EXTRACTION,
        }:
            return extraction
        current = extraction
        for pass_index in range(1, passes + 1):
            if self._is_cancelled(job_id):
                return current
            seen_summary = json.dumps(
                {
                    "抽出済みエンティティ": [item.object_name for item in current.entities],
                    "抽出済み関係": [
                        f"{item.source_object}->{item.target_object}"
                        for item in current.relationships
                    ],
                    "抽出済み指標": [item.metric_name_ja for item in current.metrics],
                    "抽出済み同義語": [item.target for item in current.synonyms],
                },
                ensure_ascii=False,
            )
            glean_prompt = (
                f"{task.prompt}\n\n【追加パス {pass_index}】前回までに抽出済みの候補は"
                f"次のとおりです: {seen_summary} 。多くの候補が見逃されている可能性が"
                "あります。これらに含まれていない候補だけを同じ JSON 形式で返して"
                "ください。新しい候補が無ければ全フィールドが空の JSON を返してください。"
            )
            if _llm_call_chars(glean_prompt, task.context) > _ONTOLOGY_BUILD_LLM_CONTEXT_MAX_CHARS:
                return current
            glean_task = _OntologyBuildLlmTask(
                name=task.name,
                prompt=glean_prompt,
                context=task.context,
                progress_ja=task.progress_ja,
            )
            try:
                addition = parse_extraction(self._generate_extraction(client, glean_task))
            except Exception:
                logger.warning(
                    "ontology_build_gleaning_failed step=%s pass=%s",
                    task.name.value,
                    pass_index,
                    exc_info=True,
                )
                return current
            merged, added = merge_build_extractions(current, addition)
            if added == 0:
                return current
            current = merged
            self._emit(
                job_id,
                f"{label}: 追加パスで候補 {added} 件を回収しました。",
                code="EXTRACTION_GLEANING",
                step=task.name,
            )
        return current

    def _execute_llm_task(
        self,
        job_id: str,
        client: Any,
        task: _OntologyBuildLlmTask,
        *,
        label: str,
        depth: int = 0,
    ) -> tuple[list[_ValidatedBuildExtraction], list[str]]:
        """1 タスクを実行する。失敗時は 1 回再試行し、なお失敗する batch は二分割して
        再帰する(最小 1 件まで)。最終的に失敗した分は warnings として返し、
        ジョブ全体は止めない。戻り値は (検証済み抽出, 警告)。"""

        if self._is_cancelled(job_id):
            return [], []
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                raw = self._generate_extraction(client, task)
                extraction = parse_extraction(raw)
                if depth == 0:
                    # 取りこぼし回収(schema_naming / text_extraction のみ・最上位のみ)
                    extraction = self._glean_extraction(
                        job_id, client, task, extraction, label=label
                    )
                return (
                    [
                        _ValidatedBuildExtraction(
                            name=task.name,
                            label_ja=label,
                            extraction=extraction,
                            cross_check_sql=task.cross_check_sql,
                            source_evidence=task.source_evidence,
                        )
                    ],
                    [],
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "ontology_build_task_attempt_failed step=%s attempt=%s depth=%s",
                    task.name.value,
                    attempt + 1,
                    depth,
                    exc_info=True,
                )
                if attempt == 0:
                    self._emit(
                        job_id,
                        f"{label}: 抽出に失敗したため再試行します。",
                        code="EXTRACTION_RETRY",
                        step=task.name,
                    )
        halves = task.split()
        if halves and depth < 6:
            self._emit(
                job_id,
                f"{label}: batch を分割して再実行します。",
                code="EXTRACTION_SPLIT",
                step=task.name,
            )
            extractions: list[_ValidatedBuildExtraction] = []
            half_warnings: list[str] = []
            for half in halves:
                half_extractions, half_warns = self._execute_llm_task(
                    job_id, client, half, label=label, depth=depth + 1
                )
                extractions.extend(half_extractions)
                half_warnings.extend(half_warns)
            return extractions, half_warnings
        return [], [f"{task.name.value} の抽出に失敗しました: {last_error}"]

    def _run_safely(self, job_id: str, business_text: str, qa_pairs: list[QaPair]) -> None:
        try:
            self._run(job_id, business_text, qa_pairs)
        except Exception as exc:  # pragma: no cover - 予期しない障害の最終防壁
            logger.warning("ontology_build_job_failed", exc_info=True)
            current = self.get(job_id)
            if current is not None and current.status == OntologyBuildStatus.CANCELLED:
                return
            message = f"オントロジー構築に失敗しました: {exc}"

            def mutate(job: OntologyBuildJob) -> None:
                job.status = OntologyBuildStatus.FAILED
                job.error_message_ja = message
                job.finished_at = utc_now()

            self._update(job_id, mutate)
            record_job(job_type="build", status="failed", error_code="unexpected")

    def _fail(
        self,
        job_id: str,
        message_ja: str,
        *,
        skip_pending_steps: bool = True,
        failed_step: OntologyBuildStepName | None = None,
        failed_step_detail_ja: str = "",
        error_code: str = "",
    ) -> None:
        now = utc_now()

        def mutate(job: OntologyBuildJob) -> None:
            job.status = OntologyBuildStatus.FAILED
            job.error_message_ja = message_ja
            if error_code:
                job.error_code = error_code
            job.finished_at = now
            for step in job.steps:
                if failed_step is not None and step.name == failed_step:
                    step.status = OntologyBuildStepStatus.FAILED
                    if failed_step_detail_ja:
                        step.detail_ja = failed_step_detail_ja
                    if error_code:
                        step.code = error_code
                    if step.started_at is None:
                        step.started_at = now
                    step.finished_at = now
                    continue
                if skip_pending_steps and step.status in {
                    OntologyBuildStepStatus.PENDING,
                    OntologyBuildStepStatus.RUNNING,
                }:
                    step.status = OntologyBuildStepStatus.SKIPPED
                    step.finished_at = now

        self._update(job_id, mutate)
        self._emit(job_id, message_ja)
        record_job(job_type="build", status="failed", error_code="build_failed")

    def _run(self, job_id: str, business_text: str, qa_pairs: list[QaPair]) -> None:
        if self._is_cancelled(job_id):
            return

        def start_job(job: OntologyBuildJob) -> None:
            job.status = OntologyBuildStatus.RUNNING
            job.started_at = utc_now()

        self._update(job_id, start_job)
        job = self.get(job_id)
        if job is None:
            return
        self._emit(job_id, "AI オントロジー構築を開始しました。")

        client = getattr(self._runtime.legacy_service, "_enterprise_ai_client", None)
        configured = getattr(client, "is_configured", None)
        text_units: list[_BuildTextUnit] = []
        if business_text.strip():
            text_units.append(
                _BuildTextUnit(
                    source_document=None,
                    source_label="業務説明(入力フォーム)",
                    locator_kind=OntologyEvidenceLocatorKind.LINE,
                    locator="manual:1",
                    text=business_text.strip(),
                )
            )
        source_evidence_count = 0
        # 部分成功の対象ステップ(資料抽出・LLM 抽出)。finish で succeeded_with_warnings に反映する
        partial_failed_steps: set[OntologyBuildStepName] = set()
        failed_source_names: list[str] = []
        failed_source_errors: list[str] = []
        if job.source_document_ids:
            self._set_step(
                job_id,
                OntologyBuildStepName.SOURCE_EXTRACTION,
                OntologyBuildStepStatus.RUNNING,
                f"資料 {len(job.source_document_ids)} 件を抽出中…",
            )
            extracted_pairs: list[QaPair] = []
            seen_hashes: set[str] = set()
            for source_id in job.source_document_ids:
                source = self._get_source_document(source_id)
                if source.sha256 in seen_hashes:
                    warning = f"{source.filename}: 同一内容の資料は 1 回だけ利用します。"
                    self._update_source_progress(
                        job_id,
                        source.id,
                        status=OntologySourceStatus.EXTRACTED,
                        warnings_ja=[warning],
                    )
                    record_source_extraction(
                        file_format=Path(source.filename).suffix, status="duplicate"
                    )
                    continue
                seen_hashes.add(source.sha256)
                self._update_source_progress(
                    job_id, source.id, status=OntologySourceStatus.EXTRACTING
                )
                source = self._update_source_document(
                    source, status=OntologySourceStatus.EXTRACTING
                )
                try:
                    image_runner = None
                    generate_image = getattr(client, "generate_from_image", None)
                    if callable(configured) and configured() and callable(generate_image):

                        def image_runner(
                            image: bytes,
                            page: int,
                            _generate_image: Any = generate_image,
                        ) -> str:
                            return str(
                                _generate_image(
                                    image,
                                    f"この資料の {page} ページ目を日本語で正確に"
                                    "文字起こししてください。",
                                    mime_type="image/jpeg",
                                )
                            )

                    extracted = extract_ontology_source(
                        source,
                        self._source_storage.load(source),
                        vlm_page_runner=image_runner,
                    )
                    if not extracted.chunks and not extracted.qa_pairs:
                        raise RuntimeError(
                            f"{source.filename}: 抽出可能なテキストまたは Q/A がありません。"
                            + (
                                f"({' / '.join(extracted.warnings_ja)})"
                                if extracted.warnings_ja
                                else ""
                            )
                        )
                    if extracted.warnings_ja:
                        # ページ単位のスキップ等は警告として残し、資料全体は成功扱いにする
                        self._add_warnings(
                            job_id,
                            [f"{source.filename}: {warning}" for warning in extracted.warnings_ja],
                        )
                    for chunk in extracted.chunks:
                        if not chunk.text.strip():
                            continue
                        text_units.append(
                            _BuildTextUnit(
                                source_document=source,
                                source_label=source.filename,
                                locator_kind=chunk.locator_kind,
                                locator=chunk.locator,
                                text=chunk.text,
                            )
                        )
                    source_evidence_count += len(extracted.chunks)
                    extracted_pairs.extend(extracted.qa_pairs)
                    source = self._update_source_document(
                        source,
                        status=OntologySourceStatus.EXTRACTED,
                        extracted_chunk_count=len(extracted.chunks),
                        warnings_ja=extracted.warnings_ja,
                    )
                    self._update_source_progress(
                        job_id,
                        source.id,
                        status=OntologySourceStatus.EXTRACTED,
                        extracted_chunk_count=len(extracted.chunks),
                        warnings_ja=extracted.warnings_ja,
                    )
                    record_source_extraction(
                        file_format=Path(source.filename).suffix, status="extracted"
                    )
                except Exception as exc:
                    error_message = f"{source.filename}: {exc}"
                    source = self._update_source_document(
                        source,
                        status=OntologySourceStatus.FAILED,
                        error_message_ja=error_message,
                    )
                    self._update_source_progress(
                        job_id,
                        source.id,
                        status=OntologySourceStatus.FAILED,
                        error_message_ja=error_message,
                    )
                    record_source_extraction(
                        file_format=Path(source.filename).suffix, status="failed"
                    )
                    # 1 資料の失敗でジョブ全体を止めず、警告として残りの入力で継続する
                    failed_source_names.append(source.filename)
                    failed_source_errors.append(error_message)
                    self._add_warnings(job_id, [error_message])
                    self._emit(
                        job_id,
                        f"{source.filename}: 抽出に失敗しました(他の入力で処理を継続します)。",
                        code="SOURCE_EXTRACTION_FAILED",
                        step=OntologyBuildStepName.SOURCE_EXTRACTION,
                    )
                    continue
            qa_by_key = {(item.question, item.sql): item for item in [*qa_pairs, *extracted_pairs]}
            qa_pairs = list(qa_by_key.values())
            extracted_source_count = len(job.source_document_ids) - len(failed_source_names)
            if failed_source_names:
                partial_failed_steps.add(OntologyBuildStepName.SOURCE_EXTRACTION)
            self._set_step(
                job_id,
                OntologyBuildStepName.SOURCE_EXTRACTION,
                (
                    OntologyBuildStepStatus.FAILED
                    if extracted_source_count == 0
                    else OntologyBuildStepStatus.SUCCEEDED
                ),
                f"資料 {extracted_source_count}/{len(job.source_document_ids)} 件、"
                f"証拠 {source_evidence_count} 件、Q/A {len(extracted_pairs)} 件を抽出しました。"
                + (f" 失敗 {len(failed_source_names)} 件。" if failed_source_names else ""),
                code="SOURCE_PARTIAL_FAILED" if failed_source_names else "",
            )
            self._emit(job_id, "資料の抽出と証拠位置の記録が完了しました。")
            if extracted_source_count == 0 and failed_source_names:
                # 全資料が失敗し、他に入力(業務説明・Q/A・schema 命名)が無ければ
                # 継続しても成果が出ないためジョブを失敗させる
                has_other_inputs = (
                    bool(business_text.strip())
                    or bool(qa_pairs)
                    or OntologyBuildStepName.SCHEMA_NAMING in {step.name for step in job.steps}
                )
                if not has_other_inputs:
                    self._fail(
                        job_id,
                        "すべての資料の抽出に失敗しました。 " + " / ".join(failed_source_errors),
                        failed_step=OntologyBuildStepName.SOURCE_EXTRACTION,
                        failed_step_detail_ja="資料の抽出に失敗しました。",
                        error_code="SOURCE_EXTRACTION_FAILED",
                    )
                    return
        if client is None or not callable(configured) or not configured():
            self._fail(
                job_id,
                "OCI Enterprise AI が未設定のため、AI オントロジー構築を実行できません。",
                error_code="LLM_UNCONFIGURED",
            )
            return

        # --- スキーマ情報の準備(DB catalog 直読みによる AI input 作成) ---
        self._set_step(
            job_id,
            OntologyBuildStepName.SCHEMA_CONTEXT,
            OntologyBuildStepStatus.RUNNING,
            "DB から profile 範囲のスキーマ情報を取得中…",
        )
        self._emit(job_id, "DB から profile 範囲のスキーマ情報を取得しています。")
        prepared_schema = self._runtime.prepare_build_schema_context(job.profile_id)
        schema_warnings = list(prepared_schema.warnings)
        schema_errors = list(prepared_schema.errors)
        object_count = int(prepared_schema.object_count)
        column_count = int(prepared_schema.column_count)
        for warning in schema_warnings:
            self._emit(job_id, warning)
        for error in schema_errors:
            self._emit(job_id, error)
        if object_count == 0 or schema_errors:
            # LLM を無駄撃ちせず、原因(schema 情報未解決)を明確に返す
            scope_code = "SCHEMA_SCOPE_AMBIGUOUS" if schema_errors else "SCHEMA_SCOPE_EMPTY"
            self._set_step(
                job_id,
                OntologyBuildStepName.SCHEMA_CONTEXT,
                OntologyBuildStepStatus.FAILED,
                (
                    "profile 範囲の DB object が曖昧です。"
                    if schema_errors
                    else "profile 範囲に DB 表・ビューがありません。"
                ),
                code=scope_code,
            )
            self._add_warnings(job_id, [*schema_warnings, *schema_errors])
            unresolved_hint = (
                f" 詳細: {' / '.join([*schema_errors, *schema_warnings])}"
                if schema_errors or schema_warnings
                else ""
            )
            self._fail(
                job_id,
                "profile の対象オブジェクトを DB schema catalog に解決できません。"
                "DB 構造を再取得するか、Profile の対象 object を確認してから再実行してください。"
                f"{unresolved_hint}",
                error_code="SCHEMA_SCOPE_UNRESOLVED",
            )
            return

        schema_context = str(prepared_schema.schema_context)
        self._set_step(
            job_id,
            OntologyBuildStepName.SCHEMA_CONTEXT,
            OntologyBuildStepStatus.SUCCEEDED,
            f"表・ビュー {object_count} 件、列 {column_count} 件",
        )
        self._emit(
            job_id,
            f"スキーマ情報を準備しました(表・ビュー {object_count} 件、列 {column_count} 件)。",
        )
        inferred_by = str(getattr(client, "model_id", lambda: "enterprise-ai")())
        qa_pairs = list({(item.question, item.sql): item for item in qa_pairs}.values())
        try:
            schema_payload = _schema_context_payload(schema_context)
        except OntologyBuildContextBudgetError as exc:
            self._fail(
                job_id,
                str(exc),
                failed_step=OntologyBuildStepName.SCHEMA_CONTEXT,
                failed_step_detail_ja="LLM 入力用 schema_context の準備に失敗しました。",
            )
            return

        drafts: list[ProposalDraft] = []
        warnings: list[str] = [*schema_warnings]
        step_names = {step.name for step in job.steps}
        llm_tasks: list[_OntologyBuildLlmTask] = []
        validated_extractions: list[_ValidatedBuildExtraction] = []
        ontology: SchemaOntology | None = None
        if OntologyBuildStepName.SCHEMA_NAMING in step_names:
            prompt = (
                "schema_context の各表・ビューに日本語の業務エンティティ名・説明・同義語を"
                "提案してください。関係と指標は提案不要です。"
            )
            try:
                _ensure_llm_call_fits(prompt, schema_context)
            except OntologyBuildContextBudgetError as exc:
                self._fail(
                    job_id,
                    str(exc),
                    failed_step=OntologyBuildStepName.SCHEMA_NAMING,
                    failed_step_detail_ja="業務エンティティ命名の LLM 入力が上限を超えています。",
                )
                return
            llm_tasks.append(
                _OntologyBuildLlmTask(
                    name=OntologyBuildStepName.SCHEMA_NAMING,
                    prompt=prompt,
                    context=schema_context,
                    progress_ja="業務エンティティ命名を処理中です。",
                )
            )
        if OntologyBuildStepName.QA_EXTRACTION in step_names:
            if not qa_pairs:
                self._set_step(
                    job_id,
                    OntologyBuildStepName.QA_EXTRACTION,
                    OntologyBuildStepStatus.SKIPPED,
                    "有効な Q/A 行がないためスキップしました。",
                )
            else:
                prompt = (
                    "入力 JSON の qa_pairs にある質問と正解 SQL から、実際に使われた "
                    "JOIN パスを relationships に、業務指標を metrics に抽出してください。"
                    "qa_pairs[].schema_resolved_columns と "
                    "qa_pairs[].schema_resolved_join_conditions にある "
                    "OWNER.OBJECT.COLUMN 形式の参照を正としてください。"
                    "SQL 内の table alias、CTE/inline view alias、出力 alias は "
                    "schema_context の object/column 名ではないため、"
                    "schema_unresolved_references に含まれない限り不存在警告にしないでください。"
                    "SQL に現れない関係を推測しないでください。"
                    "cardinality フィールドは schema_context の constraints"
                    "(主キー P / 一意 U)から判断してください"
                    "(JOIN 先の列が主キー・一意キーなら many_to_one など)。"
                )
                try:
                    qa_batches = _batch_qa_pairs(schema_payload, qa_pairs, prompt)
                except OntologyBuildContextBudgetError as exc:
                    self._fail(
                        job_id,
                        str(exc),
                        failed_step=OntologyBuildStepName.QA_EXTRACTION,
                        failed_step_detail_ja="Q/A の LLM 入力が上限を超えています。",
                    )
                    return
                processed = 0
                for batch_index, batch in enumerate(qa_batches, start=1):
                    processed += len(batch)
                    llm_tasks.append(
                        _OntologyBuildLlmTask(
                            name=OntologyBuildStepName.QA_EXTRACTION,
                            prompt=prompt,
                            context=_dump_qa_context(schema_payload, batch),
                            progress_ja=(
                                f"Q/A batch {batch_index}/{len(qa_batches)} を処理中"
                                f"({processed}/{len(qa_pairs)} 件)。"
                            ),
                            cross_check_sql=[pair.sql for pair in batch],
                            qa_batch=list(batch),
                            schema_payload=schema_payload,
                        )
                    )
        if OntologyBuildStepName.TEXT_EXTRACTION in step_names:
            if not text_units:
                self._set_step(
                    job_id,
                    OntologyBuildStepName.TEXT_EXTRACTION,
                    OntologyBuildStepStatus.SKIPPED,
                    "抽出できる業務説明がないためスキップしました。",
                )
            else:
                prompt = (
                    "入力 JSON の business_text_chunks をすべて読み、関係候補を "
                    "relationships に、同義語を synonyms に、業務指標を metrics に"
                    "抽出してください。名詞をエンティティ、"
                    "動詞・述語を関係の手がかりとして読み取り、schema_context に対応づかない"
                    "内容は warnings_ja に残してください。"
                )
                try:
                    text_batches = _batch_text_units(schema_payload, text_units, prompt)
                except OntologyBuildContextBudgetError as exc:
                    self._fail(
                        job_id,
                        str(exc),
                        failed_step=OntologyBuildStepName.TEXT_EXTRACTION,
                        failed_step_detail_ja="業務説明の LLM 入力が上限を超えています。",
                    )
                    return
                processed = 0
                total_units = sum(len(text_batch) for text_batch in text_batches)
                # 上の Q/A ループの batch(QaPair)と変数を共有しない(型の取り違え防止)。
                for text_batch_index, text_batch in enumerate(text_batches, start=1):
                    processed += len(text_batch)
                    llm_tasks.append(
                        _OntologyBuildLlmTask(
                            name=OntologyBuildStepName.TEXT_EXTRACTION,
                            prompt=prompt,
                            context=_dump_text_context(schema_payload, text_batch),
                            progress_ja=(
                                f"業務説明 chunk batch {text_batch_index}"
                                f"/{len(text_batches)} を処理中"
                                f"({processed}/{total_units} chunk)。"
                            ),
                            source_evidence=[
                                evidence
                                for unit in text_batch
                                if (evidence := unit.evidence()) is not None
                            ],
                            text_batch=list(text_batch),
                            schema_payload=schema_payload,
                        )
                    )

        # 各タスクは 1 回再試行 → なお失敗なら batch を二分割して再帰する。
        # 一部 batch の失敗はジョブ全体を止めず warning として継続し、
        # 全タスク失敗のときだけジョブを FAILED にする(部分成功)。
        # partial_failed_steps は資料抽出ステップと共有(冒頭で初期化済み)。
        for index, task in enumerate(llm_tasks, start=1):
            if self._is_cancelled(job_id):
                return
            label = _STEP_LABELS_JA[task.name]
            self._set_step(
                job_id,
                task.name,
                OntologyBuildStepStatus.RUNNING,
                task.progress_ja,
            )
            self._emit(
                job_id,
                f"Enterprise AI に問い合わせています({index}/{len(llm_tasks)}: {label})。"
                f"{task.progress_ja}",
                step=task.name,
            )
            task_extractions, task_warnings = self._execute_llm_task(
                job_id, client, task, label=label
            )
            validated_extractions.extend(task_extractions)
            if task_warnings:
                partial_failed_steps.add(task.name)
                warnings.extend(task_warnings)
                self._add_warnings(job_id, task_warnings)
                self._set_step(
                    job_id,
                    task.name,
                    (
                        OntologyBuildStepStatus.FAILED
                        if not task_extractions
                        else OntologyBuildStepStatus.RUNNING
                    ),
                    "一部の LLM 抽出に失敗しました(成功分で継続します)。",
                    code="BATCH_EXTRACTION_FAILED",
                )
                self._emit(
                    job_id,
                    f"{label}: 一部の抽出に失敗しました。成功分で処理を継続します。",
                    code="BATCH_EXTRACTION_FAILED",
                    step=task.name,
                )
            if task_extractions:
                self._set_step(
                    job_id,
                    task.name,
                    OntologyBuildStepStatus.SUCCEEDED,
                    "応答を検証しました。",
                )
                self._emit(job_id, f"{label}: 抽出候補を検証しました。", step=task.name)

        if llm_tasks and not validated_extractions:
            self._fail(
                job_id,
                "すべての LLM 抽出に失敗しました。"
                "時間をおいて再実行するか、資料を分割してください。",
                error_code="LLM_EXTRACTION_FAILED",
            )
            return

        if self._is_cancelled(job_id):
            return
        if validated_extractions:
            self._set_step(
                job_id,
                OntologyBuildStepName.PROPOSAL_REGISTRATION,
                OntologyBuildStepStatus.RUNNING,
                "DB schema revision を準備中…",
            )
            try:
                view, ontology = self._runtime.build_proposal_scope(
                    job.profile_id,
                    schema_fingerprint=str(prepared_schema.schema_fingerprint),
                )
            except (OntologyNotFoundError, OntologyStateConflictError, ValueError) as exc:
                message = getattr(exc, "message_ja", str(exc))
                self._set_step(
                    job_id,
                    OntologyBuildStepName.PROPOSAL_REGISTRATION,
                    OntologyBuildStepStatus.FAILED,
                    "DB schema revision の準備に失敗しました。",
                )
                self._fail(job_id, message)
                return
            for validated in validated_extractions:
                step_drafts, step_warnings = convert_extraction_to_proposals(
                    validated.extraction,
                    ontology=ontology,
                    view=view,
                    job_id=job_id,
                    inferred_by=inferred_by,
                    qa_sql_texts=validated.cross_check_sql,
                    source_evidence=validated.source_evidence,
                )
                drafts.extend(step_drafts)
                warnings.extend(step_warnings)
                self._set_step(
                    job_id,
                    validated.name,
                    OntologyBuildStepStatus.SUCCEEDED,
                    f"候補 {len(step_drafts)} 件、警告 {len(step_warnings)} 件",
                )
                self._emit(
                    job_id,
                    f"{validated.label_ja}: 候補 {len(step_drafts)} 件、"
                    f"警告 {len(step_warnings)} 件を抽出しました。",
                )
        if ontology is None:
            self._set_step(
                job_id,
                OntologyBuildStepName.PROPOSAL_REGISTRATION,
                OntologyBuildStepStatus.RUNNING,
                "Markdown 下書き生成用の DB schema revision を準備中…",
            )
            try:
                view, ontology = self._runtime.build_proposal_scope(
                    job.profile_id,
                    schema_fingerprint=str(prepared_schema.schema_fingerprint),
                )
            except (OntologyNotFoundError, OntologyStateConflictError, ValueError) as exc:
                message = getattr(exc, "message_ja", str(exc))
                self._set_step(
                    job_id,
                    OntologyBuildStepName.PROPOSAL_REGISTRATION,
                    OntologyBuildStepStatus.FAILED,
                    "Markdown 下書き生成用の DB schema revision 準備に失敗しました。",
                )
                self._fail(job_id, message)
                return

        # Profile 削除と競合した場合に draft を生成しないため、書込直前に再確認する。
        self._runtime.ensure_profile(job.profile_id)
        self._set_step(
            job_id,
            OntologyBuildStepName.PROPOSAL_REGISTRATION,
            OntologyBuildStepStatus.RUNNING,
            f"候補 {len(drafts)} 件から Markdown 下書きを生成中…",
        )
        draft_inputs: list[ProposalDraft] = []
        # 同一 run 内で複数ステップが同じ候補を出した場合の dedup。provenance の
        # timestamp は揺れるため、安定 ID(node/edge)と kind で同一性を判定する。
        seen_payload_keys: set[str] = set()
        for draft in drafts:
            if self._is_cancelled(job_id):
                return
            payload_key = _proposal_payload_key(draft.kind.value, dict(draft.payload.values))
            if payload_key in seen_payload_keys:
                continue
            seen_payload_keys.add(payload_key)
            draft_inputs.append(draft)
            self._set_step(
                job_id,
                OntologyBuildStepName.PROPOSAL_REGISTRATION,
                OntologyBuildStepStatus.RUNNING,
                f"{len(draft_inputs)} 件を整理済み(候補 {len(drafts)} 件)",
            )
        self._set_step(
            job_id,
            OntologyBuildStepName.PROPOSAL_REGISTRATION,
            OntologyBuildStepStatus.RUNNING,
            "Markdown 下書きをレンダリング中…",
        )
        self._emit(job_id, "Markdown 下書きをレンダリングしています。")
        try:
            markdown_output = render_ontology_build_markdown(
                profile_id=job.profile_id,
                schema_context=schema_context,
                drafts=draft_inputs,
                warnings=warnings,
                source_count=len(job.source_document_ids),
                qa_pair_count=len(qa_pairs),
                business_text_present=bool(text_units),
                qa_pairs=qa_pairs,
                ontology=ontology,
                profile_view=view,
            )
        except Exception as exc:
            logger.warning("ontology_build_markdown_render_failed", exc_info=True)
            self._set_step(
                job_id,
                OntologyBuildStepName.PROPOSAL_REGISTRATION,
                OntologyBuildStepStatus.FAILED,
                "Markdown 下書きのレンダリングに失敗しました。",
            )
            self._fail(job_id, f"Markdown 下書きのレンダリングに失敗しました: {exc}")
            return
        self._set_step(
            job_id,
            OntologyBuildStepName.PROPOSAL_REGISTRATION,
            OntologyBuildStepStatus.RUNNING,
            f"Markdown 下書きをレンダリングしました({len(markdown_output)} 文字)。",
        )
        self._emit(job_id, f"Markdown 下書きをレンダリングしました({len(markdown_output)} 文字)。")

        def save_progress(message_ja: str) -> None:
            # 保存完了イベントには機械可読コードを付け、frontend は文言正規表現ではなく
            # このコードで Markdown 下書きの再取得を判断できるようにする。
            code = "MARKDOWN_DRAFT_UPDATED" if "保存しました" in message_ja else ""
            self._set_step(
                job_id,
                OntologyBuildStepName.PROPOSAL_REGISTRATION,
                OntologyBuildStepStatus.RUNNING,
                message_ja,
                code=code,
            )
            self._emit(
                job_id,
                message_ja,
                code=code,
                step=OntologyBuildStepName.PROPOSAL_REGISTRATION,
            )

        try:
            draft_ontology, markdown_artifact = self._runtime.create_build_markdown_draft(
                profile_id=job.profile_id,
                base_revision_id=ontology.revision.id,
                payloads=[draft.payload for draft in draft_inputs],
                titles=[draft.title_ja for draft in draft_inputs],
                markdown=markdown_output,
                note=f"AI 構築 Markdown 下書き: {len(draft_inputs)} 件",
                on_progress=save_progress,
            )
        except Exception as exc:
            logger.warning("ontology_build_markdown_save_failed", exc_info=True)
            message = getattr(exc, "message_ja", str(exc))
            self._set_step(
                job_id,
                OntologyBuildStepName.PROPOSAL_REGISTRATION,
                OntologyBuildStepStatus.FAILED,
                "Markdown 下書きの保存に失敗しました。",
            )
            self._fail(job_id, message)
            return
        registered_note = (
            f"Markdown 下書き v{draft_ontology.revision.version} を生成しました"
            f"(候補 {len(draft_inputs)} 件、警告 {len(warnings)} 件)。"
        )
        self._set_step(
            job_id,
            OntologyBuildStepName.PROPOSAL_REGISTRATION,
            OntologyBuildStepStatus.RUNNING,
            "構築 job の完了状態を保存しています…",
            code="FINALIZING",
        )
        self._emit(job_id, "構築 job の完了状態を保存しています。", code="FINALIZING")

        def finish(job: OntologyBuildJob) -> None:
            finished_at = utc_now()
            for step in job.steps:
                if step.name == OntologyBuildStepName.PROPOSAL_REGISTRATION:
                    step.status = OntologyBuildStepStatus.SUCCEEDED
                    step.detail_ja = registered_note
                    step.code = ""
                    if step.started_at is None:
                        step.started_at = finished_at
                    step.finished_at = finished_at
                    break
            if any(step.status == OntologyBuildStepStatus.SUCCEEDED for step in job.steps):
                # 一部の LLM batch が失敗しても成功分で Draft を生成した場合は
                # 部分成功として区別する(全 discard しない)。
                job.status = (
                    OntologyBuildStatus.SUCCEEDED_WITH_WARNINGS
                    if partial_failed_steps
                    else OntologyBuildStatus.SUCCEEDED
                )
            else:
                job.status = OntologyBuildStatus.FAILED
            job.proposal_ids = []
            job.draft_revision_id = draft_ontology.revision.id
            job.draft_etag = str(markdown_artifact.get("etag") or "")
            job.markdown_output = markdown_output
            job.warnings_ja = [*job.warnings_ja, *warnings]
            job.finished_at = finished_at
            job.events.append(
                OntologyBuildEvent(
                    message_ja=registered_note,
                    code="MARKDOWN_DRAFT_UPDATED",
                    step=OntologyBuildStepName.PROPOSAL_REGISTRATION,
                )
            )
            job.events.append(
                OntologyBuildEvent(
                    message_ja=(
                        f"構築が完了しました(Markdown 下書き v{draft_ontology.revision.version}、"
                        f"警告 {len(warnings)} 件)。"
                    )
                )
            )
            if len(job.events) > _MAX_JOB_EVENTS:
                del job.events[: len(job.events) - _MAX_JOB_EVENTS]

        try:
            self._update_persisted_copy(job_id, finish)
        except Exception as exc:
            logger.warning("ontology_build_job_finalize_failed", exc_info=True)
            message = getattr(exc, "message_ja", str(exc))
            self._fail(
                job_id,
                f"構築 job の完了状態の保存に失敗しました: {message}",
                failed_step=OntologyBuildStepName.PROPOSAL_REGISTRATION,
                failed_step_detail_ja="構築 job の完了状態の保存に失敗しました。",
            )
            return
        finished_job = self.get(job_id)
        record_job(
            job_type="build",
            status=(
                "succeeded"
                if finished_job is not None
                and finished_job.status
                in {OntologyBuildStatus.SUCCEEDED, OntologyBuildStatus.SUCCEEDED_WITH_WARNINGS}
                else "failed"
            ),
            error_code="none" if finished_job is not None else "result_missing",
        )

    def _is_cancelled(self, job_id: str) -> bool:
        current = self.get(job_id)
        return current is not None and current.status == OntologyBuildStatus.CANCELLED
