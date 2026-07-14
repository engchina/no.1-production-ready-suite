"""NL2SQL Ontology の API / 永続化境界で共有する契約。

このモジュールは既存 NL2SQL API から独立させ、段階的な移行中でも旧 payload を
壊さない。Ontology は実データ行を物化せず、業務概念、物理 mapping、問い合わせの
解釈と SQL の意味を版管理された graph として表現する。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """UTC の timezone-aware timestamp を返す。"""

    return datetime.now(UTC)


class OntologyContract(BaseModel):
    """不明な mutation field を黙って受理しない共通契約。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class OntologyRevisionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class OntologyReviewStatus(StrEnum):
    PROPOSED = "proposed"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    ORPHANED = "orphaned"


class OntologySourceKind(StrEnum):
    INTROSPECTED = "introspected"
    MANUAL = "manual"
    INFERRED = "inferred"
    MIGRATED = "migrated"
    QUERY_SESSION = "query_session"


class OntologyNodeKind(StrEnum):
    SCHEMA = "schema"
    TABLE = "table"
    VIEW = "view"
    COLUMN = "column"
    BUSINESS_ENTITY = "business_entity"
    BUSINESS_EVENT = "business_event"
    PROPERTY = "property"
    METRIC = "metric"
    BUSINESS_TERM = "business_term"
    QUESTION_INTENT = "question_intent"
    QUERY_PLAN = "query_plan"
    SQL_ARTIFACT = "sql_artifact"
    VALIDATION_FINDING = "validation_finding"
    EXECUTION_PREVIEW = "execution_preview"


class OntologyEdgeKind(StrEnum):
    CONTAINS = "contains"
    FOREIGN_KEY = "foreign_key"
    BUSINESS_RELATIONSHIP = "business_relationship"
    MAPS_TO = "maps_to"
    LINEAGE = "lineage"
    USES = "uses"
    JOINS = "joins"
    FILTERS = "filters"
    GROUPS_BY = "groups_by"
    SORTS_BY = "sorts_by"


class RelationshipDirection(StrEnum):
    DIRECTED = "directed"
    BIDIRECTIONAL = "bidirectional"


