"""文書構造索引の構築器の出力と、版の指紋の契約を結ぶ合成の回帰（#4549）。

構築器（``build_graph_index``）の出力が変わったのに版（``STRUCTURE_INDEX_BUILDER_VERSION``）を
上げないと、旧い構築器の索引が計画で「構築済み」のまま残る。合成の決定論の fixture の出力の
digest を版ごとに記録し、出力が変わったら版を上げることを求める。LLM・Oracle は呼ばない。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any

import pytest
from rag_parser_core.language import bind_pack, builtin_pack

from app.rag import graph_index
from app.rag.chunking import Chunk
from app.rag.graph_index import (
    GraphIndex,
    build_graph_index,
    graph_body_node_id,
    graph_section_node_id,
)
from app.rag.reference_resolution import ReferenceDocument, document_title_from_paths
from app.rag.structure_index import source_fingerprint, structure_index_plan
from app.schemas.extraction import StructuredExtraction

KB = "kb-contract"
TITLE = "合成本体規程施行令"
LONG_PARENT = "合成の親見出し" * 110
LONG_FIRST = f"{LONG_PARENT} > 第5条"
LONG_SECOND = f"{LONG_PARENT} > 第6条"

# 版ごとの構築器の出力の digest。追記だけで、既存の行は変えない（配備済みの索引の版の記録）。
# explicit-references-v1 は #4057（28c8063b4b）の構築器の出力の記録で、再計算しない。この後の
# #4051・#4310・#4507・#4543 は版を上げずに出力を変えていた（#4549）。
BUILDER_OUTPUT_DIGESTS: dict[str, str] = {
    "explicit-references-v1": "c6f54a854fd8bda6269152ab303144af7327dd83a4dcefab74b322175c78505c",
    "structure-graph-v2": "d08344871a4724519ea00683a1d04790eb1bc49ab61ff5951c11938778b31e8e",
}


@pytest.fixture(autouse=True)
def _ja_pack() -> Iterator[None]:
    """環境の上書きに依らず、組み込みの既定（ja）の言語資源で構築する。"""
    with bind_pack(builtin_pack("ja")):
        yield


def _chunk(index: int, text: str, **metadata: Any) -> Chunk:
    return Chunk(text=text, index=index, start_offset=0, end_offset=0, metadata=metadata)


def _fixture() -> list[tuple[str, str, str, list[Chunk]]]:
    """（文書 ID・chunk set・ファイル名・chunk）。metadata は補建と同じ形。"""
    first = [
        # 番号の接頭辞のファイル名で、本文の題名での自分への参照（#4310）。
        _chunk(0, f"{TITLE}第2条を参照する。", section_path=f"{TITLE} > 第1条"),
        # 略称（令）の自分への参照（#4310）。
        _chunk(1, "令第3条による。", section_path=f"{TITLE} > 第2条", section_title="第2条"),
        _chunk(2, "合成の本文。", section_path=f"{TITLE} > 第3条"),
        # 本文に参照の表記が無く、保存した参照先へ後退する。
        _chunk(
            3,
            "合成の本文。",
            section_path=f"{TITLE} > 第4条",
            reference_targets_json=json.dumps(
                [
                    {
                        "label": "第1条",
                        "kind": "article",
                        "key": "1",
                        "document_title": None,
                        "section_path": f"{TITLE} > 第1条",
                    }
                ],
                ensure_ascii=False,
            ),
        ),
        # 見出しの無い本文が参照の起点になる（#4507）。
        _chunk(4, "第1条を参照する。"),
        # 区切りだけのパスも見出しの無い本文（#4543）。
        _chunk(5, "第2条を参照する。", section_path=" > "),
        # 先頭 512 文字が同じ長いパスの間の参照（#4051）。
        _chunk(6, "第6条を参照する。", section_path=LONG_FIRST),
        _chunk(7, "合成の本文。", section_path=LONG_SECOND, content_kind="table"),
        # 解決しない参照は辺にしない（「本文」へ向けない）。
        _chunk(8, "第9条を参照する。", section_path=f"{TITLE} > 第7条", page_number=3),
        # 見出しのパスが無く題名だけの chunk は、結んだ題名の節点が参照の起点になる。
        _chunk(9, "第3条を参照する。", section_title="合成の題名だけ"),
    ]
    second = [
        _chunk(0, f"{TITLE}第1条を参照する。", section_path="合成別規程 > 第1条"),
        _chunk(1, "第1条を参照する。", section_path="合成別規程 > 第2条"),
    ]
    return [
        ("doc-a", "set-a", "A001_合成資料.json", first),
        ("doc-b", "set-b", "B002_合成の別資料.json", second),
    ]


def _catalog(fixture: list[tuple[str, str, str, list[Chunk]]]) -> tuple[ReferenceDocument, ...]:
    """KB の目録（``GraphReferenceStore.documents`` と同じ形: 見出しの重複を除き題名を付ける）。"""
    documents = []
    for document_id, chunk_set_id, name, chunks in fixture:
        paths = tuple(
            dict.fromkeys(
                str(chunk.metadata["section_path"])
                for chunk in chunks
                if chunk.metadata.get("section_path")
            )
        )
        documents.append(
            ReferenceDocument(
                document_id, chunk_set_id, name, paths, document_title_from_paths(paths)
            )
        )
    return tuple(documents)


def _build_all() -> dict[str, GraphIndex]:
    fixture = _fixture()
    catalog = _catalog(fixture)
    return {
        document_id: build_graph_index(
            document_id=document_id,
            knowledge_base_ids=[KB],
            extraction=StructuredExtraction(),
            chunks=chunks,
            chunk_set_id=chunk_set_id,
            document_name=name,
            reference_documents=catalog,
        )
        for document_id, chunk_set_id, name, chunks in fixture
    }


def _canonical(graphs: dict[str, GraphIndex]) -> str:
    def rows(values: list[Any], *keys: str) -> list[dict[str, Any]]:
        items = [asdict(value) for value in values]
        return sorted(items, key=lambda item: tuple(str(item[key]) for key in keys))

    payload = {
        document_id: {
            "entities": rows(graph.entities, "entity_id"),
            "relationships": rows(graph.relationships, "relationship_id"),
            "links": rows(graph.entity_chunk_links, "entity_id", "chunk_id"),
            "references": rows(graph.references, "reference_id"),
        }
        for document_id, graph in graphs.items()
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def builder_output_digest() -> str:
    return hashlib.sha256(_canonical(_build_all()).encode()).hexdigest()


def test_builder_output_matches_the_recorded_digest_of_the_current_version() -> None:
    version = graph_index.STRUCTURE_INDEX_BUILDER_VERSION
    digest = builder_output_digest()
    assert list(BUILDER_OUTPUT_DIGESTS)[-1] == version, (
        "STRUCTURE_INDEX_BUILDER_VERSION は BUILDER_OUTPUT_DIGESTS の最後の版にする。"
    )
    assert BUILDER_OUTPUT_DIGESTS[version] == digest, (
        "文書構造索引の構築器の出力が変わった。app/rag/graph_index.py の "
        "STRUCTURE_INDEX_BUILDER_VERSION を新しい値に上げ、BUILDER_OUTPUT_DIGESTS に "
        f'"<新しい版>": "{digest}" を追記する（既存の行は変えない）。'
        "版を上げると、構築済みの索引が計画で STALE（builder_changed）になり補建の対象になる。"
    )


def test_recorded_digests_are_distinct_per_version() -> None:
    """版を上げたのに出力が同じ、または同じ出力に別の版を付けた記録を許さない。"""
    assert all(BUILDER_OUTPUT_DIGESTS.values())
    assert len(set(BUILDER_OUTPUT_DIGESTS.values())) == len(BUILDER_OUTPUT_DIGESTS)


def _relationships(graph: GraphIndex, kind: str) -> set[tuple[str, str]]:
    return {
        (r.source_entity_id, r.target_entity_id)
        for r in graph.relationships
        if r.relationship_type == kind
    }


def test_long_paths_sharing_the_first_512_chars_resolve_to_the_full_path() -> None:
    """#4051 の出力の差: 長いパスの章節が別の節点になり、その間の参照が辺になる。"""
    assert LONG_FIRST[:512] == LONG_SECOND[:512]
    graph = _build_all()["doc-a"]
    first = graph_section_node_id("set-a", KB, LONG_FIRST)
    second = graph_section_node_id("set-a", KB, LONG_SECOND)
    assert (first, second) in _relationships(graph, "references")


