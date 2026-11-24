"""現在版の原文と案内先を請求単位に載せ、保存時に実 ID へ戻す。"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from itertools import chain
from typing import Any, get_args

from pydantic import ValidationError

from app.rag.wiki_models import (
    WikiContext,
    WikiContexts,
    WikiUnit,
    context_schema,
    fingerprint,
)
from app.rag.wiki_navigation import Handles, handle_pattern
from app.rag.wiki_paragraphs import Paragraph


class WikiInputTooLarge(ValueError):
    """必須の情報だけで完全な要求の予算を超えた。"""


class WikiUnitValidationError(ValueError):
    """単一対象の応答の検証失敗。追跡には元の単位 ID のハッシュを使う。"""

    def __init__(self, unit_id: str) -> None:
        super().__init__("Wiki の単一対象の応答が形式に一致しません。")
        self.unit_id_hash = fingerprint(unit_id)


@dataclass(frozen=True)
class SourceContext:
    document_id: str
    chunk_set_id: str
    unit_source_hash: str
    text: str
    path: tuple[str, ...]
    ranges: tuple[tuple[int, int], ...]
    paragraphs: tuple[Paragraph, ...] = ()

    def verify(self, unit: WikiUnit) -> None:
        if (self.document_id, self.chunk_set_id, self.unit_source_hash) != (
            unit.document_id,
            unit.chunk_set_id,
            unit.source_hash,
        ) or unit.source_hash != fingerprint(unit.text):
            raise ValueError("補足文脈の版・原文のハッシュが一致しません。")
        if not self.ranges:
            raise ValueError("対象の原文範囲がありません。")
        parts = []
        previous_start = end = 0
        overlaps = False
        for a, b in self.ranges:
            if not previous_start <= a < b <= len(self.text):
                raise ValueError("原文範囲の順序または境界が一致しません。")
            overlaps |= a < end
            parts.append(self.text[a:b])
            previous_start, end = a, max(end, b)
        if overlaps and (
            len(parts) != len(unit.source_chunk_ids)
            or any(
                fingerprint(part) != unit.source_chunk_hashes.get(cid)
                for part, cid in zip(parts, unit.source_chunk_ids, strict=True)
            )
        ):
            raise ValueError("重なる範囲の各構成原文のハッシュが一致しません。")
        # WikiStore が仮想親を構成原文から組み直した区切りと同じ順序を保つ。
        if "\n\n".join(parts) != unit.text:
            raise ValueError("段落の対象範囲が元の単位の全文と一致しません。")


@lru_cache(maxsize=128)
def local_schema(required: int, visible: int) -> type[WikiContexts]:
    handles = [f"U{i + 1}" for i in range(visible)]
    return context_schema(handles[:required], handles)


@dataclass(frozen=True)
class WikiRequest:
    text: str
    schema: type[WikiContexts]
    handles: Handles
    estimated_tokens: int
    optional_count: int
    source_chars: int
    headings: dict[str, str]

    def resolve(self, raw: str, *, allow_partial: bool = False) -> list[WikiContext]:
        body = raw.lstrip()
        if body.startswith("```") and "\n" in body:
            body = body.split("\n", 1)[1].lstrip()
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(body)
        except json.JSONDecodeError:
            # 説明文に続く JSON は従来どおり読める。壊れた配列・オブジェクトの
            # 内側から都合のよい結果だけを取り出さない。
            start = body.find("{")
            if body.startswith(("{", "[")) or start <= 0:
                raise
            value, _ = decoder.raw_decode(body[start:])
        try:
            items = self.schema.model_validate(value).items
        except ValidationError:
            if (
                not allow_partial
                or not isinstance(value, dict)
                or set(value) != {"items"}
                or not isinstance(value["items"], list)
            ):
                raise
            rows = value["items"]
            if any(
                not isinstance(row, dict) or not isinstance(row.get("unit_id"), str) for row in rows
            ):
                raise
            counts = Counter(row["unit_id"] for row in rows)
            # 同じ応答用 schema の項目型には対象 ID と依存先 ID の両方の制約がある。
            item_type: type[WikiContext] = get_args(self.schema.model_fields["items"].annotation)[0]
            items = []
            for row in rows:
                if counts[row["unit_id"]] != 1:
                    continue
                try:
                    items.append(item_type.model_validate(row))
                except ValidationError:
                    continue
            if not items:
                raise
        pattern = handle_pattern(self.headings) if self.headings else None
        properties = WikiContext.model_json_schema()["properties"]

        def render(s: str, limit: int) -> str:
            if pattern is None:
                return s
            full = pattern.sub(lambda m: self.headings[m.group(0)], s)
            if len(full) <= limit:
                return full
            # 原文にある短い名前は headings から除外済み。モデルが返した他の文を
            # すべて残し、空いた文字数を挿入する見出しだけで分ける。
            matches = list(pattern.finditer(s))
            room = limit - len(s)
            parts: list[str] = []
            cursor = 0
            for i, match in enumerate(matches):
                handle = match.group(0)
                heading = self.headings[handle]
                width = len(handle) + room // (len(matches) - i)
                if len(heading) > width:
                    title = heading.removeprefix("「").removesuffix("」")
                    heading = (
                        f"「{title[: width - 3]}…」" if width >= 4 else title[: width - 1] + "…"
                    )
                room += len(handle) - len(heading)
                parts.extend((s[cursor : match.start()], heading))
                cursor = match.end()
            parts.append(s[cursor:])
            return "".join(parts)

        result = []
        for item in items:
            (uid,) = self.handles.real([item.unit_id])
            dependencies = []
            for dep in item.dependencies:
                (dest,) = self.handles.real([dep.unit_id])
                dependencies.append(dep.model_copy(update={"unit_id": dest}))
            result.append(
                WikiContext(
                    unit_id=uid,
                    context=render(item.context, properties["context"]["maxLength"]),
                    possible_questions=[
                        render(s, properties["possible_questions"]["items"]["maxLength"])
                        for s in item.possible_questions
                    ],
                    dependencies=dependencies,
                    unresolved=[
                        render(s, properties["unresolved"]["items"]["maxLength"])
                        for s in item.unresolved
                    ],
                    references=item.references,
                )
            )
        return result


def prepare_request(
    targets: Sequence[WikiUnit],
    optional: Sequence[WikiUnit],
    *,
    scope: Mapping[str, WikiUnit],
    source: Mapping[str, SourceContext],
    measure: Callable[[str, dict[str, Any]], int],
    max_input_tokens: int,
    max_source_chars: int | None = None,
) -> WikiRequest:
    if not targets:
        raise ValueError("対象の原文がありません。")
    first = targets[0]
    members = set()
    selected = []
    for unit in chain(targets, optional):
        if scope.get(unit.unit_id) != unit or (unit.document_id, unit.chunk_set_id) != (
            first.document_id,
            first.chunk_set_id,
        ):
            raise ValueError("対象の文書・原文の版・実体と一致しません。")
        if unit.unit_id in members:
            raise ValueError("同じ単位が重複しています。")
        members.add(unit.unit_id)
        selected.append(unit)

    def make(end: int) -> WikiRequest:
        h = Handles()
        outline: list[tuple[str, str, tuple[str, ...]]] = []
        original: list[tuple[str, str]] = []
        ranges: list[tuple[str, tuple[tuple[int, int], ...], str]] = []
        ids: list[str] = []
        for unit in selected[:end]:
            name = h.short("U", unit.unit_id)
            ids.append(name)
            bound = source.get(unit.unit_id)
            boundary = "source_paragraphs" if bound is not None else "indexed_unit"
            if bound is None:
                bound = SourceContext(
                    unit.document_id,
                    unit.chunk_set_id,
                    unit.source_hash,
                    unit.text,
                    tuple(unit.section_path),
                    ((0, len(unit.text)),),
                )
            bound.verify(unit)
            outline.append((name, bound.path[-1] if bound.path else unit.title, bound.path))
            original.append((name, bound.text))
            ranges.append((name, bound.ranges, boundary))
        data = {
            "document_complete": end == len(scope),
            "outline_columns": ["unit_id", "title", "section_path"],
            "outline": outline,
            "original_columns": ["unit_id", "original_text"],
            "original_units": original,
            "range_columns": [
                "unit_id",
                "source_ranges_in_original_units",
                "boundary_origin",
            ],
            "target_ranges": ranges,
            "target_unit_ids": ids[: len(targets)],
        }
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        schema = local_schema(len(targets), end)
        headings = {name: f"「{title}」" for name, title, _ in outline}
        material = json.dumps(
            [
                [title, path, body]
                for (_, title, path), (_, body) in zip(outline, original, strict=True)
            ],
            ensure_ascii=False,
        )
        for literal in set(handle_pattern(headings).findall(material)):
            headings.pop(literal)
        return WikiRequest(
            text,
            schema,
            h,
            measure(text, schema.model_json_schema()),
            end - len(targets),
            sum(len(t) for _, t in original),
            headings,
        )

    def fits(request: WikiRequest) -> bool:
        return request.estimated_tokens <= max_input_tokens and (
            max_source_chars is None or request.source_chars <= max_source_chars
        )

    required = make(len(targets))
    if not fits(required):
        raise WikiInputTooLarge("対象の原文と応答 schema を含む要求が入力上限を超えました。")
    # 候補は原文への既知の参照、近接位置など、呼出側が確定した優先順を保つ。
    lo, hi = len(targets), len(selected)
    best = required
    while lo < hi:
        end = (lo + hi + 1) // 2
        possible = make(end)
        if fits(possible):
            best, lo = possible, end
        else:
            hi = end - 1
    return best