class RelationshipCardinality(StrEnum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"
    MANY_TO_MANY = "many_to_many"
    UNKNOWN = "unknown"


class JoinType(StrEnum):
    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    FULL = "full"
    CROSS = "cross"
    SEMI = "semi"
    ANTI = "anti"


class OntologyRevision(OntologyContract):
    """共有 Ontology の不変 revision header。"""

    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: OntologyRevisionStatus = OntologyRevisionStatus.DRAFT
    schema_fingerprint: str = ""
    etag: str = ""
    parent_revision_id: str | None = None
    note: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None


class OntologyProvenance(OntologyContract):
    source_kind: OntologySourceKind
    source_id: str = ""
    source_detail: str = ""
    inferred_by: str = ""
    observed_at: datetime = Field(default_factory=utc_now)


class PhysicalObjectRef(OntologyContract):
    """owner を含む Oracle object の安定参照。"""

    node_id: str = ""
    owner: str = ""
    object_name: str = Field(min_length=1)
    object_type: Literal["table", "view"] = "table"


class PhysicalColumnRef(OntologyContract):
    node_id: str = ""
    owner: str = ""
    object_name: str = Field(min_length=1)
    column_name: str = Field(min_length=1)
    ordinal: int | None = Field(default=None, ge=1)


class PhysicalMapping(OntologyContract):
    object_ref: PhysicalObjectRef
    column_refs: list[PhysicalColumnRef] = Field(default_factory=list)
    expression_sql: str = ""
    lineage_source_ids: list[str] = Field(default_factory=list)


class JoinCondition(OntologyContract):
    left: PhysicalColumnRef
    right: PhysicalColumnRef
    operator: Literal["=", "<", ">", "<=", ">=", "!="] = "="
    ordinal: int = Field(default=1, ge=1)


class OntologyNode(OntologyContract):
    """物理 node と業務 node の共通表現。"""

    id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    kind: OntologyNodeKind
    technical_name: str = ""
    business_name_ja: str = Field(min_length=1)
    description_ja: str = ""
    aliases: list[str] = Field(default_factory=list)
    physical_mappings: list[PhysicalMapping] = Field(default_factory=list)
    provenance: OntologyProvenance
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    review_status: OntologyReviewStatus = OntologyReviewStatus.PROPOSED
    embedding_ref: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class OntologyEdge(OntologyContract):
    """複合 join と cardinality を失わない Ontology relationship。"""

    id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    kind: OntologyEdgeKind
    source_node_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    relationship_name_ja: str = Field(min_length=1)
    description_ja: str = ""
    direction: RelationshipDirection = RelationshipDirection.DIRECTED
    cardinality: RelationshipCardinality = RelationshipCardinality.UNKNOWN
    join_conditions: list[JoinCondition] = Field(default_factory=list)
    allowed_join_types: list[JoinType] = Field(default_factory=lambda: [JoinType.INNER])
    provenance: OntologyProvenance
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    review_status: OntologyReviewStatus = OntologyReviewStatus.PROPOSED
    metadata: dict[str, Any] = Field(default_factory=dict)


class ColumnQueryPolicy(OntologyContract):
    queryable: bool = True
    filterable: bool = True
    groupable: bool = True
    aggregatable: bool = False
    masked: bool = False
    required_filter: bool = False
    note_ja: str = ""


class ProfileOntologyView(OntologyContract):
    """共有 Ontology を profile の利用範囲へ射影した view。"""

    id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    ontology_revision_id: str = Field(min_length=1)
    view_version: int = Field(default=1, ge=1)
    status: OntologyRevisionStatus = OntologyRevisionStatus.PUBLISHED
    etag: str = ""
    node_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)
    physical_objects: list[PhysicalObjectRef] = Field(default_factory=list)
    table_usages_ja: dict[str, str] = Field(default_factory=dict)
    column_policies: dict[str, ColumnQueryPolicy] = Field(default_factory=dict)
    allowed_path_ids: list[str] = Field(default_factory=list)
    draft_node_overrides: list[dict[str, Any]] = Field(default_factory=list)
    draft_edge_overrides: list[dict[str, Any]] = Field(default_factory=list)
    draft_schema_fingerprint: str = ""
    draft_physical_scope: dict[str, list[str]] = Field(default_factory=dict)
    archived: bool = False
    updated_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None


class MetricAggregation(StrEnum):
    NONE = "none"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class MetricAdditivity(StrEnum):
    ADDITIVE = "additive"
    SEMI_ADDITIVE = "semi_additive"
    NON_ADDITIVE = "non_additive"
    UNKNOWN = "unknown"


class MetricDefinition(OntologyContract):
    """公開 Ontology で確認済みとして扱う正式指標定義。"""

    id: str = Field(min_length=1)
    metric_node_id: str = Field(min_length=1)
    expression_sql: str = Field(min_length=1)
    aggregation: MetricAggregation = MetricAggregation.NONE
    base_column_node_ids: list[str] = Field(default_factory=list)
    grain_node_ids: list[str] = Field(default_factory=list)
    distinct_key_node_ids: list[str] = Field(default_factory=list)
    unit: str = ""
    currency: str = ""
    additivity: MetricAdditivity = MetricAdditivity.UNKNOWN
    null_policy_ja: str = ""
    description_ja: str = ""


class IntentEntity(OntologyContract):
    id: str = Field(min_length=1)
    ontology_node_id: str = ""
    name_ja: str = Field(min_length=1)
    role: Literal["subject", "related", "event"] = "subject"
    physical_object_ids: list[str] = Field(default_factory=list)


class IntentMetric(OntologyContract):
    id: str = Field(min_length=1)
    ontology_node_id: str = ""
    name_ja: str = Field(min_length=1)
    aggregation: str = ""
    formula_description_ja: str = ""
    metric_definition_id: str = ""
    expression_sql: str = ""
    grain_node_ids: list[str] = Field(default_factory=list)


class IntentDimension(OntologyContract):
    id: str = Field(min_length=1)
    ontology_node_id: str = ""
    name_ja: str = Field(min_length=1)
    granularity: str = ""


