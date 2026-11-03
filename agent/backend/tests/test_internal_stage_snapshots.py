"""待ち・未実行の工程は実値だけを記録し、内部接続の文脈を公開しない。"""

import json
from datetime import UTC, datetime

import pytest
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import operation_trace, record_span

from app.features.agent.chat_progress import (
    APPROVAL_WAIT_SPAN,
    CONFIRMATION_WAIT_SPAN,
    approval_wait_display,
    confirmation_wait_display,
    tool_display,
)
from app.features.agent.confirmation import (
    ConfirmationAnswer,
    ConfirmationItem,
    ConfirmationRequest,
)
from app.features.agent.runtime import ApprovalRequest
from app.features.agent.tools import ToolCall, ToolResult


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("status", ["answered", "skipped"])
def test_confirmation_wait_records_actual_answer_without_internal_context(
    enabled: bool, status: str
) -> None:
    now = datetime.now(UTC)
    request = ConfirmationRequest(
        source="agent",
        items=[ConfirmationItem(id="item-a", prompt="合成の確認")],
        answers=[ConfirmationAnswer(item_id="item-a", other_text="合成の回答")]
        if status == "answered"
        else [],
        status=status,
        context={"connection_id": "private-internal-context"},
        round=2,
        created_at=now,
        decided_at=now,
    )
    with (
        call_content_policy(lambda: enabled),
        operation_trace("run", operation_id="synthetic") as summary,
    ):
        record_span(
            CONFIRMATION_WAIT_SPAN,
            started_at=now,
            finished_at=now,
            display=confirmation_wait_display(request),
        )
    span = next(item for item in summary.spans if item.operation_name == CONFIRMATION_WAIT_SPAN)
    assert span.display and span.display.details and span.display.details.call
    call = span.display.details.call
    assert span.display.params == {"round": 2}
    assert call.capture_enabled is enabled
    assert "private-internal-context" not in summary.model_dump_json()
    assert ("合成の確認" in call.input.text) is enabled
    if enabled:
        assert json.loads(call.output.text)["状態"] == status
        assert json.loads(call.output.text)["回答"] == [
            item.model_dump(mode="json") for item in request.answers
        ]


@pytest.mark.parametrize("enabled", [False, True])
def test_approval_and_unexecuted_tool_keep_real_decision_and_redact_secrets(enabled: bool) -> None:
    now = datetime.now(UTC)
    tool = ToolCall(
        name="synthetic-tool", arguments={"value": "合成の入力", "api_key": "private-key"}
    )
    approval = ApprovalRequest(
        run_id="synthetic-run",
        step_id="synthetic-step",
        tool_call=tool,
        reason="合成の承認理由",
        status="rejected",
        decided_at=now,
    )
    result = ToolResult(name=tool.name, success=False, error="合成の却下理由")
    with (
        call_content_policy(lambda: enabled),
        operation_trace("run", operation_id="synthetic") as summary,
    ):
        record_span(
            APPROVAL_WAIT_SPAN,
            started_at=now,
            finished_at=now,
            display=approval_wait_display([approval], 1, {}),
        )
        record_span(
            "gen_ai.execute_tool",
            started_at=now,
            finished_at=now,
            status="skipped",
            display=tool_display(tool, {}, result=result, rejected=True, include_call=True),
        )
    assert "private-key" not in summary.model_dump_json()
    spans = {item.operation_name: item for item in summary.spans}
    for name in [APPROVAL_WAIT_SPAN, "gen_ai.execute_tool"]:
        display = spans[name].display
        assert display and display.details and display.details.call
        call = display.details.call
        assert call.capture_enabled is enabled
        assert ("合成の入力" in call.input.text) is enabled
        if enabled:
            output = json.loads(call.output.text)
            if name == APPROVAL_WAIT_SPAN:
                assert output["回"] == 2
                assert output["判断"][0]["状態"] == "rejected"
            else:
                assert output["状態"] == "未実行"
                assert output["結果"]["error"] == "合成の却下理由"
