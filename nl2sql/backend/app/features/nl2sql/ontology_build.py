"""AI オントロジー構築(業務エンティティ命名・Q/A 学習・自然言語補強)。

OCI Enterprise AI の出力は Pydantic(:class:`OntologyBuildExtraction`)で検証し、
profile view スコープ外の owner/object/column を参照する候補は proposal 化せず
warnings に落とす。生成物は既存 PROPOSALS(承認フロー)にのみ登録され、
accept → draft → publish の既存ゲートを通過するまで SQL 生成には使われない。

job と実行入力は Oracle store に永続化する。local は thread、production は独立 worker が
同じ処理を実行し、成果物は proposal の承認ゲートを通る。
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

from app.features.nl2sql.ontology_catalog import SchemaOntology
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
    utc_now,
)
from app.features.nl2sql.ontology_observability import record_job, record_source_extraction
from app.features.nl2sql.ontology_sources import OntologySourceStorage, extract_ontology_source
from app.features.nl2sql.ontology_store import canonical_json, stable_ontology_id
from app.settings import get_settings

logger = logging.getLogger(__name__)

_QUESTION_HEADERS = ("QUESTION", "質問", "TEXT", "PROMPT")
_SQL_HEADERS = ("SQL", "ANSWER_SQL", "回答SQL", "正解SQL")
_NOTE_HEADERS = ("NOTE", "備考", "COMMENT", "メモ")
_DANGEROUS_EXPRESSION_TOKENS = (";", "--", "/*")
_MAX_QA_PAIRS = 200


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
    if suffix in {".xlsx", ".xlsm"}:
        try:
            import openpyxl  # type: ignore[import-untyped]

            workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            warnings.append(f"XLSX の読込に失敗しました: {exc}")
            return []
        rows: list[list[str]] = []
        for sheet in workbook.worksheets:
            for raw_row in sheet.iter_rows(values_only=True):
                rows.append([str(value).strip() if value is not None else "" for value in raw_row])
            break  # 先頭シートのみ対象
        return rows
    if suffix in {".csv", ".tsv", ".txt", ""}:
        text = content.decode("utf-8-sig", errors="replace")
        delimiter = "\t" if suffix == ".tsv" else ","
        return [
            [str(value).strip() for value in row]
            for row in csv.reader(io.StringIO(text), delimiter=delimiter)
        ]
    warnings.append(f"{suffix} は未対応の形式です。CSV または XLSX を指定してください。")
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
        if len(pairs) >= _MAX_QA_PAIRS:
            warnings.append(f"Q/A は先頭 {_MAX_QA_PAIRS} 件だけを利用します。")
            break
    if not pairs and not warnings:
        warnings.append("有効な Q/A 行がありません。")
    return pairs, warnings


# --- profile view スコープの解決 --------------------------------------------------------------


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


def build_schema_context(ontology: SchemaOntology, view: ProfileOntologyView) -> str:
    """LLM に渡す profile スコープの schema 情報(JSON 文字列、決定論)。"""

    scoped = set(view.node_ids)
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
                "logical_name": node.business_name_ja,
                "comment": node.description_ja,
            }
        )
    return json.dumps(
        {"objects": sorted(objects.values(), key=lambda item: str(item["object"]))},
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


# --- job service ------------------------------------------------------------------------------

_STEP_LABELS_JA: dict[OntologyBuildStepName, str] = {
    OntologyBuildStepName.SOURCE_EXTRACTION: "資料の抽出",
    OntologyBuildStepName.SCHEMA_CONTEXT: "スキーマ情報の準備",
    OntologyBuildStepName.SCHEMA_NAMING: "業務エンティティ命名",
    OntologyBuildStepName.QA_EXTRACTION: "Q/A からの抽出",
    OntologyBuildStepName.TEXT_EXTRACTION: "業務説明からの抽出",
    OntologyBuildStepName.PROPOSAL_REGISTRATION: "提案の登録",
}
_MAX_JOB_EVENTS = 100
# 完了(succeeded/failed)job の in-memory 保持上限。超過分は start 時に古い順へ破棄する
_MAX_FINISHED_JOBS = 20


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
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return job.model_copy(deep=True)
        document = self._runtime.store.get_document("jobs", {"job_id": job_id})
        if document is None or document.get("job_type") != "build":
            return None
        restored = OntologyBuildJob.model_validate(document["payload"])
        with self._lock:
            self._jobs[job_id] = restored
            input_payload = document.get("input_payload")
            if isinstance(input_payload, dict):
                self._inputs[job_id] = dict(input_payload)
        return restored.model_copy(deep=True)

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

    # --- internal ---------------------------------------------------------------------------

    def _prune_finished_jobs_locked(self) -> None:
        """lock 保持中に呼ぶ。完了 job が上限を超えたら古い順に破棄する(queued/running は保護)。"""
        finished = [
            job
            for job in self._jobs.values()
            if job.status in {OntologyBuildStatus.SUCCEEDED, OntologyBuildStatus.FAILED}
        ]
        overflow = len(finished) - _MAX_FINISHED_JOBS
        if overflow <= 0:
            return
        finished.sort(key=lambda job: (job.finished_at or job.created_at, job.id))
        for job in finished[:overflow]:
            del self._jobs[job.id]

    def _update(self, job_id: str, mutate: Any) -> None:
        updated: OntologyBuildJob | None = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                mutate(job)
                updated = job.model_copy(deep=True)
        if updated is not None:
            self._persist_job(updated)

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

    def _run_safely(self, job_id: str, business_text: str, qa_pairs: list[QaPair]) -> None:
        try:
            self._run(job_id, business_text, qa_pairs)
        except Exception as exc:  # pragma: no cover - 予期しない障害の最終防壁
            logger.warning("ontology_build_job_failed", exc_info=True)
            message = f"オントロジー構築に失敗しました: {exc}"

            def mutate(job: OntologyBuildJob) -> None:
                job.status = OntologyBuildStatus.FAILED
                job.error_message_ja = message
                job.finished_at = utc_now()

            self._update(job_id, mutate)
            record_job(job_type="build", status="failed", error_code="unexpected")

    def _fail(self, job_id: str, message_ja: str, *, skip_pending_steps: bool = True) -> None:
        def mutate(job: OntologyBuildJob) -> None:
            job.status = OntologyBuildStatus.FAILED
            job.error_message_ja = message_ja
            job.finished_at = utc_now()
            if skip_pending_steps:
                for step in job.steps:
                    if step.status in {
                        OntologyBuildStepStatus.PENDING,
                        OntologyBuildStepStatus.RUNNING,
                    }:
                        step.status = OntologyBuildStepStatus.SKIPPED
                        step.finished_at = utc_now()

        self._update(job_id, mutate)
        self._emit(job_id, message_ja)
        record_job(job_type="build", status="failed", error_code="build_failed")

    def _run(self, job_id: str, business_text: str, qa_pairs: list[QaPair]) -> None:
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
        source_evidence: list[Any] = []
        if job.source_document_ids:
            self._set_step(
                job_id,
                OntologyBuildStepName.SOURCE_EXTRACTION,
                OntologyBuildStepStatus.RUNNING,
                f"資料 {len(job.source_document_ids)} 件を抽出中…",
            )
            extracted_texts: list[str] = []
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
                    extracted_texts.append(extracted.business_text)
                    extracted_pairs.extend(extracted.qa_pairs)
                    source_evidence.extend(
                        chunk.evidence(source) for chunk in extracted.chunks[:100]
                    )
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
                    source = self._update_source_document(
                        source,
                        status=OntologySourceStatus.FAILED,
                        error_message_ja=str(exc),
                    )
                    self._update_source_progress(
                        job_id,
                        source.id,
                        status=OntologySourceStatus.FAILED,
                        error_message_ja=str(exc),
                    )
                    record_source_extraction(
                        file_format=Path(source.filename).suffix, status="failed"
                    )
            business_text = "\n\n".join(
                item for item in [business_text.strip(), *extracted_texts] if item.strip()
            )
            qa_by_key = {(item.question, item.sql): item for item in [*qa_pairs, *extracted_pairs]}
            qa_pairs = list(qa_by_key.values())
            current_job = self.get(job_id)
            current_sources = current_job.sources if current_job is not None else []
            failed_sources = sum(
                source.status == OntologySourceStatus.FAILED for source in current_sources
            )
            self._set_step(
                job_id,
                OntologyBuildStepName.SOURCE_EXTRACTION,
                OntologyBuildStepStatus.SUCCEEDED,
                f"資料 {len(job.source_document_ids)} 件、証拠 {len(source_evidence)} 件"
                + (f"、失敗 {failed_sources} 件" if failed_sources else ""),
            )
            self._emit(job_id, "資料の抽出と証拠位置の記録が完了しました。")
        if client is None or not callable(configured) or not configured():
            self._fail(
                job_id,
                "OCI Enterprise AI が未設定のため、AI オントロジー構築を実行できません。",
            )
            return

        # --- スキーマ情報の準備(公開 Ontology の同期を含むため時間がかかり得る) ---
        self._set_step(
            job_id,
            OntologyBuildStepName.SCHEMA_CONTEXT,
            OntologyBuildStepStatus.RUNNING,
            "公開 Ontology から profile 範囲のスキーマ情報を取得中…",
        )
        self._emit(job_id, "公開 Ontology から profile 範囲のスキーマ情報を取得しています。")
        view, ontology = self._runtime.profile_view(job.profile_id)
        scoped_node_ids = set(view.node_ids)
        object_count = sum(
            1
            for node in ontology.nodes
            if node.kind in {OntologyNodeKind.TABLE, OntologyNodeKind.VIEW}
            and node.id in scoped_node_ids
        )
        column_count = sum(
            1
            for node in ontology.nodes
            if node.kind == OntologyNodeKind.COLUMN and node.id in scoped_node_ids
        )
        if object_count == 0:
            # LLM を無駄撃ちせず、原因(schema 情報未解決)を明確に返す
            self._set_step(
                job_id,
                OntologyBuildStepName.SCHEMA_CONTEXT,
                OntologyBuildStepStatus.FAILED,
                "profile 範囲に表・ビューがありません。",
            )
            self._fail(
                job_id,
                "profile の対象オブジェクトを公開 Ontology に解決できません。"
                "スキーマ情報を更新してから再実行してください。",
            )
            return

        schema_context = build_schema_context(ontology, view)
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
        qa_sql_texts = [pair.sql for pair in qa_pairs]

        drafts: list[ProposalDraft] = []
        warnings: list[str] = []
        step_names = {step.name for step in job.steps}
        llm_steps: list[tuple[OntologyBuildStepName, str, str, list[str] | None]] = []
        if OntologyBuildStepName.SCHEMA_NAMING in step_names:
            llm_steps.append(
                (
                    OntologyBuildStepName.SCHEMA_NAMING,
                    "schema_context の各表・ビューに日本語の業務エンティティ名・説明・同義語を"
                    "提案してください。関係と指標は提案不要です。",
                    schema_context,
                    None,
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
                qa_context = json.dumps(
                    {
                        "schema_context": json.loads(schema_context),
                        "qa_pairs": [pair.model_dump(mode="json") for pair in qa_pairs],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                llm_steps.append(
                    (
                        OntologyBuildStepName.QA_EXTRACTION,
                        "qa_pairs の質問と正解 SQL から、実際に使われた JOIN パス"
                        "(relationships)と業務指標(metrics)を抽出してください。"
                        "SQL に現れない関係を推測しないでください。",
                        qa_context,
                        qa_sql_texts,
                    )
                )
        if OntologyBuildStepName.TEXT_EXTRACTION in step_names:
            if not business_text.strip():
                self._set_step(
                    job_id,
                    OntologyBuildStepName.TEXT_EXTRACTION,
                    OntologyBuildStepStatus.SKIPPED,
                    "抽出できる業務説明がないためスキップしました。",
                )
            else:
                text_context = json.dumps(
                    {
                        "schema_context": json.loads(schema_context),
                        "business_text": business_text,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                llm_steps.append(
                    (
                        OntologyBuildStepName.TEXT_EXTRACTION,
                        "business_text の業務説明から、関係候補(relationships)・同義語(synonyms)・"
                        "業務指標(metrics)を抽出してください。schema_context に対応づかない内容は "
                        "warnings_ja に残してください。",
                        text_context,
                        None,
                    )
                )

        for index, (name, prompt, context, cross_check) in enumerate(llm_steps, start=1):
            label = _STEP_LABELS_JA[name]
            self._set_step(
                job_id,
                name,
                OntologyBuildStepStatus.RUNNING,
                f"Enterprise AI({inferred_by})に問い合わせ中…",
            )
            self._emit(
                job_id,
                f"Enterprise AI に問い合わせています({index}/{len(llm_steps)}: {label})。",
            )
            try:
                raw = client.generate(
                    prompt=prompt,
                    context=context,
                    system_prompt=_EXTRACTION_SYSTEM_PROMPT,
                )
                self._set_step(job_id, name, OntologyBuildStepStatus.RUNNING, "応答を検証中…")
                self._emit(job_id, f"{label}: 応答を受信しました。検証しています。")
                extraction = parse_extraction(raw)
                step_drafts, step_warnings = convert_extraction_to_proposals(
                    extraction,
                    ontology=ontology,
                    view=view,
                    job_id=job_id,
                    inferred_by=inferred_by,
                    qa_sql_texts=cross_check,
                    source_evidence=source_evidence,
                )
                drafts.extend(step_drafts)
                warnings.extend(step_warnings)
                self._set_step(
                    job_id,
                    name,
                    OntologyBuildStepStatus.SUCCEEDED,
                    f"提案 {len(step_drafts)} 件、警告 {len(step_warnings)} 件",
                )
                self._emit(
                    job_id,
                    f"{label}: 提案 {len(step_drafts)} 件、"
                    f"警告 {len(step_warnings)} 件を抽出しました。",
                )
            except Exception as exc:
                logger.warning("ontology_build_step_failed step=%s", name.value, exc_info=True)
                warnings.append(f"{name.value} の抽出に失敗しました: {exc}")
                self._set_step(
                    job_id, name, OntologyBuildStepStatus.FAILED, "LLM 抽出に失敗しました。"
                )
                self._emit(job_id, f"{label}: LLM 抽出に失敗しました。")

        self._set_step(
            job_id,
            OntologyBuildStepName.PROPOSAL_REGISTRATION,
            OntologyBuildStepStatus.RUNNING,
            f"候補 {len(drafts)} 件を登録中…",
        )
        proposal_ids: list[str] = []
        # 新規 AI 構築のたびにレビュー一覧をリセットする(前回の承認/却下/レビュー待ちを
        # 一掃してから今回の候補だけを登録する)。取得失敗は登録処理を止めない。
        try:
            self._runtime.supersede_profile_proposals(job.profile_id)
        except Exception:  # pragma: no cover - 一掃失敗は登録処理を止めない
            logger.warning("ontology_build_supersede_failed", exc_info=True)
        # 同一 run 内で複数ステップが同じ候補を出した場合の dedup。provenance の
        # timestamp は揺れるため、安定 ID(node/edge)と kind で同一性を判定する。
        seen_payload_keys: set[str] = set()
        for draft in drafts:
            payload_key = _proposal_payload_key(draft.kind.value, dict(draft.payload.values))
            if payload_key in seen_payload_keys:
                continue
            seen_payload_keys.add(payload_key)
            proposal = self._runtime.create_build_proposal(
                profile_id=job.profile_id,
                job_id=job_id,
                title_ja=draft.title_ja,
                description_ja=draft.description_ja,
                kind=draft.kind,
                proposal_payload=draft.payload,
            )
            proposal_ids.append(proposal.id)
            self._set_step(
                job_id,
                OntologyBuildStepName.PROPOSAL_REGISTRATION,
                OntologyBuildStepStatus.RUNNING,
                f"{len(proposal_ids)} 件登録済み(候補 {len(drafts)} 件)",
            )
        registered_note = f"提案 {len(proposal_ids)} 件を登録しました。"
        self._set_step(
            job_id,
            OntologyBuildStepName.PROPOSAL_REGISTRATION,
            OntologyBuildStepStatus.SUCCEEDED,
            registered_note,
        )
        self._emit(job_id, registered_note)

        def finish(job: OntologyBuildJob) -> None:
            job.status = (
                OntologyBuildStatus.SUCCEEDED
                if any(step.status == OntologyBuildStepStatus.SUCCEEDED for step in job.steps)
                else OntologyBuildStatus.FAILED
            )
            job.proposal_ids = proposal_ids
            job.warnings_ja = [*job.warnings_ja, *warnings]
            job.finished_at = utc_now()

        self._update(job_id, finish)
        finished_job = self.get(job_id)
        record_job(
            job_type="build",
            status=(
                "succeeded"
                if finished_job is not None
                and finished_job.status == OntologyBuildStatus.SUCCEEDED
                else "failed"
            ),
            error_code="none" if finished_job is not None else "result_missing",
        )
        self._emit(
            job_id,
            f"構築が完了しました(提案 {len(proposal_ids)} 件、警告 {len(warnings)} 件)。",
        )
