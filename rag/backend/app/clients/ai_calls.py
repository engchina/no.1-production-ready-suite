"""取込の AI の呼び出し（LLM・VLM・Embedding）の上限つきの並行の実行器を作る（#2002）。

共通の部品 ``pr_backend_core.bounded_calls`` に、RAG の設定（段ごとの並行の上限・待ちの上限・
件の失敗の扱い）と、RAG の client の例外の分類を渡す。取込の job の中では、実行器の
event（待ち・再試行・並行の数・成功）を job の進捗（``progress_json`` の ``calls``）へ写す。
待っている間も heartbeat の間隔で進捗が変わるため、#2003 の停滞の検出が待ちを停滞と誤らない。

SDK の再試行との分担: この実行器を通す呼び出しは SDK の再試行を切る（OCI Enterprise AI の
openai SDK は ``sdk_retries_disabled``、OCI Generative AI の SDK は ``NoneRetryStrategy``）。
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Iterator
from typing import Literal

from pr_backend_core.bounded_calls import (
    BoundedCaller,
    BoundedCallPolicy,
    CallEvent,
    ErrorKind,
    chain_classifiers,
)

AiCallStage = Literal["vision", "field_extraction", "navigation_summary", "embedding", "rerank"]

STAGE_LABELS: dict[str, str] = {
    "vision": "図・表の説明（Vision）",
    "field_extraction": "メタデータ／項目抽出",
    "navigation_summary": "ナビゲーション要約",
    "embedding": "埋め込み（Embedding）",
    "rerank": "リランク",
}

# 空の応答・件数の食い違いは「出力の形の誤り」（今の schema の再試行と同じく回数を絞って再試行）。
_INVALID_OUTPUT_MARKERS = (
    "回答 text がありません",
    "embeddings がありません",
    "document_ranks がありません",
    "embedding の件数が一致しません",
)


def rag_error_kind(exc: BaseException) -> ErrorKind | None:
    """RAG の client の例外のうち、共通の分類では決まらないものの種類。"""
    name = type(exc).__name__
    if name in {"OciGenAiConfigError", "OciPrivateKeyPassPhraseRequiredError"}:
        return ErrorKind.AUTHENTICATION
    message = str(exc)
    if isinstance(exc, ValueError) and any(marker in message for marker in _INVALID_OUTPUT_MARKERS):
        return ErrorKind.INVALID_OUTPUT
    return None


def _concurrency(settings: object, stage: str) -> int:
    name = {
        "vision": "rag_vision_concurrency",
        "navigation_summary": "rag_navigation_summary_concurrency",
        "embedding": "rag_embedding_concurrency",
    }.get(stage)
    if name is None:
        return 1
    return max(1, int(getattr(settings, name, 1) or 1))


def ai_call_policy(settings: object, stage: str) -> BoundedCallPolicy:
    """段の実行器の設定（並行の上限・待ちの上限・最後の再試行の待ち・件の失敗の扱い）。"""
    return BoundedCallPolicy(
        max_concurrency=_concurrency(settings, stage),
        backoff_max_seconds=float(getattr(settings, "rag_ai_call_backoff_max_seconds", 60.0)),
        final_retry_delay_seconds=float(
            getattr(settings, "rag_ai_call_final_retry_delay_seconds", 30.0)
        ),
        fail_job_on_item_error=bool(getattr(settings, "rag_ai_call_fail_job_on_item_error", False)),
    )


async def _sleep(seconds: float) -> None:
    """再試行の待ち（テストは差し替えて実時間で待たない）。"""
    await asyncio.sleep(seconds)


def _random() -> float:
    """jitter の乱数（暗号の用途ではない）。"""
    return random.random()  # nosec B311 - backoff の jitter で、暗号の用途ではない


def make_ai_caller(settings: object, stage: str) -> BoundedCaller:
    """段の実行器を作る（1 回の段・1 つの job ごと）。"""
    return BoundedCaller(
        f"rag.{stage}",
        ai_call_policy(settings, stage),
        label=STAGE_LABELS.get(stage, stage),
        classifier=chain_classifiers(rag_error_kind),
        sleep=lambda seconds: _sleep(seconds),
        random=lambda: _random(),
    )


@contextlib.contextmanager
def ingestion_ai_calls(
    settings: object, stage: str, *, total: int | None = None
) -> Iterator[BoundedCaller]:
    """取込の段の実行器。job の中なら、実行器の状態を job の進捗（``calls``）へ写す。"""
    from app.rag.ingestion_progress import active_ingestion_progress

    caller = make_ai_caller(settings, stage)
    progress = active_ingestion_progress()
    if progress is None:
        yield caller
        return

    def listener(_: CallEvent) -> None:
        progress.update_calls(stage, caller.status(), total=total)

    caller.add_listener(listener)
    progress.update_calls(stage, caller.status(), total=total)
    try:
        yield caller
    finally:
        progress.update_calls(stage, caller.status(), total=total, finished=True)
