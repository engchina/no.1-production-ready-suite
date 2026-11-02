"""関係情報(文書と章・節の見出しのつながり)の index builder のテスト。"""

import hashlib

from app.clients.graph_reference_store import GraphReferenceStore
from app.rag.chunking import Chunk
from app.rag.graph_index import GRAPH_NAME_MAX_CHARS, build_graph_index, graph_section_node_id
from app.schemas.extraction import StructuredExtraction


def test_multibyte_document_label_includes_prefix_in_character_limit() -> None:
    name = "合成" * 256
    graph = build_graph_index(
        document_id="synthetic-doc",
        knowledge_base_ids=["synthetic-kb"],
        extraction=StructuredExtraction(),
        chunks=[Chunk(text="合成", index=0, start_offset=0, end_offset=2)],
        document_name=name,
    )
    label = graph.entities[0].canonical_name
    assert len(label) == 512
    assert len(label.encode("utf-8")) > 512
    assert label.endswith("合成") or label.endswith("合")


def test_build_graph_index_creates_entities_and_relationships() -> None:
    """構造化 chunk metadata から deterministic KG artifact を生成する。"""
    extraction = StructuredExtraction.model_validate(
        {
            "raw_text": "第1章 承認条件\n12万円以上は部門長承認です。",
            "document_type": "社内規程",
            "confidence": 0.91,
            "elements": [
                {
                    "kind": "title",
                    "text": "第1章 承認条件",
                    "order": 1,
                    "page_number": 1,
                },
                {
                    "kind": "table",
                    "text": "| 項目 | 値 |\n| 承認 | 部門長 |",
                    "order": 2,
                    "page_number": 2,
                    "section_path": ["承認条件", "料金表"],
                },
            ],
        }
    )
    chunks = [
        Chunk(
            text="承認条件は12万円以上の場合に部門長承認です。",
            index=0,
            start_offset=0,
            end_offset=22,
            metadata={
                "section_path": "社内規程 > 承認条件",
                "section_title": "承認条件",
                "content_kind": "text",
                "confidence": 0.88,
                "page_number": 1,
            },
        ),
        Chunk(
            text="| 項目 | 値 |\n| 承認 | 部門長 |",
            index=1,
            start_offset=23,
            end_offset=43,
            metadata={
                "section_path": "社内規程 > 料金表",
                "section_title": "料金表",
                "content_kind": "table",
                "confidence": 0.74,
                "page_number": 2,
            },
        ),
    ]

    graph = build_graph_index(
        document_id="doc-1",
        knowledge_base_ids=["kb-1"],
        extraction=extraction,
        chunks=chunks,
    )

    entity_names = {entity.canonical_name for entity in graph.entities}
    assert "文書全体: 社内規程" in entity_names
    assert "社内規程 > 承認条件" in entity_names
    assert "料金表 (table)" in entity_names
    assert all(len(entity.entity_id) == 64 for entity in graph.entities)
    assert {entity.knowledge_base_id for entity in graph.entities} == {"kb-1"}
    assert any(entity.entity_type == "table_section" for entity in graph.entities)
    assert len(graph.relationships) >= 2
    assert {relationship.relationship_type for relationship in graph.relationships} == {"contains"}
    assert any(link.chunk_id == "doc-1:1" for link in graph.entity_chunk_links)
    # claims / community summary は読む経路が無かったため作らない(#621)。
    assert {field for field in vars(graph)} == {
        "entities",
        "relationships",
        "entity_chunk_links",
        "references",
    }


def test_build_graph_index_uses_default_scope_without_knowledge_base() -> None:
    """KB 未所属 document でも fallback graph scope を生成できる。"""
    extraction = StructuredExtraction(raw_text="本文です。", document_type="メモ", confidence=0.8)
    chunks = [Chunk(text="本文です。", index=0, start_offset=0, end_offset=5)]

    graph = build_graph_index(
        document_id="doc-no-kb",
        knowledge_base_ids=[],
        extraction=extraction,
        chunks=chunks,
    )

    assert len(graph.entities) >= 1
    assert {entity.knowledge_base_id for entity in graph.entities} == {None}


def test_document_entity_uses_the_file_name() -> None:
    """文書の節点の名前はファイル名にし、同じ種類の文書を区別できる（#2051）。"""
    extraction = StructuredExtraction(raw_text="本文", document_type="社内規程", confidence=0.9)
    chunk = Chunk(
        text="本文", index=0, start_offset=0, end_offset=2, metadata={"section_path": "第 1 章"}
    )

    first = build_graph_index(
        document_id="doc-1",
        knowledge_base_ids=["kb-1"],
        extraction=extraction,
        chunks=[chunk],
        document_name="a.pdf",
    )
    second = build_graph_index(
        document_id="doc-2",
        knowledge_base_ids=["kb-1"],
        extraction=extraction,
        chunks=[chunk],
        document_name="b.pdf",
    )

    names = {
        e.canonical_name for e in [*first.entities, *second.entities] if e.entity_type == "document"
    }
    assert names == {"文書全体: a.pdf", "文書全体: b.pdf"}


