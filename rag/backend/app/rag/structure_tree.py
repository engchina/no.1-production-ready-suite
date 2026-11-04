"""文書構造グラフの章節の木（#4309）。

図の木は chunk の ``section_path`` の全文から作る。``canonical_name`` は長いと省略され、
``section_title`` だけの節点（検索の名前の照合に使う）は同じ節の別名なので、図には出さない。
実体の無い途中の段は、パスから仮想の節点を作る。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.rag.cross_references import SECTION_PATH_SEPARATOR, split_section_path
from app.rag.graph_index import _entity_type_for_content_kind, graph_section_node_id


@dataclass(frozen=True)
class StructureChunkRow:
    """木を作るのに要る chunk の値（本文は読まない）。"""

    section_path: str | None
    section_title: str | None = None
    content_kind: str | None = None


@dataclass
class StructureTreeNode:
    id: str
    name: str
    label: str
    type: str
    parent_id: str | None
    children: list[str] = field(default_factory=list)


@dataclass
class StructureTree:
    nodes: dict[str, StructureTreeNode] = field(default_factory=dict)
    roots: list[str] = field(default_factory=list)

    def children_of(self, parent_id: str | None) -> list[str]:
        if parent_id is None:
            return self.roots
        node = self.nodes.get(parent_id)
        if node is None:
            raise KeyError(parent_id)
        return node.children

    def locate(self, node_id: str) -> list[dict[str, object]]:
        """根から ``node_id`` までの節点と、各段の中の位置（その段を読むページを決める）。"""
        chain: list[str] = []
        current: str | None = node_id
        while current is not None:
            node = self.nodes.get(current)
            if node is None:
                raise KeyError(node_id)
            chain.append(current)
            current = node.parent_id
        chain.reverse()
        return [
            {
                "id": item,
                "index": self.children_of(self.nodes[item].parent_id).index(item),
            }
            for item in chain
        ]


def section_chain(chunk_set_id: str, knowledge_base_id: str, path: str | None) -> list[str]:
    """パスの先頭から末尾までの各段の節点の id。文書全体（パスなし）は空。"""
    parts = split_section_path(path)
    return [
        graph_section_node_id(
            chunk_set_id, knowledge_base_id, SECTION_PATH_SEPARATOR.join(parts[: index + 1])
        )
        for index in range(len(parts))
    ]


def build_structure_tree(
    rows: Iterable[StructureChunkRow], *, chunk_set_id: str, knowledge_base_id: str
) -> StructureTree:
    """文書の順の chunk から木を作る。節点の id は構造索引の entity と同じ規則。"""
    tree = StructureTree()

    def add(name: str, label: str, kind: str, parent_id: str | None) -> str:
        node_id = graph_section_node_id(chunk_set_id, knowledge_base_id, name)
        if node_id not in tree.nodes:
            tree.nodes[node_id] = StructureTreeNode(node_id, name, label, kind, parent_id)
            if parent_id is None:
                tree.roots.append(node_id)
            else:
                tree.nodes[parent_id].children.append(node_id)
        return node_id

    for row in rows:
        parts = split_section_path(row.section_path)
        parent: str | None = None
        for index, part in enumerate(parts):
            name = SECTION_PATH_SEPARATOR.join(parts[: index + 1])
            parent = add(name, part, "section", parent)
        if not parts and row.section_title:
            parent = add(row.section_title, row.section_title, "section", None)
        if row.content_kind in {"table", "figure"} and (parts or row.section_title):
            label = row.section_title or SECTION_PATH_SEPARATOR.join(parts)
            add(
                f"{label} ({row.content_kind})",
                row.section_title or parts[-1],
                _entity_type_for_content_kind(row.content_kind),
                parent,
            )
    # 文書の題名だけの段が先頭に続くなら飛ばし、文書の直下を章にする。
    while len(tree.roots) == 1 and tree.nodes[tree.roots[0]].children:
        only = tree.nodes.pop(tree.roots[0])
        tree.roots = only.children
        for child in only.children:
            tree.nodes[child].parent_id = None
    return tree
