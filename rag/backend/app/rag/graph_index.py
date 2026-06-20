"""GraphRAG-lite 用の決定的 KG index builder。

外部 LLM / graph DB は使わず、構造化抽出と chunk metadata から
Oracle 内の軽量 entity / claim / community summary を作る。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from app.rag.chunking import Chunk
from app.schemas.extraction import StructuredExtraction

GRAPH_SUMMARY_MAX_SECTIONS = 12
GRAPH_CLAIM_MAX_CHARS = 500
GRAPH_ENTITY_MAX_PER_KB = 80
GRAPH_CLAIM_MAX_PER_KB = 160
SENTENCE_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")


@dataclass(frozen=True)
class GraphEntity:
    """Oracle rag_graph_entities に保存する entity。"""

    entity_id: str
    knowledge_base_id: str | None
    canonical_name: str
    entity_type: str
    description: str
    confidence: float
    source_document_ids: list[str]


@dataclass(frozen=True)
class GraphRelationship:
    """Oracle rag_graph_relationships に保存する relationship。"""

    relationship_id: str
    knowledge_base_id: str | None
    source_entity_id: str
    target_entity_id: str
    relationship_type: str
    description: str
    confidence: float
    source_document_ids: list[str]


@dataclass(frozen=True)
class GraphClaim:
    """Oracle rag_graph_claims に保存する claim。"""

    claim_id: str
    knowledge_base_id: str | None
    entity_id: str
    claim_text: str
    confidence: float
    source_document_id: str
    source_chunk_id: str


@dataclass(frozen=True)
class GraphCommunitySummary:
    """Oracle rag_graph_community_summaries に保存する community summary。"""

    community_id: str
    knowledge_base_id: str | None
    level_no: int
    title: str
    summary_text: str
    entity_ids: list[str]
    source_document_ids: list[str]


@dataclass(frozen=True)
class GraphEntityChunkLink:
    """Oracle rag_graph_entity_chunks に保存する entity-chunk link。"""

    entity_id: str
    chunk_id: str
    document_id: str
    relevance_score: float


@dataclass(frozen=True)
class GraphIndex:
    """1 document から生成した GraphRAG-lite index。"""

    entities: list[GraphEntity] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)
    claims: list[GraphClaim] = field(default_factory=list)
    community_summaries: list[GraphCommunitySummary] = field(default_factory=list)
    entity_chunk_links: list[GraphEntityChunkLink] = field(default_factory=list)


def build_graph_index(
    *,
    document_id: str,
    knowledge_base_ids: list[str],
    extraction: StructuredExtraction,
    chunks: list[Chunk],
    build_claims: bool = True,
    build_community_summaries: bool = True,
) -> GraphIndex:
    """構造化抽出と chunk から軽量 KG artifact を生成する。

    `build_claims` / `build_community_summaries` は GraphRAG アダプターの profile で制御する。
    既定 True は現行 full 構築と一致する。
    """
    if not chunks:
        return GraphIndex()
    kb_ids: list[str | None] = list(knowledge_base_ids) if knowledge_base_ids else [None]
    entities: list[GraphEntity] = []
    relationships: list[GraphRelationship] = []
    claims: list[GraphClaim] = []
    summaries: list[GraphCommunitySummary] = []
    links: list[GraphEntityChunkLink] = []
    for knowledge_base_id in kb_ids:
        kb_index = _build_graph_index_for_kb(
            document_id=document_id,
            knowledge_base_id=knowledge_base_id,
            extraction=extraction,
            chunks=chunks,
            build_claims=build_claims,
            build_community_summaries=build_community_summaries,
        )
        entities.extend(kb_index.entities)
        relationships.extend(kb_index.relationships)
        claims.extend(kb_index.claims)
        summaries.extend(kb_index.community_summaries)
        links.extend(kb_index.entity_chunk_links)
    return GraphIndex(
        entities=entities,
        relationships=relationships,
        claims=claims,
        community_summaries=summaries,
        entity_chunk_links=links,
    )


def _build_graph_index_for_kb(
    *,
    document_id: str,
    knowledge_base_id: str | None,
    extraction: StructuredExtraction,
    chunks: list[Chunk],
    build_claims: bool = True,
    build_community_summaries: bool = True,
) -> GraphIndex:
    document_entity = _document_entity(
        document_id=document_id,
        knowledge_base_id=knowledge_base_id,
        extraction=extraction,
    )
    entity_by_name: dict[str, GraphEntity] = {document_entity.canonical_name: document_entity}
    relationships: dict[str, GraphRelationship] = {}
    claims: list[GraphClaim] = []
    links: dict[tuple[str, str], GraphEntityChunkLink] = {}

    for chunk in chunks:
        names = _entity_names_for_chunk(chunk)
        for name in names:
            entity = entity_by_name.get(name)
            if entity is None and len(entity_by_name) < GRAPH_ENTITY_MAX_PER_KB:
                entity = _section_entity(
                    document_id=document_id,
                    knowledge_base_id=knowledge_base_id,
                    chunk=chunk,
                    canonical_name=name,
                )
                entity_by_name[name] = entity
                relationship = _contains_relationship(
                    document_id=document_id,
                    knowledge_base_id=knowledge_base_id,
                    source_entity_id=document_entity.entity_id,
                    target_entity_id=entity.entity_id,
                    target_name=name,
                )
                relationships[relationship.relationship_id] = relationship
            if entity is None:
                continue
            links[(entity.entity_id, _chunk_id(document_id, chunk))] = GraphEntityChunkLink(
                entity_id=entity.entity_id,
                chunk_id=_chunk_id(document_id, chunk),
                document_id=document_id,
                relevance_score=_chunk_relevance(chunk),
            )
            if build_claims and len(claims) < GRAPH_CLAIM_MAX_PER_KB:
                claims.append(
                    _claim_for_chunk(
                        document_id=document_id,
                        knowledge_base_id=knowledge_base_id,
                        entity_id=entity.entity_id,
                        chunk=chunk,
                    )
                )

    community_summaries: list[GraphCommunitySummary] = []
    if build_community_summaries:
        community_summaries.append(
            _community_summary(
                document_id=document_id,
                knowledge_base_id=knowledge_base_id,
                extraction=extraction,
                entities=list(entity_by_name.values()),
                chunks=chunks,
            )
        )
    return GraphIndex(
        entities=list(entity_by_name.values()),
        relationships=list(relationships.values()),
        claims=_unique_claims(claims),
        community_summaries=community_summaries,
        entity_chunk_links=list(links.values()),
    )


def _document_entity(
    *,
    document_id: str,
    knowledge_base_id: str | None,
    extraction: StructuredExtraction,
) -> GraphEntity:
    document_type = _clean_label(extraction.document_type) or "ドキュメント"
    canonical_name = f"文書全体: {document_type}"
    return GraphEntity(
        entity_id=_graph_id("entity", document_id, knowledge_base_id, canonical_name),
        knowledge_base_id=knowledge_base_id,
        canonical_name=canonical_name,
        entity_type="document",
        description=f"{document_type} 全体を表す GraphRAG-lite entity。",
        confidence=_confidence(extraction.confidence),
        source_document_ids=[document_id],
    )


def _section_entity(
    *,
    document_id: str,
    knowledge_base_id: str | None,
    chunk: Chunk,
    canonical_name: str,
) -> GraphEntity:
    content_kind = _metadata_str(chunk, "content_kind") or "text"
    page = _metadata_str(chunk, "page_number")
    description_parts = [f"{canonical_name} に関する {content_kind} chunk。"]
    if page:
        description_parts.append(f"page={page}")
    return GraphEntity(
        entity_id=_graph_id("entity", document_id, knowledge_base_id, canonical_name),
        knowledge_base_id=knowledge_base_id,
        canonical_name=canonical_name,
        entity_type=_entity_type_for_content_kind(content_kind),
        description=" ".join(description_parts),
        confidence=_confidence(_metadata_float(chunk, "confidence")),
        source_document_ids=[document_id],
    )


def _contains_relationship(
    *,
    document_id: str,
    knowledge_base_id: str | None,
    source_entity_id: str,
    target_entity_id: str,
    target_name: str,
) -> GraphRelationship:
    relationship_id = _graph_id(
        "relationship",
        document_id,
        knowledge_base_id,
        source_entity_id,
        target_entity_id,
        "contains",
    )
    return GraphRelationship(
        relationship_id=relationship_id,
        knowledge_base_id=knowledge_base_id,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type="contains",
        description=f"文書全体は {target_name} を含みます。",
        confidence=1.0,
        source_document_ids=[document_id],
    )


def _claim_for_chunk(
    *,
    document_id: str,
    knowledge_base_id: str | None,
    entity_id: str,
    chunk: Chunk,
) -> GraphClaim:
    claim_text = _claim_text(chunk.text)
    return GraphClaim(
        claim_id=_graph_id(
            "claim",
            document_id,
            knowledge_base_id,
            entity_id,
            _chunk_id(document_id, chunk),
            claim_text,
        ),
        knowledge_base_id=knowledge_base_id,
        entity_id=entity_id,
        claim_text=claim_text,
        confidence=_confidence(_metadata_float(chunk, "confidence")),
        source_document_id=document_id,
        source_chunk_id=_chunk_id(document_id, chunk),
    )


def _community_summary(
    *,
    document_id: str,
    knowledge_base_id: str | None,
    extraction: StructuredExtraction,
    entities: list[GraphEntity],
    chunks: list[Chunk],
) -> GraphCommunitySummary:
    section_names = [
        entity.canonical_name for entity in entities if entity.entity_type != "document"
    ][:GRAPH_SUMMARY_MAX_SECTIONS]
    document_type = _clean_label(extraction.document_type) or "ドキュメント"
    title = f"{document_type} の全体要約"
    summary_text = _summary_text(
        document_type=document_type,
        section_names=section_names,
        chunks=chunks,
    )
    return GraphCommunitySummary(
        community_id=_graph_id("community", document_id, knowledge_base_id, title),
        knowledge_base_id=knowledge_base_id,
        level_no=0,
        title=title,
        summary_text=summary_text,
        entity_ids=[entity.entity_id for entity in entities],
        source_document_ids=[document_id],
    )


def _entity_names_for_chunk(chunk: Chunk) -> list[str]:
    names: list[str] = []
    section_title = _metadata_str(chunk, "section_title")
    section_path = _metadata_str(chunk, "section_path")
    content_kind = _metadata_str(chunk, "content_kind")
    if section_path:
        names.append(section_path)
    if section_title:
        names.append(section_title)
    if content_kind in {"table", "figure"}:
        label = section_title or section_path or "文書全体"
        names.append(f"{label} ({content_kind})")
    if not names:
        names.append("文書全体")
    return _dedupe_labels(names)


def _summary_text(
    *,
    document_type: str,
    section_names: list[str],
    chunks: list[Chunk],
) -> str:
    section_text = "、".join(section_names) if section_names else "文書全体"
    content_kinds = sorted(
        {
            str(chunk.metadata.get("content_kind"))
            for chunk in chunks
            if chunk.metadata.get("content_kind")
        }
    )
    kind_text = "、".join(content_kinds) if content_kinds else "text"
    return (
        f"この{document_type}全体の要約です。"
        f"主要な章節や関係は {section_text} です。"
        f"検索対象には {kind_text} の根拠 chunk が含まれます。"
        "横断要約、全体まとめ、章節間の関係確認に使う community summary です。"
    )


def _claim_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ""
    segments = [match.group(0).strip() for match in SENTENCE_RE.finditer(normalized)]
    first = next((segment for segment in segments if segment), normalized)
    return first[:GRAPH_CLAIM_MAX_CHARS]


def _unique_claims(claims: list[GraphClaim]) -> list[GraphClaim]:
    seen: set[str] = set()
    unique: list[GraphClaim] = []
    for claim in claims:
        if not claim.claim_text or claim.claim_id in seen:
            continue
        seen.add(claim.claim_id)
        unique.append(claim)
    return unique


def _chunk_id(document_id: str, chunk: Chunk) -> str:
    return f"{document_id}:{chunk.index}"


def _chunk_relevance(chunk: Chunk) -> float:
    kind = _metadata_str(chunk, "content_kind")
    if kind == "table":
        return 1.0
    if kind == "figure":
        return 0.9
    return 0.8


def _entity_type_for_content_kind(content_kind: str) -> str:
    if content_kind == "table":
        return "table_section"
    if content_kind == "figure":
        return "figure_section"
    return "section"


def _metadata_str(chunk: Chunk, key: str) -> str | None:
    value = chunk.metadata.get(key)
    if value is None or isinstance(value, bool):
        return None
    cleaned = str(value).strip()
    return cleaned[:512] if cleaned else None


def _metadata_float(chunk: Chunk, key: str) -> float | None:
    value = chunk.metadata.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _confidence(value: float | None) -> float:
    if value is None:
        return 1.0
    return max(0.0, min(float(value), 1.0))


def _clean_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:512]


def _dedupe_labels(values: list[str]) -> list[str]:
    seen: set[str] = set()
    labels: list[str] = []
    for value in values:
        label = _clean_label(value)
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels[:4]


def _graph_id(
    kind: Literal["entity", "relationship", "claim", "community"],
    *parts: object,
) -> str:
    payload = "|".join([kind, *(str(part or "") for part in parts)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
