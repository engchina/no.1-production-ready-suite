"""全文保存と短い表示用previewの分離、秘密値除去、失敗状態を確認する。"""

import json

import pytest

from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_body_capture import call_body_capture
from pr_backend_core.observability.call_details import call_content, withhold_call_details
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.trace_bodies import BodyFailure, PreparedBody
from pr_backend_core.observability.trace_body_writer import BodySubmission
from pr_backend_core.observability.tracing import (
    configure_tracing,
    observed_span,
    operation_trace,
    shutdown_tracing,
)


def test_full_snapshot_keeps_list_tail_depth_and_secret_redaction() -> None:
    bodies: list[PreparedBody] = []

    def submit(body: PreparedBody) -> BodySubmission:
        bodies.append(body)
        return BodySubmission(body.reference, True)

    deep: object = {"api_key": "sensitive-at-depth", "text": "deep-tail"}
    for _ in range(40):
        deep = {"child": deep}
    value = {
        "input": "x" * 80000 + "final-condition",
        "rows": list(range(1301)),
        "deep": deep,
        "password": "sensitive-value",
        "image_url": "sensitive-media",
    }
    with call_body_capture(submit, part_characters=4096):
        content = call_content(value)
    value["input"] = "changed-after-capture"
    assert content.status == "recorded" and content.preview_only and content.body_reference
    assert len(content.text) <= 64000
    (body,) = bodies
    full = json.loads(body.text)
    assert full["input"].endswith("final-condition")
    assert full["rows"] == list(range(1301))
    assert "deep-tail" in body.text and "omitted]" not in json.dumps(full["deep"])
    assert all(
        value not in body.text
        for value in [
            "sensitive-at-depth",
            "sensitive-value",
            "sensitive-media",
            "changed-after-capture",
        ]
    )
    assert "".join(part.text for part in body.parts()) == body.text
    assert call_content("small").body_reference is None


@pytest.mark.parametrize("reason", ["queue_full", "writer_stopped", "storage_unavailable"])
def test_rejected_capture_is_explicitly_incomplete(reason: BodyFailure) -> None:
    def submit(body: PreparedBody) -> BodySubmission:
        return BodySubmission(body.reference, False, reason)

    with call_body_capture(submit, part_characters=100):
        content = call_content({"input": "synthetic" * 10000})
    assert content.status == "incomplete" and content.failure_code == reason
    assert content.body_reference is not None


def test_capture_storage_exception_is_not_raised_or_put_in_snapshot() -> None:
    def submit(body: PreparedBody) -> BodySubmission:
        raise RuntimeError("sensitive-error")

    with call_body_capture(submit, part_characters=100):
        content = call_content("input" * 15000)
    assert content.status == "incomplete" and content.failure_code == "storage_unavailable"
    assert "sensitive-error" not in content.model_dump_json()


def test_unserializable_cycle_is_not_reported_as_complete() -> None:
    values: list[object] = []
    values.append(values)
    bodies: list[PreparedBody] = []

    def submit(body: PreparedBody) -> BodySubmission:
        bodies.append(body)
        return BodySubmission(body.reference, True)

    with call_body_capture(submit, part_characters=100):
        content = call_content(values)
    assert content.status == "incomplete" and content.failure_code == "capture_failed"
    assert not bodies


def test_restricted_input_schema_and_output_are_restricted_before_submit() -> None:
    bodies: list[PreparedBody] = []

    def submit(body: PreparedBody) -> BodySubmission:
        bodies.append(body)
        return BodySubmission(body.reference, True)

    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with (
            call_content_policy(lambda: True),
            call_body_capture(submit, part_characters=100),
            operation_trace("run", operation_id="synthetic") as result,
            observed_span("synthetic.call") as span,
        ):
            span.set_call_input(
                {"input": "private-input" * 10000},
                schema={"type": "object", "description": "schema" * 15000},
                restricted=True,
            )
            span.set_call_output({"output": "private-output" * 10000})
        assert len(bodies) == 3
        assert all(body.reference.visibility == "system_admin" for body in bodies)
        saved = next(item for item in result.spans if item.operation_name == "synthetic.call")
        assert saved.display and saved.display.details and saved.display.details.call
        data = saved.display.details.call.model_dump(by_alias=True)
        hidden = withhold_call_details(data, allow_restricted=False)
        assert all(body.reference.body_id not in json.dumps(hidden) for body in bodies)
        assert hidden["input"]["status"] == "withheld"
    finally:
        shutdown_tracing()
