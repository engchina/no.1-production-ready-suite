"""ドキュメント関連スキーマ。"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator
from rag_parser_core.source import SourceModality, SourcePreviewKind, SourceProfile

from app.config import ChunkingStrategy, ParserAdapterBackend, PreprocessProfile
from app.schemas.common import JsonValue
from app.schemas.knowledge_base import KnowledgeBaseRef

__all__ = [
    "SourceModality",
    "SourcePreviewKind",
    "SourceProfile",
]


class FileStatus(StrEnum):
    """ファイル処理状態。

    RAG はアップロード後に段階ごとに取込む。PREPROCESS は PREPROCESSED、EXTRACT は
    REVIEW、CHUNK は CHUNKED で停止でき、INDEX だけが検索対象の INDEXED へ進める。
    PREPROCESSED / REVIEW / CHUNKED / 実行中状態は検索対象に含めず、INDEXED のみを
    検索可能とする。
    """

    UPLOADED = "UPLOADED"
    PREPROCESSING = "PREPROCESSING"
    PREPROCESSED = "PREPROCESSED"
    INGESTING = "INGESTING"
    REVIEW = "REVIEW"
    CHUNKING = "CHUNKING"
    CHUNKED = "CHUNKED"
    INDEXING = "INDEXING"
    INDEXED = "INDEXED"
    ERROR = "ERROR"


class BatchUploadFailedItem(BaseModel):
    """batch upload で個別に失敗したファイル。"""

    file_name: str
    status_code: int
    message: str
    source_profile: SourceProfile | None = None


class IngestionJobStatus(StrEnum):
    """取込 job 状態。"""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class IngestionJobPhase(StrEnum):
    """取込 job の処理フェーズ。

    EXTRACT は parse/抽出を行い REVIEW で停止する前段、
    CHUNK は保存済み抽出から chunk を作成して CHUNKED で停止する中段、
    INDEX は承認済み chunk から embedding→索引を行う後段。
    """

    PREPROCESS = "PREPROCESS"
    EXTRACT = "EXTRACT"
    CHUNK = "CHUNK"
    INDEX = "INDEX"


class IngestionJob(BaseModel):
    """キュー投入された取込 job。"""

    id: str
    document_id: str
    status: IngestionJobStatus
    phase: IngestionJobPhase = IngestionJobPhase.PREPROCESS
    parser_profile: str
    quality_warnings: list[str] = Field(default_factory=list)
    # レシピ実験(Phase 3b)ジョブが持つ候補レシピ上書き(rag_* キー)。通常取込では None。
    settings_overrides: dict[str, object] | None = None
    skip_reason: str | None = None
    error_message: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DocumentSummary(BaseModel):
    """一覧表示用のドキュメント要約。"""

    id: str
    file_name: str
    status: FileStatus
    category_name: str | None = None
    content_type: str | None = None
    file_size_bytes: int | None = None
    content_sha256: str | None = None
    duplicate_of_document_id: str | None = None
    uploaded_at: datetime
    indexed_at: datetime | None = None
    knowledge_bases: list[KnowledgeBaseRef] = Field(default_factory=list)
    source_profile: SourceProfile | None = None


class DuplicateDocumentRef(BaseModel):
    """重複判定で参照している既存ドキュメントの表示用摘要。"""

    id: str
    file_name: str
    status: FileStatus
    uploaded_at: datetime
    indexed_at: datetime | None = None


class DocumentPreprocessArtifact(BaseModel):
    """ファイル準備で生成・選択された抽出入力ファイル。"""

    derivation_id: str
    profile: str
    converted: bool = False
    converter_name: str | None = None
    converter_version: str | None = None
    source_content_type: str | None = None
    source_sha256: str | None = None
    object_storage_path: str | None = None
    content_type: str | None = None
    sha256: str | None = None
    file_name: str
    page_map: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class DocumentDetail(DocumentSummary):
    """詳細表示用。VLM/LLM の抽出本文とメタデータを含む。"""

    object_storage_path: str | None = None
    preprocess_artifact: DocumentPreprocessArtifact | None = None
    extraction: dict[str, object] = Field(default_factory=dict)
    error_message: str | None = None
    duplicate_source: DuplicateDocumentRef | None = None


class UploadResult(BaseModel):
    """アップロード結果。"""

    id: str
    file_name: str
    status: FileStatus
    file_size_bytes: int
    content_sha256: str
    duplicate_of_document_id: str | None = None
    knowledge_bases: list[KnowledgeBaseRef] = Field(default_factory=list)
    source_profile: SourceProfile
    ingestion_started: bool = False
    ingestion_job: IngestionJob | None = None


class BatchUploadResult(BaseModel):
    """複数ファイル upload の結果。"""

    items: list[UploadResult] = Field(default_factory=list)
    failed_items: list[BatchUploadFailedItem] = Field(default_factory=list)
    total_count: int = 0
    uploaded_count: int = 0
    failed_count: int = 0
    queued_count: int = 0
    skipped_count: int = 0


class DocumentChunkView(BaseModel):
    """UI で chunk/citation を可視化するための非 embedding chunk view。"""

    document_id: str
    chunk_id: str
    chunk_index: int = 0
    text: str
    page_start: int | None = None
    page_end: int | None = None
    bbox: list[float] | None = None
    section_path: str | None = None
    content_kind: str | None = None
    chunk_group_id: str | None = None
    source_parser: str | None = None
    element_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class DocumentLayerStatusName(StrEnum):
    """文書 chunk_set の派生情報レイヤー状態。"""

    NOT_REQUESTED = "not_requested"
    PLANNED_ONLY = "planned_only"
    MATERIALIZED = "materialized"
    NEEDS_REINGEST = "needs_reingest"
    ERROR = "error"


class DocumentMaterializationLayerStatus(BaseModel):
    """chunk_set に紐づく派生情報レイヤーの現在状態。"""

    layer_id: str | None = None
    requested: bool = False
    status: DocumentLayerStatusName = DocumentLayerStatusName.NOT_REQUESTED
    reason: str | None = None


class DocumentChunkSetLayerStatuses(BaseModel):
    """chunk_set から派生する情報レイヤーの状態一覧。"""

    metadata: DocumentMaterializationLayerStatus = Field(
        default_factory=DocumentMaterializationLayerStatus
    )
    graph: DocumentMaterializationLayerStatus = Field(
        default_factory=DocumentMaterializationLayerStatus
    )
    navigation: DocumentMaterializationLayerStatus = Field(
        default_factory=DocumentMaterializationLayerStatus
    )


class DocumentChunkSet(BaseModel):
    """文書の chunk_set(variant = 1 レシピのチャンク集合)1 件分の状態・件数・所属/配信 KB。"""

    chunk_set_id: str
    extraction_recipe_id: str | None = None
    extraction_status: DocumentLayerStatusName = DocumentLayerStatusName.NOT_REQUESTED
    extraction_reason: str | None = None
    status: str
    chunk_count: int = 0
    vector_count: int = 0
    is_serving: bool = True
    extraction_id: str | None = None
    parser: str | None = None
    preprocess: str | None = None
    knowledge_base_ids: list[str] = Field(default_factory=list)
    serving_knowledge_base_ids: list[str] = Field(default_factory=list)
    layer_statuses: DocumentChunkSetLayerStatuses = Field(
        default_factory=DocumentChunkSetLayerStatuses
    )


class ChunkSetExperimentRequest(BaseModel):
    """別 chunking レシピで候補 chunk_set を試す実験リクエスト(分割軸)。

    指定したフィールドだけ global 既定を上書きする。parser/前処理は変えない(再抽出不要・
    既存抽出を再利用して re-chunk する)ので、上書きできるのは chunking 系のみ。
    """

    chunking_strategy: ChunkingStrategy | None = None
    chunk_size: int | None = Field(default=None, ge=200, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1000)
    chunk_child_size: int | None = Field(default=None, ge=80, le=4000)
    chunk_sentence_window_size: int | None = Field(default=None, ge=1, le=20)
    chunk_min_chars: int | None = Field(default=None, ge=0, le=2000)
    chunk_delimiter: str | None = Field(default=None, min_length=1, max_length=256)

    _FIELD_TO_SETTING = {
        "chunking_strategy": "rag_chunking_strategy",
        "chunk_size": "rag_chunk_size",
        "chunk_overlap": "rag_chunk_overlap",
        "chunk_child_size": "rag_chunk_child_size",
        "chunk_sentence_window_size": "rag_chunk_sentence_window_size",
        "chunk_min_chars": "rag_chunk_min_chars",
        "chunk_delimiter": "rag_chunk_delimiter",
    }

    @model_validator(mode="after")
    def _require_at_least_one_override(self) -> "ChunkSetExperimentRequest":
        if not self.settings_overrides():
            raise ValueError("少なくとも 1 つの chunking 設定を指定してください。")
        return self

    def settings_overrides(self) -> dict[str, object]:
        """非 None の値を Settings の rag_* キーへ写した上書き dict を返す。"""
        return {
            setting: getattr(self, field)
            for field, setting in self._FIELD_TO_SETTING.items()
            if getattr(self, field) is not None
        }


class ParserExtractionExperimentRequest(BaseModel):
    """parser/前処理を変えた候補を試す実験リクエスト(再抽出軸・非同期ジョブ)。

    parser/前処理を変えると抽出結果が変わるため再抽出が必要で、配信中文書を乱さない
    candidate モードの非同期ジョブで materialize する。指定フィールドだけ global 既定を
    上書きする(最低 1 つ必須)。
    """

    preprocess_profile: PreprocessProfile | None = None
    parser_adapter_backend: ParserAdapterBackend | None = None

    _FIELD_TO_SETTING = {
        "preprocess_profile": "rag_preprocess_profile",
        "parser_adapter_backend": "rag_parser_adapter_backend",
    }

    @model_validator(mode="after")
    def _require_at_least_one_override(self) -> "ParserExtractionExperimentRequest":
        if not self.settings_overrides():
            raise ValueError("前処理プロファイルか文書解析 backend のいずれかを指定してください。")
        return self

    def settings_overrides(self) -> dict[str, object]:
        """非 None の値を Settings の rag_* キーへ写した上書き dict を返す。"""
        return {
            setting: getattr(self, field)
            for field, setting in self._FIELD_TO_SETTING.items()
            if getattr(self, field) is not None
        }


class DocumentIngestionConfigData(BaseModel):
    """文書の取込設定スナップショット(3 層モデル: 文書単位の単一レシピ)。"""

    document_id: str
    is_indexed: bool = False
    # global 既定(「検索・回答設定」)から解決した「これから取り込むなら」の有効レシピ。
    effective_preprocess_profile: str
    effective_chunking_strategy: str
    effective_parser_adapter_backend: str
    # 実際に取込時へ刻まれた値(INDEXED 済みのときのみ観測できる)。
    observed_chunking_strategy: str | None = None
    observed_parser_backend: str | None = None
    chunking_drift: bool = False
    parser_drift: bool = False
    config_drift: bool = False


class DocumentExtractionExportFormat(StrEnum):
    """構造化抽出の監査用 export 形式。"""

    JSON = "json"
    MARKDOWN = "markdown"
    HTML = "html"
    CHUNKS = "chunks"


class DocumentExtractionExport(BaseModel):
    """Docling / Marker 風に extraction を非 embedding 形式で確認する view。"""

    document_id: str
    file_name: str
    format: DocumentExtractionExportFormat
    content_type: str
    content: str = ""
    payload: dict[str, object] = Field(default_factory=dict)
    chunks: list[DocumentChunkView] = Field(default_factory=list)
    parser_backend: str | None = None
    parser_profile: str | None = None
    page_count: int = 0
    element_count: int = 0
    table_count: int = 0
    asset_count: int = 0


class IngestionSegment(BaseModel):
    """文書取込 segment の checkpoint/status view。"""

    segment_id: str
    document_id: str
    status: str
    parser_backend: str = "enterprise_ai"
    parser_profile: str = "enterprise_ai_generic"
    page_start: int | None = None
    page_end: int | None = None
    progress_unit: str = "source"
    progress_start: int | None = None
    progress_end: int | None = None
    attempt_count: int = Field(default=0, ge=0)
    artifact_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class DocumentElementTextEdit(BaseModel):
    """REVIEW 中の人手修正: 要素 1 件のテキスト差し替え。"""

    element_id: str = Field(..., max_length=128)
    text: str = Field(default="", max_length=200000)


class DocumentTableCellTextEdit(BaseModel):
    """REVIEW 中の人手修正: 表セル 1 件のテキスト差し替え(table_id + row + col で同定)。"""

    table_id: str = Field(..., max_length=128)
    row: int = Field(..., ge=0)
    col: int = Field(..., ge=0)
    text: str = Field(default="", max_length=200000)


class DocumentApproveRequest(BaseModel):
    """承認(index)リクエスト。任意で REVIEW 中のテキスト修正を伴う。

    bbox・構造・section などはサーバ側の抽出結果を保持し、テキストのみ差し替える。
    """

    raw_text: str | None = Field(default=None, max_length=2000000)
    element_edits: list[DocumentElementTextEdit] = Field(default_factory=list, max_length=5000)
    table_cell_edits: list[DocumentTableCellTextEdit] = Field(
        default_factory=list, max_length=20000
    )


class DocumentDeleteResult(BaseModel):
    """ドキュメント削除結果。"""

    id: str
    file_name: str
    object_storage_path: str | None = None
    object_deleted: bool = False
    artifact_deleted_count: int = 0
    artifact_delete_failed_count: int = 0


class DocumentStats(BaseModel):
    """ドキュメント状態別の集計。"""

    total: int
    by_status: dict[FileStatus, int]
