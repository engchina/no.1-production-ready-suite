"""検索（RAG）関連スキーマ。"""

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import JsonValue

# PoweRAG 由来の scalar / 日付 / カテゴリ pre-filter。Oracle 26ai の JSON_VALUE 数値述語・
# TIMESTAMP 範囲・IN 述語へ再マップし、ベクトル/hybrid 検索の候補集合を事前に絞り込む。
SUPPORTED_SEARCH_NUMERIC_RANGE_FILTERS = {
    "page_number_min",
    "page_number_max",
}
SUPPORTED_SEARCH_DATE_RANGE_FILTERS = {
    "uploaded_from",
    "uploaded_to",
    "indexed_from",
    "indexed_to",
}
SUPPORTED_SEARCH_LIST_FILTERS = {
    "content_kinds",
}
SUPPORTED_SCALAR_SEARCH_FILTER_KEYS = (
    SUPPORTED_SEARCH_NUMERIC_RANGE_FILTERS
    | SUPPORTED_SEARCH_DATE_RANGE_FILTERS
    | SUPPORTED_SEARCH_LIST_FILTERS
)

SUPPORTED_SEARCH_FILTER_KEYS = {
    "document_id",
    "knowledge_base_id",
    "chunk_set_id",
    "file_name",
    "category_name",
    "status",
    "content_kind",
    "section_title",
    "section_path",
    "source_acl",
    "document_version",
    *SUPPORTED_SCALAR_SEARCH_FILTER_KEYS,
}
SUPPORTED_SEARCH_STATUS_FILTERS = {
    "UPLOADED",
    "INGESTING",
    "REVIEW",
    "INDEXING",
    "INDEXED",
    "ERROR",
}
SUPPORTED_CONTENT_KIND_FILTERS = {
    "text",
    "list",
    "table",
    "figure",
    "equation",
    "code",
    "email",
    "slide",
    "sheet",
    # 取込機能が生む合成 chunk(項目抽出 / 章節ナビゲーション要約)
    "field",
    "section_summary",
}


class SearchMode(StrEnum):
    """検索モード。Oracle 26ai 側ではベクトル・キーワード・ハイブリッドへ対応する。"""

    HYBRID = "hybrid"
    VECTOR = "vector"
    KEYWORD = "keyword"


class SearchStrategy(StrEnum):
    """検索ルーティング戦略。既存 mode は baseline retrieval mode として維持する。"""

    HYBRID = "hybrid"
    GRAPH_LOCAL = "graph_local"
    GRAPH_GLOBAL = "graph_global"


