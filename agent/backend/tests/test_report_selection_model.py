"""草稿の有無を、実際のSDK要求と再開の境界で検証する。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

import anyio
import pytest
from agents import (
    Agent,
    AgentOutputSchema,
    FunctionTool,
    ModelSettings,
    RunConfig,
    Runner,
    RunState,
    function_tool,
)
from agents.models.interface import ModelTracing
from agents.retry import ModelRetryAdvice, ModelRetryAdviceRequest
from agents.testing import ScriptedModel, assistant_message, function_call
from agents.tool import Tool

from app.features.agent.report_contract import FinalSelection
from app.features.agent.report_preview import ReportPreviewError
from app.features.agent.report_selection import ReportSelectionModel


async def _tool(*args: Any) -> str:
    return "合成結果"


def tools() -> list[Tool]:
    return [
        FunctionTool(
            name="investigate",
            description="調べる",
            params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
            on_invoke_tool=_tool,
        )
    ]


def request(settings: ModelSettings | None = None) -> dict[str, Any]:
    return {
        "system_instructions": "合成の指示",
        "input": "質問",
        "model_settings": settings or ModelSettings(),
        "tools": tools(),
        "output_schema": AgentOutputSchema(FinalSelection),
        "handoffs": [],
        "tracing": ModelTracing.DISABLED,
        "previous_response_id": "previous-a",
        "conversation_id": "conversation-a",
        "prompt": None,
    }


@pytest.mark.parametrize("stream", [False, True])
def test_every_request_uses_current_ids_and_preserves_all_other_parameters(stream: bool) -> None:
    async def scenario() -> None:
        available: list[str] = []
        inner = ScriptedModel([[assistant_message('{"preview_id":"A1"}')]] * 3)
        wrapped = ReportSelectionModel(inner, lambda: tuple(available))
        settings = ModelSettings(
            max_tokens=65536,
            temperature=0.25,
            top_p=0.8,
            parallel_tool_calls=True,
            tool_choice="auto",
            extra_body={"synthetic": True},
        )
        before = asdict(settings)
        original = request(settings)
        for ids in [[], ["A1", "A3"], []]:
            available[:] = ids
            if stream:
                async for _ in wrapped.stream_response(**original):
                    pass
            else:
                await wrapped.get_response(**original)
        assert asdict(settings) == before
        assert len(inner.calls) == 3
        assert [call.model_settings.tool_choice for call in inner.calls] == [
            "required",
            "auto",
            "required",
        ]
        assert inner.calls[0].output_schema is None and inner.calls[2].output_schema is None
        schema = inner.calls[1].output_schema
        assert schema is not None
        assert schema.json_schema()["properties"]["preview_id"]["enum"] == ["A1", "A3"]
        assert "enum" not in original["output_schema"].json_schema()["properties"]["preview_id"]
        for call in inner.calls:
            actual = asdict(call.model_settings)
            actual["tool_choice"] = before["tool_choice"]
            assert actual == before
            assert call.tools == original["tools"]
            assert (
                call.system_instructions == original["system_instructions"] and call.input == "質問"
            )
            assert (
                call.previous_response_id == "previous-a"
                and call.conversation_id == "conversation-a"
            )

    anyio.run(scenario)


@pytest.mark.parametrize("stream", [False, True])
def test_real_SDK_loop_keeps_autonomous_tools_until_preview_exists(stream: bool) -> None:
    async def scenario() -> None:
        ids: list[str] = []
        executed: list[str] = []

        @function_tool
        def investigate() -> str:
            executed.append("investigate")
            return "取得結果"

        @function_tool
        def draft_report() -> str:
            executed.append("draft_report")
            ids.append("A1")
            return '{"preview_id":"A1"}'

        inner = ScriptedModel(
            [
                [function_call("investigate", "{}", call_id="q1")],
                [function_call("draft_report", "{}", call_id="d1")],
                [assistant_message('{"preview_id":"A1"}')],
            ]
        )
        agent = Agent(
            name="合成", model=inner, tools=[investigate, draft_report], output_type=FinalSelection
        )
        wrapped = ReportSelectionModel(inner, lambda: tuple(ids))
        config = RunConfig(model=wrapped, tracing_disabled=True)
        result: Any
        if stream:
            result = Runner.run_streamed(agent, "質問", run_config=config)
            async for _ in result.stream_events():
                pass
        else:
            result = await Runner.run(agent, "質問", run_config=config)
        assert result.final_output == FinalSelection(preview_id="A1")
        assert executed == ["investigate", "draft_report"]
        assert agent.model is inner
        assert [call.model_settings.tool_choice for call in inner.calls] == [
            "required",
            "required",
            None,
        ]
        assert all(
            [tool.name for tool in call.tools] == ["investigate", "draft_report"]
            for call in inner.calls
        )
        assert inner.calls[0].output_schema is None and inner.calls[1].output_schema is None
        final = inner.calls[2].output_schema
        assert final is not None and final.json_schema()["properties"]["preview_id"]["enum"] == [
            "A1"
        ]

    anyio.run(scenario)


def test_old_public_agent_checkpoint_resumes_with_new_execution_wrapper() -> None:
    async def scenario() -> None:
        ids: list[str] = []
        writes: list[str] = []

        @function_tool(needs_approval=True)
        def draft_report() -> str:
            writes.append("draft")
            ids.append("A1")
            return '{"preview_id":"A1"}'

        old_model = ScriptedModel([[function_call("draft_report", "{}", call_id="d1")]])
        old_agent = Agent(
            name="合成", model=old_model, tools=[draft_report], output_type=FinalSelection
        )
        pending = await Runner.run(old_agent, "質問", run_config=RunConfig(tracing_disabled=True))
        assert pending.interruptions and not writes
        saved = pending.to_state().to_string()
        model = ScriptedModel([[assistant_message('{"preview_id":"A1"}')]])
        agent = old_agent.clone(model=model)
        state = await RunState.from_string(agent, saved)
        for item in state.get_interruptions():
            state.approve(item)
        result = await Runner.run(
            agent,
            state,
            run_config=RunConfig(
                model=ReportSelectionModel(model, lambda: tuple(ids)), tracing_disabled=True
            ),
        )
        assert result.final_output == FinalSelection(preview_id="A1")
        assert writes == ["draft"] and len(model.calls) == 1
        schema = model.calls[0].output_schema
        assert schema is not None and schema.json_schema()["properties"]["preview_id"]["enum"] == [
            "A1"
        ]

    anyio.run(scenario)


@pytest.mark.parametrize("stream", [False, True])
def test_bad_or_cancelled_state_stops_before_underlying_model(stream: bool) -> None:
    async def scenario() -> None:
        model = ScriptedModel([])

        def unavailable() -> tuple[str, ...]:
            raise ReportPreviewError("report.run_inactive", "停止済み")

        wrapped = ReportSelectionModel(model, unavailable)
        with pytest.raises(ReportPreviewError):
            if stream:
                async for _ in wrapped.stream_response(**request()):
                    pass
            else:
                await wrapped.get_response(**request())
        assert not model.calls

    anyio.run(scenario)


def test_no_candidates_and_no_tools_is_not_sent_to_provider() -> None:
    async def scenario() -> None:
        model = ScriptedModel([])
        kwargs = request()
        kwargs["tools"] = []
        with pytest.raises(ReportPreviewError) as failure:
            await ReportSelectionModel(model, lambda: ()).get_response(**kwargs)
        assert failure.value.code == "report.no_actions" and not model.calls

    anyio.run(scenario)


def test_lifecycle_retry_advice_and_model_attributes_are_forwarded() -> None:
    class RecordingModel(ScriptedModel):
        model = "synthetic-model"

        def __init__(self) -> None:
            super().__init__([])
            self.cleanup: list[object] = []
            self.closed = False
            self.requests: list[ModelRetryAdviceRequest] = []
            self.advice = ModelRetryAdvice(
                suggested=False, replay_safety="unsafe", response_started=True
            )

        def get_retry_advice(self, value: ModelRetryAdviceRequest) -> ModelRetryAdvice:
            self.requests.append(value)
            return self.advice

        async def _cleanup_on_run_end(self, owner: object) -> None:
            self.cleanup.append(owner)

        async def close(self) -> None:
            self.closed = True

    async def scenario() -> None:
        inner = RecordingModel()
        wrapper = ReportSelectionModel(inner, lambda: ())
        value = ModelRetryAdviceRequest(error=ValueError("合成"), attempt=2, stream=True)
        assert wrapper.get_retry_advice(value) is inner.advice and inner.requests == [value]
        owner = object()
        await wrapper._cleanup_on_run_end(owner)
        await wrapper.close()
        assert inner.cleanup == [owner] and inner.closed
        assert wrapper.model == inner.model

    anyio.run(scenario)


def test_selection_lookup_cancellation_propagates_without_model_call() -> None:
    async def scenario() -> None:
        model = ScriptedModel([])

        def cancelled() -> tuple[str, ...]:
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await ReportSelectionModel(model, cancelled).get_response(**request())
        assert not model.calls

    anyio.run(scenario)
