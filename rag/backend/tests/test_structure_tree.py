"""文書構造グラフの章節の木と読み取りを、合成の値で確かめる（#4309。Oracle は呼ばない）。"""

from typing import Any

import pytest

from app.clients.graph_reference_store import GraphReferenceStore
from app.clients.structure_graph_store import StructureGraphStore
from app.rag.graph_index import GRAPH_NAME_ELISION, graph_section_node_id
from app.rag.structure_tree import StructureChunkRow, build_structure_tree, section_chain


def node_id(path: str) -> str:
    return graph_section_node_id("set-1", "kb-1", path)


def _tree(*rows: StructureChunkRow) -> Any:
    return build_structure_tree(rows, chunk_set_id="set-1", knowledge_base_id="kb-1")


def test_tree_uses_full_paths_and_virtual_parents_in_document_order() -> None:
    tree = _tree(
        StructureChunkRow("第2章 > 第1節 > 第3条"),
        StructureChunkRow("第2章 > 第1節 > 第4条"),
        StructureChunkRow("第1章"),
    )
    assert [tree.nodes[item].label for item in tree.roots] == ["第2章", "第1章"]
    section = node_id("第2章 > 第1節")
    assert tree.nodes[section].parent_id == node_id("第2章")
    assert [tree.nodes[item].label for item in tree.children_of(section)] == ["第3条", "第4条"]


def test_tree_hides_title_alias_of_a_path_tail() -> None:
    """section_title だけの別名の節点を図に出さない（索引には残る）。"""
    tree = _tree(
        StructureChunkRow("第1章 > 第1条", section_title="第1条"), StructureChunkRow("第2章")
    )
    assert node_id("第1条") not in tree.nodes
    assert node_id("第1章 > 第1条") in tree.nodes


def test_tree_keeps_title_when_there_is_no_path() -> None:
    tree = _tree(StructureChunkRow(None, section_title="見出しだけ"), StructureChunkRow(None))
    assert [tree.nodes[item].name for item in tree.roots] == ["見出しだけ"]


def test_tree_skips_a_single_title_only_level() -> None:
    """文書の題名だけの段を飛ばし、文書の直下を章にする。段が 1 つなら残す。"""
    tree = _tree(StructureChunkRow("題名 > 第1章"), StructureChunkRow("題名 > 第2章"))
    assert [tree.nodes[item].label for item in tree.roots] == ["第1章", "第2章"]
    assert all(tree.nodes[item].parent_id is None for item in tree.roots)
    single = _tree(StructureChunkRow("第1章"))
    assert [single.nodes[item].label for item in single.roots] == ["第1章"]


def test_tree_places_tables_and_figures_under_their_section() -> None:
    tree = _tree(
        StructureChunkRow("第1章", section_title="表A", content_kind="table"),
        StructureChunkRow("第1章", content_kind="figure"),
        StructureChunkRow("第2章"),
    )
    children = [tree.nodes[item] for item in tree.children_of(node_id("第1章"))]
    assert [(item.type, item.name) for item in children] == [
        ("table_section", "表A (table)"),
        ("figure_section", "第1章 (figure)"),
    ]


def test_locate_returns_each_level_and_its_position() -> None:
    tree = _tree(
        *(StructureChunkRow(f"第1章 > 第{index}条") for index in range(1, 4)),
        StructureChunkRow("第2章"),
    )
    assert tree.locate(node_id("第1章 > 第3条")) == [
        {"id": node_id("第1章"), "index": 0},
        {"id": node_id("第1章 > 第3条"), "index": 2},
    ]
    with pytest.raises(KeyError):
        tree.locate("missing")
    with pytest.raises(KeyError):
        tree.children_of("missing")


def test_section_chain_is_empty_for_a_whole_document() -> None:
    assert section_chain("set-1", "kb-1", None) == []
    assert section_chain("set-1", "kb-1", "第1章 > 第1条") == [
        node_id("第1章"),
        node_id("第1章 > 第1条"),
    ]