def test_self_title_and_abbreviation_resolve_in_the_same_document() -> None:
    """#4310 の出力の差: 番号付きのファイル名でも、本文の題名・略称の参照が自分の章節を指す。"""
    graph = _build_all()["doc-a"]
    references = _relationships(graph, "references")

    def node(article: str) -> str:
        return graph_section_node_id("set-a", KB, f"{TITLE} > {article}")

    assert (node("第1条"), node("第2条")) in references
    assert (node("第2条"), node("第3条")) in references
    # 保存した参照先への後退も同じ文書の章節を指す。
    assert (node("第4条"), node("第1条")) in references


def test_headingless_body_chunks_are_sources_of_reference_edges() -> None:
    """#4507・#4543 の出力の差: 見出しの無い本文（区切りだけのパスを含む）が参照の起点になる。"""
    graph = _build_all()["doc-a"]
    body = graph_body_node_id("set-a", KB)
    targets = {target for source, target in _relationships(graph, "references") if source == body}
    assert targets == {
        graph_section_node_id("set-a", KB, f"{TITLE} > 第1条"),
        graph_section_node_id("set-a", KB, f"{TITLE} > 第2条"),
    }
    assert {link.chunk_id for link in graph.entity_chunk_links if link.entity_id == body} == {
        "doc-a:set-a:4",
        "doc-a:set-a:5",
    }


