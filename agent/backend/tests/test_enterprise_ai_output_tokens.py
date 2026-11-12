"""登録モデルごとの出力の上限（#1719）が、組み込み Runtime に渡ること。

未完了の応答（`status=incomplete`）は、理由・上限・使った token の分かる失敗にする。
OCI へは接続しない（Responses API の client は差し替える）。
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from pr_system_settings.model import EnterpriseAiConfiguredModel

from app.features.agent import builtin_runtime
from app.features.agent.builtin_runtime import BuiltinRuntimeError, ModelTarget
from app.observability import MODEL_INCOMPLETE_RETRIES_TOTAL
from app.settings import Settings

ENDPOINT = "https://primary.example/openai/v1"


def test_model_settings_send_model_output_limit() -> None:
    target = ModelTarget(
        model_id="m", endpoint=ENDPOINT, project_ocid="", api_key="k", max_output_tokens=16000
    )

    assert builtin_runtime.model_settings_for(target).max_tokens == 16000
    # 推論の量を送るときも同じ。
    with_effort = ModelTarget(
        model_id="m", endpoint=ENDPOINT, project_ocid="", api_key="k", reasoning_effort="high"
    )
    assert builtin_runtime.model_settings_for(with_effort).max_tokens == 32768


def test_resolve_model_target_reads_output_limit_of_agent_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        oci_enterprise_ai_endpoint=ENDPOINT,
        oci_enterprise_ai_api_key="sk-test",
        oci_enterprise_ai_models=[
            EnterpriseAiConfiguredModel(model_id="text-a", max_output_tokens=16000),
            EnterpriseAiConfiguredModel(model_id="text-small", max_input_tokens=8192),
            EnterpriseAiConfiguredModel(model_id="text-b"),
        ],
        oci_enterprise_ai_default_text_model="text-a",
    )
    monkeypatch.setattr(builtin_runtime, "get_settings", lambda: settings)

    assert builtin_runtime.resolve_model_target("").max_output_tokens == 16000
    # 入力の上限の小さいモデルは入力の上限まで。値を書かないモデルは既定の 32768。
    assert builtin_runtime.resolve_model_target("text-small").max_output_tokens == 8192
    assert builtin_runtime.resolve_model_target("text-b").max_output_tokens == 32768


class _Response:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self.status = data["status"]

    def model_dump(self) -> dict[str, Any]:
        return dict(self._data)


class _Inner:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Response:
        self.requests.append(kwargs)
        return self.response


def _incomplete() -> _Response:
    return _Response(
        {
            "status": "incomplete",
            "model": "openai.gpt-oss-120b",
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"output_tokens": 1200, "output_tokens_details": {"reasoning_tokens": 1198}},
        }
    )


def test_incomplete_response_fails_with_reason_limit_and_used_tokens() -> None:
    inner = _Inner(_incomplete())
    responses = builtin_runtime._OciResponses(inner)

    async def call() -> None:
        await responses.create(model="openai.gpt-oss-120b", input=[], max_output_tokens=1200)

    with pytest.raises(BuiltinRuntimeError) as raised:
        anyio.run(call)

    assert raised.value.code == "runtime.model_output_incomplete"
    message = raised.value.message
    assert "openai.gpt-oss-120b" in message
    assert "理由=max_output_tokens" in message
    assert "出力の上限=1200 tokens" in message
    assert "使った出力=1200 tokens（うち推論 1198）" in message
    # 送り直さない（送った上限がモデルの出力の上限）。
    assert len(inner.requests) == 1


def test_completed_response_is_returned() -> None:
    completed = _Response({"status": "completed"})
    responses = builtin_runtime._OciResponses(_Inner(completed))

    async def call() -> Any:
        return await responses.create(model="m", input=[], max_output_tokens=32768)

    assert anyio.run(call) is completed


class _Sequence(_Inner):
    """予算の各段階へ順に応答・streamを返す。"""

    def __init__(self, *responses: Any) -> None:
        super().__init__(responses[0])
        self.responses = list(responses)

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return self.responses.pop(0)


@pytest.mark.parametrize(
    ("reasoning", "reason"),
    [
        ({"effort": "low"}, "max_output_tokens"),
        (None, "max_output_tokens"),
        ({"effort": "high"}, "content_filter"),
    ],
    ids=["small-capacity-low", "small-capacity-default", "content-filter"],
)
def test_non_output_failures_and_capacity_limits_are_not_retried(
    reasoning: dict[str, str] | None, reason: str
) -> None:
    response = _incomplete()
    response._data["incomplete_details"] = {"reason": reason}
    inner = _Inner(response)
    responses = builtin_runtime._OciResponses(inner)
    kwargs: dict[str, Any] = {"model": "m", "input": [], "max_output_tokens": 1200}
    if reasoning is not None:
        kwargs["reasoning"] = reasoning

    async def call() -> None:
        await responses.create(**kwargs)

    with pytest.raises(BuiltinRuntimeError):
        anyio.run(call)
    assert len(inner.requests) == 1


def test_bounded_call_model_leaves_the_resend_to_the_caller() -> None:
    """上限つきの並行の実行器を通す呼び出し（SDK の再試行 0）は送り直しを実行器に任せる（#2023）。

    推論の量を下げた送り直しは実行器の adaptive の再試行（`ai_calls.retry_model_target`）が行い、
    再試行の主体を二重にしない。
    """
    run_model = builtin_runtime._default_model_factory(
        ModelTarget(model_id="m", endpoint=ENDPOINT, project_ocid="", api_key="k")
    )
    bounded = builtin_runtime._default_model_factory(
        ModelTarget(
            model_id="m", endpoint=ENDPOINT, project_ocid="", api_key="k", sdk_max_retries=0
        )
    )
    assert isinstance(run_model, builtin_runtime.OciResponsesModel)
    assert isinstance(bounded, builtin_runtime.OciResponsesModel)
    assert run_model.online_policy is True
    assert bounded.online_policy is False

    inner = _Inner(_incomplete())
    responses = builtin_runtime._OciResponses(inner, online_policy=False)

    async def call() -> None:
        await responses.create(
            model="m", input=[], max_output_tokens=1200, reasoning={"effort": "medium"}
        )

    with pytest.raises(BuiltinRuntimeError):
        anyio.run(call)
    assert len(inner.requests) == 1


def _tiny_incomplete() -> _Response:
    """上限よりはるかに少ない出力の未完了（呼び先の一時的な不具合。#2355）。"""
    return _Response(
        {
            "status": "incomplete",
            "model": "openai.gpt-oss-120b",
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"output_tokens": 61, "output_tokens_details": {"reasoning_tokens": 5}},
        }
    )


