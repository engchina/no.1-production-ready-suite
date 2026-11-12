"""回答の重い同期処理（LLM を呼ぶ回答フロー・回答の評価）を動かす専用の thread と期限（#1422）。

回答フロー（`rag_engine`）は同期関数なので worker thread で動かす。thread は外から止められないため、
回答を上限で打ち切っても（`run_answer_with_timeout`）、そのままでは LLM 1 回の timeout・再試行の
まま LLM を呼び続け、asyncio の既定の executor（DB・OCI SDK の呼び出しと共有）を使い切る。

- 期限（`rag_engine.resources.deadline.Deadline`）を thread の文脈に置き、待つ側が終わったとき
  （完了・打ち切り・取り消し）に取り消す。thread の中の LLM の呼び出しは、呼ぶ前に期限を確かめ、
  1 回の timeout を残り時間に縮め、期限を過ぎたら再試行しない（`rag_engine.adapters.oci`）。
- thread は既定の executor ではなく、この module の専用の executor（上限付き）で動かす。回答が
  長くかかっても、DB・API の処理の thread を食いつぶさない。
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial

from rag_engine.resources.deadline import Deadline, bind_deadline, current_deadline

# 回答の重い処理を同時に動かす thread の数の上限。超えた分は executor の中で待つ（待ちも回答の期限に
# 数える）。既定の executor（`min(32, CPU + 4)`）とは別に持つ。
ANSWER_THREAD_WORKERS = 32
_ANSWER_THREAD_NAME_PREFIX = "rag-answer"
# thread の期限を、待つ側の上限より少しだけ後ろにする（秒）。期限の切れた thread が劣化した結果を
# 返す前に、待つ側の上限（`asyncio.timeout` / `wait_for`）が先に切れるようにする。待つ側が抜けた
# ときは、この余裕を待たずに取り消す。
ANSWER_DEADLINE_GRACE_SECONDS = 1.0

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def answer_executor() -> ThreadPoolExecutor:
    """回答の重い処理の専用の executor（process で 1 つ。最初の利用で作る）。"""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=ANSWER_THREAD_WORKERS, thread_name_prefix=_ANSWER_THREAD_NAME_PREFIX
            )
        return _executor


@contextmanager
def answer_deadline(timeout_seconds: float | None = None) -> Iterator[Deadline]:
    """回答の期限をこの文脈に置き、抜けるとき（完了・打ち切り・取り消し）に取り消す。

    `timeout_seconds` は待つ側の上限で、thread の期限はそれに `ANSWER_DEADLINE_GRACE_SECONDS` を
    足したもの。外側の期限があれば引き継ぐ（期限は早いほう、取り消しは外側からも伝わる）。
    """
    limit = None if timeout_seconds is None else timeout_seconds + ANSWER_DEADLINE_GRACE_SECONDS
    deadline = Deadline(limit, parent=current_deadline())
    try:
        with bind_deadline(deadline):
            yield deadline
    finally:
        deadline.cancel()


async def run_answer_in_thread[T](
    function: Callable[..., T],
    /,
    *args: object,
    timeout_seconds: float | None = None,
    **kwargs: object,
) -> T:
    """`function` を回答の専用の executor で動かし、待つ側が終わったら thread の期限を取り消す。

    `timeout_seconds` は thread の中の期限（LLM 1 回の timeout を縮める）。await の打ち切りは
    呼び出し側（`asyncio.timeout` / `wait_for`）が行う。文脈（`ContextVar`）は thread に引き継ぐ。
    """
    loop = asyncio.get_running_loop()
    with answer_deadline(timeout_seconds):
        context = contextvars.copy_context()
        call = partial(function, *args, **kwargs)
        return await loop.run_in_executor(answer_executor(), context.run, call)
