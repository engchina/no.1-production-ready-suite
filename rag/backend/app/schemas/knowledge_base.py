"""ナレッジベース関連スキーマ。"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.rag.kb_adapter_config import KnowledgeBaseAdapterConfig
from app.schemas.search import SearchMode


class KnowledgeBaseStatus(StrEnum):
    """ナレッジベースの運用状態。"""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class KnowledgeBaseRef(BaseModel):
    """他スキーマへ埋め込む軽量なナレッジベース参照。"""

    id: str
    name: str


class KnowledgeBaseSummary(KnowledgeBaseRef):
    """一覧表示用のナレッジベース要約。"""

    description: str | None = None
    status: KnowledgeBaseStatus
    default_search_mode: SearchMode = SearchMode.HYBRID
    document_count: int = 0
    indexed_document_count: int = 0
    error_document_count: int = 0
    searchable_chunk_count: int = 0
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class KnowledgeBaseDetail(KnowledgeBaseSummary):
    """詳細表示用のナレッジベース情報。"""

    retrieval_config: dict[str, object] = Field(default_factory=dict)
    adapter_config: KnowledgeBaseAdapterConfig = Field(
        default_factory=KnowledgeBaseAdapterConfig,
        description=(
            "KB 単位の構築設定。query は legacy 互換として読めるが検索・回答 runtime へは"
            "反映しない。None フィールドはグローバル設定を継承する。"
        ),
    )
    effective_adapter_config: KnowledgeBaseAdapterConfig | None = Field(
        default=None,
        description=(
            "KB 構築上書きをグローバル既定で埋めた解決済み設定(表示専用)。継承フィールドに"
            "「実際に効く値」を出すために使う。query は常に空。materialize には使わない。"
        ),
    )
    legacy_query_config_ignored: bool = Field(
        default=False,
        description=(
            "既存 retrieval_config に legacy query 設定が残っており、現在は無視されている。"
        ),
    )


class KnowledgeBaseCreateRequest(BaseModel):
    """ナレッジベース作成 request。"""

    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    default_search_mode: SearchMode = SearchMode.HYBRID
    retrieval_config: dict[str, object] = Field(default_factory=dict)
    adapter_config: KnowledgeBaseAdapterConfig | None = Field(
        default=None,
        description="KB 単位の構築設定。未指定ならグローバル設定を全継承する。",
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """前後空白を取り、空名を拒否する。"""
        return _required_clean_text(value, "名前を入力してください。")

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        """空説明は未指定として扱う。"""
        return _optional_clean_text(value)


class KnowledgeBaseUpdateRequest(BaseModel):
    """ナレッジベース更新 request。"""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    default_search_mode: SearchMode | None = None
    retrieval_config: dict[str, object] | None = None
    adapter_config: KnowledgeBaseAdapterConfig | None = Field(
        default=None,
        description="KB 単位の構築設定。指定時は既存設定を置換する。",
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        """更新時も空名は拒否する。"""
        if value is None:
            return None
        return _required_clean_text(value, "名前を入力してください。")

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        """空説明は未指定として扱う。"""
        return _optional_clean_text(value)


class KnowledgeBaseDocumentAssignmentRequest(BaseModel):
    """既存文書をナレッジベースへ追加する request。"""

    document_ids: list[str] = Field(..., min_length=1, max_length=200)

    @field_validator("document_ids")
    @classmethod
    def normalize_document_ids(cls, values: list[str]) -> list[str]:
        """文書 ID の前後空白と重複を取り除く。"""
        return _unique_clean_ids(values)


class DocumentKnowledgeBaseReplaceRequest(BaseModel):
    """文書の所属ナレッジベースを置換する request。"""

    knowledge_base_ids: list[str] = Field(..., min_length=1, max_length=200)

    @field_validator("knowledge_base_ids")
    @classmethod
    def normalize_knowledge_base_ids(cls, values: list[str]) -> list[str]:
        """ナレッジベース ID の前後空白と重複を取り除く。"""
        return _unique_clean_ids(values)


def _required_clean_text(value: str, message: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(message)
    return cleaned


def _optional_clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _unique_clean_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned_values: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        cleaned_values.append(cleaned)
    if not cleaned_values:
        raise ValueError("ID を 1 件以上指定してください。")
    return cleaned_values


class KnowledgeBaseGraphNode(BaseModel):
    """関係情報グラフのノード(KG entity)。"""

    id: str
    name: str
    type: str | None = None
    confidence: float = 1.0


class KnowledgeBaseGraphEdge(BaseModel):
    """関係情報グラフのエッジ(KG relationship)。"""

    id: str
    source: str
    target: str
    type: str | None = None
    confidence: float = 1.0


class KnowledgeBaseGraphData(BaseModel):
    """KB の関係情報(GraphRAG)可視化用 subgraph。"""

    status: Literal["ok", "empty"] = "empty"
    nodes: list[KnowledgeBaseGraphNode] = Field(default_factory=list)
    edges: list[KnowledgeBaseGraphEdge] = Field(default_factory=list)
    truncated: bool = False
