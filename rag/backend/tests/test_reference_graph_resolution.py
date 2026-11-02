"""合成文書による参照解決の曖昧さと構造グラフの回帰。"""

from functools import partial
from typing import Any

import pytest

from app.rag.chunking import Chunk
from app.rag.cross_references import ReferenceSpec, SectionIndex, extract_references
from app.rag.graph_index import build_graph_index
from app.rag.reference_resolution import ReferenceDocument, resolve_scoped_reference
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
    resolve = partial(resolve_scoped_reference, spec, source=source, citing_path="第1条")
    resolved = resolve(documents=(target,))
    assert resolved.status == "resolved"
    assert resolved.target is not None and resolved.target.chunk_set_id == "t1"
    assert resolve(documents=()).status == "not_found"
    other_document = ReferenceDocument("another", "a1", target.name, target.section_paths)
    assert resolve(documents=(target, other_document)).status == "ambiguous"
    other_version = ReferenceDocument(target.document_id, "t2", target.name, target.section_paths)
    assert resolve(documents=(target, other_version)).status == "version_unconfirmed"
    unknown_version = ReferenceDocument(target.document_id, None, target.name, target.section_paths)
    assert resolve(documents=(unknown_version,)).target is None


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


def test_reference_targets_beyond_initial_graph_window_are_indexed() -> None:
    chunks = [
        Chunk(
            text="第100条を参照する。" if index == 0 else "合成本文。",
            index=index,
            start_offset=index * 20,
            end_offset=(index + 1) * 20,
            metadata={"section_path": f"第{index + 1}条"},
        )
        for index in range(100)
    ]
    graph = build_graph_index(
        document_id="doc",
        knowledge_base_ids=["kb"],
        extraction=StructuredExtraction(),
        chunks=chunks,
        chunk_set_id="cs",
    )
    assert len(graph.entities) == 101
    assert any(edge.relationship_type == "references" for edge in graph.relationships)
    assert graph.references[0].target_section_path == "第100条"


def test_resolving_many_references_parses_each_catalog_document_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import cross_references, reference_resolution

    alias_calls: list[str] = []
    index_calls: list[tuple[str, ...]] = []
    aliases = cross_references.document_name_aliases
    section_index = cross_references.SectionIndex

    def counted_aliases(value: str) -> set[str]:
        alias_calls.append(value)
        return aliases(value)

    def counted_index(paths: tuple[str, ...]) -> Any:
        index_calls.append(paths)
        return section_index(paths)

    monkeypatch.setattr(cross_references, "document_name_aliases", counted_aliases)
    monkeypatch.setattr(reference_resolution, "SectionIndex", counted_index)
    source = ReferenceDocument("source", "s1", "合成の起点.txt", ("第1条",))
    target = ReferenceDocument("target", "t1", "合成規程.txt", ("第1条", "第2条", "第3条"))
    other = ReferenceDocument("other", "o1", "合成細則.txt", ("第1条",))
    spec = extract_references("合成規程の第2条を参照する。")[0]
    results = [
        resolve_scoped_reference(
            spec, source=source, citing_path="第1条", documents=(target, other)
        )
        for _ in range(50)
    ]
    assert {result.status for result in results} == {"resolved"}
    assert {result.target.section_path for result in results if result.target} == {"第2条"}
    # 文書のファイル名の1回と、見出しになれる章節ごとの1回を超えない。
    assert len(alias_calls) <= 2 + len(target.section_paths) + len(other.section_paths)
    assert index_calls == [target.section_paths]
