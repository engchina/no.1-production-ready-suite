"""Ontology を利用する NL2SQL query-session API。

既存 ``/preview`` / ``/execute`` を互換 API として残しつつ、新 UI はこの router の
二段階確認フローを利用する。router 自体は独立しており、application root 側では
``ontology_router.router`` を include するだけでよい。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from threading import RLock
from typing import Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pr_backend_core import ApiResponse
from pydantic import Field

from app.settings import get_settings

from .models import (
    AllowedObjects,
    ExplainPlanData,
    Nl2SqlEngine,
    Nl2SqlProfile,
    PreviewData,
    PreviewRequest,
    QueryResults,
)
from .ontology_build import OntologyBuildService, parse_qa_workbook
from .ontology_catalog import (
    SchemaOntology,
    build_schema_ontology,
    evolve_schema_ontology,
    interpret_question_deterministically,
    migrate_profile_ontology_view,
)
from .ontology_mermaid import render_mermaid_er
from .ontology_models import (
    ColumnQueryPolicy,
    GraphPatch,
    MetricDefinition,
    OntologyBuildJob,
    OntologyContract,
    OntologyEdge,
    OntologyEdgeKind,
    OntologyNode,
    OntologyNodeKind,
    OntologyProposal,
    OntologyProposalKind,
    OntologyProposalPayload,
    OntologyProposalStatus,
    OntologyReviewStatus,
    OntologyRevision,
    OntologyRevisionStatus,
    OntologySourceKind,
    OntologySqlGenerationContext,
    ProfileOntologyView,
    QaPair,
    QuerySession,
    QuerySessionCreate,
    QuestionIntentGraph,
    SqlConfirmationRequest,
)
from .ontology_observability import observe_stage, record_findings, record_transition
from .ontology_service import (
    OntologyGateBlockedError,
    OntologyIntegrityError,
    OntologyNotFoundError,
    OntologyQuerySessionService,
    OntologyServiceError,
    OntologyStateConflictError,
    OntologyVersionConflictError,
)
from .ontology_store import (
    InMemoryOntologyStore,
    OntologyCollection,
    OntologyStore,
    OntologyVersionConflict,
    OracleOntologyStore,
    canonical_json,
    compute_etag,
    stable_ontology_id,
)
from .service import nl2sql_service

logger = logging.getLogger(__name__)

_STORE_IDENTITY_FIELDS: dict[OntologyCollection, tuple[str, ...]] = {
    "revisions": ("revision_id",),
    "nodes": ("revision_id", "node_id"),
    "edges": ("revision_id", "edge_id"),
    "profile_views": ("profile_id",),
    "query_sessions": ("session_id",),
    "artifacts": ("artifact_id",),
    "proposals": ("proposal_id",),
    "idempotency": ("operation", "idempotency_key"),
}

_BUSINESS_NODE_KINDS = frozenset(
    {
        OntologyNodeKind.BUSINESS_ENTITY,
        OntologyNodeKind.BUSINESS_EVENT,
        OntologyNodeKind.PROPERTY,
        OntologyNodeKind.METRIC,
        OntologyNodeKind.BUSINESS_TERM,
    }
)
_BUSINESS_EDGE_KINDS = frozenset({OntologyEdgeKind.BUSINESS_RELATIONSHIP, OntologyEdgeKind.MAPS_TO})


class QuerySessionApiCreate(OntologyContract):
    question: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    allowed_objects: AllowedObjects = Field(default_factory=AllowedObjects)
    row_limit: int | None = Field(default=None, ge=1, le=5000)
    engine: Nl2SqlEngine = Nl2SqlEngine.AUTO


class GenerateSqlRequest(OntologyContract):
    intent_version: int = Field(ge=1)
    base_version: int = Field(ge=1)
    ontology_revision_id: str = Field(min_length=1)
    confirm_intent: Literal[True]


class ImprovementProposalRequest(OntologyContract):
    title_ja: str = ""
    description_ja: str = ""
    kind: OntologyProposalKind = OntologyProposalKind.QUERY_EXAMPLE
    proposal_payload: OntologyProposalPayload = Field(default_factory=OntologyProposalPayload)
    patch: GraphPatch | None = None
    base_revision_id: str = ""
    intent_version: int | None = Field(default=None, ge=1)
    summary: str = ""


class OntologyDraftRequest(OntologyContract):
    """既存 revision から業務 node/edge だけを変更した新 draft を作る。"""

    base_etag: str = Field(min_length=1)
    note: str = ""
    node_upserts: list[OntologyNode] = Field(default_factory=list)
    edge_upserts: list[OntologyEdge] = Field(default_factory=list)
    remove_node_ids: list[str] = Field(default_factory=list)
    remove_edge_ids: list[str] = Field(default_factory=list)


class OntologyPublishRequest(OntologyContract):
    etag: str = Field(min_length=1)


class SqlBindingRequest(OntologyContract):
    session_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    ontology_revision_id: str = Field(min_length=1)
    intent_version: int = Field(ge=1)
    sql_hash: str = Field(min_length=1)
    validation_hash: str = Field(min_length=1)
    generation_context_hash: str = Field(min_length=1)
    confirm_sql: Literal[True]

    def binding(self) -> SqlConfirmationRequest:
        return SqlConfirmationRequest(
            artifact_id=self.artifact_id,
            ontology_revision_id=self.ontology_revision_id,
            intent_version=self.intent_version,
            sql_hash=self.sql_hash,
            validation_hash=self.validation_hash,
            generation_context_hash=self.generation_context_hash,
        )


class ProfileOntologyViewPatch(OntologyContract):
    """Profile view で編集可能な業務 metadata だけを更新する。"""

    base_etag: str = Field(min_length=1)
    table_usages_ja: dict[str, str] | None = None
    column_policies: dict[str, ColumnQueryPolicy] | None = None
    allowed_path_ids: list[str] | None = None
    node_overrides: list[dict[str, Any]] | None = None
    edge_overrides: list[dict[str, Any]] | None = None
    schema_fingerprint: str | None = None
    physical_scope: dict[str, list[str]] | None = None


class QueryRuntimeContext(OntologyContract):
    allowed_objects: AllowedObjects
    row_limit: int | None = Field(default=None, ge=1, le=5000)
    engine: Nl2SqlEngine = Nl2SqlEngine.AUTO
    retrieved_node_ids: list[str] = Field(default_factory=list)


class OntologyGraphData(OntologyContract):
    revision: OntologyRevision
    nodes: list[OntologyNode] = Field(default_factory=list)
    edges: list[OntologyEdge] = Field(default_factory=list)


class OntologyRevisionListData(OntologyContract):
    revisions: list[OntologyRevision] = Field(default_factory=list)
    active_revision_id: str = ""


class ProfileOntologyViewData(OntologyContract):
    profile_ontology_view: ProfileOntologyView
    ontology_graph: OntologyGraphData
    # 公開 Ontology に解決できなかった対象オブジェクト名の診断(応答のみ、永続化しない)
    warnings_ja: list[str] = Field(default_factory=list)


class QuerySessionData(OntologyContract):
    session: QuerySession
    profile_ontology_view: ProfileOntologyView
    ontology_graph: OntologyGraphData
    preview: PreviewData | None = None
    result: QueryResults | None = None
    performance_check: ExplainPlanData | None = None
    ontology_trace_summary: dict[str, Any] = Field(default_factory=dict)


class QueryExecutionData(QuerySessionData):
    result: QueryResults


class OntologyProposalReviewData(OntologyContract):
    proposal: OntologyProposal
    draft: OntologyGraphData | None = None


class OntologyApiRuntime:
    """既存 NL2SQL service と versioned Ontology domain/store を接続する。"""

    def __init__(
        self,
        *,
        legacy_service: Any = nl2sql_service,
        store: OntologyStore | None = None,
        session_service: OntologyQuerySessionService | None = None,
    ) -> None:
        self.legacy_service = legacy_service
        self.store = store or self._default_store(legacy_service)
        self.sessions = session_service or OntologyQuerySessionService()
        self._lock = RLock()
        self._store_ready = False
        self._ontology: SchemaOntology | None = None
        self._ontologies: dict[str, SchemaOntology] = {}
        self._session_views: dict[str, ProfileOntologyView] = {}
        self._profile_view_overrides: dict[str, ProfileOntologyView] = {}
        self._contexts: dict[str, QueryRuntimeContext] = {}
        self._previews: dict[str, PreviewData] = {}
        self._results: dict[str, QueryResults] = {}
        self._plans: dict[str, ExplainPlanData] = {}
        self._embeddings: dict[str, dict[str, list[float]]] = {}

    @staticmethod
    def _default_store(legacy_service: Any) -> OntologyStore:
        settings = get_settings()
        if settings.nl2sql_persistence_mode.strip().lower() != "oracle":
            return InMemoryOntologyStore()
        adapter = getattr(legacy_service, "_oracle_adapter", None)
        connection_factory = getattr(adapter, "connection", None)
        if not callable(connection_factory):
            raise RuntimeError("Oracle Ontology store 用の connection factory がありません。")
        return OracleOntologyStore(connection_factory=connection_factory)

    def current_ontology(self) -> SchemaOntology:
        with self._lock:
            return self._sync_ontology()

    def list_ontology_revisions(self) -> tuple[list[OntologyRevision], str]:
        with self._lock:
            self._sync_ontology()
            revisions = sorted(
                (item.revision for item in self._ontologies.values()),
                key=lambda item: (item.version, item.created_at, item.id),
                reverse=True,
            )
            active_revision_id = self._query_ontology().revision.id
            return [item.model_copy(deep=True) for item in revisions], active_revision_id

    def ontology_revision(self, revision_id: str) -> SchemaOntology:
        with self._lock:
            self._sync_ontology()
            ontology = self._ontologies.get(revision_id)
            if ontology is None:
                raise OntologyNotFoundError(
                    "ONTOLOGY_REVISION_NOT_FOUND",
                    "指定された Ontology revision が見つかりません。",
                )
            return ontology.model_copy(deep=True)

    def create_ontology_draft(
        self,
        base_revision_id: str,
        request: OntologyDraftRequest,
    ) -> SchemaOntology:
        """物理 schema node を不変に保ち、業務定義だけを新 revision へ反映する。"""

        with self._lock:
            self._sync_ontology()
            base = self._ontologies.get(base_revision_id)
            if base is None:
                raise OntologyNotFoundError(
                    "ONTOLOGY_REVISION_NOT_FOUND",
                    "元になる Ontology revision が見つかりません。",
                )
            if base.revision.etag != request.base_etag:
                raise OntologyVersionConflictError(
                    "REVISION_ETAG_MISMATCH",
                    "Ontology revision が更新されています。再読込してください。",
                )

            base_nodes = {node.id: node for node in base.nodes}
            base_edges = {edge.id: edge for edge in base.edges}
            remove_nodes = set(request.remove_node_ids)
            remove_edges = set(request.remove_edge_ids)
            illegal_node_removals = sorted(
                node_id
                for node_id in remove_nodes
                if node_id not in base_nodes or base_nodes[node_id].kind not in _BUSINESS_NODE_KINDS
            )
            illegal_edge_removals = sorted(
                edge_id
                for edge_id in remove_edges
                if edge_id not in base_edges or base_edges[edge_id].kind not in _BUSINESS_EDGE_KINDS
            )
            if illegal_node_removals or illegal_edge_removals:
                raise OntologyIntegrityError(
                    "PHYSICAL_ONTOLOGY_IMMUTABLE",
                    "物理 schema node/edge は業務 Ontology draft から削除できません。",
                )

            node_map = {
                node_id: node.model_copy(deep=True)
                for node_id, node in base_nodes.items()
                if node_id not in remove_nodes
            }
            for node in request.node_upserts:
                existing_node = base_nodes.get(node.id)
                if node.kind not in _BUSINESS_NODE_KINDS or (
                    existing_node is not None and existing_node.kind not in _BUSINESS_NODE_KINDS
                ):
                    raise OntologyIntegrityError(
                        "PHYSICAL_ONTOLOGY_IMMUTABLE",
                        "業務 draft では BusinessEntity/Event/Property/Metric/Term "
                        "だけを変更できます。",
                    )
                if node.review_status not in {
                    OntologyReviewStatus.PROPOSED,
                    OntologyReviewStatus.REVIEWED,
                    OntologyReviewStatus.APPROVED,
                }:
                    raise OntologyIntegrityError(
                        "BUSINESS_NODE_REVIEW_STATUS_INVALID",
                        "業務 node の review status が draft として不正です。",
                    )
                self._validate_business_node_mapping(node, base_nodes)
                node_map[node.id] = node.model_copy(
                    deep=True,
                    update={
                        "provenance": node.provenance.model_copy(
                            update={
                                "source_kind": OntologySourceKind.MANUAL,
                                "source_id": base.revision.id,
                            }
                        )
                    },
                )

            edge_map = {
                edge_id: edge.model_copy(deep=True)
                for edge_id, edge in base_edges.items()
                if edge_id not in remove_edges
                and edge.source_node_id not in remove_nodes
                and edge.target_node_id not in remove_nodes
            }
            for edge in request.edge_upserts:
                existing_edge = base_edges.get(edge.id)
                if edge.kind not in _BUSINESS_EDGE_KINDS or (
                    existing_edge is not None and existing_edge.kind not in _BUSINESS_EDGE_KINDS
                ):
                    raise OntologyIntegrityError(
                        "PHYSICAL_ONTOLOGY_IMMUTABLE",
                        "業務 draft では BusinessRelationship/MapsTo だけを変更できます。",
                    )
                if edge.source_node_id not in node_map or edge.target_node_id not in node_map:
                    raise OntologyIntegrityError(
                        "BUSINESS_EDGE_ENDPOINT_NOT_FOUND",
                        "業務 relation の始点または終点 node が存在しません。",
                    )
                source_kind = node_map[edge.source_node_id].kind
                target_kind = node_map[edge.target_node_id].kind
                if edge.kind == OntologyEdgeKind.BUSINESS_RELATIONSHIP and (
                    source_kind not in _BUSINESS_NODE_KINDS
                    or target_kind not in _BUSINESS_NODE_KINDS
                ):
                    raise OntologyIntegrityError(
                        "BUSINESS_RELATIONSHIP_ENDPOINT_INVALID",
                        "BusinessRelationship は業務 node 同士を接続してください。",
                    )
                if edge.kind == OntologyEdgeKind.MAPS_TO and (
                    (source_kind in _BUSINESS_NODE_KINDS) == (target_kind in _BUSINESS_NODE_KINDS)
                ):
                    raise OntologyIntegrityError(
                        "BUSINESS_MAPPING_ENDPOINT_INVALID",
                        "MapsTo は業務 node と物理 schema node を 1 つずつ接続してください。",
                    )
                if edge.review_status not in {
                    OntologyReviewStatus.PROPOSED,
                    OntologyReviewStatus.REVIEWED,
                    OntologyReviewStatus.APPROVED,
                }:
                    raise OntologyIntegrityError(
                        "BUSINESS_EDGE_REVIEW_STATUS_INVALID",
                        "業務 relation の review status が draft として不正です。",
                    )
                if edge.kind == OntologyEdgeKind.BUSINESS_RELATIONSHIP and not edge.join_conditions:
                    raise OntologyIntegrityError(
                        "BUSINESS_RELATIONSHIP_JOIN_REQUIRED",
                        "業務 relation には明示的な Join 条件が必要です。",
                    )
                ordinals = sorted(condition.ordinal for condition in edge.join_conditions)
                if ordinals != list(range(1, len(ordinals) + 1)):
                    raise OntologyIntegrityError(
                        "BUSINESS_JOIN_ORDINAL_INVALID",
                        "複合 Join 条件の ordinal は 1 から連続させてください。",
                    )
                for condition in edge.join_conditions:
                    self._validate_physical_column_ref(condition.left, base_nodes)
                    self._validate_physical_column_ref(condition.right, base_nodes)
                if any(join_type.value == "cross" for join_type in edge.allowed_join_types):
                    raise OntologyIntegrityError(
                        "BUSINESS_CROSS_JOIN_NOT_ALLOWED",
                        "業務 relation に CROSS JOIN を許可できません。",
                    )
                edge_map[edge.id] = edge.model_copy(
                    deep=True,
                    update={
                        "provenance": edge.provenance.model_copy(
                            update={
                                "source_kind": OntologySourceKind.MANUAL,
                                "source_id": base.revision.id,
                            }
                        )
                    },
                )

            next_version = (
                max(
                    (item.revision.version for item in self._ontologies.values()),
                    default=base.revision.version,
                )
                + 1
            )
            revision_id = stable_ontology_id(
                "ontology_revision",
                base.revision.id,
                request.model_dump(mode="json"),
                next_version,
            )
            revision = OntologyRevision(
                id=revision_id,
                version=next_version,
                status=OntologyRevisionStatus.DRAFT,
                schema_fingerprint=base.revision.schema_fingerprint,
                parent_revision_id=base.revision.id,
                note=request.note or "業務 Ontology draft",
            )
            nodes = sorted(
                (
                    node.model_copy(update={"revision_id": revision_id}, deep=True)
                    for node in node_map.values()
                ),
                key=lambda item: item.id,
            )
            edges = sorted(
                (
                    edge.model_copy(update={"revision_id": revision_id}, deep=True)
                    for edge in edge_map.values()
                ),
                key=lambda item: item.id,
            )
            registered = self.sessions.register_revision(revision, nodes=nodes, edges=edges)
            ontology = SchemaOntology(revision=registered, nodes=nodes, edges=edges)
            self._ontologies[registered.id] = ontology
            self._ontology = ontology
            self._persist_ontology(ontology)
            return ontology.model_copy(deep=True)

    def publish_ontology_revision(
        self,
        revision_id: str,
        request: OntologyPublishRequest,
    ) -> SchemaOntology:
        with self._lock:
            self._sync_ontology()
            ontology = self._ontologies.get(revision_id)
            if ontology is None:
                raise OntologyNotFoundError(
                    "ONTOLOGY_REVISION_NOT_FOUND",
                    "公開する Ontology revision が見つかりません。",
                )
            unresolved = [
                item.id
                for item in [*ontology.nodes, *ontology.edges]
                if (
                    (isinstance(item, OntologyNode) and item.kind in _BUSINESS_NODE_KINDS)
                    or (isinstance(item, OntologyEdge) and item.kind in _BUSINESS_EDGE_KINDS)
                )
                and item.review_status != OntologyReviewStatus.APPROVED
            ]
            if unresolved:
                raise OntologyGateBlockedError(
                    "ONTOLOGY_REVIEW_REQUIRED",
                    "未承認または orphan の業務 node/relation があるため公開できません。",
                    finding_codes=unresolved,
                )
            self._validate_metric_definitions_for_publish(ontology)
            published = self.sessions.publish_revision(revision_id, etag=request.etag)
            updated = SchemaOntology(
                revision=published,
                nodes=ontology.nodes,
                edges=ontology.edges,
            )
            for archived in self.sessions.archive_published_revisions_except(revision_id):
                previous = self._ontologies.get(archived.id)
                if previous is not None:
                    archived_ontology = previous.model_copy(
                        update={"revision": archived},
                        deep=True,
                    )
                    self._ontologies[archived.id] = archived_ontology
                    # 変更は revision status のみ(nodes/edges は不変)
                    self._persist_ontology(archived_ontology, include_graph=False)
            self._ontologies[revision_id] = updated
            if self._ontology is not None and self._ontology.revision.id == revision_id:
                self._ontology = updated
            self._persist_ontology(updated, include_graph=False)
            return updated.model_copy(deep=True)

    def _validate_metric_definitions_for_publish(self, ontology: SchemaOntology) -> None:
        node_by_id = {node.id: node for node in ontology.nodes}
        invalid_codes: list[str] = []
        dangerous_tokens = (
            ";",
            " INSERT ",
            " UPDATE ",
            " DELETE ",
            " MERGE ",
            " DROP ",
            " ALTER ",
            " CREATE ",
            " BEGIN ",
            " EXEC ",
            " CALL ",
        )
        for node in ontology.nodes:
            if node.kind != OntologyNodeKind.METRIC:
                continue
            raw = node.metadata.get("metric_definition")
            if not isinstance(raw, Mapping):
                continue
            try:
                definition = MetricDefinition.model_validate(raw)
            except Exception:
                invalid_codes.append(f"{node.id}:METRIC_DEFINITION_INVALID")
                continue
            expression = f" {definition.expression_sql.upper()} "
            if any(token in expression for token in dangerous_tokens):
                invalid_codes.append(f"{node.id}:METRIC_DEFINITION_SQL_UNSAFE")
            missing_columns = [
                column_id
                for column_id in definition.base_column_node_ids
                if (column := node_by_id.get(column_id)) is None
                or column.kind != OntologyNodeKind.COLUMN
            ]
            if missing_columns:
                invalid_codes.append(f"{node.id}:METRIC_BASE_COLUMN_UNKNOWN")
            missing_grain = [
                grain_id
                for grain_id in definition.grain_node_ids
                if grain_id not in node_by_id
            ]
            if missing_grain:
                invalid_codes.append(f"{node.id}:METRIC_GRAIN_UNKNOWN")
        if invalid_codes:
            raise OntologyGateBlockedError(
                "METRIC_DEFINITION_INVALID",
                "正式指標定義に未解決の列参照または危険な SQL 断片があります。",
                finding_codes=invalid_codes,
            )

    @staticmethod
    def _has_business_elements(ontology: SchemaOntology) -> bool:
        return any(node.kind in _BUSINESS_NODE_KINDS for node in ontology.nodes) or any(
            edge.kind in _BUSINESS_EDGE_KINDS for edge in ontology.edges
        )

    def _query_ontology(self) -> SchemaOntology:
        """Schema drift draft は、次の publish まで確認済み query scope を置換しない。

        例外として、published にもドラフトにも業務定義が 1 件も無い(純物理)場合は
        schema drift を自動 publish する。守るべき承認済み定義が無いのに古い(空の)
        published へ固定され続けると、profile view が永遠に空になるため。
        """

        latest = self._sync_ontology()
        published = [
            item
            for item in self._ontologies.values()
            if item.revision.status == OntologyRevisionStatus.PUBLISHED
        ]
        if not published:
            if latest.revision.status == OntologyRevisionStatus.PUBLISHED:
                return latest
            bootstrap = self.sessions.publish_revision(
                latest.revision.id,
                etag=latest.revision.etag,
            )
            bootstrapped = SchemaOntology(
                revision=bootstrap,
                nodes=latest.nodes,
                edges=latest.edges,
            )
            self._ontologies[bootstrap.id] = bootstrapped
            self._ontology = bootstrapped
            # nodes/edges は _sync_ontology の register 時に永続化済み(header のみ更新)
            self._persist_ontology(bootstrapped, include_graph=False)
            return bootstrapped
        best = max(
            published,
            key=lambda item: (
                item.revision.version,
                item.revision.published_at or item.revision.created_at,
                item.revision.id,
            ),
        )
        if (
            latest.revision.id != best.revision.id
            and latest.revision.status != OntologyRevisionStatus.PUBLISHED
            and latest.revision.schema_fingerprint != best.revision.schema_fingerprint
            and not self._has_business_elements(best)
            and not self._has_business_elements(latest)
        ):
            return self.publish_ontology_revision(
                latest.revision.id,
                OntologyPublishRequest(etag=latest.revision.etag),
            )
        return best

    @staticmethod
    def _validate_business_node_mapping(
        node: OntologyNode,
        physical_nodes: Mapping[str, OntologyNode],
    ) -> None:
        if node.kind != OntologyNodeKind.BUSINESS_TERM and not node.physical_mappings:
            raise OntologyIntegrityError(
                "BUSINESS_NODE_MAPPING_REQUIRED",
                "業務 entity/property/metric には物理 mapping が必要です。",
            )
        for mapping in node.physical_mappings:
            object_node = physical_nodes.get(mapping.object_ref.node_id)
            if object_node is None or object_node.kind not in {
                OntologyNodeKind.TABLE,
                OntologyNodeKind.VIEW,
            }:
                raise OntologyIntegrityError(
                    "BUSINESS_OBJECT_MAPPING_INVALID",
                    "業務 node の物理 object mapping が schema Ontology と一致しません。",
                )
            canonical = object_node.physical_mappings[0].object_ref
            if mapping.object_ref.model_dump() != canonical.model_dump():
                raise OntologyIntegrityError(
                    "BUSINESS_OBJECT_MAPPING_SPOOFED",
                    "業務 node の owner/object mapping が安定 ID と一致しません。",
                )
            for column in mapping.column_refs:
                OntologyApiRuntime._validate_physical_column_ref(column, physical_nodes)

    @staticmethod
    def _validate_physical_column_ref(
        column: Any,
        physical_nodes: Mapping[str, OntologyNode],
    ) -> None:
        column_node = physical_nodes.get(column.node_id)
        if column_node is None or column_node.kind != OntologyNodeKind.COLUMN:
            raise OntologyIntegrityError(
                "BUSINESS_COLUMN_MAPPING_INVALID",
                "業務定義の列 mapping が schema Ontology と一致しません。",
            )
        canonical = column_node.physical_mappings[0].column_refs[0]
        if column.model_dump() != canonical.model_dump():
            raise OntologyIntegrityError(
                "BUSINESS_COLUMN_MAPPING_SPOOFED",
                "業務定義の owner/object/column が安定 ID と一致しません。",
            )

    def ensure_profile(self, profile_id: str) -> Nl2SqlProfile:
        """profile の存在検証のみ(オントロジー同期を伴わない軽量チェック)。"""

        with self._lock:
            return self._strict_profile(profile_id)

    def profile_view(self, profile_id: str) -> tuple[ProfileOntologyView, SchemaOntology]:
        with self._lock:
            profile = self._strict_profile(profile_id)
            ontology = self._query_ontology()
            view = self._base_profile_view(profile, ontology)
            return view.model_copy(deep=True), ontology.model_copy(deep=True)

    def profile_view_warnings(self, profile_id: str, view: ProfileOntologyView) -> list[str]:
        """公開 Ontology に解決できなかった対象オブジェクト名の診断 warning(応答用)。"""

        with self._lock:
            profile = self._strict_profile(profile_id)
        resolved = {item.object_name.upper() for item in view.physical_objects} | {
            f"{item.owner}.{item.object_name}".upper() for item in view.physical_objects
        }

        def normalize(value: str) -> str:
            return value.replace('"', "").strip().upper()

        return [
            f"「{name}」を公開 Ontology(スキーマ情報)に解決できません。"
            "スキーマ情報を更新するか、オブジェクト名(owner 付き)を確認してください。"
            for name in [*profile.allowed_tables, *profile.allowed_views]
            if normalize(name) and normalize(name) not in resolved
        ]

    def patch_profile_view(
        self,
        profile_id: str,
        request: ProfileOntologyViewPatch,
    ) -> tuple[ProfileOntologyView, SchemaOntology]:
        """物理 FK / node / edge を変更せず profile 射影 metadata だけを更新する。"""

        with self._lock:
            profile = self._strict_profile(profile_id)
            ontology = self._query_ontology()
            current = self._base_profile_view(profile, ontology)
            if request.base_etag != current.etag:
                raise OntologyVersionConflictError(
                    "PROFILE_VIEW_ETAG_MISMATCH",
                    "Profile Ontology view が更新されています。最新版を再読込してください。",
                )
            node_by_id = {node.id: node for node in ontology.nodes}
            edge_by_id = {edge.id: edge for edge in ontology.edges}
            if request.table_usages_ja is not None:
                unknown_usage_ids = set(request.table_usages_ja) - set(current.node_ids)
                if unknown_usage_ids:
                    raise OntologyIntegrityError(
                        "PROFILE_VIEW_USAGE_NODE_UNKNOWN",
                        "用途を設定した object が Profile Ontology view の範囲外です。",
                    )
            if request.column_policies is not None:
                allowed_column_keys = {
                    value
                    for node_id in current.node_ids
                    if (node := node_by_id.get(node_id)) is not None and node.kind.value == "column"
                    for value in (node.id, node.technical_name)
                }
                unknown_policy_keys = set(request.column_policies) - allowed_column_keys
                if unknown_policy_keys:
                    raise OntologyIntegrityError(
                        "PROFILE_VIEW_COLUMN_UNKNOWN",
                        "列 policy の対象が Profile Ontology view の範囲外です。",
                    )
            if request.allowed_path_ids is not None:
                invalid_paths = [
                    path_id
                    for path_id in request.allowed_path_ids
                    if path_id not in current.edge_ids
                    or (edge := edge_by_id.get(path_id)) is None
                    or edge.review_status.value != "approved"
                ]
                if invalid_paths:
                    raise OntologyIntegrityError(
                        "PROFILE_VIEW_PATH_NOT_APPROVED",
                        "未承認または範囲外の関係 path は許可できません。",
                    )
            if request.physical_scope is not None:
                requested_scope = {
                    str(value).replace('"', "").strip().upper()
                    for values in request.physical_scope.values()
                    for value in values
                }
                current_scope = {item.object_name.upper() for item in current.physical_objects} | {
                    f"{item.owner}.{item.object_name}".upper() for item in current.physical_objects
                }
                if requested_scope - current_scope:
                    raise OntologyIntegrityError(
                        "PROFILE_DRAFT_SCOPE_OUTSIDE_VIEW",
                        "Draft の物理 object が Profile Ontology view の範囲外です。",
                    )
            updates: dict[str, Any] = {}
            if request.table_usages_ja is not None:
                updates["table_usages_ja"] = request.table_usages_ja
            if request.column_policies is not None:
                updates["column_policies"] = request.column_policies
            if request.allowed_path_ids is not None:
                updates["allowed_path_ids"] = sorted(set(request.allowed_path_ids))
            if request.node_overrides is not None:
                updates["draft_node_overrides"] = request.node_overrides
            if request.edge_overrides is not None:
                updates["draft_edge_overrides"] = request.edge_overrides
            if request.schema_fingerprint is not None:
                updates["draft_schema_fingerprint"] = request.schema_fingerprint
            if request.physical_scope is not None:
                updates["draft_physical_scope"] = request.physical_scope
            etag_payload = {
                "view_id": current.id,
                "base_etag": current.etag,
                **{
                    key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                    for key, value in updates.items()
                },
            }
            updates["etag"] = compute_etag(etag_payload, 1)
            updates["updated_at"] = datetime.now(UTC)
            updated = current.model_copy(update=updates, deep=True)
            self.sessions.register_profile_view(updated)
            self._profile_view_overrides[profile.id] = updated
            self._persist_profile_view(updated)
            return updated.model_copy(deep=True), ontology.model_copy(deep=True)

    def create_session(self, request: QuerySessionApiCreate) -> QuerySessionData:
        with self._lock, observe_stage("interpret"):
            profile = self._strict_profile(request.profile_id)
            ontology = self._query_ontology()
            base_view = self._base_profile_view(profile, ontology)
            allowed = self.legacy_service.resolve_allowed_objects(
                profile.id,
                request.allowed_objects,
            )
            if request.allowed_objects.table_names and not allowed.table_names:
                raise OntologyGateBlockedError(
                    "REQUEST_SCOPE_EMPTY",
                    "今回指定した object は profile の許可範囲に含まれていません。",
                )
            view = self._narrow_profile_view(base_view, ontology, allowed)
            self.sessions.register_profile_view(view)
            intent = self._interpret_question(request.question, profile, ontology, view)
            row_limit = request.row_limit or profile.default_row_limit
            if intent.limit is None:
                intent.limit = row_limit
            session = self.sessions.create_session(
                QuerySessionCreate(
                    question=request.question,
                    profile_id=profile.id,
                    profile_view_id=view.id,
                    ontology_revision_id=ontology.revision.id,
                    intent=intent,
                )
            )
            context = QueryRuntimeContext(
                allowed_objects=allowed,
                row_limit=row_limit,
                engine=request.engine,
                retrieved_node_ids=sorted(
                    {item.ontology_node_id for item in intent.entities if item.ontology_node_id}
                    | {item.ontology_node_id for item in intent.metrics if item.ontology_node_id}
                    | {item.ontology_node_id for item in intent.dimensions if item.ontology_node_id}
                ),
            )
            self._session_views[session.id] = view
            self._contexts[session.id] = context
            self._persist_session(session, context=context)
            record_transition(
                session_id=session.id,
                revision_id=session.ontology_revision_id,
                state=session.status.value,
            )
            return self._session_data(session)

    def create_session_idempotent(
        self,
        request: QuerySessionApiCreate,
        *,
        idempotency_key: str,
    ) -> QuerySessionData:
        return self._run_session_idempotent(
            "create_query_session",
            idempotency_key,
            request.model_dump(mode="json"),
            lambda: self.create_session(request),
        )

    def generate_sql_idempotent(
        self,
        session_id: str,
        request: GenerateSqlRequest,
        *,
        idempotency_key: str,
    ) -> QuerySessionData:
        return self._run_session_idempotent(
            "generate_query_sql",
            idempotency_key,
            {"session_id": session_id, "request": request.model_dump(mode="json")},
            lambda: self.generate_sql(session_id, request),
        )

    def confirm_sql_idempotent(
        self,
        session_id: str,
        request: SqlConfirmationRequest,
        *,
        idempotency_key: str,
    ) -> QuerySessionData:
        return self._run_session_idempotent(
            "confirm_query_sql",
            idempotency_key,
            {"session_id": session_id, "request": request.model_dump(mode="json")},
            lambda: self.confirm_sql(session_id, request),
        )

    def execute_idempotent(
        self,
        session_id: str,
        request: SqlConfirmationRequest,
        *,
        idempotency_key: str,
    ) -> QueryExecutionData:
        data = self._run_session_idempotent(
            "execute_query_session",
            idempotency_key,
            {"session_id": session_id, "request": request.model_dump(mode="json")},
            lambda: self.execute(session_id, request),
        )
        payload = data.model_dump()
        if payload.get("result") is None and self._results.get(session_id) is not None:
            payload["result"] = self._results[session_id]
        return QueryExecutionData.model_validate(payload)

    def _run_session_idempotent(
        self,
        operation: str,
        idempotency_key: str,
        request_payload: Mapping[str, Any],
        callback: Callable[[], QuerySessionData],
    ) -> QuerySessionData:
        key = idempotency_key.strip()
        if not key:
            raise OntologyIntegrityError(
                "IDEMPOTENCY_KEY_REQUIRED",
                "Idempotency-Key header を指定してください。",
            )
        self._ensure_store()
        request_hash = hashlib.sha256(
            canonical_json({"operation": operation, "payload": request_payload}).encode("utf-8")
        ).hexdigest()
        existing = self.store.get_document(
            "idempotency",
            {"operation": operation, "idempotency_key": key},
        )
        if existing is not None:
            if existing.get("request_hash") != request_hash:
                raise OntologyVersionConflictError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "同じ Idempotency-Key が異なる payload で再利用されました。",
                )
            resource_id = str(existing.get("resource_id") or "")
            if resource_id:
                return self.get_session(resource_id)
        data = callback()
        self.store.save_document(
            "idempotency",
            {
                "operation": operation,
                "idempotency_key": key,
                "request_hash": request_hash,
                "resource_id": data.session.id,
                "status": data.session.status.value,
                "payload": {
                    "operation": operation,
                    "idempotency_key": key,
                    "request_hash": request_hash,
                    "resource_id": data.session.id,
                    "status": data.session.status.value,
                },
            },
            expected_etag=None,
        )
        return data

    def _compile_sql_generation_context(
        self,
        *,
        session: QuerySession,
        intent: QuestionIntentGraph,
        view: ProfileOntologyView,
        ontology: SchemaOntology,
        runtime_context: QueryRuntimeContext,
    ) -> OntologySqlGenerationContext:
        node_by_id = {node.id: node for node in ontology.nodes}
        edge_by_id = {edge.id: edge for edge in ontology.edges}

        allowed_object_names = sorted(
            runtime_context.allowed_objects.table_names
            or [
                (
                    f"{item.owner}.{item.object_name}"
                    if item.owner
                    else item.object_name
                )
                for item in view.physical_objects
            ]
        )
        allowed_column_names: dict[str, list[str]] = {}
        if runtime_context.allowed_objects.columns:
            allowed_column_names = {
                str(table): sorted({str(column) for column in columns})
                for table, columns in runtime_context.allowed_objects.columns.items()
            }
        else:
            for node_id in view.node_ids:
                node = node_by_id.get(node_id)
                if node is None or node.kind != OntologyNodeKind.COLUMN:
                    continue
                mapping = node.physical_mappings[0] if node.physical_mappings else None
                column = mapping.column_refs[0] if mapping and mapping.column_refs else None
                if column is None:
                    continue
                object_name = (
                    f"{column.owner}.{column.object_name}"
                    if column.owner
                    else column.object_name
                )
                allowed_column_names.setdefault(object_name, []).append(column.column_name)
            allowed_column_names = {
                key: sorted(set(values)) for key, values in allowed_column_names.items()
            }

        selected_path = next(
            (path for path in intent.candidate_paths if path.id == intent.selected_path_id),
            None,
        )
        selected_edge_ids = selected_path.edge_ids if selected_path is not None else []
        join_summaries: list[str] = []
        approved_join_edge_ids: list[str] = []
        for edge_id in selected_edge_ids:
            edge = edge_by_id.get(edge_id)
            if edge is None:
                continue
            if (
                edge.review_status == OntologyReviewStatus.APPROVED
                and edge.id in view.allowed_path_ids
            ):
                approved_join_edge_ids.append(edge.id)
            for condition in sorted(edge.join_conditions, key=lambda item: item.ordinal):
                left = condition.left
                right = condition.right
                join_summaries.append(
                    (
                        f"{left.owner}.{left.object_name}.{left.column_name} "
                        f"{condition.operator} "
                        f"{right.owner}.{right.object_name}.{right.column_name}"
                    ).replace("..", ".")
                )

        metric_definitions: list[MetricDefinition] = []
        warnings: list[str] = []
        for metric in intent.metrics:
            node = node_by_id.get(metric.ontology_node_id)
            if node is None:
                warnings.append(f"指標 {metric.name_ja} の Ontology node を解決できません。")
                continue
            definition_raw = node.metadata.get("metric_definition")
            if isinstance(definition_raw, Mapping):
                try:
                    metric_definitions.append(MetricDefinition.model_validate(definition_raw))
                    continue
                except Exception:
                    warnings.append(f"指標 {node.business_name_ja} の正式定義が不正です。")
            if metric.expression_sql.strip():
                metric_definitions.append(
                    MetricDefinition(
                        id=metric.metric_definition_id
                        or stable_ontology_id("metric_definition", metric.ontology_node_id),
                        metric_node_id=metric.ontology_node_id,
                        expression_sql=metric.expression_sql,
                        aggregation=(metric.aggregation or "none").lower(),
                        grain_node_ids=metric.grain_node_ids,
                        description_ja=metric.formula_description_ja,
                    )
                )
                continue
            mapped_columns = [
                column
                for mapping in node.physical_mappings
                for column in mapping.column_refs
            ]
            if mapped_columns:
                column = mapped_columns[0]
                expression = (
                    f"{column.owner}.{column.object_name}.{column.column_name}"
                    if column.owner
                    else f"{column.object_name}.{column.column_name}"
                )
                metric_definitions.append(
                    MetricDefinition(
                        id=stable_ontology_id("metric_definition", metric.ontology_node_id),
                        metric_node_id=metric.ontology_node_id,
                        expression_sql=expression,
                        aggregation=(metric.aggregation or "none").lower(),
                        base_column_node_ids=[column.node_id] if column.node_id else [],
                        grain_node_ids=metric.grain_node_ids,
                        description_ja=metric.formula_description_ja,
                    )
                )
            else:
                warnings.append(f"指標 {node.business_name_ja} に物理列 mapping がありません。")

        filter_summaries = [
            f"{item.label_ja} {item.operator} {item.value}" for item in intent.filters
        ]
        time_summary = ""
        if intent.time_range is not None:
            time_range = intent.time_range
            if time_range.relative_expression:
                time_summary = f"{time_range.label_ja}: {time_range.relative_expression}"
            else:
                time_summary = (
                    f"{time_range.label_ja}: {time_range.start or ''} - "
                    f"{time_range.end or ''}"
                )
        sort_summaries = [
            f"{item.target_id} {item.direction}" for item in intent.sorts
        ]
        payload: dict[str, Any] = {
            "session_id": session.id,
            "profile_id": session.profile_id,
            "profile_view_id": view.id,
            "ontology_revision_id": session.ontology_revision_id,
            "intent_version": intent.version,
            "question_effective": intent.question_effective,
            "allowed_object_names": allowed_object_names,
            "allowed_column_names": allowed_column_names,
            "entity_node_ids": [
                item.ontology_node_id for item in intent.entities if item.ontology_node_id
            ],
            "metric_node_ids": [
                item.ontology_node_id for item in intent.metrics if item.ontology_node_id
            ],
            "dimension_node_ids": [
                item.ontology_node_id for item in intent.dimensions if item.ontology_node_id
            ],
            "filter_summaries_ja": filter_summaries,
            "time_range_summary_ja": time_summary,
            "granularity": intent.granularity,
            "sort_summaries_ja": sort_summaries,
            "limit": intent.limit or runtime_context.row_limit,
            "selected_path_id": intent.selected_path_id or "",
            "approved_join_edge_ids": approved_join_edge_ids,
            "join_condition_summaries": join_summaries,
            "metric_definitions": [item.model_dump(mode="json") for item in metric_definitions],
            "warnings_ja": warnings,
        }
        payload["context_hash"] = hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()
        # hash 計算後に追加する(mermaid は表現であり契約ではないため hash 非対象)。
        payload["mermaid_er"] = render_mermaid_er(ontology, view)
        return OntologySqlGenerationContext.model_validate(payload)

    def _interpret_question(
        self,
        question: str,
        profile: Nl2SqlProfile,
        ontology: SchemaOntology,
        view: ProfileOntologyView,
    ) -> QuestionIntentGraph:
        """Enterprise AI structured intent。障害時は決定論 draft をそのまま返す。"""

        deterministic = interpret_question_deterministically(
            question,
            ontology,
            view,
            profile=profile,
            embedding_callback=lambda text, candidates, limit: self._embedding_hits(
                ontology.revision.id,
                text,
                candidates,
                limit,
            ),
        )
        client = getattr(self.legacy_service, "_enterprise_ai_client", None)
        configured = getattr(client, "is_configured", None)
        generate = getattr(client, "generate", None)
        if not callable(configured) or not configured() or not callable(generate):
            return deterministic

        visible_nodes = [node for node in ontology.nodes if node.id in view.node_ids]
        visible_edges = [edge for edge in ontology.edges if edge.id in view.edge_ids]
        context = json.dumps(
            {
                "profile_id": profile.id,
                "ontology_revision_id": ontology.revision.id,
                "profile_view_id": view.id,
                "allowed_nodes": [
                    {
                        "id": node.id,
                        "kind": node.kind.value,
                        "business_name_ja": node.business_name_ja,
                        "technical_name": node.technical_name,
                        "aliases": node.aliases,
                    }
                    for node in visible_nodes
                ],
                "allowed_relationships": [
                    {
                        "id": edge.id,
                        "source_node_id": edge.source_node_id,
                        "target_node_id": edge.target_node_id,
                        "name_ja": edge.relationship_name_ja,
                        "approved": edge.id in view.allowed_path_ids,
                    }
                    for edge in visible_edges
                ],
                "deterministic_draft": deterministic.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        system_prompt = (
            "あなたは NL2SQL の質問解釈器です。QuestionIntentGraph の JSON object だけを返し、"
            "説明文や Markdown を付けないでください。allowed_nodes / allowed_relationships にない "
            "ID を作らず、業務上確定できない内容は blocking ambiguity として残してください。"
            "profile_view_id と ontology_revision_id は入力値を厳密に維持してください。"
        )
        try:
            with observe_stage("interpret_enterprise_ai"):
                raw = generate(prompt=question, context=context, system_prompt=system_prompt)
            cleaned = str(raw).strip()
            if "{" in cleaned and "}" in cleaned:
                cleaned = cleaned[cleaned.find("{") : cleaned.rfind("}") + 1]
            intent = QuestionIntentGraph.model_validate(json.loads(cleaned))
            referenced_node_ids = (
                {item.ontology_node_id for item in intent.entities if item.ontology_node_id}
                | {item.ontology_node_id for item in intent.metrics if item.ontology_node_id}
                | {item.ontology_node_id for item in intent.dimensions if item.ontology_node_id}
            )
            if referenced_node_ids - set(view.node_ids):
                raise ValueError("Enterprise AI intent referenced nodes outside profile view")
            referenced_edge_ids = {
                edge_id for path in intent.candidate_paths for edge_id in path.edge_ids
            }
            if referenced_edge_ids - set(view.edge_ids):
                raise ValueError("Enterprise AI intent referenced edges outside profile view")
            return intent.model_copy(
                update={
                    "version": 1,
                    "question_original": question,
                    "profile_view_id": view.id,
                    "ontology_revision_id": ontology.revision.id,
                },
                deep=True,
            )
        except Exception:
            logger.warning("ontology_intent_enterprise_ai_fallback", exc_info=True)
            return deterministic

    def _embedding_hits(
        self,
        revision_id: str,
        question: str,
        candidates: Any,
        limit: int,
    ) -> list[tuple[str, float]]:
        client = getattr(self.legacy_service, "_embedding_client", None)
        configured = getattr(client, "is_configured", None)
        embed = getattr(client, "embed_texts", None)
        if not callable(configured) or not configured() or not callable(embed):
            return []
        candidate_list = list(candidates)
        vectors = self._embeddings.setdefault(revision_id, {})
        missing = [node for node in candidate_list if node.id not in vectors]
        try:
            if missing:
                embedded = embed(
                    [
                        "\n".join(
                            filter(
                                None,
                                [
                                    node.business_name_ja,
                                    node.technical_name,
                                    node.description_ja,
                                    *node.aliases,
                                ],
                            )
                        )
                        for node in missing
                    ]
                )
                vectors.update(
                    {node.id: vector for node, vector in zip(missing, embedded, strict=True)}
                )
                persisted_ontology = self._ontologies.get(revision_id) or self._ontology
                if persisted_ontology is not None:
                    for node in missing:
                        self._persist_node(persisted_ontology, node)
            query_vector = embed([question])[0]
        except Exception:
            logger.warning("ontology_embedding_retrieval_failed", exc_info=True)
            return []

        try:
            store_hits = self.store.search_node_embeddings(
                revision_id=revision_id,
                query_embedding=query_vector,
                candidate_node_ids=[node.id for node in candidate_list],
                limit=limit,
            )
            if store_hits:
                return store_hits
        except Exception:
            logger.warning("ontology_oracle_vector_search_failed", exc_info=True)

        def cosine(left: list[float], right: list[float]) -> float:
            denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
                sum(value * value for value in right)
            )
            if denominator == 0:
                return 0.0
            return sum(a * b for a, b in zip(left, right, strict=True)) / denominator

        ranked = [
            (node.id, max(0.0, cosine(query_vector, vectors[node.id])))
            for node in candidate_list
            if node.id in vectors
        ]
        return sorted(ranked, key=lambda item: (-item[1], item[0]))[:limit]

    def get_session(self, session_id: str) -> QuerySessionData:
        with self._lock:
            self._ensure_store()
            return self._session_data(self.sessions.get_session(session_id))

    def patch_intent(self, session_id: str, patch: GraphPatch) -> QuerySessionData:
        with self._lock:
            self._ensure_store()
            session = self.sessions.apply_intent_patch(session_id, patch)
            self._previews.pop(session_id, None)
            self._results.pop(session_id, None)
            self._plans.pop(session_id, None)
            self._persist_session(session)
            record_transition(
                session_id=session.id,
                revision_id=session.ontology_revision_id,
                state=session.status.value,
            )
            return self._session_data(session)

    def generate_sql(
        self,
        session_id: str,
        request: GenerateSqlRequest,
    ) -> QuerySessionData:
        with self._lock:
            self._ensure_store()
            current = self.sessions.get_session(session_id)
            if request.base_version != current.current_intent_version:
                raise OntologyVersionConflictError(
                    "INTENT_VERSION_CONFLICT",
                    "質問の解釈が別の操作で更新されています。最新版を再読込してください。",
                )
            if request.ontology_revision_id != current.ontology_revision_id:
                raise OntologyIntegrityError(
                    "ONTOLOGY_REVISION_MISMATCH",
                    "確認対象の Ontology revision が query session と一致しません。",
                )
            confirmed = self.sessions.confirm_intent(
                session_id,
                intent_version=request.intent_version,
            )
            self._persist_session(confirmed)
            record_transition(
                session_id=confirmed.id,
                revision_id=confirmed.ontology_revision_id,
                state=confirmed.status.value,
            )
            intent = confirmed.intents[-1]
            context = self._require_context(session_id)
            view = self._session_views.get(session_id)
            if view is None:
                view = self.sessions.get_profile_view(confirmed.profile_view_id)
            ontology = self._ontologies.get(confirmed.ontology_revision_id)
            if ontology is None:
                ontology = self.ontology_revision(confirmed.ontology_revision_id)
            generation_context = self._compile_sql_generation_context(
                session=confirmed,
                intent=intent,
                view=view,
                ontology=ontology,
                runtime_context=context,
            )
            with observe_stage("generate_sql"):
                preview = self.legacy_service.preview(
                    PreviewRequest(
                        question=intent.question_effective,
                        engine=context.engine,
                        profile_id=confirmed.profile_id,
                        allowed_objects=context.allowed_objects,
                        row_limit=intent.limit or context.row_limit,
                        ontology_context=generation_context,
                    )
                )
            sql = preview.executable_sql.strip() or preview.sql
            with observe_stage("validate_sql"):
                session = self.sessions.register_generated_sql(
                    session_id,
                    sql,
                    generation_context_hash=generation_context.context_hash,
                )
            explain = getattr(self.legacy_service, "explain_sql", None)
            if callable(explain):
                with observe_stage("explain_plan"):
                    self._plans[session_id] = ExplainPlanData.model_validate(explain(sql))
            self._previews[session_id] = preview
            self._persist_session(session)
            artifact = session.sql_artifacts[-1]
            self._persist_artifact(session.id, artifact)
            record_findings(list(artifact.validation_report.findings))
            record_transition(
                session_id=session.id,
                revision_id=session.ontology_revision_id,
                state=session.status.value,
            )
            return self._session_data(session)

    def confirm_sql(
        self,
        session_id: str,
        request: SqlConfirmationRequest,
    ) -> QuerySessionData:
        with self._lock:
            self._ensure_store()
            session = self.sessions.confirm_sql(session_id, request)
            self._persist_session(session)
            return self._session_data(session)

    def execute(
        self,
        session_id: str,
        request: SqlConfirmationRequest,
    ) -> QueryExecutionData:
        with self._lock:
            self._ensure_store()
            before = self.sessions.get_session(session_id)
            artifact = next(
                item for item in before.sql_artifacts if item.id == before.current_sql_artifact_id
            )
            context = self._require_context(session_id)
            self.sessions.authorize_execution(session_id, request, sql=artifact.sql)
            executing = self.sessions.get_session(session_id)
            self._persist_session(executing)
            record_transition(
                session_id=executing.id,
                revision_id=executing.ontology_revision_id,
                state=executing.status.value,
            )
            try:
                with observe_stage("execute"):
                    allowed = self.legacy_service.resolve_allowed_objects(
                        executing.profile_id,
                        context.allowed_objects,
                    )
                    safety, executable_sql, result = self.legacy_service.execute_sql(
                        sql=artifact.sql,
                        allowed=allowed,
                        row_limit=context.row_limit,
                    )
                if not safety.is_safe:
                    failed = self.sessions.fail_session(
                        session_id,
                        code="LEGACY_SAFETY_BLOCKED",
                        message_ja=(
                            safety.blocked_reason or "既存 SQL safety gate が実行を阻止しました。"
                        ),
                    )
                    self._persist_session(failed)
                    raise OntologyGateBlockedError(
                        "LEGACY_SAFETY_BLOCKED",
                        safety.blocked_reason or "既存 SQL safety gate が実行を阻止しました。",
                    )
            except OntologyServiceError:
                raise
            except Exception as exc:
                failed = self.sessions.fail_session(
                    session_id,
                    code="SQL_EXECUTION_FAILED",
                    message_ja="SQL の実行に失敗しました。",
                )
                self._persist_session(failed)
                raise OntologyGateBlockedError(
                    "SQL_EXECUTION_FAILED",
                    "SQL の実行に失敗しました。",
                ) from exc

            session = self.sessions.complete_execution(
                session_id,
                row_count=result.total,
                result_ref=f"query-session:{session_id}",
            )
            self._results[session_id] = result
            self._persist_session(session, result=result)
            record_transition(
                session_id=session.id,
                revision_id=session.ontology_revision_id,
                state=session.status.value,
            )
            data = self._session_data(session)
            record_history = getattr(self.legacy_service, "record_ontology_history", None)
            if callable(record_history):
                elapsed_ms: int | None = None
                if session.execution is not None and session.execution.finished_at is not None:
                    elapsed_ms = max(
                        0,
                        int(
                            (
                                session.execution.finished_at - session.execution.started_at
                            ).total_seconds()
                            * 1000
                        ),
                    )
                try:
                    record_history(
                        session_id=session.id,
                        question=session.original_question,
                        rewritten_question=session.intents[-1].question_effective,
                        engine=context.engine,
                        generated_sql=artifact.sql,
                        executable_sql=executable_sql,
                        profile_id=session.profile_id,
                        result=result,
                        ontology_trace_summary=data.ontology_trace_summary,
                        elapsed_ms=elapsed_ms,
                    )
                except Exception:
                    # SQL は既に実行済みなので history 投影障害で結果を失わせない。
                    logger.warning(
                        "ontology_history_projection_failed",
                        extra={"session_id": session.id},
                        exc_info=True,
                    )
            payload = data.model_dump()
            payload["result"] = result
            return QueryExecutionData.model_validate(payload)

    def create_proposal(
        self,
        session_id: str,
        request: ImprovementProposalRequest,
    ) -> tuple[OntologyProposal, QuerySessionData]:
        with self._lock:
            self._ensure_store()
            session_before = self.sessions.get_session(session_id)
            if request.base_revision_id and (
                request.base_revision_id != session_before.ontology_revision_id
            ):
                raise OntologyIntegrityError(
                    "PROPOSAL_REVISION_MISMATCH",
                    "改善提案の Ontology revision が query session と一致しません。",
                )
            if request.intent_version and (
                request.intent_version != session_before.current_intent_version
            ):
                raise OntologyVersionConflictError(
                    "INTENT_VERSION_CONFLICT",
                    "改善提案の元になった質問解釈が更新されています。",
                )
            patch = request.patch
            if patch is None:
                current_intent = session_before.intents[-1]
                from .ontology_models import GraphPatchOperation

                patch = GraphPatch(
                    base_version=current_intent.version,
                    summary_ja=request.summary or "query session からの Ontology 改善提案",
                    operations=[
                        GraphPatchOperation(
                            op="replace",
                            path="/question_effective",
                            value=current_intent.question_effective,
                            reason_ja="確認済みの質問解釈を改善提案として記録",
                        )
                    ],
                )
            proposal = self.sessions.create_improvement_proposal(
                session_id,
                title_ja=request.title_ja or request.summary or "Ontology 改善提案",
                description_ja=request.description_ja,
                patch=patch,
                kind=request.kind,
                proposal_payload=request.proposal_payload.model_copy(
                    update={"kind": request.kind},
                    deep=True,
                ),
            )
            session = self.sessions.get_session(session_id)
            self._persist_proposal(proposal)
            self._persist_session(session)
            return proposal, self._session_data(session)

    def get_proposal(self, proposal_id: str) -> OntologyProposal:
        with self._lock:
            self._ensure_store()
            return self.sessions.get_proposal(proposal_id)

    def list_profile_proposals(self, profile_id: str) -> list[OntologyProposal]:
        with self._lock:
            self._ensure_store()
            self._strict_profile(profile_id)
            return self.sessions.list_proposals_by_profile(profile_id)

    def create_build_proposal(
        self,
        *,
        profile_id: str,
        job_id: str,
        title_ja: str,
        description_ja: str,
        kind: OntologyProposalKind,
        proposal_payload: OntologyProposalPayload,
    ) -> OntologyProposal:
        """AI 構築 job の生成物を承認フローへ登録する(query session 非依存)。"""

        with self._lock:
            self._ensure_store()
            profile = self._strict_profile(profile_id)
            ontology = self._query_ontology()
            proposal = self.sessions.create_build_proposal(
                session_id=f"ontology_build:{job_id}",
                profile_id=profile.id,
                base_revision_id=ontology.revision.id,
                title_ja=title_ja or "AI オントロジー提案",
                description_ja=description_ja,
                kind=kind,
                proposal_payload=proposal_payload,
            )
            self._persist_proposal(proposal)
            return proposal

    def _accept_base_revision(self) -> SchemaOntology:
        """提案を積み上げる基準 revision。

        現行 published と同じ schema fingerprint の revision(published + そこから
        派生した draft)の中で最新を選ぶ。永続化 store には過去のスキーマ世代の
        draft が残り得るため、単純な max(version) だと古い物理 schema の draft を
        拾って upsert 検証が矛盾(409)する。fingerprint で系列を固定して防ぐ。
        """

        published = self._query_ontology()
        fingerprint = published.revision.schema_fingerprint
        candidates = [
            item
            for item in self._ontologies.values()
            if item.revision.schema_fingerprint == fingerprint
            and item.revision.status != OntologyRevisionStatus.ARCHIVED
        ]
        if not candidates:
            return published
        return max(
            candidates,
            key=lambda item: (
                item.revision.version,
                item.revision.created_at,
                item.revision.id,
            ),
        )

    def _proposals_upsert_draft_request(
        self,
        proposals: list[OntologyProposal],
        base: SchemaOntology,
    ) -> OntologyDraftRequest:
        """複数 proposal の node/edge upserts を 1 つの承認済み draft request へ合成する。"""

        base_node_ids = {node.id for node in base.nodes}
        node_map: dict[str, OntologyNode] = {}
        synthetic_ids: set[str] = set()
        edge_map: dict[str, OntologyEdge] = {}
        for proposal in proposals:
            values = proposal.proposal_payload.values
            for raw in values.get("node_upserts") or []:
                node = OntologyNode.model_validate(raw)
                is_synthetic = bool(node.metadata.get("synthetic_endpoint"))
                # 関係提案の合成 endpoint は、実在ノードや命名提案の upsert を上書きしない。
                if is_synthetic and (
                    node.id in base_node_ids
                    or (node.id in node_map and node.id not in synthetic_ids)
                ):
                    continue
                node_map[node.id] = node.model_copy(
                    update={
                        "revision_id": base.revision.id,
                        "review_status": OntologyReviewStatus.APPROVED,
                    },
                    deep=True,
                )
                if is_synthetic:
                    synthetic_ids.add(node.id)
                else:
                    synthetic_ids.discard(node.id)
            for raw in values.get("edge_upserts") or []:
                edge = OntologyEdge.model_validate(raw)
                edge_map[edge.id] = edge.model_copy(
                    update={
                        "revision_id": base.revision.id,
                        "review_status": OntologyReviewStatus.APPROVED,
                    },
                    deep=True,
                )
        titles = "、".join(proposal.title_ja for proposal in proposals[:5])
        return OntologyDraftRequest(
            base_etag=base.revision.etag,
            note=f"AI 提案を承認: {titles}",
            node_upserts=sorted(node_map.values(), key=lambda node: node.id),
            edge_upserts=sorted(edge_map.values(), key=lambda edge: edge.id),
        )

    def accept_proposals(
        self, proposal_ids: list[str]
    ) -> tuple[list[OntologyProposal], SchemaOntology]:
        """複数 proposal を 1 つの draft revision へまとめて承認する(N 回の draft 生成を回避)。"""

        with self._lock:
            self._ensure_store()
            proposals = [self.sessions.get_proposal(pid) for pid in proposal_ids]
            if not proposals:
                raise OntologyIntegrityError(
                    "ONTOLOGY_PROPOSAL_IDS_REQUIRED", "承認する提案を指定してください。"
                )
            base = self._accept_base_revision()
            request = self._proposals_upsert_draft_request(proposals, base)
            try:
                draft = self.create_ontology_draft(base.revision.id, request)
            except OntologyIntegrityError as exc:
                # 提案が現在のスキーマ世代と一致しない(古い提案など)場合の案内。
                raise OntologyStateConflictError(
                    "ONTOLOGY_PROPOSAL_STALE",
                    "提案を現在の Ontology に適用できません。"
                    "スキーマ情報を更新し、AI 構築を再実行してください。"
                    f"(詳細: {exc.message_ja})",
                ) from exc
            accepted_list: list[OntologyProposal] = []
            for proposal in proposals:
                accepted = proposal.model_copy(
                    update={
                        "status": OntologyProposalStatus.ACCEPTED,
                        "proposal_payload": proposal.proposal_payload.model_copy(
                            update={
                                "values": {
                                    **proposal.proposal_payload.values,
                                    "draft_revision_id": draft.revision.id,
                                }
                            },
                            deep=True,
                        ),
                    },
                    deep=True,
                )
                accepted = self.sessions.update_proposal(accepted)
                self._persist_proposal(accepted)
                accepted_list.append(accepted)
            return accepted_list, draft

    def accept_proposal(self, proposal_id: str) -> OntologyProposalReviewData:
        with self._lock:
            accepted_list, draft = self.accept_proposals([proposal_id])
            return OntologyProposalReviewData(
                proposal=accepted_list[0],
                draft=OntologyGraphData(
                    revision=draft.revision,
                    nodes=draft.nodes,
                    edges=draft.edges,
                ),
            )

    def reject_proposal(self, proposal_id: str) -> OntologyProposalReviewData:
        with self._lock:
            self._ensure_store()
            proposal = self.sessions.get_proposal(proposal_id)
            rejected = proposal.model_copy(
                update={"status": OntologyProposalStatus.REJECTED},
                deep=True,
            )
            rejected = self.sessions.update_proposal(rejected)
            self._persist_proposal(rejected)
            return OntologyProposalReviewData(proposal=rejected)

    def _sync_ontology(self) -> SchemaOntology:
        self._ensure_store()
        catalog = self.legacy_service.get_catalog()
        if self._ontology is None:
            ontology = build_schema_ontology(catalog)
        else:
            ontology = evolve_schema_ontology(catalog, self._ontology)
            if ontology.revision.id == self._ontology.revision.id:
                return self._ontology
        self.sessions.register_revision(
            ontology.revision,
            nodes=ontology.nodes,
            edges=ontology.edges,
        )
        self._persist_ontology(ontology)
        self._ontology = ontology
        self._ontologies[ontology.revision.id] = ontology
        return ontology

    def _base_profile_view(
        self,
        profile: Nl2SqlProfile,
        ontology: SchemaOntology,
    ) -> ProfileOntologyView:
        migration_profile = profile
        if not profile.allowed_tables and not profile.allowed_views:
            tables = self.legacy_service.get_catalog().tables
            migration_profile = profile.model_copy(
                update={
                    "allowed_tables": [
                        table.table_name
                        for table in tables
                        if "view" not in table.table_type.lower()
                    ],
                    "allowed_views": [
                        table.table_name for table in tables if "view" in table.table_type.lower()
                    ],
                }
            )
        view = migrate_profile_ontology_view(migration_profile, ontology, strict=False)
        override = self._profile_view_overrides.get(profile.id)
        if override is not None:
            current_node_by_id = {node.id: node for node in ontology.nodes}
            current_column_keys = {
                value
                for node_id in view.node_ids
                if (node := current_node_by_id.get(node_id)) is not None
                and node.kind.value == "column"
                for value in (node.id, node.technical_name)
            }
            retained_updates: dict[str, Any] = {
                "table_usages_ja": {
                    key: value
                    for key, value in override.table_usages_ja.items()
                    if key in view.node_ids
                },
                "column_policies": {
                    key: value
                    for key, value in override.column_policies.items()
                    if key in current_column_keys
                },
                "allowed_path_ids": [
                    path_id for path_id in override.allowed_path_ids if path_id in view.edge_ids
                ],
                "draft_node_overrides": list(override.draft_node_overrides),
                "draft_edge_overrides": list(override.draft_edge_overrides),
                "draft_schema_fingerprint": override.draft_schema_fingerprint,
                "draft_physical_scope": dict(override.draft_physical_scope),
                "updated_at": override.updated_at,
            }
            if override.ontology_revision_id == view.ontology_revision_id:
                retained_updates["etag"] = override.etag
            else:
                previous = self._ontologies.get(override.ontology_revision_id)
                if not retained_updates["draft_schema_fingerprint"] and previous is not None:
                    retained_updates["draft_schema_fingerprint"] = (
                        previous.revision.schema_fingerprint
                    )
                retained_updates["updated_at"] = datetime.now(UTC)
                retained_updates["etag"] = compute_etag(
                    {
                        "view_id": view.id,
                        "ontology_revision_id": view.ontology_revision_id,
                        **{
                            key: (
                                value.model_dump(mode="json")
                                if hasattr(value, "model_dump")
                                else value
                            )
                            for key, value in retained_updates.items()
                            if key != "updated_at"
                        },
                    },
                    1,
                )
            view = view.model_copy(
                update=retained_updates,
                deep=True,
            )
            self._profile_view_overrides[profile.id] = view
        self.sessions.register_profile_view(view)
        self._persist_profile_view(view)
        return view

    @staticmethod
    def _narrow_profile_view(
        base: ProfileOntologyView,
        ontology: SchemaOntology,
        allowed: AllowedObjects,
    ) -> ProfileOntologyView:
        if not allowed.table_names:
            return base
        requested_full: set[str] = set()
        requested_short: set[str] = set()
        for raw_name in allowed.table_names:
            normalized = raw_name.replace('"', "").strip().upper()
            if "." in normalized:
                requested_full.add(normalized)
            else:
                requested_short.add(normalized)
        for short_name in requested_short:
            matches = [
                item for item in base.physical_objects if item.object_name.upper() == short_name
            ]
            if len(matches) > 1:
                raise OntologyIntegrityError(
                    "REQUEST_OBJECT_AMBIGUOUS",
                    "owner のない object 名が複数 schema に一致します。owner を指定してください。",
                )
        selected_objects = [
            item
            for item in base.physical_objects
            if (
                f"{item.owner}.{item.object_name}".upper() in requested_full
                or item.object_name.upper() in requested_short
            )
        ]
        selected_ids = {item.node_id for item in selected_objects}
        nodes = [
            node
            for node in ontology.nodes
            if node.id in base.node_ids
            and (
                node.id in selected_ids
                or any(
                    mapping.object_ref.node_id in selected_ids for mapping in node.physical_mappings
                )
                or (
                    node.kind.value == "schema"
                    and str(node.metadata.get("owner", "")).upper()
                    in {item.owner.upper() for item in selected_objects}
                )
            )
        ]
        node_ids = {node.id for node in nodes}
        edges = [
            edge
            for edge in ontology.edges
            if edge.id in base.edge_ids
            and edge.source_node_id in node_ids
            and edge.target_node_id in node_ids
        ]
        edge_ids = {edge.id for edge in edges}
        if selected_ids == {item.node_id for item in base.physical_objects}:
            return base
        view_id = stable_ontology_id(
            "query_profile_view",
            base.id,
            *sorted(selected_ids),
        )
        payload = {
            "id": view_id,
            "profile_id": base.profile_id,
            "ontology_revision_id": base.ontology_revision_id,
            "node_ids": sorted(node_ids),
            "edge_ids": sorted(edge_ids),
            "physical_objects": selected_objects,
            "table_usages_ja": {
                key: value for key, value in base.table_usages_ja.items() if key in selected_ids
            },
            "column_policies": base.column_policies,
            "allowed_path_ids": [
                edge_id for edge_id in base.allowed_path_ids if edge_id in edge_ids
            ],
            "archived": base.archived,
        }
        return ProfileOntologyView(
            **payload,
            etag=compute_etag(payload, 1),
        )

    def _strict_profile(self, profile_id: str) -> Nl2SqlProfile:
        try:
            return Nl2SqlProfile.model_validate(self.legacy_service.get_profile(profile_id))
        except (KeyError, ValueError) as exc:
            raise OntologyNotFoundError(
                "NL2SQL_PROFILE_NOT_FOUND",
                "指定された profile が見つからないか、利用できません。",
            ) from exc

    def _require_context(self, session_id: str) -> QueryRuntimeContext:
        context = self._contexts.get(session_id)
        if context is None:
            raise OntologyNotFoundError(
                "QUERY_SESSION_CONTEXT_NOT_FOUND",
                "query session の実行 context が見つかりません。",
            )
        return context

    def _session_data(self, session: QuerySession) -> QuerySessionData:
        view = self._session_views.get(session.id)
        if view is None:
            view = self.sessions.get_profile_view(session.profile_view_id)
        self._sync_ontology()
        ontology = self._ontologies.get(session.ontology_revision_id)
        if ontology is None:
            raise OntologyNotFoundError(
                "SESSION_ONTOLOGY_REVISION_NOT_FOUND",
                "query session が固定した Ontology revision を読み込めません。",
            )
        artifact = session.sql_artifacts[-1] if session.sql_artifacts else None
        report = artifact.validation_report if artifact is not None else None
        context = self._contexts.get(session.id)
        return QuerySessionData(
            session=session,
            profile_ontology_view=view,
            ontology_graph=OntologyGraphData(
                revision=ontology.revision,
                nodes=[node for node in ontology.nodes if node.id in view.node_ids],
                edges=[edge for edge in ontology.edges if edge.id in view.edge_ids],
            ),
            preview=self._previews.get(session.id),
            result=self._results.get(session.id),
            performance_check=self._plans.get(session.id),
            ontology_trace_summary={
                "session_id": session.id,
                "ontology_revision_id": session.ontology_revision_id,
                "intent_version": session.current_intent_version,
                "sql_artifact_id": session.current_sql_artifact_id,
                "sql_hash": artifact.sql_hash if artifact else "",
                "validation_hash": report.validation_hash if report else "",
                "generation_context_hash": artifact.generation_context_hash if artifact else "",
                "blocker_count": report.blocker_count if report else 0,
                "warning_count": report.warning_count if report else 0,
                "retrieved_node_ids": context.retrieved_node_ids if context else [],
            },
        )

    def _ensure_store(self) -> None:
        if self._store_ready:
            return
        self.store.ensure_schema()
        self._rehydrate_store()
        self._store_ready = True

    @staticmethod
    def _stored_payload(
        document: Mapping[str, Any],
        *,
        collection: str,
    ) -> Mapping[str, Any]:
        payload = document.get("payload")
        if not isinstance(payload, Mapping):
            raise OntologyIntegrityError(
                "ONTOLOGY_STORE_PAYLOAD_INVALID",
                f"永続化された {collection} payload が JSON object ではありません。",
            )
        return payload

    def _rehydrate_store(self) -> None:
        """Store を正本として domain service と runtime cache を再構築する。"""

        restored_ontologies: list[SchemaOntology] = []
        revision_documents = self.store.list_documents("revisions")
        parsed_revisions = [
            (
                OntologyRevision.model_validate(
                    self._stored_payload(document, collection="revision")
                ),
                document,
            )
            for document in revision_documents
        ]
        for revision, _document in sorted(
            parsed_revisions,
            key=lambda item: (item[0].version, item[0].created_at, item[0].id),
        ):
            node_documents = self.store.list_documents("nodes", {"revision_id": revision.id})
            edge_documents = self.store.list_documents("edges", {"revision_id": revision.id})
            nodes = [
                OntologyNode.model_validate(self._stored_payload(document, collection="node"))
                for document in node_documents
            ]
            edges = [
                OntologyEdge.model_validate(self._stored_payload(document, collection="edge"))
                for document in edge_documents
            ]
            self.sessions.register_revision(revision, nodes=nodes, edges=edges)
            ontology = SchemaOntology(revision=revision, nodes=nodes, edges=edges)
            self._ontologies[revision.id] = ontology
            restored_ontologies.append(ontology)
            restored_vectors = {
                str(document["node_id"]): [float(value) for value in document["embedding"]]
                for document in node_documents
                if document.get("embedding") is not None
            }
            if restored_vectors:
                self._embeddings[revision.id] = restored_vectors

        if restored_ontologies:
            self._ontology = max(
                restored_ontologies,
                key=lambda item: (
                    item.revision.version,
                    item.revision.created_at,
                    item.revision.id,
                ),
            )

        for document in self.store.list_documents("profile_views"):
            view = ProfileOntologyView.model_validate(
                self._stored_payload(document, collection="profile view")
            )
            self.sessions.register_profile_view(view)
            self._profile_view_overrides[view.profile_id] = view

        for document in self.store.list_documents("query_sessions"):
            session = QuerySession.model_validate(
                self._stored_payload(document, collection="query session")
            )
            context_raw = document.get("runtime_context")
            context = (
                QueryRuntimeContext.model_validate(context_raw)
                if isinstance(context_raw, Mapping)
                else None
            )
            snapshot_raw = document.get("profile_view_snapshot")
            if isinstance(snapshot_raw, Mapping):
                view = ProfileOntologyView.model_validate(snapshot_raw)
            else:
                try:
                    view = self.sessions.get_profile_view(session.profile_view_id)
                except OntologyNotFoundError:
                    base = self._profile_view_overrides.get(session.profile_id)
                    session_ontology = self._ontologies.get(session.ontology_revision_id)
                    if base is None or session_ontology is None or context is None:
                        raise OntologyIntegrityError(
                            "RESTORED_SESSION_VIEW_MISSING",
                            "永続化 query session の Profile Ontology view を復元できません。",
                        ) from None
                    view = self._narrow_profile_view(
                        base,
                        session_ontology,
                        context.allowed_objects,
                    )
                    if view.id != session.profile_view_id:
                        raise OntologyIntegrityError(
                            "RESTORED_SESSION_VIEW_MISMATCH",
                            "再構築した Profile Ontology view が query session と一致しません。",
                        ) from None
            self.sessions.register_profile_view(view)
            self.sessions.restore_session(session)
            self._session_views[session.id] = view
            if context is not None:
                self._contexts[session.id] = context
            preview_raw = document.get("preview")
            if isinstance(preview_raw, Mapping):
                self._previews[session.id] = PreviewData.model_validate(preview_raw)
            result_raw = document.get("result")
            if isinstance(result_raw, Mapping):
                self._results[session.id] = QueryResults.model_validate(result_raw)
            plan_raw = document.get("performance_check")
            if isinstance(plan_raw, Mapping):
                self._plans[session.id] = ExplainPlanData.model_validate(plan_raw)

        for document in self.store.list_documents("proposals"):
            proposal = OntologyProposal.model_validate(
                self._stored_payload(document, collection="proposal")
            )
            self.sessions.restore_proposal(proposal)

    def _persist_ontology(self, ontology: SchemaOntology, *, include_graph: bool = True) -> None:
        """revision と(必要なら)nodes/edges を永続化する。

        nodes/edges は revision 登録時に同一 revision_id で保存済みかつ不変のため、
        publish/archive のような revision header だけの変更では include_graph=False で
        再永続化を省く(大規模スキーマで数千回の store 書き込みを避ける)。
        """

        self._save(
            "revisions",
            {
                "revision_id": ontology.revision.id,
                "status": ontology.revision.status.value,
                "schema_fingerprint": ontology.revision.schema_fingerprint,
                "payload": ontology.revision,
            },
        )
        if not include_graph:
            return
        for node in ontology.nodes:
            self._persist_node(ontology, node)
        for edge in ontology.edges:
            self._save(
                "edges",
                {
                    "revision_id": ontology.revision.id,
                    "edge_id": edge.id,
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "review_status": edge.review_status.value,
                    "payload": edge,
                },
            )

    def _persist_node(self, ontology: SchemaOntology, node: OntologyNode) -> None:
        physical_id = node.physical_mappings[0].object_ref.node_id if node.physical_mappings else ""
        self._save(
            "nodes",
            {
                "revision_id": ontology.revision.id,
                "node_id": node.id,
                "node_type": node.kind.value,
                "review_status": node.review_status.value,
                "physical_id": physical_id,
                "embedding": self._embeddings.get(ontology.revision.id, {}).get(node.id),
                "payload": node,
            },
        )

    def _persist_profile_view(self, view: ProfileOntologyView) -> None:
        self._save(
            "profile_views",
            {
                "profile_id": view.profile_id,
                "revision_id": view.ontology_revision_id,
                "payload": view,
            },
        )

    def _persist_session(
        self,
        session: QuerySession,
        *,
        context: QueryRuntimeContext | None = None,
        result: QueryResults | None = None,
    ) -> None:
        resolved_context = context or self._contexts.get(session.id)
        resolved_result = result or self._results.get(session.id)
        resolved_view = self._session_views.get(session.id)
        if resolved_view is None:
            try:
                resolved_view = self.sessions.get_profile_view(session.profile_view_id)
            except OntologyNotFoundError:
                resolved_view = None
        self._save(
            "query_sessions",
            {
                "session_id": session.id,
                "ontology_revision_id": session.ontology_revision_id,
                "profile_id": session.profile_id,
                "status": session.status.value,
                "intent_version": session.current_intent_version,
                "sql_version": len(session.sql_artifacts),
                "payload": session,
                "runtime_context": resolved_context,
                "profile_view_snapshot": resolved_view,
                "preview": self._previews.get(session.id),
                "result": resolved_result,
                "performance_check": self._plans.get(session.id),
            },
        )

    def _persist_artifact(self, session_id: str, artifact: Any) -> None:
        self._save(
            "artifacts",
            {
                "artifact_id": artifact.id,
                "session_id": session_id,
                "artifact_type": "sql_semantic_graph",
                "content_hash": artifact.sql_hash,
                "payload": artifact,
            },
        )

    def _persist_proposal(self, proposal: OntologyProposal) -> None:
        self._save(
            "proposals",
            {
                "proposal_id": proposal.id,
                "session_id": proposal.session_id,
                "ontology_revision_id": proposal.base_revision_id,
                "status": proposal.status.value,
                "payload": proposal,
            },
        )

    def _save(self, collection: OntologyCollection, document: dict[str, Any]) -> None:
        self._ensure_store()
        identity = {field: document[field] for field in _STORE_IDENTITY_FIELDS[collection]}
        current = self.store.get_document(collection, identity)
        expected_etag = str(current["etag"]) if current is not None else None
        self.store.save_document(collection, document, expected_etag=expected_etag)


ontology_runtime = OntologyApiRuntime()
ontology_build_service = OntologyBuildService(ontology_runtime)
router = APIRouter(prefix="/nl2sql", tags=["nl2sql-ontology"])


def _raise_domain_error(exc: Exception) -> NoReturn:
    if isinstance(exc, OntologyNotFoundError):
        status_code = 404
    elif isinstance(
        exc,
        (
            OntologyVersionConflictError,
            OntologyStateConflictError,
            OntologyIntegrityError,
            OntologyVersionConflict,
        ),
    ):
        status_code = 409
    elif isinstance(exc, OntologyGateBlockedError):
        status_code = 422
    else:
        status_code = 500
    # アプリ共通の exception handler が detail を error_messages[0] へ文字列化するため、
    # dict ではなく読みやすい日本語メッセージ 1 本にする(code は括弧で併記)。
    code = getattr(exc, "code", type(exc).__name__)
    message_ja = str(getattr(exc, "message_ja", "") or str(exc) or "処理に失敗しました。")
    detail = f"{message_ja}({code})"
    finding_codes = getattr(exc, "finding_codes", None)
    if finding_codes:
        detail = f"{detail} 対象: {', '.join(str(item) for item in finding_codes)}"
    raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get(
    "/ontology/revisions",
    response_model=ApiResponse[OntologyRevisionListData],
)
async def list_ontology_revisions() -> ApiResponse[OntologyRevisionListData]:
    try:
        revisions, active_revision_id = ontology_runtime.list_ontology_revisions()
        return ApiResponse(
            data=OntologyRevisionListData(
                revisions=revisions,
                active_revision_id=active_revision_id,
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


@router.get(
    "/ontology/revisions/current",
    response_model=ApiResponse[OntologyGraphData],
)
async def get_current_ontology_revision() -> ApiResponse[OntologyGraphData]:
    try:
        ontology = ontology_runtime.current_ontology()
        return ApiResponse(
            data=OntologyGraphData(
                revision=ontology.revision,
                nodes=ontology.nodes,
                edges=ontology.edges,
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


@router.get(
    "/ontology/revisions/{revision_id}",
    response_model=ApiResponse[OntologyGraphData],
)
async def get_ontology_revision(revision_id: str) -> ApiResponse[OntologyGraphData]:
    try:
        ontology = ontology_runtime.ontology_revision(revision_id)
        return ApiResponse(
            data=OntologyGraphData(
                revision=ontology.revision,
                nodes=ontology.nodes,
                edges=ontology.edges,
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


@router.post(
    "/ontology/revisions/{revision_id}/drafts",
    response_model=ApiResponse[OntologyGraphData],
)
async def create_ontology_revision_draft(
    revision_id: str,
    request: OntologyDraftRequest,
) -> ApiResponse[OntologyGraphData]:
    try:
        ontology = ontology_runtime.create_ontology_draft(revision_id, request)
        return ApiResponse(
            data=OntologyGraphData(
                revision=ontology.revision,
                nodes=ontology.nodes,
                edges=ontology.edges,
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


@router.post(
    "/ontology/revisions/{revision_id}/publish",
    response_model=ApiResponse[OntologyGraphData],
)
async def publish_ontology_revision(
    revision_id: str,
    request: OntologyPublishRequest,
) -> ApiResponse[OntologyGraphData]:
    try:
        ontology = ontology_runtime.publish_ontology_revision(revision_id, request)
        return ApiResponse(
            data=OntologyGraphData(
                revision=ontology.revision,
                nodes=ontology.nodes,
                edges=ontology.edges,
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


@router.get(
    "/profiles/{profile_id}/ontology-view",
    response_model=ApiResponse[ProfileOntologyViewData],
)
async def get_profile_ontology_view(profile_id: str) -> ApiResponse[ProfileOntologyViewData]:
    try:
        view, ontology = ontology_runtime.profile_view(profile_id)
        return ApiResponse(
            data=ProfileOntologyViewData(
                profile_ontology_view=view,
                ontology_graph=OntologyGraphData(
                    revision=ontology.revision,
                    nodes=[node for node in ontology.nodes if node.id in view.node_ids],
                    edges=[edge for edge in ontology.edges if edge.id in view.edge_ids],
                ),
                warnings_ja=ontology_runtime.profile_view_warnings(profile_id, view),
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


class ProfileOntologyMermaidData(OntologyContract):
    profile_id: str
    ontology_revision_id: str
    mermaid: str


@router.get(
    "/profiles/{profile_id}/ontology-view/mermaid",
    response_model=ApiResponse[ProfileOntologyMermaidData],
)
async def get_profile_ontology_mermaid(profile_id: str) -> ApiResponse[ProfileOntologyMermaidData]:
    """Profile スコープの erDiagram(SQL 生成プロンプトへ注入するものと同じ表現)。"""

    try:
        view, ontology = ontology_runtime.profile_view(profile_id)
        return ApiResponse(
            data=ProfileOntologyMermaidData(
                profile_id=profile_id,
                ontology_revision_id=ontology.revision.id,
                mermaid=render_mermaid_er(ontology, view),
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


@router.patch(
    "/profiles/{profile_id}/ontology-view",
    response_model=ApiResponse[ProfileOntologyViewData],
)
async def patch_profile_ontology_view(
    profile_id: str,
    request: ProfileOntologyViewPatch,
) -> ApiResponse[ProfileOntologyViewData]:
    try:
        view, ontology = ontology_runtime.patch_profile_view(profile_id, request)
        return ApiResponse(
            data=ProfileOntologyViewData(
                profile_ontology_view=view,
                ontology_graph=OntologyGraphData(
                    revision=ontology.revision,
                    nodes=[node for node in ontology.nodes if node.id in view.node_ids],
                    edges=[edge for edge in ontology.edges if edge.id in view.edge_ids],
                ),
                warnings_ja=ontology_runtime.profile_view_warnings(profile_id, view),
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


@router.post(
    "/query-sessions",
    response_model=ApiResponse[QuerySessionData],
)
async def create_query_session(
    request: QuerySessionApiCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> ApiResponse[QuerySessionData]:
    try:
        return ApiResponse(
            data=ontology_runtime.create_session_idempotent(
                request,
                idempotency_key=idempotency_key,
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


@router.get(
    "/query-sessions/{session_id}",
    response_model=ApiResponse[QuerySessionData],
)
async def get_query_session(session_id: str) -> ApiResponse[QuerySessionData]:
    try:
        return ApiResponse(data=ontology_runtime.get_session(session_id))
    except Exception as exc:
        _raise_domain_error(exc)


@router.patch(
    "/query-sessions/{session_id}/intent",
    response_model=ApiResponse[QuerySessionData],
)
async def patch_query_intent(
    session_id: str,
    patch: GraphPatch,
) -> ApiResponse[QuerySessionData]:
    try:
        return ApiResponse(data=ontology_runtime.patch_intent(session_id, patch))
    except OntologyVersionConflictError as exc:
        try:
            current = ontology_runtime.get_session(session_id).session
        except Exception:
            _raise_domain_error(exc)
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message_ja": exc.message_ja,
                "current_version": current.current_intent_version,
                "session": current.model_dump(mode="json"),
            },
        ) from exc
    except Exception as exc:
        _raise_domain_error(exc)


@router.post(
    "/query-sessions/{session_id}/generate-sql",
    response_model=ApiResponse[QuerySessionData],
)
async def generate_query_sql(
    session_id: str,
    request: GenerateSqlRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> ApiResponse[QuerySessionData]:
    try:
        return ApiResponse(
            data=ontology_runtime.generate_sql_idempotent(
                session_id,
                request,
                idempotency_key=idempotency_key,
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


@router.post(
    "/query-sessions/{session_id}/confirm-sql",
    response_model=ApiResponse[QuerySessionData],
)
async def confirm_query_sql(
    session_id: str,
    request: SqlBindingRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> ApiResponse[QuerySessionData]:
    try:
        if request.session_id != session_id:
            raise OntologyIntegrityError(
                "SESSION_BINDING_MISMATCH",
                "確認 binding の session ID が URL と一致しません。",
            )
        return ApiResponse(
            data=ontology_runtime.confirm_sql_idempotent(
                session_id,
                request.binding(),
                idempotency_key=idempotency_key,
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


@router.post(
    "/query-sessions/{session_id}/execute",
    response_model=ApiResponse[QueryExecutionData],
)
async def execute_query_session(
    session_id: str,
    request: SqlBindingRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> ApiResponse[QueryExecutionData]:
    try:
        if request.session_id != session_id:
            raise OntologyIntegrityError(
                "SESSION_BINDING_MISMATCH",
                "実行 binding の session ID が URL と一致しません。",
            )
        return ApiResponse(
            data=ontology_runtime.execute_idempotent(
                session_id,
                request.binding(),
                idempotency_key=idempotency_key,
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


@router.post(
    "/query-sessions/{session_id}/improvement-proposal",
    response_model=ApiResponse[OntologyProposal],
)
async def create_ontology_improvement_proposal(
    session_id: str,
    request: ImprovementProposalRequest,
) -> ApiResponse[OntologyProposal]:
    try:
        proposal, _session = ontology_runtime.create_proposal(session_id, request)
        return ApiResponse(data=proposal)
    except Exception as exc:
        _raise_domain_error(exc)


@router.get(
    "/ontology/proposals/{proposal_id}",
    response_model=ApiResponse[OntologyProposal],
)
async def get_ontology_proposal(proposal_id: str) -> ApiResponse[OntologyProposal]:
    try:
        return ApiResponse(data=ontology_runtime.get_proposal(proposal_id))
    except Exception as exc:
        _raise_domain_error(exc)


@router.post(
    "/ontology/proposals/{proposal_id}/accept",
    response_model=ApiResponse[OntologyProposalReviewData],
)
async def accept_ontology_proposal(
    proposal_id: str,
) -> ApiResponse[OntologyProposalReviewData]:
    try:
        return ApiResponse(data=ontology_runtime.accept_proposal(proposal_id))
    except Exception as exc:
        _raise_domain_error(exc)


@router.post(
    "/ontology/proposals/{proposal_id}/reject",
    response_model=ApiResponse[OntologyProposalReviewData],
)
async def reject_ontology_proposal(
    proposal_id: str,
) -> ApiResponse[OntologyProposalReviewData]:
    try:
        return ApiResponse(data=ontology_runtime.reject_proposal(proposal_id))
    except Exception as exc:
        _raise_domain_error(exc)


class OntologyProposalBatchAcceptRequest(OntologyContract):
    proposal_ids: list[str] = Field(min_length=1)


class OntologyProposalBatchReviewData(OntologyContract):
    proposals: list[OntologyProposal]
    draft: OntologyGraphData


@router.post(
    "/ontology/proposals/batch-accept",
    response_model=ApiResponse[OntologyProposalBatchReviewData],
)
async def batch_accept_ontology_proposals(
    request: OntologyProposalBatchAcceptRequest,
) -> ApiResponse[OntologyProposalBatchReviewData]:
    """複数提案を 1 つの draft revision へまとめて承認する(一括承認)。"""

    try:
        proposals, draft = ontology_runtime.accept_proposals(request.proposal_ids)
        return ApiResponse(
            data=OntologyProposalBatchReviewData(
                proposals=proposals,
                draft=OntologyGraphData(
                    revision=draft.revision,
                    nodes=draft.nodes,
                    edges=draft.edges,
                ),
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


# --- AI オントロジー構築 -----------------------------------------------------------------------


class OntologyBuildJobData(OntologyContract):
    job: OntologyBuildJob


class OntologyProposalListData(OntologyContract):
    proposals: list[OntologyProposal]


@router.post(
    "/profiles/{profile_id}/ontology-build",
    response_model=ApiResponse[OntologyBuildJobData],
)
async def start_ontology_build(
    profile_id: str,
    business_text: Annotated[str, Form()] = "",
    run_schema_naming: Annotated[bool, Form()] = True,
    run_qa_extraction: Annotated[bool, Form()] = True,
    run_text_extraction: Annotated[bool, Form()] = True,
    qa_file: Annotated[UploadFile | None, File()] = None,
) -> ApiResponse[OntologyBuildJobData]:
    """AI オントロジー構築 job を投入する。Q/A ファイルは同期パースし、不正なら 400。"""

    qa_pairs: list[QaPair] = []
    parse_warnings: list[str] = []
    if qa_file is not None:
        content = await qa_file.read()
        qa_pairs, parse_warnings = parse_qa_workbook(qa_file.filename or "qa.xlsx", content)
        if run_qa_extraction and not qa_pairs:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "ONTOLOGY_BUILD_QA_FILE_INVALID",
                    "message_ja": " ".join(parse_warnings)
                    or "Q/A ファイルから有効な行を読み取れませんでした。",
                },
            )
    try:
        job = ontology_build_service.start(
            profile_id,
            business_text=business_text,
            qa_pairs=qa_pairs,
            run_schema_naming=run_schema_naming,
            run_qa_extraction=run_qa_extraction,
            run_text_extraction=run_text_extraction,
            initial_warnings=parse_warnings,
        )
        return ApiResponse(data=OntologyBuildJobData(job=job))
    except Exception as exc:
        _raise_domain_error(exc)


@router.get(
    "/ontology-build/{job_id}",
    response_model=ApiResponse[OntologyBuildJobData],
)
async def get_ontology_build_job(job_id: str) -> ApiResponse[OntologyBuildJobData]:
    job = ontology_build_service.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "ONTOLOGY_BUILD_JOB_NOT_FOUND",
                "message_ja": "AI オントロジー構築 job が見つかりません。",
            },
        )
    return ApiResponse(data=OntologyBuildJobData(job=job))


@router.get(
    "/profiles/{profile_id}/ontology-proposals",
    response_model=ApiResponse[OntologyProposalListData],
)
async def list_profile_ontology_proposals(
    profile_id: str,
) -> ApiResponse[OntologyProposalListData]:
    try:
        return ApiResponse(
            data=OntologyProposalListData(
                proposals=ontology_runtime.list_profile_proposals(profile_id)
            )
        )
    except Exception as exc:
        _raise_domain_error(exc)


__all__ = [
    "GenerateSqlRequest",
    "ImprovementProposalRequest",
    "OntologyApiRuntime",
    "OntologyGraphData",
    "ProfileOntologyViewPatch",
    "ProfileOntologyViewData",
    "QueryExecutionData",
    "QuerySessionApiCreate",
    "QuerySessionData",
    "SqlBindingRequest",
    "ontology_runtime",
    "router",
]
