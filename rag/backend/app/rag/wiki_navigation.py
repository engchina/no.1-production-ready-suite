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


def terminal_sections(
    pages: Sequence[WikiPage],
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    """子章節のある広い祖先は読取対象にせず、末端章節は全単位を含める。"""
    keys = {
        (p.unit.document_id, p.unit.chunk_set_id, tuple(p.unit.section_path))
        for p in pages
        if p.unit.section_path
    }
    ancestors = {
        (did, saved, path[:depth]) for did, saved, path in keys for depth in range(1, len(path))
    }
    leaves = keys - ancestors
    members: dict[str, list[str]] = {}
    cards: dict[str, dict[str, Any]] = {}
    for page in pages:
        unit = page.unit
        key = unit.document_id, unit.chunk_set_id, tuple(unit.section_path)
        if key not in leaves:
            continue
        sid = fingerprint([unit.document_id, unit.chunk_set_id, unit.section_path])
        members.setdefault(sid, []).append(unit.unit_id)
        cards[sid] = {"section_id": sid, "document_id": unit.document_id, "path": unit.section_path}
    return members, cards
