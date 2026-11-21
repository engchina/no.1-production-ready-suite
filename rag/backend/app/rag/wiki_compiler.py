"""Wiki の原文付き語境を背景で構築する。完了した小単位は再開時に再利用する。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from itertools import groupby
from pathlib import Path
from typing import Any, Protocol

from pr_backend_core.bounded_calls import (
    Attempt,
    BoundedCaller,
    BoundedCallError,
    ErrorKind,
    classify_error,
)
from pr_backend_core.observability.model_purpose import async_call_with_model_purpose
from pydantic import ValidationError
from rag_parser_core.oci_enterprise_ai import (
    EnterpriseAiIncompleteResponseError,
    sdk_retries_disabled,
)

from app.rag.wiki_models import (
    WIKI_SCHEMA_VERSION,
    WikiContext,
    WikiPage,
    WikiUnit,
    context_schema,
    fingerprint,
)
from app.rag.wiki_packet import WikiInputTooLarge, WikiRequest, WikiUnitValidationError

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
    max_input_chars: int,
    batch_size: int,
    saved: Sequence[WikiPage] = (),
    authorize: Callable[[], Awaitable[None]],
    checkpoint: Callable[[Sequence[WikiPage]], Awaitable[None]],
    system_prompt: str = WIKI_CONTEXT_PROMPT,
    caller: BoundedCaller | None = None,
    prepare: Callable[[Sequence[WikiUnit], bool], WikiRequest] | None = None,
    preparation_fingerprint: str = "",
    oversized_unit: Callable[[WikiUnit, str, int | None, int], Awaitable[WikiPage]] | None = None,
) -> list[WikiPage]:
    """各出力を検証して保存する。

    切断・JSON・schema の不正は対象を二分して小さい出力で取り直す（#4977）。
    単一対象の形式の不正と一時的な呼び出しの失敗は、共通の実行器の予算内で再試行する。
    """
    if not units or batch_size < 1:
        return []
    source = fingerprint([unit.model_dump() for unit in units])
    contract = [WIKI_SCHEMA_VERSION, source, system_prompt]
    if prepare is not None:
        if not preparation_fingerprint:
            raise ValueError("構築する入力契約と原文対応の指紋が必要です。")
        contract.append(preparation_fingerprint)
    version = fingerprint(contract)
    expected = {unit.unit_id: unit for unit in units}
    pages = {
        page.unit.unit_id: page
        for page in saved
        if page.input_fingerprint == version
        and page.unit.unit_id in expected
        and page.unit == expected[page.unit.unit_id]
    }

    async def save_large(unit: WikiUnit, previous_size: int | None) -> None:
        if oversized_unit is None:
            raise WikiInputTooLarge("原文の段落と文の境界を確かめられません。")
        await authorize()
        page = await oversized_unit(unit, version, previous_size, max_input_chars)
        if (
            page.unit != unit
            or page.input_fingerprint != version
            or page.navigation.unit_id != unit.unit_id
        ):
            raise ValueError("大きな単位の案内が現在の原文または入力と一致しません。")
        await authorize()
        await checkpoint([page])
        pages[unit.unit_id] = page

    async def split(targets: Sequence[WikiUnit], *, smaller_than: int | None = None) -> None:
        middle = len(targets) // 2
        await batch(targets[:middle], smaller_than=smaller_than)
        await batch(targets[middle:], smaller_than=smaller_than)

    async def batch(targets: Sequence[WikiUnit], *, smaller_than: int | None = None) -> None:
        missing = [unit for unit in targets if unit.unit_id not in pages]
        if not missing:
            return
        request = None
        if prepare is not None:
            try:
                request = prepare(missing, smaller_than is None)
                if smaller_than is not None and request.estimated_tokens >= smaller_than:
                    raise WikiInputTooLarge("要求の大きさを減らせません。")
            except WikiInputTooLarge:
                if len(missing) == 1:
                    if oversized_unit is None:
                        raise
                    await save_large(missing[0], smaller_than)
                    return
                await split(missing, smaller_than=smaller_than)
                return
            schema, text = request.schema, request.text
        else:
            try:
                data = build_input(units, missing, max_chars=max_input_chars)
            except ValueError:
                if len(missing) == 1:
                    raise
                await split(missing)
                return
            schema = context_schema([unit.unit_id for unit in missing], list(expected))
            text = json.dumps(data, ensure_ascii=False)

        async def once(attempt: Attempt | None = None) -> list[WikiContext] | None:
            await authorize()
            try:
                with sdk_retries_disabled():
                    raw = await async_call_with_model_purpose(
                        "document.extract",
                        model.generate,
                        "",
                        text,
                        system_prompt=system_prompt,
                        response_schema=schema.model_json_schema(),
                        response_schema_name="wiki_contexts",
                    )
            except EnterpriseAiIncompleteResponseError:
                if len(missing) == 1:
                    raise
                return None
            try:
                if request is not None:
                    return request.resolve(raw, allow_partial=True)
                start = raw.find("{")
                parsed, _ = json.JSONDecoder().raw_decode(raw[start:] if start >= 0 else raw)
                return schema.model_validate(parsed).items
            except (json.JSONDecodeError, ValidationError) as error:
                # 複数なら分割し、単一なら形式の誤りを実行器に返して同じ入力だけを再試行する。
                if len(missing) == 1:
                    if caller is not None:
                        raise WikiUnitValidationError(missing[0].unit_id) from error
                    raise
                return None

        try:
            items = await (caller.call(once, label="wiki_build") if caller else once())
        except Exception as exc:
            kind = exc.kind if isinstance(exc, BoundedCallError) else classify_error(exc).kind
            if request is None or kind != ErrorKind.TOO_LARGE:
                raise
            if request.optional_count:
                await batch(missing, smaller_than=request.estimated_tokens)
            elif len(missing) > 1:
                await split(missing, smaller_than=request.estimated_tokens)
            elif oversized_unit is not None:
                await save_large(missing[0], request.estimated_tokens)
            else:
                raise
            return
        if items is None:
            await split(missing, smaller_than=smaller_than)
            return
        completed = [
            WikiPage(unit=expected[item.unit_id], navigation=item, input_fingerprint=version)
            for item in items
        ]
        await authorize()
        await checkpoint(completed)
        pages.update((page.unit.unit_id, page) for page in completed)
        # 一意で妥当な項目がある場合だけここへ来る。残りがある限り対象数は必ず減る。
        if len(completed) < len(missing):
            await batch(
                [unit for unit in missing if unit.unit_id not in pages],
                smaller_than=smaller_than,
            )

    if prepare is None:
        groups = [units]
    else:
        groups = [
            list(group) for _, group in groupby(units, lambda u: (u.document_id, u.chunk_set_id))
        ]
    for group in groups:
        for offset in range(0, len(group), batch_size):
            await batch(group[offset : offset + batch_size])
    await authorize()
    return [pages[unit.unit_id] for unit in units]