def _settings(monkeypatch: pytest.MonkeyPatch, **values: Any) -> None:
    settings = Settings(_env_file=None, **values)
    monkeypatch.setattr(builtin_runtime, "get_settings", lambda: settings)


def _retries(model: str) -> float:
    value: float = MODEL_INCOMPLETE_RETRIES_TOTAL.labels(model=model)._value.get()
    return value


def test_sdk_call_snapshot_records_input_and_response() -> None:
    from pr_backend_core.observability.tracing import operation_trace

    responses = builtin_runtime._OciResponses(
        _Inner(_Response({"status": "completed", "output_text": "synthetic-result"}))
    )

    async def call() -> Any:
        return await responses.create(model="m", input="synthetic-input", max_output_tokens=32768)

    with operation_trace("synthetic", operation_id="a") as trace:
        anyio.run(call)
    span = next(s for s in trace.spans if s.operation_name == "gen_ai.chat")
    assert span.display and span.display.details and span.display.details.call
    assert "synthetic-input" in span.display.details.call.input.text
    assert "synthetic-result" in span.display.details.call.output.text


@pytest.fixture(autouse=True)
def enable_trace_content_for_snapshot_tests() -> Any:
    from pr_backend_core.observability.call_policy import call_content_policy

    with call_content_policy(lambda: True):
        yield


def test_output_limit_stops_without_changing_reasoning_or_input() -> None:
    completed = _Response({"status": "completed"})
    inner = _Sequence(_incomplete(), _incomplete(), completed)
    responses = builtin_runtime._OciResponses(inner, max_output_tokens=65536)

    async def call() -> Any:
        return await responses.create(
            model="m",
            input=[{"role": "user", "content": "synthetic"}],
            max_output_tokens=65536,
            reasoning={"effort": "high"},
        )

    with pytest.raises(BuiltinRuntimeError):
        anyio.run(call)
    assert [r["max_output_tokens"] for r in inner.requests] == [65536]
    assert all(r["reasoning"] == {"effort": "high"} for r in inner.requests)
    assert all(r["input"] == inner.requests[0]["input"] for r in inner.requests)


