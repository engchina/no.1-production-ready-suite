"""単独で入力枠を超える原文を実段落・文末に分け、同じ原文の案内一件へまとめる。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

from app.rag.wiki_models import (
    WikiContext,
    WikiDependency,
    WikiPage,
    WikiUnit,
)
from app.rag.wiki_navigation import Handles, handle_pattern
from app.rag.wiki_packet import SourceContext, WikiInputTooLarge, WikiRequest, local_schema
from app.rag.wiki_paragraphs import Paragraph, pack


@dataclass(frozen=True)
class FragmentNavigation:
    intervals: tuple[tuple[int, int], ...]
    navigation: WikiContext


async def compile_large_unit(
    unit: WikiUnit,
    source: SourceContext,
    paragraphs: Sequence[Paragraph],
    *,
    limit: int,
    max_source_chars: int,
    measure: Callable[[str, dict[str, Any]], int],
    generate: Callable[[WikiRequest], Awaitable[str]],
    authorize: Callable[[], Awaitable[Mapping[str, WikiUnit]]],
    checkpoint: Callable[[WikiPage], Awaitable[None]] | None = None,
    input_fingerprint: str,
    related: Sequence[WikiUnit] = (),
) -> tuple[WikiPage, tuple[tuple[int, int], ...], tuple[WikiDependency, ...]]:
    source.verify(unit)
    options = dict.fromkeys([unit.unit_id, *(u.unit_id for u in related)])
    if len(options) != 1 + len(related) or any(
        (u.document_id, u.chunk_set_id) != (unit.document_id, unit.chunk_set_id) for u in related
    ):
        raise ValueError("参照候補の文書または版が一致しません。")
    short_names = handle_pattern(f"U{i + 1}" for i in range(len(options)))
    source_literals = {m for s in (source.text, *source.path) for m in short_names.findall(s)}
    title_literals = {
        u.unit_id: {m for s in (u.title, *u.section_path) for m in short_names.findall(s)}
        for u in (unit, *related)
    }

    async def check_scope() -> None:
        now = await authorize()
        if any(now.get(u.unit_id) != u for u in [unit, *related]):
            raise PermissionError("原文または参照候補の権限・版・単位が変わりました。")

    def request(
        visible: Sequence[WikiUnit] = (),
        *,
        span: tuple[int, int] | None = None,
        parts: Sequence[FragmentNavigation] = (),
    ) -> WikiRequest:
        handles = Handles()
        names = {u.unit_id: handles.short("U", u.unit_id) for u in (unit, *visible)}
        uid = names[unit.unit_id]
        titles = {names[u.unit_id]: u.title for u in (unit, *visible)}
        schema = local_schema(1, len(names))
        payload: dict[str, Any] = {"target_unit_ids": [uid], "related_titles": titles}
        if span is not None:
            a, b = span
            ranges = [
                [max(a, left) - a, min(b, right) - a]
                for left, right in source.ranges
                if left < b and right > a
            ]
            payload.update(
                purpose="同じ原文単位の一部から、範囲が示す内容に沿って検索案内を作る。",
                original_columns=["unit_id", "text", "target_ranges"],
                original_units=[[uid, source.text[a:b], ranges]],
            )
        else:
            payload.update(
                purpose="全ての部分案内を統合し、重複を除いた一件の検索案内を作る。",
                fragment_navigation=[
                    {
                        **p.navigation.model_dump(exclude={"unit_id", "dependencies"}),
                        "dependencies": [
                            [names[d.unit_id], d.relation]
                            for d in p.navigation.dependencies
                            if d.unit_id in names
                        ],
                    }
                    for p in parts
                ],
            )
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        count = measure(text, schema.model_json_schema())
        literals = source_literals.union(*(title_literals[key] for key in names))
        return WikiRequest(
            text,
            schema,
            handles,
            count,
            len(visible),
            0,
            {k: f"「{title}」" for k, title in titles.items() if k not in literals},
        )

    async def produce(make: Callable[[Sequence[WikiUnit]], WikiRequest]) -> WikiContext:
        selected: list[WikiUnit] = []
        req = make(selected)
        if req.estimated_tokens > limit:
            raise WikiInputTooLarge("必須の案内要求が入力上限を超えました。")
        # 原文や統合の大きさを先に決める。長い見出しを省いても後続の候補は試す。
        for candidate in related:
            trial = make([*selected, candidate])
            if trial.estimated_tokens <= limit:
                req = trial
                selected.append(candidate)
        await check_scope()
        (output,) = req.resolve(await generate(req))
        if output.unit_id != unit.unit_id:
            raise ValueError("別の原文の案内が返されました。")
        return output

    # 生成前に分け方を確定し、巨大な単文で後から部分結果だけ残ることを防ぐ。
    intervals = pack(
        source.text,
        paragraphs,
        lambda a, b: b - a <= max_source_chars and request(span=(a, b)).estimated_tokens <= limit,
    )
    fragments = []
    for a, b in intervals:
        if any(left < b and right > a for left, right in source.ranges):
            fragments.append(
                FragmentNavigation(((a, b),), await produce(partial(request, span=(a, b))))
            )
    if not fragments:
        raise ValueError("対象の原文を覆う部分がありません。")
    sent = tuple(interval for f in fragments for interval in f.intervals)
    deps = {(d.unit_id, d.relation): d for f in fragments for d in f.navigation.dependencies}
    for a, b in source.ranges:
        if sum(max(0, min(b, right) - max(a, left)) for left, right in sent) != b - a:
            raise ValueError("元の原文範囲を欠けなく覆っていません。")

    while len(fragments) > 1:
        groups: list[list[FragmentNavigation]] = []
        current: list[FragmentNavigation] = []
        for f in fragments:
            if current and request(parts=[*current, f]).estimated_tokens > limit:
                groups.append(current)
                current = []
            current.append(f)
        groups.append(current)
        if len(groups) == len(fragments):
            raise WikiInputTooLarge("二つの部分案内をまとめる完全な要求が入力上限を超えました。")
        reduced = []
        for group in groups:
            if len(group) == 1:
                reduced.append(group[0])
            else:
                reduced.append(
                    FragmentNavigation(
                        tuple(i for f in group for i in f.intervals),
                        await produce(partial(request, parts=group)),
                    )
                )
        fragments = reduced
    await check_scope()
    nav = fragments[0].navigation
    chosen = {
        (d.unit_id, d.relation): d for d in nav.dependencies if (d.unit_id, d.relation) in deps
    }
    # 既存の WikiPage の上限まで確認済み関連を残し、はみ出す分も呼出側の監査へ返す。
    ordered = list((chosen | deps).values())
    nav = WikiContext.model_validate(
        {**nav.model_dump(), "dependencies": [d.model_dump() for d in ordered[:16]]}
    )
    page = WikiPage(unit=unit, navigation=nav, input_fingerprint=input_fingerprint)
    if checkpoint is not None:
        await checkpoint(page)
    return page, sent, tuple(ordered[16:])
