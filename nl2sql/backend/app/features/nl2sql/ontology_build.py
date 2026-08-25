"""AI オントロジー構築(業務エンティティ命名・Q/A 学習・自然言語補強)。

OCI Enterprise AI の入力 schema は Profile + DB schema catalog から直接作る。
出力は Pydantic(:class:`OntologyBuildExtraction`)で検証し、profile スコープ外の
owner/object/column を参照する候補は Markdown Draft へ入れず warnings に落とす。
生成物は承認済み draft revision と Markdown Draft artifact として保存され、
publish で Published Markdown へコピーされるまで SQL 生成には使われない。

job と実行入力は Oracle store に永続化する。local は thread、production は独立 worker が
同じ処理を実行し、成果物は Markdown Draft の確認ゲートを通る。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import threading
import time
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
)
from app.features.nl2sql.ontology_sources import (
    ExtractedSourceChunk,
    OntologySourceStorage,
    extract_ontology_source,
)
from app.features.nl2sql.ontology_store import canonical_json, stable_ontology_id
from app.features.nl2sql.tabular_files import (
    WORKBOOK_SUFFIXES,
    TabularFileReadError,
    normalize_workbook_scalar,
    read_workbook_sheets,
    select_workbook_sheet,
    validate_tabular_text_signature,
)
from app.settings import get_settings

logger = logging.getLogger(__name__)

_QUESTION_HEADERS = ("QUESTION", "質問", "TEXT", "PROMPT")
_SQL_HEADERS = ("SQL", "ANSWER_SQL", "回答SQL", "正解SQL")
_NOTE_HEADERS = ("NOTE", "備考", "COMMENT", "メモ")
_DANGEROUS_EXPRESSION_TOKENS = (";", "--", "/*")
_ONTOLOGY_BUILD_LLM_CONTEXT_MAX_CHARS = 100_000
_LLM_CONTEXT_HEADROOM_CHARS = 512


# --- Q/A workbook ---------------------------------------------------------------------------


def _normalized_header(value: str) -> str:
    return value.strip().upper().replace(" ", "").replace("_", "")


def _header_index(headers: list[str], candidates: tuple[str, ...]) -> int | None:
    normalized = [_normalized_header(header) for header in headers]
    for candidate in candidates:
        key = _normalized_header(candidate)
        if key in normalized:
            return normalized.index(key)
    return None


def _rows_from_content(filename: str, content: bytes, warnings: list[str]) -> list[list[str]]:
    suffix = Path(filename).suffix.lower()
    if suffix in WORKBOOK_SUFFIXES:
        try:
            sheet, sheet_warnings = select_workbook_sheet(read_workbook_sheets(filename, content))
        except TabularFileReadError as exc:
            warnings.append(str(exc))
            return []
        warnings.extend(sheet_warnings)
        return [
            [normalize_workbook_scalar(value).strip() for value in raw_row]
            for raw_row in sheet.rows
        ]
    if suffix in {".csv", ".txt", ""}:
        try:
            validate_tabular_text_signature(content)
        except TabularFileReadError as exc:
            warnings.append(str(exc))
            return []
        text = content.decode("utf-8-sig", errors="replace")
        return [
            [str(value).strip() for value in row]
            for row in csv.reader(io.StringIO(text), delimiter=",")
        ]
    warnings.append(f"{suffix} は未対応の形式です。CSV、XLSX、XLS のいずれかを指定してください。")
    return []


def parse_qa_workbook(filename: str, content: bytes) -> tuple[list[QaPair], list[str]]:
    """Q/A Excel/CSV を検証済み :class:`QaPair` へ変換する(SELECT/WITH 以外は warning)。"""

    warnings: list[str] = []
    rows = _rows_from_content(filename, content, warnings)
    if not rows:
        if not warnings:
            warnings.append("Q/A ファイルに行がありません。")
        return [], warnings
    headers = rows[0]
    question_index = _header_index(headers, _QUESTION_HEADERS)
    sql_index = _header_index(headers, _SQL_HEADERS)
    if question_index is None or sql_index is None:
        warnings.append("Q/A ファイルには QUESTION(質問)列と SQL 列が必要です。")
        return [], warnings
    note_index = _header_index(headers, _NOTE_HEADERS)
    pairs: list[QaPair] = []
    for line_no, row in enumerate(rows[1:], start=2):
        question = row[question_index] if len(row) > question_index else ""
        sql = row[sql_index] if len(row) > sql_index else ""
        if not question.strip() and not sql.strip():
            continue
        if not question.strip() or not sql.strip():
            warnings.append(f"{line_no} 行目: 質問または SQL が空のため無視しました。")
            continue
        first_token = sql.strip().split(None, 1)[0].upper() if sql.strip() else ""
        if first_token not in {"SELECT", "WITH"}:
            warnings.append(f"{line_no} 行目: SELECT/WITH 以外の SQL のため無視しました。")
            continue
        note = row[note_index] if note_index is not None and len(row) > note_index else ""
        pairs.append(QaPair(question=question.strip(), sql=sql.strip(), note_ja=note.strip()))
    if not pairs and not warnings:
        warnings.append("有効な Q/A 行がありません。")
    return pairs, warnings


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
                "description_ja": "Oracle view dependency",
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

    def resolve_object(self, reference: str) -> OntologyNode | None:
        key = reference.replace('"', "").strip().upper()
        if not key:
            return None
        if key in self.objects:
            return self.objects[key]
        candidates = self.objects_by_name.get(key, [])
        return candidates[0] if len(candidates) == 1 else None

    def resolve_column(self, reference: str) -> OntologyNode | None:
        key = reference.replace('"', "").strip().upper()
        parts = [part for part in key.split(".") if part]
        if len(parts) == 3:
            return self.columns.get(".".join(parts))
        if len(parts) == 2:
            # OBJECT.COLUMN 形式は owner が一意に決まる場合だけ解決する
            matches = [
                node
                for node_key, node in self.columns.items()
                if node_key.endswith("." + ".".join(parts))
            ]
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


def _draft_nodes(draft: ProposalDraft) -> list[OntologyNode]:
    nodes: list[OntologyNode] = []
    for value in draft.payload.values.get("node_upserts") or []:
        try:
            nodes.append(OntologyNode.model_validate(value))
        except Exception:
            continue
    return nodes


def _draft_edges(draft: ProposalDraft) -> list[OntologyEdge]:
    edges: list[OntologyEdge] = []
    for value in draft.payload.values.get("edge_upserts") or []:
        try:
            edges.append(OntologyEdge.model_validate(value))
        except Exception:
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
    if view is None:
        return bool(edge.enabled)
    override = _profile_edge_override_map(view).get(edge.id, {})
    if "allowed_path" in override:
        return bool(override.get("allowed_path"))
    return edge.id in set(view.allowed_path_ids) or bool(edge.enabled)


def _node_markdown_lines(node: OntologyNode, view: ProfileOntologyView | None) -> list[str]:
    aliases = ", ".join(sorted(set(node.aliases))) or "なし"
    usage = _effective_table_usage(node, view)
    lines = [
        f"- {_effective_business_name(node, view)} ({_md_code(_physical_mapping_label(node))})",
        f"  - kind: {node.kind.value}",
        f"  - description: {_md_text(node.description_ja)}",
        f"  - aliases: {aliases}",
    ]
    if usage:
        lines.append(f"  - usage: {_md_text(usage)}")
    if node.provenance.evidence:
        lines.append("  - evidence:")
        for evidence in node.provenance.evidence[:5]:
            label = str(evidence.label or evidence.location or evidence.source_id or "").strip()
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
        f"- {source_label} -> {target_label}: {_md_text(edge.relationship_name_ja)}",
        f"  - kind: {edge.kind.value}",
        f"  - cardinality: {_effective_edge_cardinality(edge, view)}",
        f"  - allowed_path: {'true' if _effective_edge_allowed(edge, view) else 'false'}",
        f"  - evidence: {_md_text(edge.description_ja)}",
    ]
    if edge.join_conditions:
        lines.append("  - join_conditions:")
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
                    f"  - business_name: {_md_text(business_name)}",
                    f"  - description: {description}",
                    f"  - usage: {_md_text(usage, '未設定')}",
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
        columns = item.get("columns") if isinstance(item.get("columns"), list) else []
        lines.extend(
            [
                f"- {_md_code(object_name)} ({_md_text(object_type)})",
                f"  - business_name: {_md_text(logical_name)}",
                f"  - description: {_md_text(comment)}",
                f"  - columns: {len(columns)}",
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
                lines.append(f"  - expression_sql: {_md_code(definition.get('expression_sql'))}")
                lines.append(f"  - aggregation: {_md_text(definition.get('aggregation'))}")
                lines.append(f"  - unit: {_md_text(definition.get('unit'), 'なし')}")
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
                    "  - type: business_rule",
                    f"  - statement: {_md_text(rule.statement_ja)}",
                    f"  - applies_to: {_md_text(applies_to, '未設定')}",
                    f"  - severity: {rule.severity.value}",
                    f"  - execution_mode: {rule.execution_mode.value}",
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
                    "  - type: enum_value",
                    f"  - code: {_md_text(enum_value.code)}",
                    f"  - literal: {_md_code(enum_value.physical_literal)}",
                    f"  - label: {_md_text(enum_value.label_ja)}",
                    f"  - property: {_md_text(property_label)}",
                    f"  - aliases: {', '.join(enum_value.aliases) or 'なし'}",
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
    ontology: SchemaOntology | None = None,
    profile_view: ProfileOntologyView | None = None,
) -> str:
    """AI 構築結果から、確認・編集用 Markdown Draft を決定論生成する。"""

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
        "# Ontology Draft",
        "",
        "## Input Summary",
        f"- Profile: {_md_code(profile_id)}",
        f"- Business description: {'あり' if business_text_present else 'なし'}",
        f"- Q/A pairs: {qa_pair_count}",
        f"- Source documents: {source_count}",
        f"- DB schema objects: {object_count}",
        f"- DB schema columns: {column_count}",
        f"- Existing schema relationships: {relationship_count}",
        "",
        "## Physical Objects",
    ]
    lines.extend(physical_lines or ["- なし"])
    lines.extend(
        [
            "",
            "## Entities",
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
                        f"  - description: {_md_text(node.description_ja)}",
                        f"  - aliases: {aliases}",
                        f"  - confidence: {node.confidence:.2f}",
                    ]
                )
                if node.aliases:
                    synonym_lines.extend(
                        [
                            f"- target: {_md_code(_physical_mapping_label(node))}",
                            f"  - aliases: {aliases}",
                            f"  - evidence: {_md_text(node.description_ja)}",
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
                        f"- {source_label} -> {target_label}: {edge.relationship_name_ja}",
                        f"  - cardinality: {edge.cardinality.value}",
                        f"  - evidence: {_md_text(edge.description_ja)}",
                        f"  - confidence: {edge.confidence:.2f}",
                    ]
                )
                if edge.join_conditions:
                    relationship_lines.append("  - join_conditions:")
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
                        f"  - expression_sql: {_md_code(expression)}",
                        f"  - base_columns: {base_columns or 'なし'}",
                        f"  - aggregation: {_md_text(aggregation)}",
                        f"  - unit: {_md_text(unit, 'なし')}",
                        f"  - description: {_md_text(node.description_ja)}",
                        f"  - confidence: {node.confidence:.2f}",
                    ]
                )
        elif draft.kind == OntologyProposalKind.ALIAS:
            for node in nodes:
                aliases = ", ".join(sorted(set(node.aliases))) or "なし"
                synonym_lines.extend(
                    [
                        f"- target: {_md_code(_physical_mapping_label(node))}",
                        f"  - aliases: {aliases}",
                        f"  - evidence: {_md_text(node.description_ja)}",
                    ]
                )

    merged_entity_lines = [*profile_entity_lines, *entity_lines]
    merged_relationship_lines = [*profile_relationship_lines, *relationship_lines]
    lines.extend(merged_entity_lines or ["- なし"])
    lines.extend(["", "## Relationships / Join"])
    lines.extend(merged_relationship_lines or ["- なし"])
    lines.extend(["", "## Metrics"])
    lines.extend([*profile_metric_lines, *metric_lines] or ["- なし"])
    lines.extend(["", "## Business Rules / Enum Values"])
    lines.extend(profile_rule_enum_lines or ["- なし"])
    lines.extend(["", "## Synonyms"])
    lines.extend(synonym_lines or ["- なし"])
    lines.extend(["", "## Evidence / Warnings"])
    unique_warnings = list(dict.fromkeys(warning for warning in warnings if warning.strip()))
    lines.extend(f"- {warning}" for warning in unique_warnings)
    if not unique_warnings:
        lines.append("- なし")
    return "\n".join(lines).rstrip() + "\n"


# --- LLM 呼び出し -----------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = (
    "あなたは NL2SQL 用オントロジーの構築支援器です。JSON object だけを返し、"
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
    "抽出ルール: (1) 業務文中の名詞をエンティティ候補、動詞・述語を関係候補として抽出する。"
    "(2) 各関係の cardinality は one_to_one / one_to_many / many_to_one / many_to_many から"
    "必ず選ぶ。判断できない場合のみ unknown とし、理由を warnings_ja に 1 行残す。"
    "(3) 各エンティティの主識別子(主キーに相当する列)を description_ja に明記する。"
    "確信が持てない候補は confidence を下げるか warnings_ja に残してください。"
    "文言はすべて日本語にしてください。"
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


@dataclass(frozen=True)
class _ValidatedBuildExtraction:
    name: OntologyBuildStepName
    label_ja: str
    extraction: OntologyBuildExtraction
    cross_check_sql: list[str] | None
    source_evidence: list[OntologyEvidence]


def _llm_call_chars(prompt: str, context: str) -> int:
    return (
        len(_EXTRACTION_SYSTEM_PROMPT)
        + len(prompt)
        + len(context)
        + _LLM_CONTEXT_HEADROOM_CHARS
    )


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
            "qa_pairs": [pair.model_dump(mode="json") for pair in pairs],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


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
    batches: list[list[_BuildTextUnit]] = []
    current: list[_BuildTextUnit] = []
    for unit in expanded:
        candidate = [*current, unit]
        candidate_context = _dump_text_context(schema_context, candidate)
        if _llm_call_chars(prompt, candidate_context) <= _ONTOLOGY_BUILD_LLM_CONTEXT_MAX_CHARS:
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
    batches: list[list[QaPair]] = []
    current: list[QaPair] = []
    for pair in pairs:
        candidate = [*current, pair]
        candidate_context = _dump_qa_context(schema_context, candidate)
        if _llm_call_chars(prompt, candidate_context) <= _ONTOLOGY_BUILD_LLM_CONTEXT_MAX_CHARS:
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
    OntologyBuildStepName.PROPOSAL_REGISTRATION: "Markdown Draft 生成",
}
_MAX_JOB_EVENTS = 100
# 完了(succeeded/failed/cancelled)job の in-memory 保持上限。超過分は start 時に古い順へ破棄する
_MAX_FINISHED_JOBS = 20
_TERMINAL_STATUSES = {
    OntologyBuildStatus.SUCCEEDED,
    OntologyBuildStatus.FAILED,
    OntologyBuildStatus.CANCELLED,
}
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
                    raise ValueError(
                        "同じ Idempotency-Key が別の構築リクエストに使用されています。"
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
        finished_marker = job.finished_at.isoformat() if job.finished_at else "none"
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
            # 二度押しは既存 idempotency 機構で同じ再実行 job に合流させる
            idempotency_key=f"retry:{job_id}:{finished_marker}",
        )

    # --- internal ---------------------------------------------------------------------------

    def _prune_finished_jobs_locked(self) -> None:
        """lock 保持中に呼ぶ。完了 job が上限を超えたら古い順に破棄する(queued/running は保護)。"""
        finished = [
            job
            for job in self._jobs.values()
            if job.status
            in {
                OntologyBuildStatus.SUCCEEDED,
                OntologyBuildStatus.FAILED,
                OntologyBuildStatus.CANCELLED,
            }
        ]
        overflow = len(finished) - _MAX_FINISHED_JOBS
        if overflow <= 0:
            return
        finished.sort(key=lambda job: (job.finished_at or job.created_at, job.id))
        for job in finished[:overflow]:
            del self._jobs[job.id]

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
        if (
            normalized.status != OntologyBuildStatus.SUCCEEDED
            or not (
                normalized.draft_revision_id
                or normalized.draft_etag
                or normalized.markdown_output
            )
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
        if not step.detail_ja or "構築 job の完了状態" in step.detail_ja:
            step.detail_ja = "Markdown Draft を生成しました。"
        step.finished_at = normalized.finished_at or step.finished_at or step.started_at
        return normalized

    def _persist_job(self, job: OntologyBuildJob) -> None:
        current = self._runtime.store.get_document("jobs", {"job_id": job.id})
        with self._lock:
            input_payload = self._inputs.get(job.id)
        if input_payload is None and current is not None:
            current_input = current.get("input_payload")
            input_payload = dict(current_input) if isinstance(current_input, dict) else {}
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
    ) -> None:
        now = utc_now()

        def mutate(job: OntologyBuildJob) -> None:
            for step in job.steps:
                if step.name == name:
                    step.status = status
                    if detail_ja:
                        step.detail_ja = detail_ja
                    if status == OntologyBuildStepStatus.RUNNING and step.started_at is None:
                        step.started_at = now
                    if status in {
                        OntologyBuildStepStatus.SUCCEEDED,
                        OntologyBuildStepStatus.FAILED,
                        OntologyBuildStepStatus.SKIPPED,
                    }:
                        step.finished_at = now

        self._update(job_id, mutate)

    def _emit(self, job_id: str, message_ja: str) -> None:
        """アクティビティタイムラインへ 1 行追記する(上限超過は古い順に間引く)。"""

        event = OntologyBuildEvent(message_ja=message_ja)

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
    ) -> None:
        now = utc_now()

        def mutate(job: OntologyBuildJob) -> None:
            job.status = OntologyBuildStatus.FAILED
            job.error_message_ja = message_ja
            job.finished_at = now
            for step in job.steps:
                if failed_step is not None and step.name == failed_step:
                    step.status = OntologyBuildStepStatus.FAILED
                    if failed_step_detail_ja:
                        step.detail_ja = failed_step_detail_ja
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
                    if extracted.warnings_ja:
                        raise RuntimeError(" / ".join(extracted.warnings_ja))
                    if not extracted.chunks and not extracted.qa_pairs:
                        raise RuntimeError(
                            f"{source.filename}: 抽出可能なテキストまたは Q/A がありません。"
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
                    self._fail(
                        job_id,
                        error_message,
                        failed_step=OntologyBuildStepName.SOURCE_EXTRACTION,
                        failed_step_detail_ja="資料の抽出に失敗しました。",
                    )
                    return
            qa_by_key = {(item.question, item.sql): item for item in [*qa_pairs, *extracted_pairs]}
            qa_pairs = list(qa_by_key.values())
            self._set_step(
                job_id,
                OntologyBuildStepName.SOURCE_EXTRACTION,
                OntologyBuildStepStatus.SUCCEEDED,
                f"資料 {len(job.source_document_ids)} 件、証拠 {source_evidence_count} 件、"
                f"Q/A {len(extracted_pairs)} 件を抽出しました。",
            )
            self._emit(job_id, "資料の抽出と証拠位置の記録が完了しました。")
        if client is None or not callable(configured) or not configured():
            self._fail(
                job_id,
                "OCI Enterprise AI が未設定のため、AI オントロジー構築を実行できません。",
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
            self._set_step(
                job_id,
                OntologyBuildStepName.SCHEMA_CONTEXT,
                OntologyBuildStepStatus.FAILED,
                (
                    "profile 範囲の DB object が曖昧です。"
                    if schema_errors
                    else "profile 範囲に DB 表・ビューがありません。"
                ),
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
                    "qa_pairs の質問と正解 SQL から、実際に使われた JOIN パス"
                    "(relationships)と業務指標(metrics)を抽出してください。"
                    "SQL に現れない関係を推測しないでください。"
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
                    "business_text_chunks の全項目を読み、関係候補(relationships)・同義語"
                    "(synonyms)・業務指標(metrics)を抽出してください。名詞をエンティティ、"
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
                total_units = sum(len(batch) for batch in text_batches)
                for batch_index, batch in enumerate(text_batches, start=1):
                    processed += len(batch)
                    llm_tasks.append(
                        _OntologyBuildLlmTask(
                            name=OntologyBuildStepName.TEXT_EXTRACTION,
                            prompt=prompt,
                            context=_dump_text_context(schema_payload, batch),
                            progress_ja=(
                                f"業務説明 chunk batch {batch_index}/{len(text_batches)} を処理中"
                                f"({processed}/{total_units} chunk)。"
                            ),
                            source_evidence=[
                                evidence
                                for unit in batch
                                if (evidence := unit.evidence()) is not None
                            ],
                        )
                    )

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
            )
            try:
                raw = client.generate(
                    prompt=task.prompt,
                    context=task.context,
                    system_prompt=_EXTRACTION_SYSTEM_PROMPT,
                )
                self._set_step(job_id, task.name, OntologyBuildStepStatus.RUNNING, "応答を検証中…")
                self._emit(job_id, f"{label}: 応答を受信しました。検証しています。")
                extraction = parse_extraction(raw)
                validated_extractions.append(
                    _ValidatedBuildExtraction(
                        name=task.name,
                        label_ja=label,
                        extraction=extraction,
                        cross_check_sql=task.cross_check_sql,
                        source_evidence=task.source_evidence,
                    )
                )
                self._set_step(
                    job_id,
                    task.name,
                    OntologyBuildStepStatus.SUCCEEDED,
                    "応答を検証しました。",
                )
                self._emit(job_id, f"{label}: 抽出候補を検証しました。")
            except Exception as exc:
                logger.warning("ontology_build_step_failed step=%s", task.name.value, exc_info=True)
                warning = f"{task.name.value} の抽出に失敗しました: {exc}"
                warnings.append(warning)
                self._add_warnings(job_id, [warning])
                self._set_step(
                    job_id, task.name, OntologyBuildStepStatus.FAILED, "LLM 抽出に失敗しました。"
                )
                self._emit(job_id, f"{label}: LLM 抽出に失敗しました。")
                self._fail(
                    job_id,
                    f"{label}: LLM 抽出に失敗しました。{exc}",
                    failed_step=task.name,
                    failed_step_detail_ja="LLM 抽出に失敗しました。",
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
                "Markdown Draft 生成用の DB schema revision を準備中…",
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
                    "Markdown Draft 生成用の DB schema revision 準備に失敗しました。",
                )
                self._fail(job_id, message)
                return

        # Profile 削除と競合した場合に draft を生成しないため、書込直前に再確認する。
        self._runtime.ensure_profile(job.profile_id)
        self._set_step(
            job_id,
            OntologyBuildStepName.PROPOSAL_REGISTRATION,
            OntologyBuildStepStatus.RUNNING,
            f"候補 {len(drafts)} 件から Markdown Draft を生成中…",
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
            "Markdown Draft をレンダリング中…",
        )
        self._emit(job_id, "Markdown Draft をレンダリングしています。")
        try:
            markdown_output = render_ontology_build_markdown(
                profile_id=job.profile_id,
                schema_context=schema_context,
                drafts=draft_inputs,
                warnings=warnings,
                source_count=len(job.source_document_ids),
                qa_pair_count=len(qa_pairs),
                business_text_present=bool(text_units),
                ontology=ontology,
                profile_view=view,
            )
        except Exception as exc:
            logger.warning("ontology_build_markdown_render_failed", exc_info=True)
            self._set_step(
                job_id,
                OntologyBuildStepName.PROPOSAL_REGISTRATION,
                OntologyBuildStepStatus.FAILED,
                "Markdown Draft のレンダリングに失敗しました。",
            )
            self._fail(job_id, f"Markdown Draft のレンダリングに失敗しました: {exc}")
            return
        self._set_step(
            job_id,
            OntologyBuildStepName.PROPOSAL_REGISTRATION,
            OntologyBuildStepStatus.RUNNING,
            f"Markdown Draft をレンダリングしました({len(markdown_output)} 文字)。",
        )
        self._emit(job_id, f"Markdown Draft をレンダリングしました({len(markdown_output)} 文字)。")

        def save_progress(message_ja: str) -> None:
            self._set_step(
                job_id,
                OntologyBuildStepName.PROPOSAL_REGISTRATION,
                OntologyBuildStepStatus.RUNNING,
                message_ja,
            )
            self._emit(job_id, message_ja)

        try:
            draft_ontology, markdown_artifact = self._runtime.create_build_markdown_draft(
                profile_id=job.profile_id,
                base_revision_id=ontology.revision.id,
                payloads=[draft.payload for draft in draft_inputs],
                titles=[draft.title_ja for draft in draft_inputs],
                markdown=markdown_output,
                note=f"AI 構築 Markdown Draft: {len(draft_inputs)} 件",
                on_progress=save_progress,
            )
        except Exception as exc:
            logger.warning("ontology_build_markdown_save_failed", exc_info=True)
            message = getattr(exc, "message_ja", str(exc))
            self._set_step(
                job_id,
                OntologyBuildStepName.PROPOSAL_REGISTRATION,
                OntologyBuildStepStatus.FAILED,
                "Markdown Draft の保存に失敗しました。",
            )
            self._fail(job_id, message)
            return
        registered_note = (
            f"Markdown Draft v{draft_ontology.revision.version} を生成しました"
            f"(候補 {len(draft_inputs)} 件、警告 {len(warnings)} 件)。"
        )
        self._set_step(
            job_id,
            OntologyBuildStepName.PROPOSAL_REGISTRATION,
            OntologyBuildStepStatus.RUNNING,
            "構築 job の完了状態を保存しています…",
        )
        self._emit(job_id, "構築 job の完了状態を保存しています。")

        def finish(job: OntologyBuildJob) -> None:
            finished_at = utc_now()
            for step in job.steps:
                if step.name == OntologyBuildStepName.PROPOSAL_REGISTRATION:
                    step.status = OntologyBuildStepStatus.SUCCEEDED
                    step.detail_ja = registered_note
                    if step.started_at is None:
                        step.started_at = finished_at
                    step.finished_at = finished_at
                    break
            job.status = (
                OntologyBuildStatus.SUCCEEDED
                if any(step.status == OntologyBuildStepStatus.SUCCEEDED for step in job.steps)
                else OntologyBuildStatus.FAILED
            )
            job.proposal_ids = []
            job.draft_revision_id = draft_ontology.revision.id
            job.draft_etag = str(markdown_artifact.get("etag") or "")
            job.markdown_output = markdown_output
            job.warnings_ja = [*job.warnings_ja, *warnings]
            job.finished_at = finished_at
            job.events.append(OntologyBuildEvent(message_ja=registered_note))
            job.events.append(
                OntologyBuildEvent(
                    message_ja=(
                        f"構築が完了しました(Markdown Draft v{draft_ontology.revision.version}、"
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
                if finished_job is not None and finished_job.status == OntologyBuildStatus.SUCCEEDED
                else "failed"
            ),
            error_code="none" if finished_job is not None else "result_missing",
        )

    def _is_cancelled(self, job_id: str) -> bool:
        current = self.get(job_id)
        return current is not None and current.status == OntologyBuildStatus.CANCELLED