class SearchRequest(BaseModel):
    """RAG 検索リクエスト。"""

    query: str = Field(..., min_length=1)
    top_k: int = Field(default=20, ge=1, le=100)
    rerank_top_n: int = Field(default=5, ge=1, le=50)
    mode: SearchMode = SearchMode.HYBRID
    strategy: SearchStrategy = SearchStrategy.HYBRID
    filters: dict[str, str] = Field(default_factory=dict)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=200)
    business_view_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "業務ビュー(Business View)ID。business_view_ids が無い旧クライアント向け互換値。"
            "指定時は参照 KB 群を検索対象へ展開し、"
            "業務ビューの query 設定・persona を適用する(request 明示パラメータが最優先)。"
        ),
    )
    business_view_ids: list[str] = Field(
        default_factory=list,
        max_length=50,
        description=(
            "検索対象にする業務ビュー(Business View)ID。複数指定時は参照 KB 群を union し、"
            "query 設定・persona は先頭の業務ビューを代表として適用する。"
        ),
    )

    @field_validator("business_view_id")
    @classmethod
    def validate_business_view_id(cls, value: str | None) -> str | None:
        """空文字は未指定として扱う。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("business_view_ids")
    @classmethod
    def validate_business_view_ids(cls, values: list[str]) -> list[str]:
        """業務ビュー ID を重複排除する。"""
        return normalize_search_id_list(values)

    @field_validator("query")
    @classmethod
    def validate_query(cls, query: str) -> str:
        """空白だけのクエリを拒否し、前後空白を落とす。"""
        return normalize_query_text(query)

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, filters: dict[str, str]) -> dict[str, str]:
        """対応済み filter key のみ許可し、値を正規化する。"""
        return normalize_search_filters(filters)

    @field_validator("knowledge_base_ids")
    @classmethod
    def validate_knowledge_base_ids(cls, values: list[str]) -> list[str]:
        """検索対象のナレッジベース ID を重複排除する。"""
        return normalize_search_id_list(values)

    @model_validator(mode="after")
    def validate_search_options(self) -> Self:
        """rerank 深さとナレッジベース指定の整合性を検証する。"""
        validate_rerank_top_n(self.top_k, self.rerank_top_n)
        filter_knowledge_base_ids = parse_search_id_filter(self.filters.get("knowledge_base_id"))
        if (
            self.knowledge_base_ids
            and filter_knowledge_base_ids
            and self.knowledge_base_ids != filter_knowledge_base_ids
        ):
            raise ValueError(
                "knowledge_base_ids と filters.knowledge_base_id は同じ値を指定してください。"
            )
        resolved_knowledge_base_ids = self.knowledge_base_ids or filter_knowledge_base_ids
        if resolved_knowledge_base_ids:
            self.knowledge_base_ids = resolved_knowledge_base_ids
            self.filters = {
                **self.filters,
                "knowledge_base_id": format_search_id_filter(resolved_knowledge_base_ids),
            }
        if self.business_view_id:
            self.business_view_ids = normalize_search_id_list(
                [self.business_view_id, *self.business_view_ids]
            )
        elif self.business_view_ids:
            self.business_view_id = self.business_view_ids[0]
        return self


class RetrievedChunk(BaseModel):
    """検索でヒットしたチャンク。"""

    document_id: str
    chunk_id: str
    text: str
    score: float
    rerank_score: float | None = None
    file_name: str | None = None
    category_name: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class SearchRetrievalBreakdown(BaseModel):
    """検索候補が各段階で何件残ったかを示す非機密サマリ。"""

    vector_count: int = 0
    keyword_count: int = 0
    overlap_count: int = 0
    fused_count: int = 0
    fusion_dropped_count: int = 0
    rerank_input_count: int = 0
    rerank_kept_count: int = 0
    rerank_dropped_count: int = 0
    evidence_count: int = 0
    citation_count: int = 0
    dropped_count: int = 0


class SearchRetrievalCandidate(BaseModel):
    """検索候補の取得元と採用状態。本文は含めない。"""

    chunk_id: str
    document_id: str
    file_name: str | None = None
    sources: list[str] = Field(default_factory=list)
    vector_rank: int | None = None
    vector_score: float | None = None
    keyword_rank: int | None = None
    keyword_score: float | None = None
    rrf_score: float | None = None
    rerank_rank: int | None = None
    rerank_score: float | None = None
    status: str = "retrieved"
    drop_reason: str | None = None


class SearchDiagnostics(BaseModel):
    """検索実行時の非機密診断情報。"""

    mode: str = ""
    retrieval_strategy: str = "hybrid"
    retrieval_strategy_adapter: str = "hybrid_rrf"
    # 検索方法の有効トグル(query_expansion / gap_stop / corrective_retrieval /
    # business_fit_weighting)。settings トグル OR legacy 強制トグルの合成結果。
    retrieval_toggles: dict[str, bool] = Field(default_factory=dict)
    # クエリ拡張の実行元: llm(OCI Enterprise AI)/ deterministic(同義語)/ off。
    query_expansion_source: str = "off"
    post_retrieval_pipeline: str = "custom"
    generation_profile: str = "grounded_concise"
    guardrail_policy: str = "standard"
    vector_index_profile: str = "balanced"
    graph_profile: str = "off"
    serving_mode: str = "single"
    agentic_profile: str = "off"
    agentic_subquery_count: int = 0
    agentic_hops: int = 0
    route_reason: str = "default_hybrid"
    keyword_terms: list[str] = Field(default_factory=list)
    retrieval_breakdown: SearchRetrievalBreakdown = Field(default_factory=SearchRetrievalBreakdown)
    retrieval_candidates: list[SearchRetrievalCandidate] = Field(default_factory=list)
    memory_plan_id: str | None = None
    graph_hit_count: int = 0
    fallback_reason: str | None = None
    gap_stopped: bool = False
    corrective_retried: bool = False
    crag_confidence_score: float | None = None
    crag_fallback_triggered: bool = False
    hyde_generated: bool = False
    business_context: dict[str, object] = Field(default_factory=dict)
    retrieval_plan: dict[str, object] = Field(default_factory=dict)
    retrieved_context_pack: dict[str, object] = Field(default_factory=dict)
    context_builder: dict[str, object] = Field(default_factory=dict)
    stream_stage_timings: dict[str, float] = Field(default_factory=dict)
    top_k: int = 0
    rerank_top_n: int = 0
    retrieved_count: int = 0
    reranked_count: int = 0
    deduplicated_count: int = 0
    context_diversified_count: int = 0
    context_group_expanded_count: int = 0
    context_expanded_count: int = 0
    context_adaptive_expanded_count: int = 0
    context_dependency_promoted_count: int = 0
    context_compressed_count: int = 0
    context_compression_saved_chars: int = 0
    business_fit_reordered_count: int = 0
    agent_memory_retrieved_count: int = 0
    agent_memory_writeback_count: int = 0
    agent_memory_writeback_status: str = "skipped"
    evidence_count: int = 0
    support_count: int = 0
    structure_count: int = 0
    history_count: int = 0
    resolver_rejected_count: int = 0
    insufficient_context_count: int = 0
    citation_count: int = 0
    context_chars: int = 0
    context_window_chars: int = 0
    rrf_k: int = 0
    query_variant_count: int = 1
    oracle_vector_target_accuracy: int = 0
    filter_keys: list[str] = Field(default_factory=list)
    scalar_filter_keys: list[str] = Field(default_factory=list)
    knowledge_base_count: int = 0
    kb_adapter_config_applied: str | None = None
    business_view_applied: str | None = None
    config_fingerprint: str = ""


class SearchResponse(BaseModel):
    """RAG 検索レスポンス。"""

    answer: str
    citations: list[RetrievedChunk] = Field(default_factory=list)
    trace_id: str
    guardrail_warnings: list[str] = Field(default_factory=list)
    elapsed_ms: float
    diagnostics: SearchDiagnostics = Field(default_factory=SearchDiagnostics)
    # 回答側ガードレールが本文をマスク/差し替えしたか。realtime stream 時に
    # マスク済み本文を再送(置換)するかの判定に使う内部フラグ。
    answer_replaced: bool = False

    @field_validator("guardrail_warnings")
    @classmethod
    def _dedup_warnings(cls, value: list[str]) -> list[str]:
        """クエリ側/回答側で同一文言が重複するため順序保持で重複除去する。"""
        return list(dict.fromkeys(value))


def normalize_search_filters(filters: dict[str, str]) -> dict[str, str]:
    """検索 filter key/value を検証・正規化する。"""
    unsupported = sorted(set(filters) - SUPPORTED_SEARCH_FILTER_KEYS)
    if unsupported:
        raise ValueError(f"未対応の検索フィルターです: {', '.join(unsupported)}")

    normalized: dict[str, str] = {}
    for key, value in filters.items():
        cleaned = value.strip()
        if not cleaned:
            continue
        if key == "status":
            normalized[key] = cleaned.upper()
        elif key == "content_kind":
            normalized[key] = cleaned.casefold()
        elif key == "knowledge_base_id":
            formatted_ids = format_search_id_filter(parse_search_id_filter(cleaned))
            if formatted_ids:
                normalized[key] = formatted_ids
        elif key in SUPPORTED_SEARCH_NUMERIC_RANGE_FILTERS:
            normalized[key] = _normalize_filter_integer(key, cleaned)
        elif key in SUPPORTED_SEARCH_DATE_RANGE_FILTERS:
            normalized[key] = _normalize_filter_date(key, cleaned)
        elif key in SUPPORTED_SEARCH_LIST_FILTERS:
            if formatted_kinds := _normalize_content_kind_list(cleaned):
                normalized[key] = formatted_kinds
        else:
            normalized[key] = cleaned

    if (status := normalized.get("status")) and status not in SUPPORTED_SEARCH_STATUS_FILTERS:
        raise ValueError(f"未対応のファイル状態フィルターです: {status}")
    if (content_kind := normalized.get("content_kind")) and (
        content_kind not in SUPPORTED_CONTENT_KIND_FILTERS
    ):
        raise ValueError(f"未対応の内容種別フィルターです: {content_kind}")
    _validate_filter_range_consistency(normalized)
    return normalized


def _normalize_filter_integer(key: str, value: str) -> str:
    """数値 range filter を整数として検証し、正規化した文字列を返す。"""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"数値フィルターの形式が不正です: {key}={value}") from exc
    if parsed < 0:
        raise ValueError(f"数値フィルターは 0 以上にしてください: {key}={value}")
    return str(parsed)


def _normalize_filter_date(key: str, value: str) -> str:
    """日付 range filter を ISO 8601 として検証し、入力表現を保持して返す。

    Oracle 側で date-only か datetime かを判別して TIMESTAMP へ束ねるため、ここでは
    解析可能性のみ検証し、trim した入力をそのまま保持する。
    """
    candidate = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"日付フィルターの形式が不正です: {key}={value}") from exc
    return value


def _normalize_content_kind_list(value: str) -> str:
    """content_kinds の list membership filter を正規化する。"""
    seen: set[str] = set()
    kinds: list[str] = []
    for part in value.split(","):
        cleaned = part.strip().casefold()
        if not cleaned or cleaned in seen:
            continue
        if cleaned not in SUPPORTED_CONTENT_KIND_FILTERS:
            raise ValueError(f"未対応の内容種別フィルターです: {cleaned}")
        seen.add(cleaned)
        kinds.append(cleaned)
    return ",".join(kinds)


def _validate_filter_range_consistency(filters: dict[str, str]) -> None:
    """min/max・from/to の範囲が逆転していないか検証する。"""
    low = filters.get("page_number_min")
    high = filters.get("page_number_max")
    if low and high and int(low) > int(high):
        raise ValueError("page_number_min は page_number_max 以下にしてください。")
    for from_key, to_key in (
        ("uploaded_from", "uploaded_to"),
        ("indexed_from", "indexed_to"),
    ):
        start = filters.get(from_key)
        end = filters.get(to_key)
        if start and end and _filter_date_sort_key(start) > _filter_date_sort_key(end):
            raise ValueError(f"{from_key} は {to_key} 以前にしてください。")


def _filter_date_sort_key(value: str) -> datetime:
    """検証済み日付文字列を比較用の tz-aware datetime へ変換する。"""
    candidate = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def normalize_search_id_list(values: Sequence[str]) -> list[str]:
    """ID リストの前後空白と重複を取り除く。"""
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def parse_search_id_filter(value: str | None) -> list[str]:
    """カンマ区切り filter 値を ID リストへ戻す。"""
    if value is None:
        return []
    return normalize_search_id_list(value.split(","))


def format_search_id_filter(values: Sequence[str]) -> str:
    """ID リストを既存 filters 経路へ渡すための表現へ変換する。"""
    return ",".join(normalize_search_id_list(values))


def normalize_query_text(query: str) -> str:
    """検索・評価に使う自然言語クエリを正規化する。"""
    cleaned = query.strip()
    if not cleaned:
        raise ValueError("クエリを入力してください。")
    return cleaned


def validate_rerank_top_n(top_k: int, rerank_top_n: int) -> None:
    """rerank_top_n は top_k 以下に制限する。"""
    if rerank_top_n > top_k:
        raise ValueError("rerank_top_n は top_k 以下にしてください。")