def test_reference_edges_start_at_the_linked_entity_and_never_point_to_the_body() -> None:
    """#4551: 起点は chunk を結んだ節点。解決しない参照・題名だけの参照を「本文」に付けない。"""
    graph = _build_all()["doc-a"]
    body = graph_body_node_id("set-a", KB)
    references = _relationships(graph, "references")
    assert all(target != body for _source, target in references)
    title_only = graph_section_node_id("set-a", KB, "合成の題名だけ")
    assert (title_only, graph_section_node_id("set-a", KB, f"{TITLE} > 第3条")) in references
    unresolved = graph_section_node_id("set-a", KB, f"{TITLE} > 第7条")
    assert all(source != unresolved for source, _target in references)


# 旧い契約（#4026 の "explicit-references-v1"）で保存した構築記録の指紋。
def _old_contract_fingerprint(chunk_sets: list[str]) -> str:
    payload = {"version": "explicit-references-v1", "chunk_sets": sorted(chunk_sets)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class _PlanOracle:
    """計画の読み取りだけに答える合成の Oracle。本文の結び付きの欠けは無い。"""

    def __init__(self, build: dict[str, Any]) -> None:
        self.build = build

    async def get_knowledge_base(self, _kb: str) -> Any:
        return SimpleNamespace(retrieval_config={"structure_index_enabled": True})

    async def _fetch_all(self, sql: str, binds: Any) -> list[dict[str, Any]]:
        if "AS body_links" in sql:
            return []
        if "rag_structure_index_builds" in sql:
            return [{"document_id": "doc-a", "quality": "COMPLETE", **self.build}]
        return [
            {
                "document_id": "doc-a",
                "file_name": "A001_合成資料.json",
                "chunk_set_id": "set-a",
                "chunks": 10,
                "headings": 8,
            }
        ]


async def test_build_saved_with_the_old_contract_is_stale_with_builder_changed() -> None:
    """配備済みの旧い索引（旧い指紋・版の記録なし）は、本文の欠けが無くても STALE にする。"""
    oracle = _PlanOracle({"source_fingerprint": _old_contract_fingerprint(["set-a"])})
    [item] = await structure_index_plan(oracle, KB, document_ids=["doc-a"])
    assert item["body_links_missing"] is False
    assert (item["status"], item["reason"]) == ("STALE", "builder_changed")


async def test_build_of_the_current_version_is_current() -> None:
    oracle = _PlanOracle(
        {
            "source_fingerprint": source_fingerprint(["set-a"]),
            "builder_version": graph_index.STRUCTURE_INDEX_BUILDER_VERSION,
        }
    )
    [item] = await structure_index_plan(oracle, KB, document_ids=["doc-a"])
    assert (item["status"], item["reason"]) == ("CURRENT", None)


async def test_bumping_the_builder_version_makes_existing_builds_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = {
        "source_fingerprint": source_fingerprint(["set-a"]),
        "builder_version": graph_index.STRUCTURE_INDEX_BUILDER_VERSION,
    }
    monkeypatch.setattr(graph_index, "STRUCTURE_INDEX_BUILDER_VERSION", "synthetic-next")
    assert source_fingerprint(["set-a"]) != saved["source_fingerprint"]
    [item] = await structure_index_plan(_PlanOracle(saved), KB, document_ids=["doc-a"])
    assert (item["status"], item["reason"]) == ("STALE", "builder_changed")


async def test_changed_source_of_the_current_version_is_stale_without_builder_changed() -> None:
    """版が同じで chunk set だけが変わった索引は、構築器の変更を理由にしない。"""
    oracle = _PlanOracle(
        {
            "source_fingerprint": source_fingerprint(["set-old"]),
            "builder_version": graph_index.STRUCTURE_INDEX_BUILDER_VERSION,
        }
    )
    [item] = await structure_index_plan(oracle, KB, document_ids=["doc-a"])
    assert (item["status"], item["reason"]) == ("STALE", None)