def _long_path_chunks() -> tuple[list[Chunk], str, str]:
    # 先頭 512 文字が共通で、末尾の見出しだけが違う合成の章節。
    common = "合成の親見出し" * 110
    first, second = f"{common} > 第1条", f"{common} > 第2条"
    assert first[:512] == second[:512]
    return (
        [
            Chunk(
                text="本文1",
                index=0,
                start_offset=0,
                end_offset=3,
                metadata={"section_path": first},
            ),
            Chunk(
                text="本文2",
                index=1,
                start_offset=3,
                end_offset=6,
                metadata={"section_path": second},
            ),
        ],
        first,
        second,
    )


def test_sections_sharing_the_long_prefix_stay_distinct_nodes() -> None:
    """#4051: 表示の長さで切り詰めても、章節の同一性は完全なパスで保つ。"""
    chunks, first, second = _long_path_chunks()
    graph = build_graph_index(
        document_id="doc-1",
        knowledge_base_ids=["kb-1"],
        extraction=StructuredExtraction(),
        chunks=chunks,
        chunk_set_id="set-1",
    )
    sections = [entity for entity in graph.entities if entity.entity_type == "section"]
    assert {entity.entity_id for entity in sections} == {
        graph_section_node_id("set-1", "kb-1", first),
        graph_section_node_id("set-1", "kb-1", second),
    }
    # 表示名は列の上限に収まり、区別に要る末尾の見出しが残る。
    assert {len(entity.canonical_name) for entity in sections} == {GRAPH_NAME_MAX_CHARS}
    assert {entity.canonical_name[-3:] for entity in sections} == {"第1条", "第2条"}
    # 包含の辺と chunk のリンクも章節ごとに分かれる。
    contains = {
        r.target_entity_id for r in graph.relationships if r.relationship_type == "contains"
    }
    assert contains == {entity.entity_id for entity in sections}
    assert {link.chunk_id for link in graph.entity_chunk_links} == {
        "doc-1:set-1:0",
        "doc-1:set-1:1",
    }
    assert len({link.entity_id for link in graph.entity_chunk_links}) == 2


def test_short_names_keep_the_saved_entity_id() -> None:
    """上限以下の名前の ID は変わらず、構築済みの索引の作り直しを求めない。"""
    chunk = Chunk(
        text="本文",
        index=0,
        start_offset=0,
        end_offset=2,
        metadata={"section_path": "第1章  > 第1条", "section_title": "第1条"},
    )
    graph = build_graph_index(
        document_id="doc-1",
        knowledge_base_ids=["kb-1"],
        extraction=StructuredExtraction(),
        chunks=[chunk],
        chunk_set_id="set-1",
        document_name="synthetic.pdf",
    )
    ids = {entity.canonical_name: entity.entity_id for entity in graph.entities}
    assert ids == {
        name: hashlib.sha256(f"entity|set-1|kb-1|{name}".encode()).hexdigest()
        for name in ("文書全体: synthetic.pdf", "第1章 > 第1条", "第1条")
    }


def test_long_reference_in_the_same_document_uses_the_full_path() -> None:
    """長いパスの章節の間でも、同じ文書の参照が正しい章節を指す。"""
    chunks, first, second = _long_path_chunks()
    chunks[0].text = "第2条を参照する。"
    chunks[1].text = "合成の本文。"
    graph = build_graph_index(
        document_id="doc-1",
        knowledge_base_ids=["kb-1"],
        extraction=StructuredExtraction(),
        chunks=chunks,
        chunk_set_id="set-1",
        document_name="synthetic.pdf",
    )
    references = [r for r in graph.relationships if r.relationship_type == "references"]
    assert [(r.source_entity_id, r.target_entity_id) for r in references] == [
        (
            graph_section_node_id("set-1", "kb-1", first),
            graph_section_node_id("set-1", "kb-1", second),
        )
    ]


def test_reference_view_id_matches_the_entity_for_a_long_path() -> None:
    chunks, first, _second = _long_path_chunks()
    graph = build_graph_index(
        document_id="doc-1",
        knowledge_base_ids=["kb-1"],
        extraction=StructuredExtraction(),
        chunks=chunks,
        chunk_set_id="set-1",
    )
    row = {
        "reference_id": "ref",
        "label": "第1条",
        "resolution_status": "resolved",
        "source_document_id": "doc-1",
        "source_chunk_set_id": "set-1",
        "source_section_path": first,
        "source_name": "synthetic.pdf",
        "target_document_id": "doc-1",
        "target_chunk_set_id": "set-1",
        "target_section_path": first,
        "target_name": "synthetic.pdf",
        "active_target_set": "set-1",
    }
    view = GraphReferenceStore._view("kb-1", row)
    assert view["source"]["id"] in {entity.entity_id for entity in graph.entities}
    assert view["source"]["section_path"] == first
