"""合成文書による参照解決の曖昧さと構造グラフの回帰。"""

from app.rag.chunking import Chunk
from app.rag.cross_references import ReferenceSpec, SectionIndex
from app.rag.graph_index import build_graph_index
from app.schemas.extraction import StructuredExtraction


def test_equally_specific_targets_are_not_selected_by_document_order() -> None:
    index = SectionIndex(["第1章 > 第2節", "第2章 > 第2節"])
    assert index.resolve(ReferenceSpec("第2節", "jsection", "2")) is None


def test_explicit_reference_creates_directional_graph_edge() -> None:
    chunks = [
        Chunk(
            text="第2条を参照する。",
            index=0,
            start_offset=0,
            end_offset=10,
            metadata={"section_path": "第1条"},
        ),
        Chunk(
            text="合成の参照先。",
            index=1,
            start_offset=11,
            end_offset=20,
            metadata={"section_path": "第2条"},
        ),
    ]
    graph = build_graph_index(
        document_id="synthetic-doc",
        knowledge_base_ids=["synthetic-kb"],
        extraction=StructuredExtraction(),
        chunks=chunks,
        chunk_set_id="synthetic-set",
    )
    names = {entity.entity_id: entity.canonical_name for entity in graph.entities}
    references = [edge for edge in graph.relationships if edge.relationship_type == "references"]
    assert len(references) == 1
    assert names[references[0].source_entity_id] == "第1条"
    assert names[references[0].target_entity_id] == "第2条"


def test_scoped_cross_document_resolution_pins_version_and_rejects_ambiguity() -> None:
    from app.rag.reference_resolution import ReferenceDocument, resolve_scoped_reference

    source = ReferenceDocument("source", "s1", "synthetic-source.txt", ("第1条",))
    target = ReferenceDocument("target", "t1", "合成参照文書.txt", ("第2条",))
    spec = ReferenceSpec("合成参照文書第2条", "article", "2", "合成参照文書")
    args = dict(spec=spec, source=source, citing_path="第1条")
    resolved = resolve_scoped_reference(**args, documents=(target,))
    assert resolved.status == "resolved"
    assert resolved.target is not None and resolved.target.chunk_set_id == "t1"
    assert resolve_scoped_reference(**args, documents=()).status == "not_found"
    other_document = ReferenceDocument("another", "a1", target.name, target.section_paths)
    assert (
        resolve_scoped_reference(**args, documents=(target, other_document)).status == "ambiguous"
    )
    other_version = ReferenceDocument(target.document_id, "t2", target.name, target.section_paths)
    assert (
        resolve_scoped_reference(**args, documents=(target, other_version)).status
        == "version_unconfirmed"
    )
    unknown_version = ReferenceDocument(target.document_id, None, target.name, target.section_paths)
    assert resolve_scoped_reference(**args, documents=(unknown_version,)).target is None


def test_legacy_resolved_path_is_not_trusted_when_rebuilding_graph() -> None:
    import json

    from app.rag.cross_references import ReferenceTarget

    chunks = [
        Chunk(
            text="",
            index=0,
            start_offset=0,
            end_offset=0,
            metadata={
                "section_path": "序文",
                "reference_targets_json": json.dumps(
                    [ReferenceTarget("第2節", "jsection", "2", None, "第1章 > 第2節").to_dict()]
                ),
            },
        ),
        Chunk(
            text="",
            index=1,
            start_offset=0,
            end_offset=0,
            metadata={"section_path": "第1章 > 第2節"},
        ),
        Chunk(
            text="",
            index=2,
            start_offset=0,
            end_offset=0,
            metadata={"section_path": "第2章 > 第2節"},
        ),
    ]
    graph = build_graph_index(
        document_id="doc",
        knowledge_base_ids=["kb"],
        extraction=StructuredExtraction(),
        chunks=chunks,
        chunk_set_id="cs",
    )
    assert not any(edge.relationship_type == "references" for edge in graph.relationships)


def test_relative_reference_and_cycle_are_directional_without_recursive_expansion() -> None:
    chunks = [
        Chunk(
            text="次条を参照。",
            index=0,
            start_offset=0,
            end_offset=6,
            metadata={"section_path": "第1条"},
        ),
        Chunk(
            text="前条を参照。",
            index=1,
            start_offset=7,
            end_offset=13,
            metadata={"section_path": "第2条"},
        ),
    ]
    graph = build_graph_index(
        document_id="doc",
        knowledge_base_ids=["kb"],
        extraction=StructuredExtraction(),
        chunks=chunks,
        chunk_set_id="cs",
    )
    names = {entity.entity_id: entity.canonical_name for entity in graph.entities}
    assert {
        (names[edge.source_entity_id], names[edge.target_entity_id])
        for edge in graph.relationships
        if edge.relationship_type == "references"
    } == {("第1条", "第2条"), ("第2条", "第1条")}
