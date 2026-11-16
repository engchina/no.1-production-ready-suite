"""構造化出力の応答 schema の項目の説明を、指示の文の Step にして渡す（#5230・#5238）。

項目の意味（Pydantic の ``Field(description=...)``）の正本は応答 schema のまま変えない。
Responses API の ``text.format`` の JSON Schema の説明は、モデルによっては読まれず、出力の形の
制約にだけ使われる。そこで送る時に説明を指示の文の最後の Step に移し、schema からは説明を外す。
全モデルに同じ入力を送り、同じ説明を 2 回渡さない（モデルごとの分岐はしない）。
3 製品の Enterprise AI の adapter（RAG・NL2SQL・Agent）が、schema を送る直前に使う。

型の説明（model の docstring 由来の説明）は実装の説明なので、モデルへ渡さない
（画像の説明の schema と同じ扱い。#4782）。
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from typing import Any

from pr_backend_core.prompt_steps import continue_prompt_steps

FIELD_GUIDE_HEADING = "## Step 1: 応答の各項目を書く"
FIELD_GUIDE_LEAD = "応答の JSON の各項目は、次の説明に従って書く。"

# 値が schema（または schema の並び・名前から schema への対応）の key。ほかの key
# （enum・default・const など）の値はデータなので、中の ``description`` を説明として扱わない。
_SCHEMA_VALUE_KEYS = ("items", "additionalProperties", "not", "if", "then", "else", "contains")
_SCHEMA_LIST_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")
_SCHEMA_MAP_KEYS = ("properties", "$defs", "definitions", "patternProperties")


def _without_descriptions(node: Any) -> Any:
    """schema の各所の ``description`` を外した写し。

    ``properties`` の key（項目の名前）は外さない。
    """
    if not isinstance(node, Mapping):
        return node
    stripped: dict[str, Any] = {}
    for key, value in node.items():
        if key == "description":
            continue
        if key in _SCHEMA_MAP_KEYS and isinstance(value, Mapping):
            stripped[key] = {name: _without_descriptions(child) for name, child in value.items()}
        elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
            stripped[key] = [_without_descriptions(child) for child in value]
        elif key in _SCHEMA_VALUE_KEYS:
            stripped[key] = _without_descriptions(value)
        else:
            stripped[key] = copy.deepcopy(value)
    return stripped


def _definition(root: Mapping[str, Any], ref: str) -> tuple[str, Mapping[str, Any]] | None:
    for prefix in ("#/$defs/", "#/definitions/"):
        if ref.startswith(prefix):
            name = ref.removeprefix(prefix)
            found = (root.get(prefix[2:-1]) or {}).get(name)
            return (ref, found) if isinstance(found, Mapping) else None
    return None


def _field_descriptions(
    root: Mapping[str, Any], node: Any, path: str, *, field: bool, refs: frozenset[str]
) -> Iterator[tuple[str, str]]:
    """項目の説明を schema の項目の順に返す（``(項目の位置の表記, 説明)``）。

    ``field`` は、``node`` が項目そのもの（``properties`` の値か、その中の配列の要素・選択肢）か。
    ``$ref`` でたどった定義の直下の説明は型の説明なので返さない。循環する ``$ref`` は
    1 度だけたどる。
    """
    if not isinstance(node, Mapping):
        return
    description = node.get("description")
    if field and path and isinstance(description, str) and description.strip():
        # 選択肢ごとに固定の値を持つ項目（判別の値）は、どの値の説明かを位置に添える。
        yield (
            (
                f"`{path}` = {json.dumps(node['const'], ensure_ascii=False)}"
                if "const" in node
                else f"`{path}`"
            ),
            description.strip(),
        )
    ref = node.get("$ref")
    if isinstance(ref, str) and ref not in refs and (found := _definition(root, ref)) is not None:
        yield from _field_descriptions(root, found[1], path, field=False, refs=refs | {found[0]})
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        for name, child in properties.items():
            yield from _field_descriptions(
                root, child, f"{path}.{name}" if path else str(name), field=True, refs=refs
            )
    items = node.get("items")
    if isinstance(items, Mapping):
        yield from _field_descriptions(root, items, f"{path}[]", field=True, refs=refs)
    additional = node.get("additionalProperties")
    if isinstance(additional, Mapping):
        yield from _field_descriptions(root, additional, f"{path}.*", field=True, refs=refs)
    for key in ("anyOf", "oneOf", "allOf"):
        for child in node.get(key) or ():
            yield from _field_descriptions(root, child, path, field=True, refs=refs)


def schema_field_guide(schema: Mapping[str, Any]) -> list[tuple[tuple[str, ...], str]]:
    """応答 schema の項目の説明を、``(項目の位置の表記の並び, 説明)`` の並びにする。

    並びは schema の項目の順（最初に現れた位置の順）。同じ説明は 1 行にまとめ、その説明を持つ
    位置をすべて並べる（同じ定義を複数の項目が使うときに、説明を繰り返さない）。
    """
    grouped: dict[str, list[str]] = {}
    for path, description in _field_descriptions(schema, schema, "", field=False, refs=frozenset()):
        paths = grouped.setdefault(description, [])
        if path not in paths:
            paths.append(path)
    return [(tuple(paths), description) for description, paths in grouped.items()]


def field_guide_step(preceding: str, guide: list[tuple[tuple[str, ...], str]]) -> str:
    """項目の説明の Step。番号は ``preceding``（指示の文）の最後の Step の次。説明が無ければ空。"""
    if not guide:
        return ""
    lines = [FIELD_GUIDE_HEADING, FIELD_GUIDE_LEAD]
    lines.extend(f"- {'、'.join(paths)}: {description}" for paths, description in guide)
    return continue_prompt_steps(preceding, "\n".join(lines))


def with_schema_field_guide(
    instructions: str, schema: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    """指示の文の最後に項目の説明の Step を足した文と、説明を外した schema を返す。

    説明が 1 つも無い schema では、指示の文はそのまま（schema の型の説明だけを外す）。
    """
    step = field_guide_step(instructions, schema_field_guide(schema))
    stripped = _without_descriptions(schema)
    if not step:
        return instructions, stripped
    head = instructions.rstrip()
    return (f"{head}\n\n{step}" if head else step), stripped
