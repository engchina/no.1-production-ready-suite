"""検索（RAG）関連スキーマ。"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

SUPPORTED_SEARCH_FILTER_KEYS = {"document_id", "file_name", "category_name", "status"}
SUPPORTED_SEARCH_STATUS_FILTERS = {"UPLOADED", "ANALYZING", "ANALYZED", "REGISTERED", "ERROR"}


class SearchMode(StrEnum):
    """検索モード。Oracle 26ai 側ではベクトル・キーワード・ハイブリッドへ対応する。"""

    HYBRID = "hybrid"
    VECTOR = "vector"
    KEYWORD = "keyword"


class SearchRequest(BaseModel):
    """RAG 検索リクエスト。"""

    query: str = Field(..., min_length=1)
    top_k: int = Field(default=20, ge=1, le=100)
    rerank_top_n: int = Field(default=5, ge=1, le=50)
    mode: SearchMode = SearchMode.HYBRID
    filters: dict[str, str] = Field(default_factory=dict)

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

    @model_validator(mode="after")
    def validate_rerank_depth(self) -> Self:
        """rerank は retrieval で取得した候補数以内に制限する。"""
        validate_rerank_top_n(self.top_k, self.rerank_top_n)
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
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class SearchDiagnostics(BaseModel):
    """検索実行時の非機密診断情報。"""

    adapter: str = ""
    mode: str = ""
    top_k: int = 0
    rerank_top_n: int = 0
    retrieved_count: int = 0
    reranked_count: int = 0
    citation_count: int = 0
    context_chars: int = 0
    context_window_chars: int = 0
    filter_keys: list[str] = Field(default_factory=list)
    config_fingerprint: str = ""


class SearchResponse(BaseModel):
    """RAG 検索レスポンス。"""

    answer: str
    citations: list[RetrievedChunk] = Field(default_factory=list)
    trace_id: str
    guardrail_warnings: list[str] = Field(default_factory=list)
    elapsed_ms: float
    diagnostics: SearchDiagnostics = Field(default_factory=SearchDiagnostics)


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
        normalized[key] = cleaned.upper() if key == "status" else cleaned

    if (status := normalized.get("status")) and status not in SUPPORTED_SEARCH_STATUS_FILTERS:
        raise ValueError(f"未対応のファイル状態フィルターです: {status}")
    return normalized


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