class _Oracle:
    def __init__(self, *results: list[dict[str, Any]]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((" ".join(sql.split()), binds))
        return self.results.pop(0)


async def test_overview_counts_per_document_pair_and_ignores_other_sets() -> None:
    def document(entity: str, chunk_set: str) -> dict[str, Any]:
        return {
            "entity_id": entity,
            "chunk_set_id": chunk_set,
            "document_id": f"doc-{entity}",
            "file_name": f"{entity}.pdf",
            "section_entities": 0,
        }

    oracle = _Oracle(
        [document("a", "set-a"), document("b", "set-b")],
        [
            {"source_set": "set-a", "target_set": "set-b", "reference_count": 3},
            {"source_set": "set-a", "target_set": "set-other", "reference_count": 9},
        ],
    )
    overview = await StructureGraphStore(oracle).overview("kb-1")
    assert overview["edges"] == [{"id": "a:b", "source": "a", "target": "b", "count": 3}]
    counts = {
        item["id"]: (item["has_sections"], item["outgoing_count"], item["incoming_count"])
        for item in overview["documents"]
    }
    assert counts == {"a": (False, 3, 0), "b": (False, 0, 3)}
    # 文書の組の件数に、同じ文書の中の参照を入れない。
    assert "r.source_document_id<>r.target_document_id" in oracle.calls[1][0]


async def test_overview_without_documents_is_empty() -> None:
    oracle = _Oracle([])
    assert await StructureGraphStore(oracle).overview("kb-1") == {
        "status": "empty",
        "documents": [],
        "edges": [],
    }
    assert len(oracle.calls) == 1


async def test_references_return_chains_and_look_up_elided_paths() -> None:
    elided = f"長い見出し{GRAPH_NAME_ELISION}末尾"
    oracle = _Oracle(
        [
            {
                "source_set": "set-1",
                "target_set": "set-2",
                "source_path": "第1章 > 第1条",
                "target_path": None,
                "reference_count": 2,
            },
            {
                "source_set": "set-1",
                "target_set": "set-2",
                "source_path": elided,
                "target_path": elided,
                "reference_count": 1,
            },
        ],
        [{"entity_id": "entity-source"}],
        [],
    )
    items = (await StructureGraphStore(oracle).references("kb-1", "set-1"))["items"]
    assert items[0]["source_chain"] == [node_id("第1章"), node_id("第1章 > 第1条")]
    assert items[0]["target_chain"] == []
    assert items[0]["count"] == 2
    # 表示名で保存したパスは節点だけ。見つからなければ文書へまとめる。
    assert items[1]["source_chain"] == ["entity-source"]
    assert items[1]["target_chain"] == []


async def test_search_escapes_like_and_normalizes_width() -> None:
    oracle = _Oracle([])
    store = StructureGraphStore(oracle)
    assert await store.search("kb-1", "   ", offset=0, limit=25) == {
        "items": [],
        "total": 0,
        "offset": 0,
    }
    assert oracle.calls == []
    assert (await store.search("kb-1", "Ａ%_", offset=0, limit=25))["total"] == 0
    assert oracle.calls[0][1]["pattern"] == "%a\\%\\_%"


async def test_reference_page_lists_only_resolved_and_ambiguous() -> None:
    """KB の外と区別できない not_found は、件数がずれないよう SQL で除く。"""
    oracle = _Oracle(
        [
            {
                "entity_id": "n",
                "canonical_name": "第1条",
                "entity_type": "section",
                "chunk_set_id": "set-1",
                "document_id": "doc-1",
                "file_name": "合成.pdf",
            }
        ],
        [],
    )
    await GraphReferenceStore(oracle).page("kb-1", "n", direction="outgoing", offset=0, limit=25)
    sql = oracle.calls[1][0]
    assert "r.resolution_status='ambiguous' OR (r.resolution_status='resolved'" in sql
    assert "t.document_id IS NOT NULL AND ts.chunk_set_id IS NOT NULL" in sql