class IntentFilter(OntologyContract):
    id: str = Field(min_length=1)
    property_node_id: str = ""
    label_ja: str = Field(min_length=1)
    operator: str = "="
    value: Any = None
    value_type: str = "string"
    required: bool = False


class IntentTimeRange(OntologyContract):
    property_node_id: str = ""
    label_ja: str = "期間"
    start: str | None = None
    end: str | None = None
    start_inclusive: bool = True
    end_inclusive: bool = True
    relative_expression: str = ""
    timezone: str = "Asia/Tokyo"


class IntentSort(OntologyContract):
    target_id: str = Field(min_length=1)
    direction: Literal["asc", "desc"] = "asc"


class IntentRelationshipPath(OntologyContract):
    id: str = Field(min_length=1)
    name_ja: str = Field(min_length=1)
    edge_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    approved: bool = False
    explanation_ja: str = ""


class IntentAmbiguity(OntologyContract):
    id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message_ja: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    resolution: str | None = None
    blocking: bool = True
    resolved: bool = False


class QuestionIntentGraph(OntologyContract):
    """自然言語質問を SQL より前に確認する業務意味 graph。"""

    version: int = Field(default=1, ge=1)
    question_original: str = Field(min_length=1)
    question_effective: str = Field(min_length=1)
    profile_view_id: str = Field(min_length=1)
    ontology_revision_id: str = Field(min_length=1)
    entities: list[IntentEntity] = Field(default_factory=list)
    metrics: list[IntentMetric] = Field(default_factory=list)
    dimensions: list[IntentDimension] = Field(default_factory=list)
    filters: list[IntentFilter] = Field(default_factory=list)
    time_range: IntentTimeRange | None = None
    granularity: str = ""
    sorts: list[IntentSort] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=5000)
    candidate_paths: list[IntentRelationshipPath] = Field(default_factory=list)
    selected_path_id: str | None = None
    ambiguities: list[IntentAmbiguity] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utc_now)


class SqlCteDefinition(OntologyContract):
    id: str
    name: str
    scope_id: str
    query_sql: str
    depends_on: list[str] = Field(default_factory=list)


class SqlTableReference(OntologyContract):
    id: str
    scope_id: str
    catalog: str = ""
    owner: str = ""
    name: str
    alias: str = ""
    qualified_name: str
    is_cte: bool = False
    source_sql: str = ""


class SqlColumnReference(OntologyContract):
    id: str
    scope_id: str
    catalog: str = ""
    owner: str = ""
    table: str = ""
    name: str
    clause: str
    expression_sql: str


class SqlJoinReference(OntologyContract):
    id: str
    scope_id: str
    left_source: str
    right_source: str
    join_type: str
    condition_sql: str = ""
    using_columns: list[str] = Field(default_factory=list)
    referenced_columns: list[str] = Field(default_factory=list)
    is_cartesian: bool = False


class SqlProjection(OntologyContract):
    id: str
    scope_id: str
    output_name: str
    expression_sql: str
    referenced_columns: list[str] = Field(default_factory=list)
    contains_aggregate: bool = False
    contains_window: bool = False
    contains_wildcard: bool = False


class SqlPredicate(OntologyContract):
    id: str
    scope_id: str
    clause: Literal["where", "having", "qualify"]
    expression_sql: str
    referenced_columns: list[str] = Field(default_factory=list)


class SqlAggregate(OntologyContract):
    id: str
    scope_id: str
    function_name: str
    expression_sql: str
    referenced_columns: list[str] = Field(default_factory=list)


class SqlGroupExpression(OntologyContract):
    id: str
    scope_id: str
    expression_sql: str
    referenced_columns: list[str] = Field(default_factory=list)


class SqlOrderExpression(OntologyContract):
    id: str
    scope_id: str
    expression_sql: str
    direction: Literal["asc", "desc"] = "asc"
    referenced_columns: list[str] = Field(default_factory=list)


class SqlWindowExpression(OntologyContract):
    id: str
    scope_id: str
    expression_sql: str
    partition_by: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    referenced_columns: list[str] = Field(default_factory=list)


class SqlSetOperation(OntologyContract):
    id: str
    scope_id: str
    operator: Literal["union", "union_all", "intersect", "except"]
    expression_sql: str


