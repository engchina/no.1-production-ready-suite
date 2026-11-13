"""独立読取の並行数・文脈・順序・失敗時の後始末を確認する。"""

import json
import threading
import time
from contextvars import ContextVar
from dataclasses import replace

import pytest
from test_batched_evidence import NO_TERM_ANSWER, block, config, finding, span

from rag_engine.generation.answering import _invalid_structured_output
from rag_engine.generation.batched_evidence import prepare_batched_evidence
from rag_engine.generation.parallel_read import ordered_parallel_results


def test_parallel_calls_keep_order_and_request_context_and_bound_workers():
    context = ContextVar("parallel_read_test", default="none")
    context.set("request-a")
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = peak = started = 0

    def run(value):
        nonlocal active, peak, started
        assert context.get() == "request-a"
        context.set("worker-" + str(value))
        with lock:
            active += 1
            peak = max(peak, active)
            started += 1
        try:
            barrier.wait(timeout=2)
            if value % 2 == 0:
                time.sleep(0.01)
            return value
        finally:
            with lock:
                active -= 1

    with ordered_parallel_results(run, range(8), workers=2) as results:
        assert list(results) == list(range(8))
    assert active == 0 and peak == 2 and started == 8
    assert context.get() == "request-a"


def test_parallel_failure_keeps_original_exception_and_joins_active_calls():
    failure = ValueError("合成の失敗")
    barrier = threading.Barrier(2)
    active = []
    done = []

    def run(value):
        active.append(value)
        barrier.wait(timeout=2)
        try:
            if value == 0:
                raise failure
            time.sleep(0.01)
            return value
        finally:
            done.append(value)

    with pytest.raises(ValueError) as caught, ordered_parallel_results(run, range(8), workers=2) as results:
        list(results)
    assert caught.value is failure
    assert sorted(active) == sorted(done) == [0, 1]


def test_parallel_batch_reading_merges_the_same_evidence_in_original_order():
    values = [span(i, f"条件{i}。" + "合成の補足本文。" * 300) for i in range(4)]

    def run(workers):
        observed = []

        def read(system, prompt, model):
            payload = json.loads(prompt)
            observed.append(prompt)
            return model.model_validate({"findings": [finding(item, NO_TERM_ANSWER) for item in payload["evidence"]]})

        result = prepare_batched_evidence(
            values,
            replace(config(), batch_read_workers=workers),
            question="条件を比較する",
            render=block,
            evidence_text=block,
            system="指示",
            schema={"type": "object"},
            read=read,
            unusable_output=_invalid_structured_output,
        )
        return result, observed

    serial, serial_calls = run(1)
    parallel, parallel_calls = run(2)
    assert serial == parallel
    assert sorted(serial_calls) == sorted(parallel_calls)
    assert serial[2]["read_source_count"] == 4


def test_slow_first_call_does_not_block_refilling_free_workers():
    reached_last = threading.Event()

    def run(value):
        if value == 0:
            assert reached_last.wait(timeout=2), "空いた worker に後続の読取が渡されていない"
        elif value == 4:
            reached_last.set()
        return value

    with ordered_parallel_results(run, range(5), workers=2) as results:
        assert list(results) == list(range(5))
