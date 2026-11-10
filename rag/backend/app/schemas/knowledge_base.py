"""ナレッジベース関連スキーマ。"""

from enum import StrEnum
from typing import Literal

from pr_backend_core.timezones import UtcDatetime
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.rag.kb_adapter_config import KnowledgeBaseAdapterConfig
from app.schemas.search import SearchMode

DEFAULT_KNOWLEDGE_BASE_NAME = "DEFAULT"
# DEFAULT は改名できないため、説明は既定の文言を補う（作成・読み込み時と migration。#521）。
DEFAULT_KNOWLEDGE_BASE_DESCRIPTION = (
    "ナレッジベースを指定せずにアップロードした文書が入る、既定のナレッジベースです。"
)
DESCRIPTION_REQUIRED_MESSAGE = "説明を入力してください。"


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
    searchable_chunk_count: int | None = Field(
        default=None,
        description="検索対象Chunk数。一覧では集計せずnull。正確な値は詳細で取得する。",
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime
    archived_at: UtcDatetime | None = None


class KnowledgeBaseDetail(KnowledgeBaseSummary):
    """詳細表示用のナレッジベース情報。"""

    searchable_chunk_count: int = 0
    retrieval_config: dict[str, object] = Field(default_factory=dict)
    structure_index_enabled: bool | None = None
    structure_index_global_enabled: bool = False
    adapter_config: KnowledgeBaseAdapterConfig = Field(
        default_factory=KnowledgeBaseAdapterConfig,
        description=(
            "KB 単位の legacy 構築設定(互換のための保存値)。3 層モデルでは文書レシピが"
            "global から解決するため、取込・検索・回答 runtime へは反映しない。"
        ),
    )


class KnowledgeBaseCreateRequest(BaseModel):
    """ナレッジベース作成 request。"""

    name: str = Field(..., min_length=1, max_length=256)
    description: str = Field(..., max_length=2000)
    default_search_mode: SearchMode = SearchMode.HYBRID
    retrieval_config: dict[str, object] = Field(default_factory=dict)
    adapter_config: object | None = Field(
        default=None,
        description=(
            "廃止(#302)。指定すると 422。KB は所属だけを持ち、構築設定は文書レシピ、"
            "検索・回答設定は検索・回答プロファイルで指定する。"
        ),
        json_schema_extra={"deprecated": True},
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """前後空白を取り、空名を拒否する。"""
        return _knowledge_base_name(value)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        """前後空白を取り、空の説明を拒否する（#521）。"""
        return _required_clean_text(value, DESCRIPTION_REQUIRED_MESSAGE)


class KnowledgeBaseUpdateRequest(BaseModel):
    """ナレッジベース更新 request。"""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    default_search_mode: SearchMode | None = None
    retrieval_config: dict[str, object] | None = None
    adapter_config: object | None = Field(
        default=None,
        description=(
            "廃止(#302)。指定すると 422。KB は所属だけを持ち、構築設定は文書レシピ、"
            "検索・回答設定は検索・回答プロファイルで指定する。"
        ),
        json_schema_extra={"deprecated": True},
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        """更新時も空名は拒否する。"""
        if value is None:
            return None
        return _knowledge_base_name(value)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str:
        """指定したときは空・空白だけ・null を拒否する。省略すれば変更しない（#521）。"""
        return _required_clean_text(value or "", DESCRIPTION_REQUIRED_MESSAGE)


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


def _knowledge_base_name(value: str) -> str:
    cleaned = _required_clean_text(value, "名前を入力してください。")
    if cleaned.casefold() == DEFAULT_KNOWLEDGE_BASE_NAME.casefold():
        raise ValueError("DEFAULT は予約名のため使用できません。")
    return cleaned


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


class StructureGraphDocument(BaseModel):
    """文書構造グラフの総覧の文書（文書の entity）。"""

    id: str
    document_id: str
    chunk_set_id: str
    name: str
    has_sections: bool = False
    outgoing_count: int = 0
    incoming_count: int = 0


class StructureGraphDocumentEdge(BaseModel):
    """文書の組の間の、解決済みの参照の件数。"""

    id: str
    source: str
    target: str
    count: int


class StructureGraphOverview(BaseModel):
    """KB の全文書と、文書の間の参照。"""

    status: Literal["ok", "empty"] = "empty"
    documents: list[StructureGraphDocument] = Field(default_factory=list)
    edges: list[StructureGraphDocumentEdge] = Field(default_factory=list)


class StructureGraphTreeNode(BaseModel):
    """章節の木の 1 節点。``virtual`` はパスの途中の段で、構造索引に実体が無い。"""

    id: str
    name: str
    label: str
    type: str
    document_id: str
    document_name: str
    chunk_set_id: str
    parent_id: str | None = None
    virtual: bool = False
    child_count: int = 0


class StructureGraphTreePage(BaseModel):
    items: list[StructureGraphTreeNode] = Field(default_factory=list)
    total: int = 0
    offset: int = 0


class StructureGraphLocatedStep(BaseModel):
    id: str
    index: int


class StructureGraphLocation(BaseModel):
    """根から節点までの段と、各段の中の位置。"""

    path: list[StructureGraphLocatedStep] = Field(default_factory=list)


class StructureGraphReference(BaseModel):
    """章節の組の参照。祖先の列（根 → 節点）で、見えている段へ画面がまとめる。"""

    source_set: str
    target_set: str
    source_chain: list[str] = Field(default_factory=list)
    target_chain: list[str] = Field(default_factory=list)
    count: int = 1


class StructureGraphReferences(BaseModel):
    items: list[StructureGraphReference] = Field(default_factory=list)


class StructureGraphSearchItem(BaseModel):
    id: str
    name: str
    type: str
    document_id: str
    document_name: str
    chunk_set_id: str


class StructureGraphSearchPage(BaseModel):
    items: list[StructureGraphSearchItem] = Field(default_factory=list)
    total: int = 0
    offset: int = 0


class KnowledgeBaseStructureIndexUpdate(BaseModel):
    """Noneはレシピ/globalの構築設定を継承する。"""

    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None


class KnowledgeBaseStructureIndexData(KnowledgeBaseStructureIndexUpdate):
    global_enabled: bool


class StructureIndexStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["missing", "rebuild", "retry", "continue"] = "missing"
    # 指定した文書だけを対象にする（少数の文書での試験の補建。#4543）。省略は KB の全文書。
    document_ids: list[str] | None = Field(None, min_length=1, max_length=200)

    @field_validator("document_ids")
    @classmethod
    def normalize_document_ids(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else _unique_clean_ids(values)


class StructureGraphChunk(BaseModel):
    chunk_id: str
    chunk_index: int
    page_number: int | None = None


class StructureGraphChunkPage(BaseModel):
    items: list[StructureGraphChunk] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