class SqlSubqueryReference(OntologyContract):
    id: str
    scope_id: str
    alias: str = ""
    query_sql: str


class SqlLineage(OntologyContract):
    id: str
    scope_id: str
    output_name: str
    source_columns: list[str] = Field(default_factory=list)


class SqlSemanticGraph(OntologyContract):
    """sqlglot AST から決定論的に生成する SQL の意味 graph。"""

    version: int = Field(default=1, ge=1)
    sql_hash: str
    dialect: Literal["oracle"] = "oracle"
    statement_type: str
    raw_sql: str
    parse_complete: bool = True
    ctes: list[SqlCteDefinition] = Field(default_factory=list)
    tables: list[SqlTableReference] = Field(default_factory=list)
    columns: list[SqlColumnReference] = Field(default_factory=list)
    joins: list[SqlJoinReference] = Field(default_factory=list)
    projections: list[SqlProjection] = Field(default_factory=list)
    filters: list[SqlPredicate] = Field(default_factory=list)
    aggregates: list[SqlAggregate] = Field(default_factory=list)
    groups: list[SqlGroupExpression] = Field(default_factory=list)
    having: list[SqlPredicate] = Field(default_factory=list)
    orders: list[SqlOrderExpression] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=0)
    windows: list[SqlWindowExpression] = Field(default_factory=list)
    set_operations: list[SqlSetOperation] = Field(default_factory=list)
    subqueries: list[SqlSubqueryReference] = Field(default_factory=list)
    lineage: list[SqlLineage] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)


