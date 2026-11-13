"""検索計画の短いハンドルと、最小の完全な章節を作る。"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from app.rag.wiki_models import WikiPage, fingerprint

IDENTIFIER_FIELDS = {
    "document_id": "document_ids",
    "unit_id": "unit_ids",
    "section_id": "section_ids",
}


class PlanningHandles:
    """要求内だけで原文 ID を短縮し、質問・本文の文字列は変えない。"""

    def __init__(self, data: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
        self.forward: dict[str, dict[str, str]] = {key: {} for key in IDENTIFIER_FIELDS.values()}

        def add(field: str, value: object) -> None:
            if isinstance(value, str) and value not in self.forward[field]:
                prefix = field[0].upper()
                self.forward[field][value] = f"{prefix}{len(self.forward[field]) + 1}"

        for field, prop in schema.get("properties", {}).items():
            if field in self.forward:
                for value in prop.get("items", {}).get("enum", []):
                    add(field, value)
                if "const" in prop.get("items", {}):
                    add(field, prop["items"]["const"])

        def visit(value: object) -> None:
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if key in IDENTIFIER_FIELDS:
                        add(IDENTIFIER_FIELDS[key], item)
                    elif key in self.forward and isinstance(item, list):
                        for identity in item:
                            add(key, identity)
                    else:
                        visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(data)
        self.reverse = {
            field: {v: k for k, v in values.items()} for field, values in self.forward.items()
        }

    def encode_data(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if key in IDENTIFIER_FIELDS and isinstance(item, str):
                    result[key] = self.forward[IDENTIFIER_FIELDS[key]][item]
                elif key in self.forward and isinstance(item, list):
                    result[key] = [self.forward[key][v] for v in item]
                else:
                    result[key] = self.encode_data(item)
            return result
        if isinstance(value, list):
            return [self.encode_data(item) for item in value]
        return value

    def encode_schema(self, value: Mapping[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(dict(value))
        for field, prop in result.get("properties", {}).items():
            if field in self.forward and "enum" in prop.get("items", {}):
                prop["items"]["enum"] = [self.forward[field][v] for v in prop["items"]["enum"]]
            if field in self.forward and "const" in prop.get("items", {}):
                prop["items"]["const"] = self.forward[field][prop["items"]["const"]]
        return result

    def decode(self, value: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(value)
        for field, mapping in self.reverse.items():
            if field not in result:
                continue
            if not isinstance(result[field], list):
                raise ValueError("Wiki の選択 ID は配列で返してください。")
            if any(not isinstance(v, str) or v not in mapping for v in result[field]):
                raise ValueError("Wiki の選択 ID が候補にありません。")
            result[field] = [mapping[v] for v in result[field]]
        return result


def section_frontier(
    pages: Sequence[WikiPage], *, limit: int, ranked_ids: Mapping[str, int]
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    """原文全体を覆う章節の境界を予算内で細かくする。末尾の章節を捨てない。"""
    grouped: dict[tuple[str, str], list[WikiPage]] = {}
    for page in pages:
        grouped.setdefault((page.unit.document_id, page.unit.chunk_set_id), []).append(page)
    frontier = list(grouped.values())
    if len(frontier) > limit:
        raise ValueError("章節候補の上限は、選択した文書の保存版数以上にしてください。")

    def common_path(group: Sequence[WikiPage]) -> tuple[str, ...]:
        paths = [p.unit.section_path for p in group]
        shared: list[str] = []
        for column in zip(*paths, strict=False):
            if len(set(column)) != 1:
                break
            shared.append(column[0])
        return tuple(shared)

    while True:
        options = []
        for index, group in enumerate(frontier):
            prefix = common_path(group)
            children: dict[str | None, list[WikiPage]] = {}
            for page in group:
                path = page.unit.section_path
                child = path[len(prefix)] if len(path) > len(prefix) else None
                children.setdefault(child, []).append(page)
            if len(children) <= 1 or len(frontier) - 1 + len(children) > limit:
                continue
            rank = min(
                (ranked_ids.get(p.unit.unit_id, len(ranked_ids)) for p in group),
                default=len(ranked_ids),
            )
            options.append(((len(prefix), rank, index), index, list(children.values())))
        if not options:
            break
        _, index, replacements = min(options, key=lambda value: value[0])
        frontier[index : index + 1] = replacements

    members, cards = {}, {}
    for group in frontier:
        first = group[0].unit
        path = list(common_path(group))
        sid = fingerprint([first.document_id, first.chunk_set_id, path])
        members[sid] = [p.unit.unit_id for p in group]
        cards[sid] = {"section_id": sid, "document_id": first.document_id, "path": path}
    return members, cards