def test_registered_capacity_is_used_once() -> None:
    inner = _Sequence(_tiny_incomplete(), _tiny_incomplete())
    responses = builtin_runtime._OciResponses(inner, max_output_tokens=32768)

    async def call() -> None:
        await responses.create(
            model="m", input=[], max_output_tokens=32768, reasoning={"effort": "high"}
        )

    with pytest.raises(BuiltinRuntimeError) as raised:
        anyio.run(call)
    assert [r["max_output_tokens"] for r in inner.requests] == [32768]
    assert "途中の回答は確定していません" in raised.value.message
    assert "推論の量を下げ" not in raised.value.message


def test_failed_attempt_usage_is_charged_without_another_call() -> None:
    from app.features.agent.run_budget import RunBudget, RunBudgetLimits

    budget = RunBudget(RunBudgetLimits(model_calls=2))
    assert budget.reserve_model_call(10) is None
    inner = _Sequence(_incomplete(), _incomplete(), _Response({"status": "completed"}))
    responses = builtin_runtime._OciResponses(inner, max_output_tokens=65536)

    async def call() -> None:
        token = builtin_runtime._ACTIVE_RUN_BUDGET.set(budget)
        try:
            await responses.create(model="m", input=[], max_output_tokens=65536)
        finally:
            builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)

    with pytest.raises(BuiltinRuntimeError) as raised:
        anyio.run(call)
    assert raised.value.code == "runtime.model_output_incomplete"
    assert len(inner.requests) == 1
    assert budget.model_calls == 1
    assert budget.measured_tokens == 1200


def test_incomplete_stream_never_publishes_partial_tool_calls() -> None:
    from types import SimpleNamespace

    class Stream:
        def __init__(self, events: list[Any]) -> None:
            self.events = iter(events)
            self.closed = False

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            value = next(self.events, None)
            if value is None:
                raise StopAsyncIteration
            return value

        async def close(self) -> None:
            self.closed = True

    incomplete = _incomplete()
    completed = _Response({"status": "completed"})
    first = Stream(
        [
            SimpleNamespace(type="response.output_item.added", item={"call_id": "discarded-tool"}),
            SimpleNamespace(type="response.function_call_arguments.delta", delta="partial"),
            SimpleNamespace(type="response.incomplete", response=incomplete),
        ]
    )
    second = Stream(
        [
            SimpleNamespace(type="response.output_item.added", item={"call_id": "successful-tool"}),
            SimpleNamespace(type="response.completed", response=completed),
        ]
    )
    inner = _Sequence(first, second)
    responses = builtin_runtime._OciResponses(inner, max_output_tokens=65536)

    async def call() -> list[Any]:
        result = await responses.create(model="m", input=[], max_output_tokens=65536, stream=True)
        return [event async for event in result]

    with pytest.raises(BuiltinRuntimeError):
        anyio.run(call)
    assert first.closed and not second.closed
    assert [r["max_output_tokens"] for r in inner.requests] == [65536]


@pytest.mark.parametrize("terminal", [None, "response.failed", "malformed_completed"])
def test_failed_or_unterminated_stream_never_publishes_partial_tools(terminal: str | None) -> None:
    from types import SimpleNamespace

    class Stream:
        def __init__(self) -> None:
            events = [
                SimpleNamespace(type="response.output_item.added", item={"call_id": "partial-tool"})
            ]
            if terminal:
                kind = "response.completed" if terminal == "malformed_completed" else terminal
                events.append(SimpleNamespace(type=kind, response=SimpleNamespace(status="failed")))
            self.events = iter(events)
            self.closed = False

        async def __anext__(self) -> Any:
            event = next(self.events, None)
            if event is None:
                raise StopAsyncIteration
            return event

        async def close(self) -> None:
            self.closed = True

    stream = Stream()
    inner = _Sequence(stream)
    responses = builtin_runtime._OciResponses(inner, max_output_tokens=65536)

    async def call() -> None:
        await responses.create(model="m", input=[], max_output_tokens=65536, stream=True)

    with pytest.raises(BuiltinRuntimeError) as raised:
        anyio.run(call)
    assert raised.value.code == "runtime.model_stream_failed"
    assert len(inner.requests) == 1 and stream.closed