class ValidationSeverity(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    BLOCKER = "blocker"


class OntologyValidationFinding(OntologyContract):
    id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    severity: ValidationSeverity
    message_ja: str = Field(min_length=1)
    intent_element_ids: list[str] = Field(default_factory=list)
    sql_element_ids: list[str] = Field(default_factory=list)
    ontology_node_ids: list[str] = Field(default_factory=list)
    suggested_action_ja: str = ""


class OntologyValidationReport(OntologyContract):
    """Question ↔ SQL ↔ Profile view の三方 validation。"""

    id: str = Field(min_length=1)
    intent_version: int = Field(ge=1)
    sql_hash: str
    ontology_revision_id: str
    is_valid: bool
    intent_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    findings: list[OntologyValidationFinding] = Field(default_factory=list)
    passed_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    blocker_count: int = Field(default=0, ge=0)
    validation_hash: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class SqlSemanticAnalysis(OntologyContract):
    graph: SqlSemanticGraph | None = None
    validation: OntologyValidationReport
    parser: Literal["sqlglot"] = "sqlglot"


class GraphPatchOperation(OntologyContract):
    op: Literal["add", "replace", "remove"]
    path: str = Field(pattern=r"^/")
    value: Any = None
    reason_ja: str = ""


class GraphPatch(OntologyContract):
    base_version: int = Field(ge=1)
    operations: list[GraphPatchOperation] = Field(min_length=1)
    summary_ja: str = ""


class OntologyProposalStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    # 新規 AI 構築の実行時に、前回のレビュー一覧を一掃するための終端状態。
    # レビュー一覧(list_proposals_by_profile)からは除外され UI には現れない。
    SUPERSEDED = "superseded"


class OntologyProposalKind(StrEnum):
    ALIAS = "alias"
    METRIC_DEFINITION = "metric_definition"
    RELATIONSHIP = "relationship"
    MAPPING = "mapping"
    PROFILE_POLICY = "profile_policy"
    QUERY_EXAMPLE = "query_example"


class OntologyProposalPayload(OntologyContract):
    kind: OntologyProposalKind = OntologyProposalKind.QUERY_EXAMPLE
    target_node_id: str = ""
    target_edge_id: str = ""
    values: dict[str, Any] = Field(default_factory=dict)


class OntologyProposal(OntologyContract):
    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    base_revision_id: str = Field(min_length=1)
    title_ja: str = Field(min_length=1)
    description_ja: str = ""
    kind: OntologyProposalKind = OntologyProposalKind.QUERY_EXAMPLE
    proposal_payload: OntologyProposalPayload = Field(default_factory=OntologyProposalPayload)
    patch: GraphPatch | None = None
    status: OntologyProposalStatus = OntologyProposalStatus.SUBMITTED
    created_at: datetime = Field(default_factory=utc_now)


# --- AI オントロジー構築(Enterprise AI 出力契約と job) ------------------------------------
#
# LLM 出力は必ずこれらの schema で検証し、profile view スコープ外の参照は proposal 化せず
# warnings に落とす。生成物は OntologyProposal(承認フロー)経由でのみ Ontology に入る。


class OntologyBuildJoinConditionCandidate(OntologyContract):
    """LLM が返す join 条件候補。列は OWNER.OBJECT.COLUMN 形式。"""

    left: str = Field(min_length=1)
    right: str = Field(min_length=1)
    operator: Literal["=", "<", ">", "<=", ">=", "!="] = "="


class OntologyEntityNamingCandidate(OntologyContract):
    object_name: str = Field(min_length=1, description="OWNER.OBJECT または OBJECT")
    business_name_ja: str = Field(min_length=1)
    description_ja: str = ""
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class OntologyRelationshipCandidate(OntologyContract):
    source_object: str = Field(min_length=1)
    target_object: str = Field(min_length=1)
    relationship_name_ja: str = Field(min_length=1)
    cardinality: RelationshipCardinality = RelationshipCardinality.UNKNOWN
    join_conditions: list[OntologyBuildJoinConditionCandidate] = Field(default_factory=list)
    evidence_ja: str = ""
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class OntologyMetricCandidate(OntologyContract):
    metric_name_ja: str = Field(min_length=1)
    expression_sql: str = Field(min_length=1)
    aggregation: MetricAggregation = MetricAggregation.NONE
    base_columns: list[str] = Field(default_factory=list, description="OWNER.OBJECT.COLUMN")
    unit: str = ""
    description_ja: str = ""
    evidence_ja: str = ""
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class OntologySynonymCandidate(OntologyContract):
    target: str = Field(min_length=1, description="OWNER.OBJECT または OWNER.OBJECT.COLUMN")
    aliases: list[str] = Field(min_length=1)
    evidence_ja: str = ""


class OntologyBuildExtraction(OntologyContract):
    """AI オントロジー構築 1 ステップ分の LLM 出力契約。"""

    entities: list[OntologyEntityNamingCandidate] = Field(default_factory=list)
    relationships: list[OntologyRelationshipCandidate] = Field(default_factory=list)
    metrics: list[OntologyMetricCandidate] = Field(default_factory=list)
    synonyms: list[OntologySynonymCandidate] = Field(default_factory=list)
    warnings_ja: list[str] = Field(default_factory=list)


class QaPair(OntologyContract):
    """Q/A Excel の 1 行(質問と正解 SQL)。"""

    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    note_ja: str = ""


class OntologyBuildStepName(StrEnum):
    SCHEMA_CONTEXT = "schema_context"
    SCHEMA_NAMING = "schema_naming"
    QA_EXTRACTION = "qa_extraction"
    TEXT_EXTRACTION = "text_extraction"
    PROPOSAL_REGISTRATION = "proposal_registration"


class OntologyBuildStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


class OntologyBuildStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OntologyBuildStep(OntologyContract):
    name: OntologyBuildStepName
    status: OntologyBuildStepStatus = OntologyBuildStepStatus.PENDING
    detail_ja: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class OntologyBuildEvent(OntologyContract):
    """アクティビティタイムライン 1 行(時刻付きの進捗イベント)。"""

    at: datetime = Field(default_factory=utc_now)
    message_ja: str = Field(min_length=1)


class OntologyBuildJob(OntologyContract):
    """AI 構築 job(in-memory)。成果物の proposal は store に永続化される。"""

    id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    status: OntologyBuildStatus = OntologyBuildStatus.QUEUED
    steps: list[OntologyBuildStep] = Field(default_factory=list)
    events: list[OntologyBuildEvent] = Field(default_factory=list)
    proposal_ids: list[str] = Field(default_factory=list)
    warnings_ja: list[str] = Field(default_factory=list)
    error_message_ja: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class OntologySqlGenerationContext(OntologyContract):
    """確認済み intent から SQL 生成エンジンへ渡す決定論的な業務制約。"""

    session_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    profile_view_id: str = Field(min_length=1)
    ontology_revision_id: str = Field(min_length=1)
    intent_version: int = Field(ge=1)
    question_effective: str = Field(min_length=1)
    allowed_object_names: list[str] = Field(default_factory=list)
    allowed_column_names: dict[str, list[str]] = Field(default_factory=dict)
    entity_node_ids: list[str] = Field(default_factory=list)
    metric_node_ids: list[str] = Field(default_factory=list)
    dimension_node_ids: list[str] = Field(default_factory=list)
    filter_summaries_ja: list[str] = Field(default_factory=list)
    time_range_summary_ja: str = ""
    granularity: str = ""
    sort_summaries_ja: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=5000)
    selected_path_id: str = ""
    approved_join_edge_ids: list[str] = Field(default_factory=list)
    join_condition_summaries: list[str] = Field(default_factory=list)
    metric_definitions: list[MetricDefinition] = Field(default_factory=list)
    warnings_ja: list[str] = Field(default_factory=list)
    # LLM プロンプト用の erDiagram 表現。context_hash の計算対象には含めない
    # (永続化済み session・確認 binding との互換を保つ)。
    mermaid_er: str = ""
    context_hash: str = Field(min_length=1)


