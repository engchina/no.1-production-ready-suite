"""設定 API のスキーマ。secret はレスポンスに含めない。"""

import json
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import (
    AgenticProfile,
    ChunkingStrategy,
    EnterpriseAiVlmInputMode,
    EvaluationSuite,
    GenerationProfile,
    GraphProfile,
    GuardrailPolicyName,
    ParserAdapterBackend,
    PostRetrievalPipeline,
    PreprocessProfile,
    RetrievalStrategy,
    UploadStorageBackend,
    VectorIndexProfile,
)

ModelSettingsCheckStatus = Literal["ok", "missing", "invalid"]
ModelSettingsTestStatus = Literal["success", "failed"]
ModelSettingsTestTargetType = Literal["enterprise_text", "enterprise_vision", "embedding", "rerank"]
DatabaseConnectionTestStatus = Literal["success", "failed"]
OciConfigTestStatus = Literal["success", "failed"]
OciConfigField = Literal["user", "fingerprint", "tenancy", "region", "key_file"]
ParserAdapterBackendName = Literal["docling", "marker", "unstructured", "mineru", "dots_ocr"]
ParserAdapterScoreBackendName = Literal[
    "local", "docling", "marker", "unstructured", "mineru", "dots_ocr"
]
ParserAdapterStatus = Literal["active", "available", "disabled", "ignored", "missing"]
ParserAdapterContractStatus = Literal[
    "passed",
    "failed",
    "fallback",
    "available",
    "ignored",
    "disabled",
    "missing",
    "unsupported",
    "fixture_missing",
]
ParserAdapterScoreStatus = Literal[
    "recommended",
    "eligible",
    "available",
    "disabled",
    "ignored",
    "missing",
]