def test_sdk_streaming_response_manager_uses_single_budget_and_preserves_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    class Stream:
        def __init__(self, events: list[Any], request_id: str) -> None:
            self.events = iter(events)
            self.response = SimpleNamespace(headers={"x-request-id": request_id})
            self.closed = False

        async def __anext__(self) -> Any:
            value = next(self.events, None)
            if value is None:
                raise StopAsyncIteration
            return value

        async def close(self) -> None:
            self.closed = True

    completed = _Response({"status": "completed"})
    second = Stream(
        [
            SimpleNamespace(type="response.output_item.added", item={"call_id": "success"}),
            SimpleNamespace(type="response.completed", response=completed),
        ],
        "successful-request",
    )
    inner = _Sequence(second)
    target = ModelTarget(
        model_id="m", endpoint=ENDPOINT, project_ocid="", api_key="k", max_output_tokens=65536
    )
    model = builtin_runtime._default_model_factory(target)
    assert isinstance(model, builtin_runtime.OciResponsesModel)
    monkeypatch.setattr(model, "_client", SimpleNamespace(responses=inner))

    async def call() -> list[Any]:
        stream = await model._fetch_response(
            system_instructions="synthetic",
            input="synthetic",
            model_settings=builtin_runtime.model_settings_for(target),
            tools=[],
            output_schema=None,
            handoffs=[],
            previous_response_id=None,
            conversation_id=None,
            stream=True,
        )
        return [event async for event in stream]

    events = anyio.run(call)
    assert events[0].item["call_id"] == "success"
    assert [r["max_output_tokens"] for r in inner.requests] == [65536]
    assert getattr(completed, "_request_id", None) == "successful-request"
    assert second.closed


def test_context_without_output_room_fails_before_model_request() -> None:
    inner = _Inner(_Response({"status": "completed"}))
    responses = builtin_runtime._OciResponses(inner, max_output_tokens=65536, max_input_tokens=4096)

    async def call() -> None:
        await responses.create(model="m", input="あ" * 4096, max_output_tokens=65536)

    with pytest.raises(BuiltinRuntimeError) as raised:
        anyio.run(call)
    assert raised.value.code == "runtime.context_overflow"
    assert not inner.requests


def test_total_budget_and_config_constrain_sent_output() -> None:
    from app.features.agent.run_budget import RunBudget, RunBudgetLimits

    budget = RunBudget(RunBudgetLimits(total_tokens=1000))
    assert budget.reserve_model_call(400) is None
    inner = _Inner(_Response({"status": "completed"}))
    responses = builtin_runtime._OciResponses(
        inner, max_output_tokens=500_000, online_max_output_tokens=128_000
    )

    async def call() -> None:
        token = builtin_runtime._ACTIVE_RUN_BUDGET.set(budget)
        try:
            await responses.create(model="m", input=[], max_output_tokens=500_000)
        finally:
            builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)

    anyio.run(call)
    assert inner.requests[0]["max_output_tokens"] == 600


def test_shared_output_setting_loads_from_platform_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_ONLINE_MAX_OUTPUT_TOKENS", "128000")
    assert Settings(_env_file=None).online_max_output_tokens == 128_000


def test_exhausted_total_budget_never_forces_one_output_token() -> None:
    from app.features.agent.run_budget import RunBudget, RunBudgetLimits

    budget = RunBudget(RunBudgetLimits(total_tokens=400))
    assert budget.reserve_model_call(400) is None
    inner = _Inner(_Response({"status": "completed"}))
    responses = builtin_runtime._OciResponses(inner, max_output_tokens=500_000)

    async def call() -> None:
        token = builtin_runtime._ACTIVE_RUN_BUDGET.set(budget)
        try:
            await responses.create(model="m", input=[], max_output_tokens=500_000)
        finally:
            builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)

    with pytest.raises(BuiltinRuntimeError):
        anyio.run(call)
    assert not inner.requests


def test_small_context_tightens_actual_request_to_remaining_room() -> None:
    inner = _Inner(_Response({"status": "completed"}))
    responses = builtin_runtime._OciResponses(
        inner, max_output_tokens=500_000, max_input_tokens=4096
    )

    async def call() -> None:
        await responses.create(model="m", input="合成" * 512, max_output_tokens=500_000)

    anyio.run(call)
    assert 0 < inner.requests[0]["max_output_tokens"] < 4096


