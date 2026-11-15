"""質問の入力の安全チェックを、入口の最初に 1 回だけ行う（#5099）。

検索・チャット・MCP・FAQ の類似問・確認の候補の各入口は、質問を使うほかの処理（業務ガイド・FAQ の
照合、埋め込み、検索、LLM）より先にここを呼ぶ。通ったときは伏せた後の質問だけを後へ渡し、止めたと
きは後の処理へ進まない。回答の工程（``RagPipeline``）には結果を渡し、同じ質問を検査し直さない。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pr_system_settings.guardrails import GuardrailPolicy, GuardrailResult

from app.schemas.search import SearchRequest


async def check_question_text(text: str, settings: Any) -> GuardrailResult:
    """1 つの質問の文を検査する（無効なら検査せずに通す）。"""
    return await asyncio.to_thread(GuardrailPolicy(settings).validate_query, text)


def combine_question_results(query: GuardrailResult, original: GuardrailResult) -> GuardrailResult:
    """検索の質問と原文の 2 つの結果をまとめる（どちらかが止めれば止める）。

    ``sanitized_text`` は、通れば検索の質問の伏せた後の文、止めれば止めた側の文。
    """
    return GuardrailResult(
        allowed=query.allowed and original.allowed,
        sanitized_text=(
            query.sanitized_text
            if query.allowed and original.allowed
            else original.sanitized_text
            if query.allowed
            else query.sanitized_text
        ),
        findings=[*original.findings, *query.findings],
        backend_degraded=query.backend_degraded or original.backend_degraded,
    )


async def check_search_question(
    request: SearchRequest, settings: Any
) -> tuple[SearchRequest, GuardrailResult]:
    """検索の質問と、回答の対象の原文（``original_question``）を検査する。

    返す request は、通ったときだけ伏せた後の質問・原文に置き換える。結果は 2 つの検査をまとめた
    もの（どちらかが止めれば止める。``sanitized_text`` は検索の質問の伏せた後の文）。
    """
    policy = GuardrailPolicy(settings)
    query = await asyncio.to_thread(policy.validate_query, request.query)
    result = query
    original_text = request.original_question
    if query.allowed and original_text is not None and original_text != request.query:
        original = await asyncio.to_thread(policy.validate_query, original_text)
        result = combine_question_results(query, original)
        original_text = original.sanitized_text
    elif query.allowed and original_text is not None:
        original_text = query.sanitized_text
    if not result.allowed:
        return request, result
    return (
        request.model_copy(
            update={"query": query.sanitized_text, "original_question": original_text}
        ),
        result,
    )


__all__ = ["check_question_text", "check_search_question", "combine_question_results"]
