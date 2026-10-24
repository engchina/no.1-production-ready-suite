"""処理の経過（trace の span のイベント。#3226）のテストの補助。

偽の pipeline が本物と同じ span（実行の全体・パイプラインの段・回答フローの工程）を作れるように、
`operation_trace` と工程の観測（`_observe_engine_call`）を束ね、工程の span を開く関数を持つ。
"""

from __future__ import annotations

from collections.abc import Awaitable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from pr_backend_core.chat_progress import ChatProgressStepEvent, fold_chat_progress_events
from pr_backend_core.observability.tracing import SpanListener, operation_trace
from rag_engine.generation.execution_record import _execution_step
from rag_engine.resources.tracing import bind_trace_observer

from app.rag.pipeline import _observe_engine_call, _observe_stage

__all__ = [
    "answer_step",
    "answer_trace",
    "pipeline_stage",
    "span_rows",
    "span_steps",
]


@contextmanager
def answer_trace(
    span_listener: SpanListener | None,
    *,
    trace_id: str = "trace",
    name: str = "rag.answer",
) -> Iterator[None]:
    """本物の pipeline と同じ実行の全体の span と工程の観測を開く。"""
    with (
        operation_trace(name, operation_id=trace_id, listener=span_listener),
        bind_trace_observer(_observe_engine_call),
    ):
        yield


@contextmanager
def answer_step(name: str) -> Iterator[Any]:
    """回答フローの工程（`rag_engine` の `_execution_step`）を 1 つ開く。"""
    with _execution_step(name) as step:
        yield step


async def pipeline_stage(
    stage: str,
    work: Awaitable[object] | None = None,
    *,
    trace_id: str = "trace",
    progress_callback: Any = None,
    result: object = None,
    error: BaseException | None = None,
    result_details: Any = None,
) -> object:
    """パイプラインの段（`_observe_stage`）を 1 つ実行する。

    `work` を渡すと段の中で待つ（中の工程は段の子になる）。`error` を渡すと失敗にする。
    """

    async def operation() -> object:
        if work is not None:
            await work
        if error is not None:
            raise error
        return result

    return await _observe_stage(
        trace_id,
        stage,
        operation(),
        progress_callback=progress_callback,
        result_details=result_details,
    )


def span_steps(events: Sequence[object]) -> list[ChatProgressStepEvent]:
    """記録の最後の状態の段（span）のイベント（記録の順。root を含む）。"""
    latest: dict[str, ChatProgressStepEvent] = {}
    for event in events:
        if isinstance(event, ChatProgressStepEvent):
            latest[event.step_id] = event
    return list(latest.values())


def span_rows(events: Sequence[object]) -> list[tuple[str, str, object, str]]:
    """root を除く段の一覧（span の名前・種類・工程名・状態）。種類の無い span の種類は空。"""
    rows: list[tuple[str, str, object, str]] = []
    fold = fold_chat_progress_events(
        [event for event in events if isinstance(event, ChatProgressStepEvent)]
    )
    steps = {step.step_id: step for step in fold.step_list()}
    for event in span_steps(events):
        if event.root:
            continue
        step = steps[event.step_id]
        kind = "" if step.kind == step.step_id else str(step.kind)
        params: Mapping[str, object] = step.params or {}
        rows.append((str(event.name), kind, params.get("name"), step.status))
    return rows
