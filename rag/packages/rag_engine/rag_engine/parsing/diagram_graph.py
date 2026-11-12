"""隣接する接続段だけから共有分岐を投影する。ラベルの語の意味は解釈しない。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from rag_engine.parsing.vision_prompt_rules import MAX_DIAGRAM_EDGES


def normalize_graph(description: Mapping[str, Any]) -> dict[str, Any]:
    """端点のIDが不明・重複ならその接続だけ未確認にする。画像全体を破棄しない。"""
    nodes, edges = description.get("diagram_nodes"), description.get("diagram_edges")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes) or not isinstance(edges, list):
        return dict(description)
    if any(not isinstance(edge, dict) or "direction" not in edge for edge in edges):
        return dict(description)
    counts = Counter(node.get("id") for node in nodes)
    known = {identity for identity, count in counts.items() if identity and count == 1}
    normalized = []
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        source = source if source in known else None
        target = target if target in known else None
        normalized.append(
            {
                **edge,
                "source": source,
                "target": target,
                "direction": "uncertain" if source is None or target is None else edge.get("direction"),
            }
        )
    return {**description, "diagram_edges": normalized}


def graph_projection(description: Mapping[str, Any]) -> tuple[list[str], list[str]] | None:
    """新しいノード契約なら、確定した経路と未確認の接続を分ける。旧名称配列は従来の整形へ渡す。"""
    description = normalize_graph(description)
    raw_nodes = description.get("diagram_nodes")
    raw_edges = description.get("diagram_edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        return None
    if any(not isinstance(node, dict) for node in raw_nodes):
        return None
    if any(not isinstance(edge, dict) or "direction" not in edge for edge in raw_edges):
        return None
    if not raw_nodes and not raw_edges:
        return None
    nodes = {node["id"]: node for node in raw_nodes if node.get("id")}
    adjacent: dict[str, list[tuple[str, dict[str, Any], int]]] = {}
    uncertain: list[str] = []

    def name(identity: str | None) -> str:
        node = nodes.get(identity or "", {})
        return "●" if node.get("kind") == "junction" else str(node.get("label") or "?")

    def step(edge: Mapping[str, Any], target: str | None, *, directed: bool) -> str:
        label = f"［{edge['label']}］" if edge.get("label") else ""
        return f" ─{label}{'→' if directed else '─'} {name(target)}"

    for index, edge in enumerate(raw_edges):
        source, target = edge.get("source"), edge.get("target")
        direction = edge.get("direction")
        if direction == "uncertain" or source not in nodes or target not in nodes:
            uncertain.append(name(source) + " ?" + step(edge, target, directed=True))
            continue
        adjacent.setdefault(source, []).append((target, edge, index))
        if direction in {"none", "both"}:
            adjacent.setdefault(target, []).append((source, edge, index))

    paths: list[str] = []
    remaining = max(1, len(nodes) * len(raw_edges))
    # 接続を持つ始点だけを積む。候補が残るときだけ打切りを表示する。
    pending: list[tuple[str, str, frozenset[str], frozenset[int]]] = [
        (identity, name(identity), frozenset({identity}), frozenset())
        for identity, node in reversed(list(nodes.items()))
        if node.get("kind") != "junction" and adjacent.get(identity)
    ]
    while pending:
        if remaining <= 0:
            uncertain.append("…")
            break
        remaining -= 1
        current, text, visited, used = pending.pop()
        truncated = False
        for target, edge, index in adjacent.get(current, ()):
            if index in used:
                continue
            line = text + step(edge, target, directed=edge.get("direction") in {"forward", "both"})
            if nodes[target].get("kind") != "junction":
                if line in paths:
                    continue
                if len(paths) >= MAX_DIAGRAM_EDGES:
                    truncated = True
                    break
                paths.append(line)
            elif target not in visited:
                pending.append((target, line, visited | {target}, used | {index}))
        if truncated:
            uncertain.append("…")
            break
    return paths, uncertain