class EnterpriseAiModelEntrySettings(BaseModel):
    """OCI Enterprise AI provider に登録する LLM。"""

    model_id: str = Field(default="", max_length=256)
    display_name: str = Field(default="", max_length=256)
    vision_enabled: bool = False

    @field_validator("model_id", "display_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class EnterpriseAiModelSettings(BaseModel):
    """OCI Enterprise AI モデル provider 設定。"""

    endpoint: str = Field(default="", max_length=2048)
    project_ocid: str = Field(default="", max_length=512)
    api_key: str = Field(default="", max_length=4096)
    has_api_key: bool = False
    clear_api_key: bool = False
    models: list[EnterpriseAiModelEntrySettings] = Field(default_factory=list, max_length=20)
    default_model_id: str = Field(default="", max_length=256)
    api_path: str = Field(default="/responses", max_length=512)
    vlm_input_mode: EnterpriseAiVlmInputMode = "auto"
    text_payload_template: str = Field(default="", max_length=20000)
    vision_payload_template: str = Field(default="", max_length=20000)
    text_response_path: str = Field(default="", max_length=1024)
    vision_response_path: str = Field(default="", max_length=1024)
    timeout_seconds: float = Field(default=600.0, gt=0.0, le=600.0)
    max_retries: int = Field(default=3, ge=0, le=5)
    llm_max_output_tokens: int = Field(default=1200, ge=1, le=65536)
    vlm_max_output_tokens: int = Field(default=65536, ge=1, le=65536)

    @field_validator(
        "endpoint",
        "project_ocid",
        "api_key",
        "default_model_id",
        "api_path",
        "text_payload_template",
        "vision_payload_template",
        "text_response_path",
        "vision_response_path",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        """endpoint の readiness 判定は保存後のチェックへ委譲する。"""
        return value

    @field_validator("project_ocid")
    @classmethod
    def validate_project_ocid(cls, value: str) -> str:
        """project OCID の readiness 判定は保存後のチェックへ委譲する。"""
        return value

    @field_validator("api_path")
    @classmethod
    def validate_api_path(cls, value: str) -> str:
        """API path の readiness 判定は保存後のチェックへ委譲する。"""
        return value

    @field_validator("text_payload_template", "vision_payload_template")
    @classmethod
    def validate_payload_template(cls, value: str) -> str:
        """payload template は空または JSON object 文字列だけを許可する。"""
        if not value:
            return value
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise ValueError("payload template は JSON object で入力してください。") from exc
        if not isinstance(parsed, dict):
            raise ValueError("payload template は JSON object で入力してください。")
        return value

    @field_validator("text_response_path", "vision_response_path")
    @classmethod
    def validate_response_path(cls, value: str) -> str:
        """response path は空または JSON Pointer 形式だけを許可する。"""
        if value and not value.startswith("/"):
            raise ValueError("response path は / で始まる JSON Pointer で入力してください。")
        return value


class GenerativeAiModelSettings(BaseModel):
    """OCI Generative AI（embedding/rerank）モデル設定。"""

    embedding_model: str = Field(default="cohere.embed-v4.0", max_length=256)
    embedding_dim: int = Field(
        default=1536,
        ge=1536,
        le=1536,
        description="Oracle VECTOR(1536, FLOAT32) と互換にするため 1536 固定。",
    )
    rerank_model: str = Field(default="cohere.rerank-v4.0-fast", max_length=256)

    @field_validator("embedding_model", "rerank_model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class ModelSettingsPayload(BaseModel):
    """モデル設定の読み書き payload。"""

    enterprise_ai: EnterpriseAiModelSettings
    generative_ai: GenerativeAiModelSettings


class ModelSettingsData(BaseModel):
    """モデル設定 API のレスポンス data。"""

    settings: ModelSettingsPayload
    checks: dict[str, ModelSettingsCheckStatus]
    model_settings_file: str
    source: Literal["runtime"]


class ModelSettingsTestRequest(BaseModel):
    """保存前のモデル設定で特定モデルを実 API に対してテストする request。"""

    settings: ModelSettingsPayload
    target_type: ModelSettingsTestTargetType
    model_id: str = Field(default="", max_length=256)
    vision_enabled: bool = False

    @field_validator("model_id")
    @classmethod
    def strip_model_id(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class ModelSettingsTestResult(BaseModel):
    """モデル単位の実接続テスト結果。"""

    status: ModelSettingsTestStatus
    target_type: ModelSettingsTestTargetType
    model_id: str
    message: str
    troubleshooting: list[str] = Field(default_factory=list)
    raw_error: str | None = None
    error_type: str | None = None
    elapsed_ms: int
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class DatabaseSettingsData(BaseModel):
    """Oracle 26ai 接続設定の表示用データ。"""

    user: str
    dsn: str
    wallet_dir: str
    wallet_uploaded: bool
    available_services: list[str]
    has_password: bool
    has_wallet_password: bool
    readiness: str
    embedding_dimension: int
    vector_column: str
    adb_ocid: str
    region: str
    config_source: Literal["runtime"]


AdbOperationStatus = Literal[
    "success",
    "not_configured",
    "error",
    "accepted",
    "already_available",
    "already_stopped",
    "cannot_start",
    "cannot_stop",
]


class AdbSettingsUpdate(BaseModel):
    """Autonomous Database 操作対象の OCID と region の更新 payload。"""

    adb_ocid: str = Field(default="", max_length=512)
    region: str = Field(default="", max_length=128)

    @field_validator("adb_ocid", "region")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class AdbInfoData(BaseModel):
    """Autonomous Database の情報 / 操作結果の表示用データ。"""

    status: AdbOperationStatus
    message: str
    id: str | None = None
    display_name: str | None = None
    lifecycle_state: str | None = None
    db_name: str | None = None
    cpu_core_count: int | None = None
    data_storage_size_in_tbs: float | None = None
    region: str | None = None


class DatabaseSettingsUpdate(BaseModel):
    """Oracle 26ai 接続設定の更新 payload。

    password / wallet_password は未指定または空文字なら既存値を保持する。
    clear_* が true の場合だけ保存済み secret を削除する。
    """

    user: str = Field(default="", max_length=256)
    dsn: str = Field(default="", max_length=1024)
    wallet_dir: str = Field(default="", max_length=1024)
    password: str | None = Field(default=None, max_length=4096)
    wallet_password: str | None = Field(default=None, max_length=4096)
    clear_password: bool = False
    clear_wallet_password: bool = False

    @field_validator("user", "dsn", "wallet_dir")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class UploadStorageSettingsData(BaseModel):
    """アップロード原本保存先の表示用データ。"""

    backend: UploadStorageBackend
    local_storage_dir: str
    object_storage_region: str
    object_storage_namespace: str
    object_storage_bucket: str
    readiness: str
    max_upload_bytes: int
    config_source: Literal["runtime"]


class UploadStorageSettingsUpdate(BaseModel):
    """アップロード原本保存先の更新 payload。"""

    backend: UploadStorageBackend
    local_storage_dir: str = Field(default="", max_length=1024)
    object_storage_namespace: str | None = Field(default=None, max_length=256)
    object_storage_bucket: str = Field(default="", max_length=256)

    @field_validator("local_storage_dir", "object_storage_bucket")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("object_storage_namespace")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        """省略時は既存の OCI 認証設定 namespace を保持する。"""
        return value.strip() if value is not None else None

    @field_validator("object_storage_namespace", "object_storage_bucket")
    @classmethod
    def validate_object_storage_name(cls, value: str | None) -> str | None:
        """OCI Object Storage の namespace / bucket 名で危険な文字を拒否する。"""
        if value and not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError(
                "Object Storage の値は英数字、ハイフン、アンダースコア、ドットで入力してください。"
            )
        return value


class ParserAdapterStatusData(BaseModel):
    """任意 parser adapter の feature flag / package readiness。"""

    backend: ParserAdapterBackendName
    package_name: str
    import_name: str
    distribution_name: str | None = None
    install_package: str
    enabled: bool
    selected: bool
    installed: bool
    status: ParserAdapterStatus
    version: str | None = None
    warning_code: str | None = None


class ParserAdapterScorecardEntryData(BaseModel):
    """parser backend 推奨 scorecard の 1 行。"""

    backend: ParserAdapterScoreBackendName
    rank: int
    score: float
    status: ParserAdapterScoreStatus
    recommended: bool
    executable: bool
    selected: bool
    enabled: bool
    installed: bool
    metric_source: str
    metric_count: int
    signals: dict[str, float] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)


class ParserAdapterScorecardData(BaseModel):
    """parser backend の評価駆動推奨。"""

    selected_backend: ParserAdapterBackend
    recommended_backend: ParserAdapterScoreBackendName
    metrics_source: str
    metrics_applied_to: ParserAdapterScoreBackendName | None = None
    entries: list[ParserAdapterScorecardEntryData]


class ParserAdapterSourceRouteData(BaseModel):
    """source kind ごとの adapter routing evidence。"""

    source_kind: str
    candidate_order: list[ParserAdapterScoreBackendName] = Field(default_factory=list)
    attempted_order: list[ParserAdapterScoreBackendName] = Field(default_factory=list)
    active_order: list[ParserAdapterScoreBackendName] = Field(default_factory=list)
    selected_backend: ParserAdapterScoreBackendName
    reason_codes: list[str] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)


class ParserAdapterBackendSourceMatrixData(BaseModel):
    """runtime 設定から見た backend-source routing matrix。"""

    evidence_source: Literal["runtime_routes"]
    required_source_kinds: list[str] = Field(default_factory=list)
    covered_source_kinds: list[str] = Field(default_factory=list)
    missing_source_kinds: list[str] = Field(default_factory=list)
    backend_source_kinds: dict[ParserAdapterScoreBackendName, list[str]] = Field(
        default_factory=dict
    )
    route_evidence: list[ParserAdapterSourceRouteData] = Field(default_factory=list)


class ParserAdapterContractCaseData(BaseModel):
    """adapter/source の実 remap compatibility 結果。"""

    backend: ParserAdapterBackendName
    source_kind: str
    fixture_name: str
    content_type: str
    status: ParserAdapterContractStatus
    blocking: bool
    parser_backend: str | None = None
    parser_version: str | None = None
    adapter_import_name: str | None = None
    adapter_distribution_name: str | None = None
    adapter_package_version: str | None = None
    template: str | None = None
    element_count: int = 0
    page_count: int = 0
    table_count: int = 0
    table_cell_count: int = 0
    asset_count: int = 0
    bbox_count: int = 0
    warning_codes: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class ParserAdapterContractSummaryData(BaseModel):
    """compatibility matrix の低機密 summary。"""

    passed: bool
    case_count: int
    blocking_failure_count: int
    source_kinds: list[str] = Field(default_factory=list)
    backends: list[ParserAdapterBackendName] = Field(default_factory=list)
    passed_source_kinds: list[str] = Field(default_factory=list)
    missing_source_kinds: list[str] = Field(default_factory=list)
    blocking_failure_source_kinds: list[str] = Field(default_factory=list)
    blocking_failure_backends: list[ParserAdapterBackendName] = Field(default_factory=list)
    backend_status_counts: dict[ParserAdapterBackendName, dict[str, int]] = Field(
        default_factory=dict
    )
    backend_source_status: dict[ParserAdapterBackendName, dict[str, str]] = Field(
        default_factory=dict
    )
    backend_source_status_counts: dict[
        ParserAdapterBackendName,
        dict[str, dict[str, int]],
    ] = Field(default_factory=dict)
    source_kind_status_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    backend_passed_source_kinds: dict[ParserAdapterBackendName, list[str]] = Field(
        default_factory=dict
    )
    scenarios: list[str] = Field(default_factory=list)
    passed_scenarios: list[str] = Field(default_factory=list)
    missing_scenarios: list[str] = Field(default_factory=list)
    blocking_failure_scenarios: list[str] = Field(default_factory=list)
    backend_passed_scenarios: dict[ParserAdapterBackendName, list[str]] = Field(
        default_factory=dict
    )
    reason_code_counts: dict[str, int] = Field(default_factory=dict)
    warning_code_counts: dict[str, int] = Field(default_factory=dict)
    blocking_failure_reason_counts: dict[str, int] = Field(default_factory=dict)
    blocking_failures: list[dict[str, object]] = Field(default_factory=list)


class ParserAdapterContractData(BaseModel):
    """parser adapter compatibility matrix の API payload。"""

    passed: bool
    fixture_root: str
    source_kinds: list[str] = Field(default_factory=list)
    backends: list[ParserAdapterBackendName] = Field(default_factory=list)
    case_count: int
    blocking_failure_count: int
    cases: list[ParserAdapterContractCaseData] = Field(default_factory=list)
    summary: ParserAdapterContractSummaryData
    config_source: Literal["runtime"]


class ParserServiceBackendData(BaseModel):
    """service 系 parser backend(OCI クラウドサービス直呼び)の選択状態と可用性。

    package readiness の対象外。backend から OCI Enterprise AI VLM / Document
    Understanding を直接呼ぶため、設定の完全性で「利用可能か」を示す。
    """

    backend: Literal["enterprise_ai_vlm", "oci_document_understanding"]
    selected: bool
    configured: bool
    warning_code: str | None = None


class ParserAdapterSettingsData(BaseModel):
    """任意 parser adapter 設定の非機密 runtime snapshot。"""

    adapter_backend: ParserAdapterBackend
    effective_order: list[ParserAdapterBackendName]
    adapters: list[ParserAdapterStatusData]
    service_backends: list[ParserServiceBackendData] = Field(default_factory=list)
    scorecard: ParserAdapterScorecardData
    source_routes: list[ParserAdapterSourceRouteData] = Field(default_factory=list)
    backend_source_kind_matrix: ParserAdapterBackendSourceMatrixData
    config_source: Literal["runtime"]


class ParserAdapterSettingsUpdate(BaseModel):
    """任意 parser adapter feature flags の更新 payload。"""

    adapter_backend: ParserAdapterBackend
    docling_enabled: bool = False
    marker_enabled: bool = False
    unstructured_enabled: bool = False


ChunkingStrategyName = ChunkingStrategy


class PreprocessProfileStatusData(BaseModel):
    """前処理(Preprocess)段階の 1 変換プリセットの選択状態と実行基盤。"""

    name: PreprocessProfile
    origin: str
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool
    in_process: bool = False
    requires_service: bool = False
    available: bool = True


class PreprocessSettingsData(BaseModel):
    """前処理アダプター設定の非機密 runtime snapshot。"""

    profile: PreprocessProfile
    service_enabled: bool
    service_url: str
    canonical_artifact_prefix: str
    profiles: list[PreprocessProfileStatusData] = Field(default_factory=list)
    config_source: Literal["runtime"]


class PreprocessSettingsUpdate(BaseModel):
    """前処理アダプター設定の更新 payload。"""

    profile: PreprocessProfile


class ChunkingStrategyStatusData(BaseModel):
    """chunks 段階の 1 分割戦略の選択状態と適用場面。"""

    name: ChunkingStrategyName
    origin: str
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool
    uses_child_size: bool = False
    uses_sentence_window: bool = False


class ChunkingSettingsData(BaseModel):
    """Chunking アダプター設定の非機密 runtime snapshot。"""

    strategy: ChunkingStrategyName
    chunk_size: int
    overlap: int
    child_size: int
    sentence_window_size: int
    min_chars: int
    strategies: list[ChunkingStrategyStatusData] = Field(default_factory=list)
    config_source: Literal["runtime"]


class ChunkingSettingsUpdate(BaseModel):
    """Chunking アダプター設定の更新 payload。"""

    strategy: ChunkingStrategyName
    chunk_size: int = Field(default=800, ge=200, le=4000)
    overlap: int = Field(default=120, ge=0, le=1000)
    child_size: int = Field(default=320, ge=80, le=4000)
    sentence_window_size: int = Field(default=3, ge=1, le=20)
    min_chars: int = Field(default=0, ge=0, le=2000)

    @model_validator(mode="after")
    def validate_chunk_bounds(self) -> "ChunkingSettingsUpdate":
        """chunk size と各パラメータの整合性を保存前に検証する。"""
        if self.overlap >= self.chunk_size:
            raise ValueError("overlap は chunk_size より小さくしてください。")
        if self.child_size >= self.chunk_size:
            raise ValueError("child_size は chunk_size より小さくしてください。")
        if self.min_chars >= self.chunk_size:
            raise ValueError("min_chars は chunk_size より小さくしてください。")
        return self


RetrievalStrategyName = RetrievalStrategy
PostRetrievalPipelineName = PostRetrievalPipeline
ExpansionModeName = Literal["none", "neighbor", "group", "adaptive"]


class RetrievalStrategyStatusData(BaseModel):
    """検索段階の 1 戦略の選択状態と適用場面。"""

    name: RetrievalStrategyName
    origin: str
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool
    gap_stop: bool = False
    corrective_retrieval: bool = False
    business_fit_weighting: bool = False


class RetrievalSettingsData(BaseModel):
    """Retrieval アダプター設定の非機密 runtime snapshot。"""

    strategy: RetrievalStrategyName
    query_expansion: bool
    gap_stop: bool
    corrective_retrieval: bool
    business_fit_weighting: bool
    strategies: list[RetrievalStrategyStatusData] = Field(default_factory=list)
    config_source: Literal["runtime"]


class RetrievalSettingsUpdate(BaseModel):
    """Retrieval アダプター設定の更新 payload。"""

    strategy: RetrievalStrategyName


class GroundingPipelineStatusData(BaseModel):
    """検索後処理の 1 プリセットの選択状態と束ねる段。"""

    name: PostRetrievalPipelineName
    origin: str
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool
    dependency_promotion: bool = False
    diversity: bool = False
    expansion_mode: ExpansionModeName = "none"
    compression: bool = False


class GroundingSettingsData(BaseModel):
    """Grounding アダプター設定の非機密 runtime snapshot。"""

    pipeline: PostRetrievalPipelineName
    dependency_promotion_enabled: bool
    diversity_enabled: bool
    expansion_mode: ExpansionModeName
    compression_enabled: bool
    pipelines: list[GroundingPipelineStatusData] = Field(default_factory=list)
    config_source: Literal["runtime"]


class GroundingSettingsUpdate(BaseModel):
    """Grounding アダプター設定の更新 payload。"""

    pipeline: PostRetrievalPipelineName


GenerationProfileName = GenerationProfile
GuardrailPolicyNameSchema = GuardrailPolicyName


class GenerationProfileStatusData(BaseModel):
    """回答生成の 1 プロファイルの選択状態と適用場面。"""

    name: GenerationProfileName
    origin: str
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool
    structured_output: bool = False


class GenerationSettingsData(BaseModel):
    """Generation アダプター設定の非機密 runtime snapshot。"""

    profile: GenerationProfileName
    structured_output: bool
    profiles: list[GenerationProfileStatusData] = Field(default_factory=list)
    config_source: Literal["runtime"]


class GenerationSettingsUpdate(BaseModel):
    """Generation アダプター設定の更新 payload。"""

    profile: GenerationProfileName


class PromptVersionData(BaseModel):
    """回答生成 system prompt の 1 版(PoweRAG の prompt versioning 由来)。"""

    version_id: str
    name: str
    system_prompt: str
    note: str = ""
    created_at: datetime
    created_by: str = ""
    active: bool = False


class PromptVersionsData(BaseModel):
    """prompt 版一覧と有効版。"""

    active_version_id: str | None = None
    versions: list[PromptVersionData] = Field(default_factory=list)


class PromptVersionCreate(BaseModel):
    """新しい prompt 版の作成 payload。"""

    name: str = Field(min_length=1, max_length=120)
    system_prompt: str = Field(min_length=1, max_length=20000)
    note: str = Field(default="", max_length=2000)
    activate: bool = True

    @field_validator("name", "system_prompt")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name と system_prompt は空にできません。")
        return cleaned


class FieldDefinitionData(BaseModel):
    """抽出対象 field の宣言(PoweRAG/LangExtract 由来)。"""

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    value_type: Literal["string", "number", "date", "bool"] = "string"

    @field_validator("name")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field name は空にできません。")
        return cleaned


class ExtractionFieldsSettingsData(BaseModel):
    """field 抽出 schema 定義の snapshot。"""

    fields: list[FieldDefinitionData] = Field(default_factory=list)
    config_source: Literal["runtime"] = "runtime"


class ExtractionFieldsSettingsUpdate(BaseModel):
    """field 抽出 schema 定義の更新 payload。"""

    fields: list[FieldDefinitionData] = Field(default_factory=list, max_length=50)


class GuardrailPolicyStatusData(BaseModel):
    """安全の 1 ポリシーの選択状態と groundedness 厳格度。"""

    name: GuardrailPolicyNameSchema
    origin: str
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool
    grounding_min_overlap: int
    grounding_min_ratio: float
    audit_emphasis: bool = False


class GuardrailSettingsData(BaseModel):
    """Guardrail アダプター設定の非機密 runtime snapshot。"""

    policy: GuardrailPolicyNameSchema
    block_prompt_injection: bool
    mask_sensitive_identifiers: bool
    max_query_chars: int
    grounding_min_overlap: int
    grounding_min_ratio: float
    audit_emphasis: bool
    policies: list[GuardrailPolicyStatusData] = Field(default_factory=list)
    config_source: Literal["runtime"]


class GuardrailSettingsUpdate(BaseModel):
    """Guardrail アダプター設定の更新 payload。"""

    policy: GuardrailPolicyNameSchema


VectorIndexProfileName = VectorIndexProfile


class VectorIndexProfileStatusData(BaseModel):
    """索引/検索精度の 1 プロファイルの選択状態と推奨値。"""

    name: VectorIndexProfileName
    origin: str
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool
    target_accuracy: int
    neighbors: int
    efconstruction: int
    distance: str


class VectorIndexSettingsData(BaseModel):
    """Vector Index アダプター設定の非機密 runtime snapshot。"""

    profile: VectorIndexProfileName
    target_accuracy: int
    neighbors: int
    efconstruction: int
    distance: str
    requires_reprovision: bool
    profiles: list[VectorIndexProfileStatusData] = Field(default_factory=list)
    config_source: Literal["runtime"]


class VectorIndexSettingsUpdate(BaseModel):
    """Vector Index アダプター設定の更新 payload。"""

    profile: VectorIndexProfileName


EvaluationSuiteName = EvaluationSuite


class EvaluationSuiteStatusData(BaseModel):
    """評価の 1 スイートの選択状態と閾値。"""

    name: EvaluationSuiteName
    origin: str
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool
    thresholds: dict[str, float] = Field(default_factory=dict)
    focus_metrics: list[str] = Field(default_factory=list)


class EvaluationSettingsData(BaseModel):
    """Evaluation アダプター設定の非機密 runtime snapshot。"""

    suite: EvaluationSuiteName
    thresholds: dict[str, float] = Field(default_factory=dict)
    focus_metrics: list[str] = Field(default_factory=list)
    suites: list[EvaluationSuiteStatusData] = Field(default_factory=list)
    config_source: Literal["runtime"]


class EvaluationSettingsUpdate(BaseModel):
    """Evaluation アダプター設定の更新 payload。"""

    suite: EvaluationSuiteName


GraphProfileName = GraphProfile
AgenticProfileName = AgenticProfile


class GraphProfileStatusData(BaseModel):
    """知識グラフ構築の 1 プロファイルの選択状態と構築深度。"""

    name: GraphProfileName
    origin: str
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool
    enabled: bool
    build_claims: bool
    build_community_summaries: bool


class GraphSettingsData(BaseModel):
    """GraphRAG アダプター設定の非機密 runtime snapshot。"""

    profile: GraphProfileName
    enabled: bool
    build_claims: bool
    build_community_summaries: bool
    profiles: list[GraphProfileStatusData] = Field(default_factory=list)
    config_source: Literal["runtime"]


class GraphSettingsUpdate(BaseModel):
    """GraphRAG アダプター設定の更新 payload。"""

    profile: GraphProfileName


class AgenticProfileStatusData(BaseModel):
    """クエリ計画の 1 プロファイルの選択状態と挙動。"""

    name: AgenticProfileName
    origin: str
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool
    enabled: bool
    rewrite: bool
    decompose: bool
    multi_hop: bool


class AgenticSettingsData(BaseModel):
    """Agentic アダプター設定の非機密 runtime snapshot。"""

    profile: AgenticProfileName
    enabled: bool
    rewrite: bool
    decompose: bool
    multi_hop: bool
    max_subqueries: int
    profiles: list[AgenticProfileStatusData] = Field(default_factory=list)
    config_source: Literal["runtime"]


class AgenticSettingsUpdate(BaseModel):
    """Agentic アダプター設定の更新 payload。"""

    profile: AgenticProfileName


class OciConfigReadRequest(BaseModel):
    """バックエンドから OCI config file の指定 profile を読み取る request。"""

    config_file: str = Field(default="", max_length=1024)
    profile: str = Field(default="DEFAULT", max_length=128)

    @field_validator("config_file", "profile")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("config_file")
    @classmethod
    def require_config_file(cls, value: str) -> str:
        """読み取り対象 path は必須。"""
        if not value:
            raise ValueError("OCI config ファイルの path を入力してください。")
        return value

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        """profile 名は INI section として安全な文字列に限定する。"""
        profile = value or "DEFAULT"
        if any(char in profile for char in "[]\r\n"):
            raise ValueError("プロファイル名に [ ] や改行は使用できません。")
        return profile


class OciConfigReadData(BaseModel):
    """OCI config profile から取り込んだ表示用データ。"""

    profile: str
    user: str = ""
    fingerprint: str = ""
    tenancy: str = ""
    region: str = ""
    key_file: str = ""
    applied_fields: list[OciConfigField] = Field(default_factory=list)


class OciSettingsUpdate(BaseModel):
    """OCI SDK config の DEFAULT profile へ保存する認証設定。"""

    user: str = Field(default="", max_length=512)
    fingerprint: str = Field(default="", max_length=128)
    tenancy: str = Field(default="", max_length=512)
    region: str = Field(default="", max_length=128)

    @field_validator("user", "fingerprint", "tenancy", "region")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("user")
    @classmethod
    def validate_user_ocid(cls, value: str) -> str:
        """OCI user OCID は入力時だけ形式を確認する。"""
        if value and not value.startswith("ocid1.user."):
            raise ValueError("ユーザー OCID は ocid1.user. で始めてください。")
        return value

    @field_validator("fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        """API key fingerprint は入力時だけ OCI 形式を確認する。"""
        if value and not re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2})+", value):
            raise ValueError("fingerprint は 16 進数をコロン区切りで入力してください。")
        return value

    @field_validator("tenancy")
    @classmethod
    def validate_tenancy_ocid(cls, value: str) -> str:
        """OCI tenancy OCID は入力時だけ形式を確認する。"""
        if value and not value.startswith("ocid1.tenancy."):
            raise ValueError("テナンシ OCID は ocid1.tenancy. で始めてください。")
        return value

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        """リージョン名は入力時だけ OCI region identifier として確認する。"""
        if value and not re.fullmatch(r"[a-z0-9-]+", value):
            raise ValueError("リージョンは英小文字、数字、ハイフンで入力してください。")
        return value


class OciSettingsData(BaseModel):
    """OCI 認証設定画面の初期表示用 runtime データ。"""

    config_file: str
    profile: str
    user: str = ""
    fingerprint: str = ""
    tenancy: str = ""
    region: str = ""
    key_file: str = ""
    key_file_exists: bool = False
    config_file_exists: bool = False
    config_source: Literal["runtime"]


class OciObjectStorageSettingsUpdate(BaseModel):
    """OCI Object Storage 共通設定の更新 payload。"""

    object_storage_region: str = Field(default="", max_length=128)
    object_storage_namespace: str = Field(default="", max_length=256)

    @field_validator("object_storage_region", "object_storage_namespace")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("object_storage_region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        """Object Storage region は入力時だけ OCI region identifier として確認する。"""
        if value and not re.fullmatch(r"[a-z0-9-]+", value):
            raise ValueError("リージョンは英小文字、数字、ハイフンで入力してください。")
        return value

    @field_validator("object_storage_namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        """Object Storage namespace は入力時だけ危険な文字を拒否する。"""
        if value and not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError(
                "Object Storage の値は英数字、ハイフン、アンダースコア、ドットで入力してください。"
            )
        return value


class OciConfigTestResult(BaseModel):
    """保存済み OCI SDK config の検証結果。"""

    status: OciConfigTestStatus
    profile: str
    config_file: str
    key_file: str
    config_file_exists: bool
    key_file_exists: bool
    missing_fields: list[OciConfigField] = Field(default_factory=list)
    permission_issues: list[str] = Field(default_factory=list)
    oci_directory_mode: str | None = None
    config_file_mode: str | None = None
    key_file_mode: str | None = None
    message: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_type: str | None = None


class OciObjectStorageNamespaceRequest(BaseModel):
    """OCI Object Storage namespace 取得 request。"""

    config_file: str = Field(default="", max_length=1024)
    profile: str = Field(default="DEFAULT", max_length=128)
    region: str = Field(default="", max_length=128)

    @field_validator("config_file", "profile", "region")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("config_file")
    @classmethod
    def require_config_file(cls, value: str) -> str:
        """OCI SDK が読む config path は必須。"""
        if not value:
            raise ValueError("OCI config ファイルの path を入力してください。")
        return value

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        """profile 名は INI section として安全な文字列に限定する。"""
        profile = value or "DEFAULT"
        if any(char in profile for char in "[]\r\n"):
            raise ValueError("プロファイル名に [ ] や改行は使用できません。")
        return profile

    @field_validator("region")
    @classmethod
    def require_region(cls, value: str) -> str:
        """Object Storage namespace 取得に使う region は必須。"""
        if not value:
            raise ValueError("Object Storage リージョンを入力してください。")
        return value


class OciObjectStorageNamespaceData(BaseModel):
    """OCI Object Storage namespace 取得結果。"""

    namespace: str


class OciPrivateKeyUploadData(BaseModel):
    """OCI API 秘密鍵アップロード結果。secret 内容は含めない。"""

    key_file: str
    saved: bool


class DatabaseConnectionTestResult(BaseModel):
    """Oracle 接続検証の結果。"""

    status: DatabaseConnectionTestStatus
    readiness: str
    message: str
    elapsed_ms: int = 0
    troubleshooting: list[str] = Field(default_factory=list)
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_type: str | None = None