class QuerySessionStatus(StrEnum):
    INTERPRETING = "interpreting"
    AWAITING_INTENT_CONFIRMATION = "awaiting_intent_confirmation"
    GENERATING_SQL = "generating_sql"
    AWAITING_SQL_CONFIRMATION = "awaiting_sql_confirmation"
    EXECUTING = "executing"
    DONE = "done"
    ERROR = "error"


class SqlArtifact(OntologyContract):
    id: str = Field(min_length=1)
    intent_version: int = Field(ge=1)
    ontology_revision_id: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    sql_hash: str = Field(min_length=1)
    generation_context_hash: str = Field(default="")
    semantic_graph: SqlSemanticGraph | None = None
    validation_report: OntologyValidationReport
    created_at: datetime = Field(default_factory=utc_now)


class SqlConfirmationBinding(OntologyContract):
    artifact_id: str = Field(min_length=1)
    ontology_revision_id: str = Field(min_length=1)
    intent_version: int = Field(ge=1)
    sql_hash: str = Field(min_length=1)
    validation_hash: str = Field(min_length=1)
    generation_context_hash: str = Field(min_length=1)
    confirmed_at: datetime = Field(default_factory=utc_now)


class QueryExecutionRecord(OntologyContract):
    binding: SqlConfirmationBinding
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    row_count: int | None = Field(default=None, ge=0)
    result_ref: str = ""


class QuerySession(OntologyContract):
    """二段階確認を含む問い合わせ session の全 version。"""

    id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    profile_view_id: str = Field(min_length=1)
    ontology_revision_id: str = Field(min_length=1)
    status: QuerySessionStatus = QuerySessionStatus.INTERPRETING
    original_question: str = Field(min_length=1)
    current_intent_version: int = Field(default=1, ge=1)
    intents: list[QuestionIntentGraph] = Field(default_factory=list)
    sql_artifacts: list[SqlArtifact] = Field(default_factory=list)
    current_sql_artifact_id: str | None = None
    intent_confirmed_version: int | None = None
    sql_confirmation: SqlConfirmationBinding | None = None
    execution: QueryExecutionRecord | None = None
    proposal_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    error_code: str = ""
    error_message_ja: str = ""


class QuerySessionCreate(OntologyContract):
    question: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    profile_view_id: str = Field(min_length=1)
    ontology_revision_id: str = Field(min_length=1)
    intent: QuestionIntentGraph | None = None


class SqlConfirmationRequest(OntologyContract):
    artifact_id: str = Field(min_length=1)
    ontology_revision_id: str = Field(min_length=1)
    intent_version: int = Field(ge=1)
    sql_hash: str = Field(min_length=1)
    validation_hash: str = Field(min_length=1)
    generation_context_hash: str = Field(min_length=1)
