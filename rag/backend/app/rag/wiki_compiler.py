"""Wiki の原文付き語境を背景で構築する。完了した小単位は再開時に再利用する。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pr_backend_core.bounded_calls import Attempt, BoundedCaller
from pr_backend_core.observability.model_purpose import async_call_with_model_purpose
from pydantic import ValidationError
from rag_parser_core.oci_enterprise_ai import (
    EnterpriseAiIncompleteResponseError,
    sdk_retries_disabled,
)

from app.rag.wiki_models import (
    WIKI_SCHEMA_VERSION,
    WikiContexts,
    WikiPage,
    WikiUnit,
    context_schema,
    fingerprint,
)

WIKI_CONTEXT_PROMPT = (Path(__file__).with_name("templates") / "wiki_build.txt").read_text(
    encoding="utf-8"
)


class WikiTextModel(Protocol):
    async def generate(
        self,
        prompt: str,
        context: str,
        *,
        system_prompt: str,
        instruction: str = "",
        response_schema: Mapping[str, Any] | None = None,
        response_schema_name: str = "structured_answer",
    ) -> str: ...


def build_input(
    units: Sequence[WikiUnit], targets: Sequence[WikiUnit], *, max_chars: int
) -> dict[str, object]:
    """原文単位を切らずに入力へ入れ、省いた単位は一覧と状態で区別する。"""
    chosen = {unit.unit_id: unit for unit in targets}
    used = sum(len(unit.text) for unit in chosen.values())
    if used > max_chars:
        raise ValueError("Wiki の原文単位が入力予算を超えています。")
    for unit in units:
        if unit.unit_id not in chosen and used + len(unit.text) <= max_chars:
            chosen[unit.unit_id] = unit
            used += len(unit.text)
    return {
        "document_complete": len(chosen) == len(units),
        "outline_columns": ["unit_id", "title", "section_path"],
        "outline": [[unit.unit_id, unit.title, unit.section_path] for unit in units],
        "original_columns": ["unit_id", "original_text"],
        "original_units": [[unit.unit_id, unit.text] for unit in units if unit.unit_id in chosen],
        "target_unit_ids": [unit.unit_id for unit in targets],
    }


async def compile_pages(
    units: Sequence[WikiUnit],
    *,
    model: WikiTextModel,
    model_id: str,
    max_input_chars: int,
    batch_size: int,
    saved: Sequence[WikiPage] = (),
    authorize: Callable[[], Awaitable[None]],
    checkpoint: Callable[[Sequence[WikiPage]], Awaitable[None]],
    system_prompt: str = WIKI_CONTEXT_PROMPT,
    caller: BoundedCaller | None = None,
) -> list[WikiPage]:
    """各出力を検証して保存する。

    切断・JSON・schema の不正は対象を二分して小さい出力で取り直す（#4977）。
    単一対象でも不正なら保存せずに失敗する。一時的な失敗（429・5xx・接続・時間切れ）は
    ``caller``（共通の上限つきの実行器）が再試行する（#5426）。
    """
    if not units or batch_size < 1:
        return []
    source = fingerprint([unit.model_dump() for unit in units])
    version = fingerprint([WIKI_SCHEMA_VERSION, source, model_id, system_prompt, max_input_chars])
    expected = {unit.unit_id: unit for unit in units}
    pages = {
        page.unit.unit_id: page
        for page in saved
        if page.input_fingerprint == version
        and page.unit.unit_id in expected
        and page.unit == expected[page.unit.unit_id]
    }

    async def split(targets: Sequence[WikiUnit]) -> None:
        middle = len(targets) // 2
        await batch(targets[:middle])
        await batch(targets[middle:])

    async def batch(targets: Sequence[WikiUnit]) -> None:
        missing = [unit for unit in targets if unit.unit_id not in pages]
        if not missing:
            return
        try:
            data = build_input(units, missing, max_chars=max_input_chars)
        except ValueError:
            if len(missing) == 1:
                raise
            await split(missing)
            return
        schema = context_schema([unit.unit_id for unit in missing], list(expected))
        await authorize()

        async def once(attempt: Attempt | None = None) -> str | None:
            # 出力の途切れは再試行せず、呼び出しの外で対象を二分する。
            try:
                with sdk_retries_disabled():
                    return await async_call_with_model_purpose(
                        "document.extract",
                        model.generate,
                        "",
                        json.dumps(data, ensure_ascii=False),
                        system_prompt=system_prompt,
                        response_schema=schema.model_json_schema(),
                        response_schema_name="wiki_contexts",
                    )
            except EnterpriseAiIncompleteResponseError:
                if len(missing) == 1:
                    raise
                return None

        raw = await (caller.call(once, label="wiki_build") if caller else once())
        if raw is None:
            await split(missing)
            return
        try:
            decoder = json.JSONDecoder()
            start = raw.find("{")
            parsed, _ = decoder.raw_decode(raw[start:] if start >= 0 else raw)
            output: WikiContexts = schema.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError):
            if len(missing) == 1:
                raise
            await split(missing)
            return
        completed = [
            WikiPage(unit=expected[item.unit_id], navigation=item, input_fingerprint=version)
            for item in output.items
        ]
        await authorize()
        await checkpoint(completed)
        pages.update((page.unit.unit_id, page) for page in completed)

    for offset in range(0, len(units), batch_size):
        await batch(units[offset : offset + batch_size])
    await authorize()
    return [pages[unit.unit_id] for unit in units]
