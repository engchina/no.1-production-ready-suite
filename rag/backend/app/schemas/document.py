"""ドキュメント関連スキーマ。"""

from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from rag_parser_core.source import SourceModality, SourcePreviewKind, SourceProfile

from app.config import (
    CHUNK_OVERLAP_MAX_CHARS,
    CHUNK_SIZE_MAX_CHARS,
    CHUNK_SIZE_MIN_CHARS,
    DOCRAG_CHILD_TARGET_CHARS_MAX,
    DOCRAG_CHILD_TARGET_CHARS_MIN,
    DOCRAG_PARENT_MAX_CHILDREN_MAX,
    DOCRAG_PARENT_MAX_CHILDREN_MIN,
    DOCRAG_PARENT_MAX_PAGES_MAX,
    DOCRAG_PARENT_MAX_PAGES_MIN,
    DOCRAG_PARENT_TARGET_CHARS_MAX,
    DOCRAG_PARENT_TARGET_CHARS_MIN,
    DOCRAG_TABLE_CHILD_TARGET_CHARS_MAX,
    DOCRAG_TABLE_CHILD_TARGET_CHARS_MIN,
    ChunkingStrategy,
)
from app.rag.kb_adapter_config import KnowledgeBaseIngestionConfig
from app.schemas.classification import (
    CLASSIFICATION_CATEGORY_KEYS,
    category_label,
    normalize_category_value,
)
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
    # 一覧で「どのファイルか」を示すための文書のファイル名（rag_documents.file_name）。
    # 一覧・取得の SELECT が文書表と JOIN して埋める。作成直後の応答などでは None（#306）。
    document_file_name: str | None = None
    recipe_id: str | None = None
    recipe_revision: int | None = Field(default=None, ge=1)
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


class IngestionJobLease(BaseModel):
    """取込 job の状態と lease の持ち主(worker が自分の実行かどうかを確かめる。#359)。

    API の応答には出さない(``lease_owner`` は worker の host・pid を含むため)。
    """

    status: IngestionJobStatus
    lease_owner: str | None = None


