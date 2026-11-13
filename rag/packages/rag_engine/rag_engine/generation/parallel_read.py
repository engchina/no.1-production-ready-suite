"""独立呼出を完了順に補充し、結果は原順序で受け取る。"""

from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import copy_context


@contextmanager
def ordered_parallel_results[T, R](function: Callable[[T], R], values: Iterable[T], *, workers: int) -> Iterator[Iterator[R]]:
    if workers < 1:
        raise ValueError("並行数は 1 以上にしてください。")
    if workers == 1:
        yield map(function, values)
        return
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rag-read")
    pending: dict[Future[R], int] = {}
    ready: dict[int, R] = {}
    remaining = iter(enumerate(values))
    parent_context = copy_context()

    def submit_next() -> None:
        try:
            index, value = next(remaining)
        except StopIteration:
            return
        pending[executor.submit(parent_context.copy().run, function, value)] = index

    def results() -> Iterator[R]:
        expected = 0
        while pending or ready:
            while expected in ready:
                yield ready.pop(expected)
                expected += 1
            if not pending:
                continue
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            ordered = sorted(completed, key=pending.__getitem__)
            # 失敗があれば新しい呼出を始めず、元の例外をそのまま伝える。
            values = [(future, future.result()) for future in ordered]
            for future, value in values:
                ready[pending.pop(future)] = value
            for _ in values:
                submit_next()

    try:
        for _ in range(workers):
            submit_next()
        yield results()
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
