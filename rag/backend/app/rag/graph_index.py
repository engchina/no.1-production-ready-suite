"""関係情報(文書と章・節の見出しのつながり)の決定的 index builder。

外部 LLM / graph DB は使わず、構造化抽出と chunk metadata から Oracle 内の entity
(文書全体・章節の見出し・表 / 図のラベル)と relationship(文書全体が見出しを「含む」)を作る。
構築した関係情報はナレッジベースの「文書構造グラフ」の表示に使い、``rag_graph_expansion_enabled``
(既定 OFF)を ON にすると回答の検索の候補を広げるのにも使う(``app.rag.graph_expansion``。#2055)。
claims / community summary は読む経路が無かったため作らない(#621)。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Literal

from rag_parser_core.language import current_pack

from app.rag.chunking import Chunk
from app.rag.cross_references import (
    extract_references,
    reference_targets,
    split_section_path,
)
from app.rag.reference_records import GraphReferenceRecord, build_reference_records
from app.rag.reference_resolution import (
    ReferenceDocument,
    document_title_from_paths,
    resolve_scoped_reference,
)
from app.schemas.extraction import StructuredExtraction

# 構築器の版。同じ chunk から作る entity・relationship・link・参照の記録が変わったら新しい値に
# 上げる（版の指紋に入り、旧い版の索引を補建の対象にする。#4549）。出力の digest は
# tests/test_structure_index_builder_contract.py の BUILDER_OUTPUT_DIGESTS に版ごとに追記する。
STRUCTURE_INDEX_BUILDER_VERSION = "structure-graph-v2"

# 表示名の上限（rag_graph_entities.canonical_name の列の大きさ）。同一性には使わない。
GRAPH_NAME_MAX_CHARS = 512
# 長い表示名で省く位置の印。区別に要る先頭（文書・上位の見出し）と末尾（最後の見出し）を残す。
GRAPH_NAME_ELISION = "…"
_GRAPH_NAME_HEAD_CHARS = (GRAPH_NAME_MAX_CHARS - len(GRAPH_NAME_ELISION)) // 2
_GRAPH_NAME_TAIL_CHARS = GRAPH_NAME_MAX_CHARS - len(GRAPH_NAME_ELISION) - _GRAPH_NAME_HEAD_CHARS


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
class GraphEntityChunkLink:
    """Oracle rag_graph_entity_chunks に保存する entity-chunk link。"""

    entity_id: str
    chunk_id: str
    document_id: str
    relevance_score: float


@dataclass(frozen=True)
class GraphIndex:
    """1 document から生成した関係情報の index。"""

    references: list[GraphReferenceRecord] = field(default_factory=list)
    entities: list[GraphEntity] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)
    entity_chunk_links: list[GraphEntityChunkLink] = field(default_factory=list)


def build_graph_index(
    *,
    document_id: str,
    knowledge_base_ids: list[str],
    extraction: StructuredExtraction,
    chunks: list[Chunk],
    chunk_set_id: str | None = None,
    document_name: str | None = None,
    reference_documents: tuple[ReferenceDocument, ...] = (),
) -> GraphIndex:
    """構造化抽出と chunk から関係情報(entity / relationship / entity-chunk link)を生成する。

    ``document_name``(文書のファイル名)は文書の節点の名前に使う(#2051。無ければ文書の種類)。
    """
    if not chunks:
        return GraphIndex()
    kb_ids: list[str | None] = list(knowledge_base_ids) if knowledge_base_ids else [None]
    references: list[GraphReferenceRecord] = []
    entities: list[GraphEntity] = []
    relationships: list[GraphRelationship] = []
    links: list[GraphEntityChunkLink] = []
    for knowledge_base_id in kb_ids:
        kb_index = _build_graph_index_for_kb(
            document_id=document_id,
            identity_scope=chunk_set_id or document_id,
            chunk_set_id=chunk_set_id,
            knowledge_base_id=knowledge_base_id,
            extraction=extraction,
            chunks=chunks,
            document_name=document_name,
            reference_documents=reference_documents,
        )
        references.extend(kb_index.references)
        entities.extend(kb_index.entities)
        relationships.extend(kb_index.relationships)
        links.extend(kb_index.entity_chunk_links)
    return GraphIndex(
        references=references,
        entities=entities,
        relationships=relationships,
        entity_chunk_links=links,
    )


def _build_graph_index_for_kb(
    *,
    document_id: str,
    identity_scope: str,
    chunk_set_id: str | None,
    knowledge_base_id: str | None,
    extraction: StructuredExtraction,
    chunks: list[Chunk],
    document_name: str | None = None,
    reference_documents: tuple[ReferenceDocument, ...] = (),
) -> GraphIndex:
    document_entity = _document_entity(
        document_id=document_id,
        identity_scope=identity_scope,
        knowledge_base_id=knowledge_base_id,
        extraction=extraction,
        document_name=document_name,
    )
    # 同一性は切り詰めない完全な名前。表示名(canonical_name)は上限で省略され、一意とは限らない。
    entity_by_name: dict[str | tuple[str], GraphEntity] = {
        _document_identity(extraction, document_name): document_entity
    }
    relationships: dict[str, GraphRelationship] = {}
    links: dict[tuple[str, str], GraphEntityChunkLink] = {}
    # chunk ごとの参照の起点（chunk を結んだ最初の entity）と、引用元のパス（本文は空）。
    sources: list[tuple[GraphEntity, str]] = []

    for chunk in chunks:
        body = is_body_chunk(
            chunk.metadata.get("section_path"), chunk.metadata.get("section_title")
        )
        names = (
            [current_pack().text("search_labels", "body")]
            if body
            else _entity_names_for_chunk(chunk)
        )
        for position, name in enumerate(names):
            key: str | tuple[str] = ("body",) if body else name
            entity = entity_by_name.get(key)
            if entity is None:
                entity = _section_entity(
                    document_id=document_id,
                    identity_scope=identity_scope,
                    knowledge_base_id=knowledge_base_id,
                    chunk=chunk,
                    identity=name,
                )
                if body:
                    entity = replace(
                        entity,
                        entity_id=graph_body_node_id(identity_scope, knowledge_base_id),
                        entity_type="body",
                        description=name,
                    )
                entity_by_name[key] = entity
                relationship = _contains_relationship(
                    document_id=document_id,
                    identity_scope=identity_scope,
                    knowledge_base_id=knowledge_base_id,
                    source_entity_id=document_entity.entity_id,
                    target_entity_id=entity.entity_id,
                    target_name=entity.canonical_name,
                )
                relationships[relationship.relationship_id] = relationship
            if entity is None:
                continue
            if position == 0:
                sources.append((entity, "" if body else _metadata_str(chunk, "section_path") or ""))
            chunk_id = _chunk_id(document_id, chunk, chunk_set_id=chunk_set_id)
            links[(entity.entity_id, chunk_id)] = GraphEntityChunkLink(
                entity_id=entity.entity_id,
                chunk_id=chunk_id,
                document_id=document_id,
                relevance_score=_chunk_relevance(chunk),
            )

    # 参照は包含と別に構築する。旧metadataの推測済みパスをそのまま確定辺へ転用しない。
    # 起点は chunk を結んだ entity（本文・区切りだけのパスは「本文」）で、包含と同じ判定（#4551）。
    section_paths = tuple(path for _entity, path in sources)
    reference_source = ReferenceDocument(
        document_id=document_id,
        chunk_set_id=chunk_set_id,
        name=document_name or "",
        section_paths=section_paths,
        title=document_title_from_paths(section_paths),
    )
    # 参照先は見出しのパスで引く。「本文」はパスを持たず、参照先にならない。
    entity_by_path = {
        split_section_path(name): entity
        for name, entity in entity_by_name.items()
        if isinstance(name, str)
    }
    for chunk, (source_entity, source_path) in zip(chunks, sources, strict=True):
        specs = extract_references(chunk.text) or [
            target.spec for target in reference_targets(chunk.metadata)
        ]
        for spec in specs:
            resolution = resolve_scoped_reference(
                spec,
                source=reference_source,
                citing_path=source_path,
                documents=(),
            )
            if resolution.target is None:
                continue
            target_entity = entity_by_path.get(split_section_path(resolution.target.section_path))
            if target_entity is None:
                continue
            relationship = GraphRelationship(
                relationship_id=_graph_id(
                    "relationship",
                    identity_scope,
                    knowledge_base_id,
                    source_entity.entity_id,
                    target_entity.entity_id,
                    "references",
                ),
                knowledge_base_id=knowledge_base_id,
                source_entity_id=source_entity.entity_id,
                target_entity_id=target_entity.entity_id,
                relationship_type="references",
                description=spec.label,
                confidence=1.0,
                source_document_ids=[document_id],
            )
            relationships[relationship.relationship_id] = relationship

    return GraphIndex(
        references=build_reference_records(
            knowledge_base_id=knowledge_base_id,
            source=reference_source,
            chunks=chunks,
            documents=reference_documents,
        )
        if knowledge_base_id is not None
        else [],
        entities=list(entity_by_name.values()),
        relationships=list(relationships.values()),
        entity_chunk_links=list(links.values()),
    )


def _document_type_label(extraction: StructuredExtraction) -> str:
    return _clean_label(extraction.document_type) or current_pack().text(
        "labels", "default_document_type"
    )


def _document_identity(extraction: StructuredExtraction, document_name: str | None) -> str:
    # 文書の種類だけでは同じ種類の文書が同じ名前になり、グラフで区別できない(#2051)。
    # 文書全体の名前は言語資源(#2181)。
    whole = current_pack().text("labels", "whole_document")
    name = _normalize_label(document_name or "") or _document_type_label(extraction)
    return _normalize_label(f"{whole}: {name}")


def _document_entity(
    *,
    document_id: str,
    identity_scope: str,
    knowledge_base_id: str | None,
    extraction: StructuredExtraction,
    document_name: str | None = None,
) -> GraphEntity:
    pack = current_pack()
    document_type = _document_type_label(extraction)
    identity = _document_identity(extraction, document_name)
    return GraphEntity(
        entity_id=_graph_id("entity", identity_scope, knowledge_base_id, identity),
        knowledge_base_id=knowledge_base_id,
        canonical_name=_clean_label(identity),
        entity_type="document",
        description=pack.text("labels", "graph_document").format(document_type=document_type),
        confidence=_confidence(extraction.confidence),
        source_document_ids=[document_id],
    )


def _section_entity(
    *,
    document_id: str,
    identity_scope: str,
    knowledge_base_id: str | None,
    chunk: Chunk,
    identity: str,
) -> GraphEntity:
    content_kind = _metadata_str(chunk, "content_kind") or "text"
    page = _metadata_str(chunk, "page_number")
    canonical_name = _clean_label(identity)
    description_parts = [
        current_pack()
        .text("labels", "graph_section")
        .format(name=canonical_name, kind=content_kind)
    ]
    if page:
        description_parts.append(f"page={page}")
    return GraphEntity(
        entity_id=_graph_id("entity", identity_scope, knowledge_base_id, identity),
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
    identity_scope: str,
    knowledge_base_id: str | None,
    source_entity_id: str,
    target_entity_id: str,
    target_name: str,
) -> GraphRelationship:
    relationship_id = _graph_id(
        "relationship",
        identity_scope,
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
        description=current_pack()
        .text("labels", "graph_contains")
        .format(whole=current_pack().text("labels", "whole_document"), target=target_name),
        confidence=1.0,
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
        label = section_title or section_path or current_pack().text("labels", "whole_document")
        names.append(f"{label} ({content_kind})")
    if not names:
        names.append(current_pack().text("labels", "whole_document"))
    return _dedupe_labels(names)


def _chunk_id(
    document_id: str,
    chunk: Chunk,
    *,
    chunk_set_id: str | None,
) -> str:
    if chunk_set_id is not None:
        return f"{document_id}:{chunk_set_id}:{chunk.index}"
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
    return _metadata_text(chunk.metadata.get(key))


def _metadata_text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    cleaned = str(value).strip()
    return cleaned or None


def is_body_chunk(section_path: object, section_title: object) -> bool:
    """見出しの無い保存本文か。索引の構築・章節の木・旧索引の検出が同じ判定を使う。

    パスは「 > 」でつないだ文字列か配列で、区切りと空白だけの段は見出しにしない。
    題名は空白だけなら無い扱い。
    """
    path = section_path if isinstance(section_path, list | str) else _metadata_text(section_path)
    return not split_section_path(path) and not _metadata_text(section_title)


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


def _normalize_label(value: str) -> str:
    """同一性に使う名前。空白だけをそろえ、長さでは切らない。"""
    return re.sub(r"\s+", " ", value).strip()


def _clean_label(value: str) -> str:
    """表示名。上限を超えた分は中間を省く(SQL の同じ規則は graph_display_name_sql)。"""
    label = _normalize_label(value)
    if len(label) <= GRAPH_NAME_MAX_CHARS:
        return label
    return f"{label[:_GRAPH_NAME_HEAD_CHARS]}{GRAPH_NAME_ELISION}{label[-_GRAPH_NAME_TAIL_CHARS:]}"


def graph_display_name_sql(column: str) -> str:
    """保存した完全なパスの列から、entity の表示名(``_clean_label``)を SQL で求める式。"""
    label = f"TRIM(REGEXP_REPLACE({column},'[[:space:]]+',' '))"
    return (
        f"CASE WHEN LENGTH({label})<={GRAPH_NAME_MAX_CHARS} THEN {label} "
        f"ELSE SUBSTR({label},1,{_GRAPH_NAME_HEAD_CHARS})||'{GRAPH_NAME_ELISION}'"
        f"||SUBSTR({label},-{_GRAPH_NAME_TAIL_CHARS}) END"
    )


def graph_body_node_id(chunk_set_id: object, knowledge_base_id: object) -> str:
    """章節名と独立した、見出しなしの保存本文の identity。"""
    return _graph_id("body", chunk_set_id, knowledge_base_id)


def graph_section_node_id(chunk_set_id: object, knowledge_base_id: object, name: str) -> str:
    """完全なパス・文書全体の名前から、構造図の entity と同じ ID を求める。"""
    return _graph_id("entity", chunk_set_id, knowledge_base_id, _normalize_label(name))


def _dedupe_labels(values: list[str]) -> list[str]:
    seen: set[str] = set()
    labels: list[str] = []
    for value in values:
        label = _normalize_label(value)
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels[:4]


def _graph_id(
    kind: Literal["entity", "relationship", "body"],
    *parts: object,
) -> str:
    payload = "|".join([kind, *(str(part or "") for part in parts)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