def test_context_retry_recomputes_output_room_and_total_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import httpx2 as httpx
    from openai import BadRequestError

    from app.features.agent.run_budget import RunBudget, RunBudgetLimits

    budget = RunBudget(RunBudgetLimits(total_tokens=1000))
    assert budget.reserve_model_call(400) is None

    class Window:
        run_id = "synthetic"
        provider_limit = None
        last_input = SimpleNamespace(tokens_after=400)

        def retry_request(self, request: dict[str, Any], overflow: Any) -> dict[str, Any]:
            self.last_input = SimpleNamespace(tokens_after=200)
            return {**request, "input": "compacted"}

        def measure(self, response: Any) -> None:
            pass

    class Inner(_Inner):
        async def create(self, **kwargs: Any) -> _Response:
            self.requests.append(kwargs)
            if len(self.requests) == 1:
                response = httpx.Response(400, request=httpx.Request("POST", ENDPOINT))
                raise BadRequestError("input overflow", response=response, body={})
            return self.response

    inner = Inner(_Response({"status": "completed"}))
    responses = builtin_runtime._OciResponses(
        inner, max_output_tokens=500_000, max_input_tokens=1000
    )
    monkeypatch.setattr(builtin_runtime, "provider_overflow", lambda *args: object())

    async def call() -> None:
        token = builtin_runtime._ACTIVE_RUN_BUDGET.set(budget)
        window_token = builtin_runtime._ACTIVE_CONTEXT_WINDOW.set(Window())  # type: ignore[arg-type]
        try:
            await responses.create(model="m", input="original", max_output_tokens=500_000)
        finally:
            builtin_runtime._ACTIVE_CONTEXT_WINDOW.reset(window_token)
            builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)

    anyio.run(call)
    assert [request["max_output_tokens"] for request in inner.requests] == [600, 400]
    assert budget.model_calls == 2
    assert budget.estimated_tokens == 400


def test_stream_transport_failure_settles_usage_without_publishing_partial_tool() -> None:
    from types import SimpleNamespace

    import httpx2 as httpx

    from app.features.agent.run_budget import RunBudget, RunBudgetLimits

    budget = RunBudget(RunBudgetLimits())
    assert budget.reserve_model_call(400) is None

    class Stream:
        closed = False
        emitted = False

        async def __anext__(self) -> Any:
            if not self.emitted:
                self.emitted = True
                return SimpleNamespace(
                    type="response.output_item.added", item={"call_id": "partial"}
                )
            raise httpx.ReadError("synthetic transport failure")

        async def close(self) -> None:
            self.closed = True

    stream = Stream()
    inner = _Sequence(stream)
    responses = builtin_runtime._OciResponses(inner, max_output_tokens=500_000)
    published = []

    async def call() -> None:
        token = builtin_runtime._ACTIVE_RUN_BUDGET.set(budget)
        try:
            result = await responses.create(model="m", input=[], stream=True)
            async for event in result:
                published.append(event)
        finally:
            builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)

    with pytest.raises(httpx.ReadError):
        anyio.run(call)
    assert not published
    assert stream.closed and len(inner.requests) == 1
    assert budget.model_calls == 1
    assert budget.unmeasured_calls == 1
    assert budget.estimated_tokens == 400
    assert budget.failed_usage_records == [{"requests": 1}]


def test_failure_before_response_settles_unknown_usage_once() -> None:
    import httpx2 as httpx

    from app.features.agent.run_budget import RunBudget, RunBudgetLimits

    budget = RunBudget(RunBudgetLimits())
    assert budget.reserve_model_call(400) is None

    class Inner(_Inner):
        async def create(self, **kwargs: Any) -> _Response:
            self.requests.append(kwargs)
            raise httpx.ConnectError("synthetic connection failure")

    inner = Inner(_Response({"status": "completed"}))
    responses = builtin_runtime._OciResponses(inner, max_output_tokens=500_000)

    async def call() -> None:
        token = builtin_runtime._ACTIVE_RUN_BUDGET.set(budget)
        try:
            await responses.create(model="m", input=[], stream=True)
        finally:
            builtin_runtime._ACTIVE_RUN_BUDGET.reset(token)

    with pytest.raises(httpx.ConnectError):
        anyio.run(call)
    assert len(inner.requests) == 1
    assert budget.unmeasured_calls == 1
    assert budget.estimated_tokens == 400
    assert budget.failed_usage_records == [{"requests": 1}]
