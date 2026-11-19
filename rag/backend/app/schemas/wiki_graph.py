"""KB 詳細の Wiki の図（文書 → 章節 → 原文単位と、依存・文書をまたぐ関連。#5502）。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.rag.wiki_models import WikiRelation

WikiGraphScope = Literal["same_document", "cross_document"]


class WikiGraphDocument(BaseModel):
    """案内のある文書。関連の数は文書をまたぐ関連（起点 = outgoing / 終点 = incoming）。"""

    id: str
    document_id: str
    name: str
    unit_count: int = 0
    outgoing_links: int = 0
    incoming_links: int = 0


class WikiGraphOverview(BaseModel):
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
    unit: WikiGraphUnitRef
    # 文書をまたぐ関連の判定の理由（AI が書いた短い説明）。
    reason: str | None = None


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
