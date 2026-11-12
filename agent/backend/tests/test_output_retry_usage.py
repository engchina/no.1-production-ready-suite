"""内部再試行の実測を SDK の累計へ混ぜず、保存と表示で一度だけ合計する。"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from agents import Usage

from app.features.agent import builtin_runtime, runtime
from app.features.agent.run_budget import RUN_BUDGET_METADATA_KEY, RunBudget, RunBudgetLimits
from app.features.agent.runtime import RunUsage


class _Repository:
    def __init__(self) -> None:
        self.run = SimpleNamespace(metadata={}, steps=[], usage=None)

    def get_run(self, _run_id: str) -> Any:
        return self.run

    def record_run_budget(self, _run_id: str, snapshot: dict[str, Any]) -> None:
        self.run.metadata[RUN_BUDGET_METADATA_KEY] = deepcopy(snapshot)

    def record_builtin_usage(self, _run_id: str, usage: RunUsage | None) -> None:
        self.run.usage = usage


def _source(usage: Usage) -> Any:
    return SimpleNamespace(context_wrapper=SimpleNamespace(usage=usage))


def _failed(budget: RunBudget, value: object) -> None:
    assert budget.reserve_model_call(100) is None
    budget.settle_failed(value)


def test_failed_attempts_are_settled_once_and_records_do_not_change_sdk_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository()
    monkeypatch.setattr(runtime, "runtime_repository", repository)
    budget = RunBudget(RunBudgetLimits())
    _failed(budget, {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})
    _failed(budget, SimpleNamespace(input_tokens=100, output_tokens=40, total_tokens=140))
    assert budget.reserve_model_call(100) is None
    successful = Usage(requests=1, input_tokens=100, output_tokens=80, total_tokens=180)
    budget.settle(successful)
    token = builtin_runtime._ACTIVE_RUN_BUDGET.set(budget)
    try:
        for _ in range(3):
            builtin_runtime._record_usage("run-synthetic", _source(successful), "model-a")
            assert repository.run.usage == RunUsage(
                model="model-a",
                requests=3,
                input_tokens=300,
                output_tokens=140,
                total_tokens=440,
            )
        assert successful == Usage(requests=1, input_tokens=100, output_tokens=80, total_tokens=180)
        assert budget.measured_tokens == 440
        assert budget.model_calls == 3
        assert len(budget.failed_usage_records) == 2
        saved = repository.run.metadata[RUN_BUDGET_METADATA_KEY]
        assert saved["failed_model_usage"]["unmeasured_requests"] == 0
        assert saved["sdk_model_usage"]["requests"] == 1
    finally:
        builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)


def test_resume_and_answer_format_retry_keep_cumulative_failed_usage_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository()
    monkeypatch.setattr(runtime, "runtime_repository", repository)
    first = RunBudget(RunBudgetLimits())
    _failed(first, {"input_tokens": 100, "output_tokens": 20})
    assert first.reserve_model_call(100) is None
    successful = Usage(requests=1, input_tokens=100, output_tokens=50, total_tokens=150)
    first.settle(successful)
    token = builtin_runtime._ACTIVE_RUN_BUDGET.set(first)
    try:
        builtin_runtime._record_usage("run-synthetic", _source(successful), "model-a")
    finally:
        builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)
    resumed = RunBudget.for_run(deepcopy(repository.run.metadata), RunBudgetLimits())
    _failed(resumed, {"input_tokens": 200, "output_tokens": 30, "total_tokens": 230})
    assert resumed.reserve_model_call(200) is None
    resumed_success = Usage(requests=1, input_tokens=200, output_tokens=60, total_tokens=260)
    resumed.settle(resumed_success)
    cumulative_success = deepcopy(successful)
    cumulative_success.add(resumed_success)
    token = builtin_runtime._ACTIVE_RUN_BUDGET.set(resumed)
    try:
        builtin_runtime._record_usage("run-synthetic", _source(cumulative_success), "model-a")
        expected = RunUsage(
            model="model-a",
            requests=4,
            input_tokens=600,
            output_tokens=160,
            total_tokens=760,
        )
        assert repository.run.usage == expected
        # 形式修正・承認の保存が同じ SDK 累計を再度記録しても、失敗分を二重に足さない。
        builtin_runtime._record_usage("run-synthetic", _source(cumulative_success), "model-a")
        assert repository.run.usage == expected
        assert resumed.total_tokens == 760
    finally:
        builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)


def test_terminal_failure_without_sdk_result_preserves_previous_success_and_new_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository()
    monkeypatch.setattr(runtime, "runtime_repository", repository)
    first = RunBudget(RunBudgetLimits())
    assert first.reserve_model_call(100) is None
    successful = Usage(requests=1, input_tokens=100, output_tokens=50, total_tokens=150)
    first.settle(successful)
    token = builtin_runtime._ACTIVE_RUN_BUDGET.set(first)
    try:
        builtin_runtime._record_usage("run-synthetic", _source(successful), "model-a")
    finally:
        builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)
    resumed = RunBudget.for_run(deepcopy(repository.run.metadata), RunBudgetLimits())
    _failed(resumed, {"input_tokens": 200, "output_tokens": 30, "total_tokens": 230})
    token = builtin_runtime._ACTIVE_RUN_BUDGET.set(resumed)
    try:
        builtin_runtime._record_usage("run-synthetic", None, "model-a")
        assert repository.run.usage == RunUsage(
            model="model-a",
            requests=2,
            input_tokens=300,
            output_tokens=80,
            total_tokens=380,
        )
        builtin_runtime._record_usage("run-synthetic", None, "model-a")
        assert repository.run.usage.total_tokens == 380
    finally:
        builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)


@pytest.mark.parametrize("failed", [None, {"output_tokens": 30}, {"total_tokens": 230}])
def test_unknown_or_partial_usage_is_not_displayed_as_zero_or_exact_total(
    monkeypatch: pytest.MonkeyPatch,
    failed: object,
) -> None:
    repository = _Repository()
    monkeypatch.setattr(runtime, "runtime_repository", repository)
    budget = RunBudget(RunBudgetLimits())
    _failed(budget, failed)
    token = builtin_runtime._ACTIVE_RUN_BUDGET.set(budget)
    try:
        # 前の記録が残っていても、欠測のある新しい合計へ流用しない。
        repository.run.usage = RunUsage(
            model="model-a",
            requests=1,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )
        builtin_runtime._record_usage("run-synthetic", None, "model-a")
        assert repository.run.usage is None
        snapshot = repository.run.metadata[RUN_BUDGET_METADATA_KEY]
        [record] = snapshot["failed_model_usage"]["records"]
        assert record["requests"] == 1
        assert "input_tokens" not in record
        assert snapshot["failed_model_usage"]["unmeasured_requests"] == 1
        if failed is None:
            assert "output_tokens" not in record and "total_tokens" not in record
            assert budget.estimated_tokens == 100
            assert budget.unmeasured_calls == 1
        elif isinstance(failed, dict):
            for key, value in failed.items():
                assert record[key] == value
    finally:
        builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)


def test_missing_sdk_result_is_not_fabricated_from_a_known_failed_attempt() -> None:
    budget = RunBudget(RunBudgetLimits())
    _failed(budget, {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})
    # SDK の成功分を取得できていない場合、失敗分だけを全体の精確な合計にしない。
    assert budget.reserve_model_call(100) is None
    budget.settle(Usage(requests=1, input_tokens=100, output_tokens=50, total_tokens=150))
    assert budget.reported_usage() is None
    assert budget.measured_tokens == 270


def test_legacy_checkpoint_success_usage_is_not_lost_on_new_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository()
    monkeypatch.setattr(runtime, "runtime_repository", repository)
    first = RunBudget(RunBudgetLimits())
    assert first.reserve_model_call(100) is None
    first.settle(Usage(requests=1, input_tokens=100, output_tokens=50, total_tokens=150))
    old_snapshot = first.snapshot()
    old_snapshot.pop("failed_model_usage")
    old_snapshot.pop("sdk_model_usage")
    repository.run.metadata[RUN_BUDGET_METADATA_KEY] = old_snapshot
    repository.run.usage = RunUsage(
        model="model-a",
        requests=1,
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
    )
    resumed = RunBudget.for_run(repository.run.metadata, RunBudgetLimits())
    _failed(resumed, {"input_tokens": 200, "output_tokens": 30, "total_tokens": 230})
    token = builtin_runtime._ACTIVE_RUN_BUDGET.set(resumed)
    try:
        builtin_runtime._record_usage("run-synthetic", None, "model-a")
        assert repository.run.usage == RunUsage(
            model="model-a",
            requests=2,
            input_tokens=300,
            output_tokens=80,
            total_tokens=380,
        )
        builtin_runtime._record_usage("run-synthetic", None, "model-a")
        assert repository.run.usage.total_tokens == 380
    finally:
        builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)


def test_all_tiers_fail_but_known_provider_usage_is_still_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository()
    monkeypatch.setattr(runtime, "runtime_repository", repository)
    budget = RunBudget(RunBudgetLimits())
    for _ in range(3):
        _failed(budget, {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})
    token = builtin_runtime._ACTIVE_RUN_BUDGET.set(budget)
    try:
        builtin_runtime._record_usage("run-synthetic", None, "model-a")
        assert repository.run.usage == RunUsage(
            model="model-a",
            requests=3,
            input_tokens=300,
            output_tokens=60,
            total_tokens=360,
        )
        assert budget.total_tokens == 360
    finally:
        builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)


def test_recording_outside_active_context_keeps_the_saved_call_completeness_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository()
    monkeypatch.setattr(runtime, "runtime_repository", repository)
    budget = RunBudget(RunBudgetLimits())
    _failed(budget, {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})
    assert budget.reserve_model_call(100) is None
    budget.settle(Usage(requests=1, input_tokens=100, output_tokens=50, total_tokens=150))
    repository.run.metadata[RUN_BUDGET_METADATA_KEY] = budget.snapshot()
    token = builtin_runtime._ACTIVE_RUN_BUDGET.set(None)
    try:
        builtin_runtime._record_usage("run-synthetic", None, "model-a")
        assert repository.run.usage is None
    finally:
        builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)