class DocumentClassification(BaseModel):
    """文書の分類と有効期間(rag_poc の document.classification / effective_from / effective_to)。

    文書のメタデータで、レシピを切り替えても変わらない。ACL に使う category_name とは別に持つ。
    有効期間の終了日は排他的(effective_to の当日は期間外)。
    分類の値は `normalize_category_value` で表記をそろえて保存する(#547)。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    large_category: str | None = Field(default=None, max_length=200)
    middle_category: str | None = Field(default=None, max_length=200)
    small_category: str | None = Field(default=None, max_length=200)
    effective_from: date | None = None
    effective_to: date | None = None

    @field_validator(*CLASSIFICATION_CATEGORY_KEYS, mode="before")
    @classmethod
    def _normalize_category(cls, value: object) -> object:
        return normalize_category_value(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _normalize(self) -> "DocumentClassification":
        if self.effective_from and self.effective_to and self.effective_from >= self.effective_to:
            raise ValueError("有効期間の終了日は開始日より後の日付にしてください。")
        return self

    def is_empty(self) -> bool:
        return not any(self.model_dump().values())


class DocumentClassificationOptions(BaseModel):
    """分類の入力の候補(保存済みの文書の分類の値。#547)。"""

    large_categories: list[str] = Field(default_factory=list)
    middle_categories: list[str] = Field(default_factory=list)
    small_categories: list[str] = Field(default_factory=list)

    @classmethod
    def from_values(
        cls, values: Iterable[tuple[str, object]], *, limit: int = 500
    ) -> "DocumentClassificationOptions":
        """(分類の項目, 保存値) の組から候補を作る。

        表記をそろえて重複を除き、番号の接頭辞だけが違う値(`10_業務A` と `業務A`)は、接頭辞の
        付いた値だけを出す(新しく入力する値を、番号で並ぶ表記へ寄せる)。項目ごとに `limit` 件まで。
        """
        by_label: dict[tuple[str, str], set[str]] = {}
        for key, raw in values:
            value = normalize_category_value(raw)
            if key not in CLASSIFICATION_CATEGORY_KEYS or value is None:
                continue
            by_label.setdefault((key, category_label(value)), set()).add(value)
        options: dict[str, set[str]] = {key: set() for key in CLASSIFICATION_CATEGORY_KEYS}
        for (key, label), group in by_label.items():
            prefixed = {value for value in group if value != label}
            options[key].update(prefixed or group)
        return cls(
            large_categories=sorted(options["large_category"])[:limit],
            middle_categories=sorted(options["middle_category"])[:limit],
            small_categories=sorted(options["small_category"])[:limit],
        )


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
    # 検索対象(active)のレシピの派生情報レイヤーに、作り直しが必要なものがあるか(#550)。
    # 一覧の API だけが埋める(詳細はレイヤーごとの ``rebuild_required`` を見る)。
    layers_rebuild_required: bool = False


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


class DocumentPreviewPage(BaseModel):
    """プレビューのページ画像 1 ページの表示寸法(pt。ページの /Rotate を反映した向き)。"""

    page_number: int
    width: float
    height: float


class DocumentPreviewPages(BaseModel):
    """プレビューのページ画像の一覧(PDF のページごとに画像を描き、bbox の強調を重ねる。#349)。"""

    page_count: int
    pages: list[DocumentPreviewPage] = Field(default_factory=list)


class DocumentDetail(DocumentSummary):
    """詳細表示用。VLM/LLM の抽出本文とメタデータを含む。"""

    object_storage_path: str | None = None
    preprocess_artifact: DocumentPreprocessArtifact | None = None
    extraction: dict[str, object] = Field(default_factory=dict)
    error_message: str | None = None
    duplicate_source: DuplicateDocumentRef | None = None
    classification: DocumentClassification | None = None


class ParserSourceNotice(BaseModel):
    """選んだ文書解析エンジン(既定は Docling)で扱えない形式の案内(取込を始める前に止める。#286)。"""

    code: str = Field(description="parser_source_unsupported")
    backend: str = Field(description="取込で使う文書解析エンジン(既定レシピ)。")
    file_format: str = Field(description="原本の拡張子(無ければ content type)。")
    suggested_backend: str | None = Field(
        default=None, description="この形式を扱える文書解析エンジン(例: unstructured)。"
    )
    message: str = Field(description="利用者向けの理由と対処。")


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
    parser_notice: ParserSourceNotice | None = Field(
        default=None,
        description="既定の文書解析エンジンで扱えない形式のとき、取込を始める前の案内(#286)。",
    )


class BatchUploadResult(BaseModel):
    """複数ファイル upload の結果。"""

    items: list[UploadResult] = Field(default_factory=list)
    failed_items: list[BatchUploadFailedItem] = Field(default_factory=list)
    total_count: int = 0
    uploaded_count: int = 0
    failed_count: int = 0


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
    # 作ったときの入力(項目の定義など)が今の設定と違い、作り直しが必要か(#550)。status とは
    # 別の印で、今の設定との比較で決まる(保存しない)。指紋の無い古い行は False(不明)。
    rebuild_required: bool = False
    # 変わった入力の名前(``app.rag.layer_fingerprint`` の *_INPUT)。画面が表示名にする。
    rebuild_inputs: list[str] = Field(default_factory=list)


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
    created_at: datetime | None = None
    extraction_id: str | None = None
    parser: str | None = None
    preprocess: str | None = None
    knowledge_base_ids: list[str] = Field(default_factory=list)
    serving_knowledge_base_ids: list[str] = Field(default_factory=list)
    layer_statuses: DocumentChunkSetLayerStatuses = Field(
        default_factory=DocumentChunkSetLayerStatuses
    )


class DocumentChunkPreviewRequest(BaseModel):
    """保存しない分割プレビュー用の一時 chunking 上書き。"""

    chunking_strategy: ChunkingStrategy | None = None
    chunk_size: int | None = Field(
        default=None,
        ge=CHUNK_SIZE_MIN_CHARS,
        le=CHUNK_SIZE_MAX_CHARS,
    )
    chunk_overlap: int | None = Field(default=None, ge=0, le=CHUNK_OVERLAP_MAX_CHARS)
    chunk_min_chars: int | None = Field(default=None, ge=0, le=2000)
    chunk_delimiter: str | None = Field(default=None, min_length=1, max_length=256)
    docrag_child_target_chars: int | None = Field(
        default=None, ge=DOCRAG_CHILD_TARGET_CHARS_MIN, le=DOCRAG_CHILD_TARGET_CHARS_MAX
    )
    docrag_table_child_target_chars: int | None = Field(
        default=None,
        ge=DOCRAG_TABLE_CHILD_TARGET_CHARS_MIN,
        le=DOCRAG_TABLE_CHILD_TARGET_CHARS_MAX,
    )
    docrag_parent_target_chars: int | None = Field(
        default=None, ge=DOCRAG_PARENT_TARGET_CHARS_MIN, le=DOCRAG_PARENT_TARGET_CHARS_MAX
    )
    docrag_parent_max_pages: int | None = Field(
        default=None, ge=DOCRAG_PARENT_MAX_PAGES_MIN, le=DOCRAG_PARENT_MAX_PAGES_MAX
    )
    docrag_parent_max_children: int | None = Field(
        default=None, ge=DOCRAG_PARENT_MAX_CHILDREN_MIN, le=DOCRAG_PARENT_MAX_CHILDREN_MAX
    )
    chunk_context_header_enabled: bool | None = None

    _FIELD_TO_SETTING = {
        "chunking_strategy": "rag_chunking_strategy",
        "chunk_size": "rag_chunk_size",
        "chunk_overlap": "rag_chunk_overlap",
        "chunk_min_chars": "rag_chunk_min_chars",
        "chunk_delimiter": "rag_chunk_delimiter",
        "docrag_child_target_chars": "rag_docrag_child_target_chars",
        "docrag_table_child_target_chars": "rag_docrag_table_child_target_chars",
        "docrag_parent_target_chars": "rag_docrag_parent_target_chars",
        "docrag_parent_max_pages": "rag_docrag_parent_max_pages",
        "docrag_parent_max_children": "rag_docrag_parent_max_children",
        "chunk_context_header_enabled": "rag_chunk_context_header_enabled",
    }

    def settings_overrides(self) -> dict[str, object]:
        return {
            setting: getattr(self, field)
            for field, setting in self._FIELD_TO_SETTING.items()
            if getattr(self, field) is not None
        }


class DocumentChunkPreviewStats(BaseModel):
    """分割結果の軽量な文字数統計。"""

    chunk_count: int = Field(ge=0)
    min_chars: int = Field(ge=0)
    average_chars: float = Field(ge=0)
    max_chars: int = Field(ge=0)
    overflow_count: int = Field(ge=0)
    embedding_overflow_count: int = Field(ge=0)


class DocumentChunkPreviewResponse(BaseModel):
    """DB を変更しないレシピ別 chunk preview。"""

    chunks: list[DocumentChunkView] = Field(default_factory=list)
    stats: DocumentChunkPreviewStats
    warnings: list[str] = Field(default_factory=list)


class DocumentProcessingConfig(KnowledgeBaseIngestionConfig):
    """文書単位の処理レシピ上書き。None は global 既定を継承する。"""

    model_config = ConfigDict(extra="forbid")
    chunk_context_header_enabled: bool | None = None


class DocumentRecipeStepStatus(StrEnum):
    """文書レシピの各工程の表示状態。"""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DocumentRecipeStep(BaseModel):
    """文書レシピ 1 工程の状態。"""

    phase: IngestionJobPhase
    status: DocumentRecipeStepStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None


class DocumentRecipeView(BaseModel):
    """文書に属する 1〜3 件の処理レシピ。"""

    recipe_id: str
    document_id: str
    slot_no: int = Field(ge=1, le=3)
    status: FileStatus
    failed_phase: IngestionJobPhase | None = None
    processing_config: DocumentProcessingConfig = Field(default_factory=DocumentProcessingConfig)
    effective_processing_config: DocumentProcessingConfig = Field(
        default_factory=DocumentProcessingConfig
    )
    preprocess_artifact: DocumentPreprocessArtifact | None = None
    active_extraction_recipe_id: str | None = None
    active_chunk_set_id: str | None = None
    chunk_count: int = Field(default=0, ge=0)
    vector_count: int = Field(default=0, ge=0)
    config_revision: int = Field(default=1, ge=1)
    materialized_revision: int | None = Field(default=None, ge=1)
    searchable: bool = False
    needs_reprocessing: bool = False
    error_message: str | None = None
    steps: list[DocumentRecipeStep] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DocumentRecipeCreateRequest(BaseModel):
    """文書レシピ追加。指定があれば既存レシピの明示設定を複製する。"""

    copy_from_recipe_id: str | None = Field(default=None, max_length=64)


class DocumentRecipeDeleteResult(BaseModel):
    """文書レシピ削除結果。"""

    recipe_id: str
    document_id: str
    removed_chunk_set_count: int = Field(default=0, ge=0)


class DocumentExtractionExportFormat(StrEnum):
    """構造化抽出の監査用 export 形式。"""

    JSON = "json"
    MARKDOWN = "markdown"
    HTML = "html"
    CHUNKS = "chunks"


class DocumentExtractionExport(BaseModel):
    """Docling 風に extraction を非 embedding 形式で確認する view。"""

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
    recipe_id: str | None = None
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


class DocumentReviewEditsRequest(BaseModel):
    """REVIEW 中に保存する構造化要素・表セルのテキスト修正。"""

    element_edits: list[DocumentElementTextEdit] = Field(default_factory=list, max_length=5000)
    table_cell_edits: list[DocumentTableCellTextEdit] = Field(
        default_factory=list, max_length=20000
    )


class DocumentApproveRequest(DocumentReviewEditsRequest):
    """承認リクエスト。raw_text は旧クライアントとの後方互換用。"""

    raw_text: str | None = Field(default=None, max_length=2000000)


class DocumentDeleteResult(BaseModel):
    """ドキュメント削除結果。"""

    id: str
    file_name: str
    object_storage_path: str | None = None
    object_deleted: bool = False
    artifact_deleted_count: int = 0
    artifact_delete_failed_count: int = 0


class DocumentDeleteImpact(BaseModel):
    """削除の前に確認する影響（#303）。

    重複文書は chunk を持たず、正本の chunk を所属 KB の検索対象として使う。正本を消すと
    その KB の検索対象から内容が消えるため、自前の索引を持たない重複文書の件数と所属 KB を返す。
    """

    document_id: str
    duplicate_count: int = 0
    knowledge_bases: list[KnowledgeBaseRef] = Field(default_factory=list)
