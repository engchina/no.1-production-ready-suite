"""KB 詳細の Wiki の図（文書 → 章節 → 原文単位と、依存・文書をまたぐ関連。#5502）。"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.rag.wiki_models import WikiRelation
from app.schemas.knowledge_base import KnowledgeBaseRef
from app.schemas.search import RetrievedChunk

WikiGraphScope = Literal["same_document", "cross_document"]


class WikiGraphDocument(BaseModel):
    """案内のある文書。関連の数は文書をまたぐ関連（起点 = outgoing / 終点 = incoming）。"""

    id: str
    document_id: str
    name: str


class WikiGraphDocumentCount(BaseModel):
    total: int


class WikiGraphOverview(BaseModel):
    has_more: bool = False
    status: Literal["ok", "empty"] = "empty"
    documents: list[WikiGraphDocument] = Field(default_factory=list)


class WikiGraphEdge(BaseModel):
    """単位から出る線。関係は終点が起点に対して持つ役割。"""

    target: str
    relation: WikiRelation
    scope: WikiGraphScope


class WikiGraphTreeNode(BaseModel):
    """木の 1 節点。章節は ``section_path`` の階層、単位は原文単位（ID は単位 ID）。"""

    id: str
    kind: Literal["section", "unit"]
    label: str
    path: list[str] = Field(default_factory=list)
    document_id: str
    document_name: str
    parent_id: str | None = None
    child_count: int = 0
    unit_count: int = 0
    # 文書をまたぐ関連の数（章節は配下の単位の合計）。
    outgoing_links: int = 0
    incoming_links: int = 0
    outgoing: list[WikiGraphEdge] = Field(default_factory=list)
    # 単位の案内の要約（AI が作った検索用の文）。章節は空。
    summary: str = ""
    excerpt: str = ""
    page: int | str | None = None
    line: int | str | None = None


class WikiGraphTreePage(BaseModel):
    items: list[WikiGraphTreeNode] = Field(default_factory=list)
    total: int = 0
    offset: int = 0


class WikiGraphLocatedStep(BaseModel):
    id: str
    index: int


class WikiGraphLocation(BaseModel):
    """文書の直下から節点までの段と、各段の中の位置（最後が節点）。"""

    path: list[WikiGraphLocatedStep] = Field(default_factory=list)


class WikiGraphSearchItem(BaseModel):
    id: str
    label: str
    document_id: str
    document_name: str


class WikiSearchHit(WikiGraphSearchItem):
    knowledge_base_ids: list[str] = Field(default_factory=list)
    vector_rank: int | None = None
    keyword_rank: int | None = None


class WikiSearchHits(BaseModel):
    items: list[WikiSearchHit]
    total: int
    offset: int


class WikiSearchScope(BaseModel):
    knowledge_bases: list[KnowledgeBaseRef]
    # 選んでチャットへ渡せる原文単位の数の上限（`RAG_WIKI_SELECTION_MAX_UNITS`。#5981）。
    selection_limit: int


class WikiGraphSearchPage(BaseModel):
    items: list[WikiGraphSearchItem] = Field(default_factory=list)
    total: int = 0
    offset: int = 0


class WikiGraphUnitRef(BaseModel):
    id: str
    label: str
    path: list[str] = Field(default_factory=list)
    document_id: str
    document_name: str


class WikiGraphRelation(BaseModel):
    direction: Literal["outgoing", "incoming"]
    relation: WikiRelation
    scope: WikiGraphScope
    # 開ける相手の単位。別の文書への参照で、参照先がこの KB に無ければ None（開けない。#5784）。
    unit: WikiGraphUnitRef | None = None
    # 別の文書への参照（出る向き）の、原文の表記（文書名と章節）。
    target_name: str | None = None
    target_section: str | None = None


class WikiSourceRef(BaseModel):
    unit_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class WikiGraphRelations(BaseModel):
    items: list[WikiGraphRelation]
    has_more: bool


class WikiDirectedGroup(BaseModel):
    relation: WikiRelation
    direction: Literal["outgoing", "incoming"]
    count: int
    neighbor_count: int


class WikiNeighborConnection(BaseModel):
    source_id: str
    target_id: str
    relation: WikiRelation


class WikiRelationGroups(WikiGraphRelations):
    connections: list[WikiNeighborConnection] = Field(default_factory=list)
    groups: list[WikiDirectedGroup] = Field(default_factory=list)
    preview: list[WikiGraphRelation] = Field(default_factory=list)
    counts: dict[WikiRelation, int] = Field(default_factory=dict)
    total: int = 0


class WikiGraphSourceChunk(BaseModel):
    chunk_id: str
    chunk_index: int


class WikiGraphUnitDetail(BaseModel):
    """単位の案内（AI が作った検索用の文。回答の根拠ではない）と、原文の位置と関係。"""

    id: str
    label: str
    path: list[str] = Field(default_factory=list)
    document_id: str
    document_name: str
    context: str
    possible_questions: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    source_chunks: list[WikiGraphSourceChunk] = Field(default_factory=list)
    relations: list[WikiGraphRelation] = Field(default_factory=list)


class WikiOriginal(WikiGraphUnitRef):
    parent_id: str | None = None
    source: WikiSourceRef
    chunks: list[RetrievedChunk]
    context: str
    possible_questions: list[str]
    unresolved: list[str]


class WikiDocumentGraphNode(WikiGraphDocument):
    """図の見本の文書。``reference_count`` は別の文書との参照の件数（出る・入るの合計）。"""

    reference_count: int = 0


class WikiDocumentLink(BaseModel):
    """文書の間の参照（向き・種類ごとの件数）。"""

    source_id: str
    target_id: str
    relation: WikiRelation
    count: int


class WikiDocumentGraph(BaseModel):
    documents: list[WikiDocumentGraphNode] = Field(default_factory=list)
    links: list[WikiDocumentLink] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    has_more: bool = False


class WikiUnitConnection(WikiNeighborConnection):
    scope: WikiGraphScope


class WikiUnitConnections(BaseModel):
    connections: list[WikiUnitConnection] = Field(default_factory=list)
