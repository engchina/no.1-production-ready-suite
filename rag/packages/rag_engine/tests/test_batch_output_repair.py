"""分割読取だけに適用する、上限内の契約修正と匿名の診断。"""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError
from test_batched_evidence import config, prepare, span

from rag_engine.adapters import oci
from rag_engine.generation import batched_evidence
from rag_engine.generation.batch_output_repair import batch_validation_detail
from rag_engine.generation.batched_evidence import _reading_model
from rag_engine.generation.input_budget import GenerationInputBudgetExceeded
from rag_engine.resources.deadline import Deadline, DeadlineExceeded, bind_deadline
from rag_engine.resources.structured_output_retry import bind_structured_output_repair, structured_output_prompt


def reader(system, prompt, schema):
    settings = config()
    provider = next(iter(settings.llm_providers.values()))
    return oci._parse_text_with_retry(system, prompt, settings, schema, provider)


def output(prompt):
    original = prompt.split("\n\n## Step 4:", 1)[0]
    values = json.loads(original)["evidence"]
    return {
        "read_receipts": {row["evidence_id"]: True for row in values},
        "findings": [{"evidence_id": row["evidence_id"], "quote": row["text"][:16], "answer": "合成の回答"} for row in values],
        "unanswered": [],
    }


@pytest.mark.parametrize("failure", ["receipt", "quote"])
def test_one_existing_retry_receives_targeted_feedback_and_keeps_all_inputs(monkeypatch, failure):
    calls = []

    def provider(system, prompt, settings, schema, ignored):
        calls.append(prompt)
        result = output(prompt)
        if len(calls) == 1:
            if failure == "receipt":
                result["read_receipts"] = {}
            else:
                result["findings"][0]["quote"] = "入力に存在しない合成の引用"
        elif len(calls) == 2:
            assert "validation_errors" in prompt
            assert ("read_receipts" if failure == "receipt" else "batch_quote_not_in_input") in prompt
        return schema.model_validate(result)

    monkeypatch.setattr(oci, "_parse_text_once", provider)
    selected, _, diagnostic = prepare([span(0, "合成の条件A。" * 600)], reader)
    assert len(calls) == diagnostic["batch_count"] + 1
    assert all(row["status"] == "completed" for row in diagnostic["batches"])
    first = diagnostic["batches"][0]
    assert first["repair_input"]["original_prompt_chars"] == len(calls[0])
    assert calls[1].startswith(calls[0] + "\n\n## Step 4:")
    assert all("Step 4:" not in call for call in calls[2:])
    assert diagnostic["excluded_span_count"] == 0
    assert diagnostic["read_source_count"] == 1
    assert selected
    assert structured_output_prompt("ordinary") == "ordinary"


def test_first_success_keeps_prompt_bytes_and_call_count(monkeypatch):
    first = []
    delivered = []

    def read(system, prompt, schema):
        first.append(prompt)
        return reader(system, prompt, schema)

    def provider(system, prompt, settings, schema, ignored):
        delivered.append(prompt)
        return schema.model_validate(output(prompt))

    monkeypatch.setattr(oci, "_parse_text_once", provider)
    _, _, diagnostic = prepare([span(0, "合成の条件A。" * 600)], read)
    assert first == delivered
    assert len(delivered) == diagnostic["batch_count"]
    assert all("repair_input" not in batch for batch in diagnostic["batches"])


def test_two_invalid_outputs_do_not_add_a_third_call_and_restore_context(monkeypatch):
    calls = []
    diagnostics = []

    def provider(system, prompt, settings, schema, ignored):
        calls.append(prompt)
        return schema.model_validate({"read_receipts": {}, "findings": [], "unanswered": []})

    monkeypatch.setattr(oci, "_parse_text_once", provider)
    monkeypatch.setattr(batched_evidence, "record_retrieval_stage", diagnostics.append)
    with pytest.raises(ValidationError):
        prepare([span(0, "合成の条件A。" * 600)], reader)
    assert len(calls) == 2
    assert len(diagnostics[0]["validation_failures"]) == 2
    assert diagnostics[0]["status"] == "failed"
    assert structured_output_prompt("ordinary") == "ordinary"


