"""形式修正の範囲とSDK継続の対応を合成fixtureで検証する。"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from agents import Agent, RunContextWrapper, RunResult
from agents.items import MessageOutputItem
from agents.usage import Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from test_answer_evidence import _step

from app.features.agent.answer_data import DataReferenceError, data_index, model_data_references
from app.features.agent.answer_data_patches import (
    PatchPlan,
    PatchTarget,
    ReferencePatch,
    ReferencePatches,
    applied_result,
    plan_patches,
)


def sdk_result(text: str) -> RunResult:
    agent: Agent[Any] = Agent(name="合成の回答")
    item = MessageOutputItem(
        agent=agent,
        raw_item=ResponseOutputMessage(
            id="message-a",
            type="message",
            role="assistant",
            status="completed",
            content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
        ),
    )
    return RunResult(
        input="合成の質問",
        new_items=[item],
        raw_responses=[],
        final_output=text,
        input_guardrail_results=[],
        output_guardrail_results=[],
        tool_input_guardrail_results=[],
        tool_output_guardrail_results=[],
        context_wrapper=RunContextWrapper(context={}),
        _last_agent=agent,
    )


def validator(text: str) -> None:
    if "[[cell:bad]]" in text or "[[cite:bad]]" in text:
        raise DataReferenceError("合成の形式エラー")


def response(plan: PatchPlan, replacements: list[str]) -> ReferencePatches:
    return ReferencePatches(
        patches=[
            ReferencePatch(target_id=t.target_id, replacement=value)
            for t, value in zip(plan.targets, replacements, strict=True)
        ],
    )


def test_multiple_raw_targets_unicode_crlf_outside_text_is_byte_exact() -> None:
    answer = "前😀\r\n[[cell:bad]]\r\n正常な値 [[cell:valid]]\r\n[[cite:bad]]\r\n後𠮷"
    plan = plan_patches(answer, validator)
    assert [t.text for t in plan.targets] == ["[[cell:bad]]", "[[cite:bad]]"]
    patched = plan.apply(response(plan, ["[[cell:fixed]]", "[[cite:1]]"]), current_answer=answer)
    assert (
        patched.encode()
        == answer.replace("[[cell:bad]]", "[[cell:fixed]]")
        .replace("[[cite:bad]]", "[[cite:1]]")
        .encode()
    )
    assert "answer_sha256" not in plan.payload()


@pytest.mark.parametrize("kind", ["unknown", "duplicate", "missing"])
def test_response_targets_and_original_hash_are_strict(kind: str) -> None:
    plan = plan_patches("[[cell:bad]]", validator)
    value = response(plan, ["fixed"])
    if kind == "unknown":
        value.patches[0].target_id = "P2"
    elif kind == "duplicate":
        value.patches.append(value.patches[0])
    elif kind == "missing":
        value.patches = []
    with pytest.raises(DataReferenceError):
        plan.apply(value, current_answer=plan.answer)


def test_overlapping_and_mutated_ranges_are_rejected() -> None:
    plan = PatchPlan(
        "abcdef", (PatchTarget("P1", 0, 4, "abcd", "e"), PatchTarget("P2", 2, 6, "cdef", "e"))
    )
    with pytest.raises(DataReferenceError, match="範囲"):
        plan.apply(response(plan, ["x", "y"]), current_answer=plan.answer)
    changed = replace(plan, targets=(PatchTarget("P1", 0, 4, "other", "e"),))
    with pytest.raises(DataReferenceError, match="範囲"):
        changed.apply(response(changed, ["x"]), current_answer=changed.answer)


def test_invalid_matrix_is_one_target_valid_external_quantity_unchanged() -> None:
    output: dict[str, Any] = {
        "status": "done",
        "columns": ["ID", "VALUE"],
        "rows": [{"ID": "A", "VALUE": 2.5}],
        "column_metadata": [
            {"name": "ID", "unit_status": "unknown"},
            {"name": "VALUE", "unit_status": "known", "unit": "m"},
        ],
    }
    step = _step("nl2sql_query", output)
    refs = model_data_references(step.id, output)
    assert refs is not None
    quantity = refs["quantities"][0]["VALUE"]
    index = data_index([step])
    matrix = f"| 項目 | 値 |\r\n|---|---|\r\n| A | {quantity} |\r\n"
    answer = f"正しい値 {quantity}。\r\n" + matrix + "\r\n末尾"
    plan = plan_patches(answer, index.render)
    assert len(plan.targets) == 1 and plan.targets[0].text == matrix
    patched = plan.apply(response(plan, [refs["table"] + "\r\n"]), current_answer=answer)
    assert patched.startswith(f"正しい値 {quantity}。\r\n") and patched.endswith("\r\n末尾")
    assert "2.5 m" in index.render(patched)


def test_unlocatable_aggregate_failure_does_not_rewrite_answer() -> None:
    with pytest.raises(DataReferenceError, match="修正範囲"):
        plan_patches("正常な本文", lambda _: None)


def test_applied_result_history_checkpoint_and_raw_json_are_separate() -> None:
    original = sdk_result("prefix [[cell:bad]] suffix")
    repair = sdk_result('{"patches":[]}')
    repair.context_wrapper.usage = Usage(requests=2, total_tokens=33)
    applied = applied_result(original, repair, "prefix fixed suffix")
    assert (
        applied.final_output == "prefix fixed suffix"
        and applied.context_wrapper.usage.requests == 2
    )
    assert "prefix fixed suffix" in json.dumps(applied.to_input_list(), ensure_ascii=False)
    assert "patches" not in json.dumps(applied.to_input_list())
    state = applied.to_state().to_string()
    assert "prefix fixed suffix" in state and "patches" not in state
    assert (
        repair.final_output == '{"patches":[]}'
        and original.final_output == "prefix [[cell:bad]] suffix"
    )
    assert "[[cell:bad]]" in json.dumps(original.to_input_list())


async def test_runtime_one_structured_repair_without_tools_and_no_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.agent import builtin_runtime as runtime
    from app.features.agent import tool_grounding
    from app.features.agent.runtime import runtime_repository

    answer = "prefix [[cell:bad]] suffix"
    current = SimpleNamespace(id="run-synthetic", metadata={}, steps=[])
    original = sdk_result(answer)
    calls: list[Any] = []

    async def run_sdk(run_id: str, agent: Agent[Any], inputs: Any, *, context: Any) -> Any:
        calls.append((agent.tools, agent.output_type, inputs))
        plan = plan_patches(answer, data_index([]).render)
        value = response(plan, ["fixed"])
        repair = sdk_result(value.model_dump_json())
        repair.final_output = value
        return repair

    def record(run_id: str, key: str, value: str) -> None:
        current.metadata[tool_grounding.METADATA_KEY] = {key: True}

    monkeypatch.setattr(runtime_repository, "get_run", lambda _: current)
    monkeypatch.setattr(runtime_repository, "record_tool_grounding", record)
    monkeypatch.setattr(runtime, "_run_sdk", run_sdk)
    actual = await runtime._require_data_references(current, Agent(name="合成"), original, None)
    assert actual.final_output == "prefix fixed suffix"
    assert len(calls) == 1 and calls[0][0] == [] and calls[0][1] is ReferencePatches
    assert (
        await runtime._require_data_references(current, Agent(name="合成"), actual, None) is actual
    )
    assert len(calls) == 1


def test_program_hash_binding_rejects_changed_original_without_model_copy() -> None:
    plan = plan_patches("[[cell:bad]]", validator)
    with pytest.raises(DataReferenceError, match="回答が一致"):
        plan.apply(response(plan, ["fixed"]), current_answer="changed [[cell:bad]]")
    assert plan.sha256 not in json.dumps(plan.payload())
    assert "answer_sha256" not in ReferencePatches.model_json_schema()["properties"]


def test_inline_table_targets_only_marker_not_valid_same_line_prose() -> None:
    plan = plan_patches("前 [[table:T1]] 後", lambda _: None)
    assert [t.text for t in plan.targets] == ["[[table:T1]]"]
    patched = plan.apply(response(plan, ["\n[[table:T1]]\n"]), current_answer=plan.answer)
    assert patched == "前 \n[[table:T1]]\n 後"


@pytest.mark.parametrize(
    "marker", ["[[cell:bad", "[[quantity:bad]]", "[[table:bad]]", "[[cite:bad]]"]
)
def test_malformed_or_unknown_marker_is_exact_target(marker: str) -> None:
    def fail(text: str) -> None:
        raise DataReferenceError("不正な形式")

    plan = plan_patches(marker, fail)
    assert [t.text for t in plan.targets] == [marker]


def test_source_expansion_never_changes_raw_patch_coordinates() -> None:
    def validate(text: str) -> None:
        expanded = text.replace("[[cite:1]]", "【根拠: " + "x" * 200 + "】")
        validator(expanded)

    answer = "😀 [[cite:1]]\r\n[[cell:bad]]\r\n保持"
    plan = plan_patches(answer, validate)
    assert len(plan.targets) == 1 and plan.targets[0].start == answer.index("[[cell:bad]]")
    assert plan.apply(response(plan, ["fixed"]), current_answer=answer) == answer.replace(
        "[[cell:bad]]", "fixed"
    )


async def test_runtime_truncation_does_not_restart_or_publish_patch_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.agent import builtin_runtime as runtime
    from app.features.agent.runtime import runtime_repository

    current = SimpleNamespace(id="run-synthetic", metadata={}, steps=[])
    original = sdk_result("[[cell:bad]]")
    calls: list[int] = []

    class TerminalOutputLimit(RuntimeError):
        pass

    async def stop(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        raise TerminalOutputLimit("合成の出力上限")

    monkeypatch.setattr(runtime_repository, "get_run", lambda _: current)
    monkeypatch.setattr(runtime_repository, "record_tool_grounding", lambda *args: None)
    monkeypatch.setattr(runtime, "_run_sdk", stop)
    with pytest.raises(TerminalOutputLimit):
        await runtime._require_data_references(current, Agent(name="合成"), original, None)
    assert len(calls) == 1 and original.final_output == "[[cell:bad]]"


async def test_runtime_metadata_failure_and_pause_do_not_call_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.agent import builtin_runtime as runtime
    from app.features.agent import source_handles
    from app.features.agent.runtime import runtime_repository

    current = SimpleNamespace(
        id="run-synthetic", metadata={source_handles.REGISTRY_KEY: "broken"}, steps=[]
    )

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("保存状態の不正や中断中に修正を呼んではいけません。")

    monkeypatch.setattr(runtime_repository, "get_run", lambda _: current)
    monkeypatch.setattr(runtime, "_run_sdk", forbidden)
    original = sdk_result("[[cite:1]]")
    with pytest.raises(DataReferenceError, match="対応表"):
        await runtime._require_data_references(current, Agent(name="合成"), original, None)
    original.interruptions = [SimpleNamespace()]  # type: ignore[list-item]
    assert (
        await runtime._require_data_references(current, Agent(name="合成"), original, None)
        is original
    )


async def test_actual_sdk_processed_response_and_restored_state_hold_applied_answer() -> None:
    from agents import RunConfig, Runner, RunState
    from agents.testing import ModelStep, ScriptedModel, assistant_message

    model = ScriptedModel([ModelStep(output=[assistant_message("合成 [[cell:bad]] 終了")])])
    agent: Agent[Any] = Agent(name="合成", model=model)
    original = await Runner.run(agent, "合成", run_config=RunConfig(tracing_disabled=True))
    repair = sdk_result('{"patches":[]}')
    applied = applied_result(original, repair, "合成 fixed 終了")
    restored = await RunState.from_string(agent, applied.to_state().to_string())
    restored_json = json.dumps(json.loads(restored.to_string()), ensure_ascii=False)
    assert "合成 fixed 終了" in restored_json
    generated = json.dumps(
        [item.to_input_item() for item in restored._generated_items], ensure_ascii=False
    )
    assert "合成 fixed 終了" in generated and "[[cell:bad]]" not in generated
    assert "patches" not in restored.to_string()
    assert "[[cell:bad]]" not in json.dumps(applied.to_input_list(mode="normalized"))
    assert "[[cell:bad]]" in original.final_output


async def test_runtime_invalid_replacement_preserves_usage_and_no_second_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.agent import builtin_runtime as runtime
    from app.features.agent.runtime import runtime_repository

    current = SimpleNamespace(id="run-synthetic", metadata={}, steps=[])
    original = sdk_result("[[cell:bad]]")

    async def run_sdk(*args: Any, **kwargs: Any) -> Any:
        repair = sdk_result("patch")
        repair.context_wrapper.usage = Usage(requests=2, total_tokens=99)
        repair.final_output = ReferencePatches(
            patches=[ReferencePatch(target_id="P1", replacement="[[cell:stillbad]]")]
        )
        return repair

    monkeypatch.setattr(runtime_repository, "get_run", lambda _: current)
    monkeypatch.setattr(runtime_repository, "record_tool_grounding", lambda *args: None)
    monkeypatch.setattr(runtime, "_run_sdk", run_sdk)
    with pytest.raises(DataReferenceError):
        await runtime._require_data_references(current, Agent(name="合成"), original, None)
    assert (
        original.context_wrapper.usage.requests == 2
        and original.context_wrapper.usage.total_tokens == 99
    )
    assert original.final_output == "[[cell:bad]]"


def test_manual_suffix_outside_bad_marker_is_not_silently_rewritten() -> None:
    plan = plan_patches("値は[[cell:bad]]%。", validator)
    patched = plan.apply(response(plan, ["[[quantity:T1:R1:C1]]"]), current_answer=plan.answer)
    assert patched == "値は[[quantity:T1:R1:C1]]%。"


def test_applied_answer_retains_recorded_custom_fields_and_sdk_item_identity() -> None:
    from agents.items import ToolCallOutputItem

    original = sdk_result("合成 [[cell:bad]]")
    output = {"custom_field": "合成の値"}
    record = ToolCallOutputItem(
        agent=original.last_agent,
        raw_item={
            "type": "function_call_output",
            "call_id": "record-a",
            "output": json.dumps(output),
        },
        output=output,
    )
    original.new_items.insert(0, record)
    message = original.new_items[-1]
    applied = applied_result(original, sdk_result("patch"), "合成 fixed")
    assert applied.new_items[0] is record and record.output == output
    final_message = applied.new_items[-1]
    assert isinstance(final_message, MessageOutputItem)
    assert isinstance(message, MessageOutputItem)
    assert final_message.raw_item.id == message.raw_item.id
    assert final_message.agent is message.agent
    recorded = next(
        item
        for item in applied.to_input_list()
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    )
    serialized = recorded.get("output")
    assert isinstance(serialized, str)
    assert json.loads(serialized) == output