def test_safe_errors_do_not_expose_input_message_context_or_unknown_keys():
    schema = _reading_model([span(0, "非公開の合成本文")])
    secret = "PRIVATE_SYNTHETIC_VALUE"
    with pytest.raises(ValidationError) as caught:
        schema.model_validate(
            {
                "read_receipts": {"B1": True, secret: False},
                "findings": [{"evidence_id": "B1", "quote": secret, "answer": secret}],
                "unanswered": [],
            }
        )
    detail = batch_validation_detail(caught.value, schema.model_json_schema())
    saved = json.dumps(detail)
    assert secret not in saved
    assert "非公開" not in saved
    assert "<redacted>" in saved
    assert all(set(row) <= {"loc", "type", "protocol_code"} for row in detail["validation_errors"])


def test_unknown_validator_context_is_not_serialized():
    secret = "PRIVATE_SYNTHETIC_CONTEXT"
    schema = _reading_model([span(0, "合成の本文")])
    error = ValidationError.from_exception_data(
        "BatchReading",
        [
            {
                "type": "value_error",
                "loc": ("findings", 0),
                "input": secret,
                "ctx": {"error": ValueError(secret)},
            }
        ],
    )
    detail = batch_validation_detail(error, schema.model_json_schema())
    assert secret not in json.dumps(detail)
    assert "protocol_code" not in detail["validation_errors"][0]


def test_known_quote_protocol_code_is_preserved_without_quote():
    schema = _reading_model([span(0, "合成の本文")])
    with pytest.raises(ValidationError) as caught:
        schema.model_validate(
            {
                "read_receipts": {"B1": True},
                "findings": [{"evidence_id": "B1", "quote": "不存在の合成引用", "answer": "合成回答"}],
                "unanswered": [],
            }
        )
    detail = batch_validation_detail(caught.value, schema.model_json_schema())
    assert detail["validation_errors"][0]["protocol_code"] == "batch_quote_not_in_input"
    assert "不存在" not in json.dumps(detail)


def test_feedback_over_input_budget_stops_before_retry(monkeypatch):
    calls = []
    original_measure = batched_evidence.model_input_measurement

    def measurement(settings, system, prompt, schema, **kwargs):
        measured = original_measure(settings, system, prompt, schema, **kwargs)
        return {**measured, "fits": False} if "## Step 4:" in prompt else measured

    def provider(system, prompt, settings, schema, ignored):
        calls.append(prompt)
        return schema.model_validate({"read_receipts": {}, "findings": [], "unanswered": []})

    monkeypatch.setattr(batched_evidence, "model_input_measurement", measurement)
    monkeypatch.setattr(oci, "_parse_text_once", provider)
    with pytest.raises(GenerationInputBudgetExceeded) as caught:
        prepare([span(0, "合成の条件A。" * 600)], reader)
    assert len(calls) == 1
    assert caught.value.diagnostic["reason"] == "batch_repair_input_budget_exceeded"
    assert caught.value.diagnostic["batches"][0]["repair_input"]["status"] == "input_budget_exceeded"


def test_cancellation_after_first_rejection_prevents_retry(monkeypatch):
    calls = []
    deadline = Deadline()

    def provider(system, prompt, settings, schema, ignored):
        calls.append(prompt)
        deadline.cancel()
        return schema.model_validate({"read_receipts": {}, "findings": [], "unanswered": []})

    monkeypatch.setattr(oci, "_parse_text_once", provider)
    with bind_deadline(deadline), pytest.raises(DeadlineExceeded):
        prepare([span(0, "合成の条件A。" * 600)], reader)
    assert len(calls) == 1
    assert structured_output_prompt("ordinary") == "ordinary"


def test_context_is_scoped_to_each_thread_and_restored():
    barrier = Barrier(2)

    def run(number):
        with bind_structured_output_repair(lambda error, retry: f"synthetic-{number}"):
            from rag_engine.resources.structured_output_retry import notify_invalid_output

            notify_invalid_output(ValueError(), True)
            barrier.wait(timeout=5)
            assert structured_output_prompt("original") == f"synthetic-{number}"
        return structured_output_prompt("original")

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, [1, 2])) == ["original", "original"]
