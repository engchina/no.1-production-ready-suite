"""分割読取だけに適用する、上限内の契約修正と匿名の診断。"""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError
from test_batched_evidence import config, prepare, span

from rag_engine.adapters import oci
from rag_engine.generation import batched_evidence
from rag_engine.generation.batch_output_repair import batch_failure_summary, batch_validation_detail
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


TABLE = "| 項目 | 条件 | 期限 |\n| --- | --- | --- |\n| 申請A | 条件甲〜条件乙 | 10 日 |\n| 申請B | 条件丙 | 20 日 |\n"
LONG_INPUT = "第一段の合成の説明です。\n\n" + TABLE + "\n" + "第二段の合成の説明です。" * 40
SECOND_INPUT = "別の入力の合成の記載です。" * 5


def findings_of(*rows: tuple[str, str]) -> dict:
    return {
        "read_receipts": {"B1": True, "B2": True},
        "findings": [{"evidence_id": evidence_id, "quote": quote, "answer": "合成の回答"} for evidence_id, quote in rows],
        "unanswered": [],
    }


def reading_model():
    return _reading_model([{"text": LONG_INPUT}, {"text": SECOND_INPUT}])


@pytest.mark.parametrize(
    "quote",
    [
        "第一段の合成の説明です。 | 項目 | 条件 | 期限 |",
        "| 申請A | 条件甲〜条件乙 | 10 日 |\r\n| 申請B | 条件丙 | 20 日 |",
        "|申請A|条件甲〜条件乙|10日|",
        "第一段の合成の説明です。\n\n| 項目 |",
    ],
    ids=["newline_to_space", "crlf", "table_spacing", "paragraph_break"],
)
def test_whitespace_in_long_table_and_line_quotes_is_already_accepted(quote):
    """#4090: 改行・表の空白の違いは今の照合で通るので、不合格の原因にならない（検査は緩めない）。"""
    reading_model().model_validate(findings_of(("B1", quote)))


def mismatch_detail(*rows: tuple[str, str]) -> dict:
    model = reading_model()
    with pytest.raises(ValidationError) as raised:
        model.model_validate(findings_of(*rows))
    detail = batch_validation_detail(raised.value, model.model_json_schema())
    assert json.dumps(detail, ensure_ascii=False).count("合成") == 0
    row = detail["validation_errors"][0]
    assert row["protocol_code"] == "batch_quote_not_in_input"
    return row["quote_mismatch"]


@pytest.mark.parametrize(
    ("row", "reason", "expected"),
    [
        (("B9", "第一段の合成の説明です。"), "evidence_id_unknown", {"other_input_matches": 1, "input_chars": 0}),
        (("B2", "第一段の合成の説明です。"), "quote_in_other_input", {"other_input_matches": 1}),
        (("B1", "第二段の合成の説明です。別の入力の合成の記載です。"), "quote_across_inputs", {"other_input_matches": 0}),
        (("B1", "入力に存在しない合成の引用"), "quote_not_found", {"prefix_match_chars": 0}),
        (("B1", "第一段の合成の説明です。…第二段の合成の説明です。"), "quote_not_found", {"elision_mark": True, "prefix_match_chars": 12}),
        (("B1", "| 申請A |\\n| 申請B |"), "quote_not_found", {"literal_escape": True}),
        (("B1", "申請A条件甲〜条件乙10日"), "quote_not_found", {"symbol_fold_match": False}),
        (("B1", "条件甲~条件乙"), "quote_not_found", {"symbol_fold_match": True}),
    ],
    ids=["unknown_id", "other_input", "across_inputs", "not_found", "elision", "literal_escape", "table_cells_joined", "wave_dash"],
)
def test_quote_mismatch_is_classified_without_contents(row, reason, expected):
    mismatch = mismatch_detail(row)
    assert mismatch["unmatched_count"] == 1
    assert mismatch["reasons"] == {reason: 1}
    sample = mismatch["samples"][0]
    assert sample["reason"] == reason
    assert sample["quote_chars"] > 0
    assert {key: sample[key] for key in expected} == expected


def test_quote_mismatch_keeps_counts_for_all_findings_and_a_bounded_sample():
    rows = [("B1", f"存在しない合成の引用{number}") for number in range(20)]
    mismatch = mismatch_detail(("B1", "第一段の合成の説明です。"), *rows)
    assert mismatch["unmatched_count"] == 20
    assert mismatch["reasons"] == {"quote_not_found": 20}
    assert len(mismatch["samples"]) == 8


@pytest.mark.parametrize(
    ("payload", "category", "code"),
    [
        (findings_of(("B1", "入力に存在しない合成の引用")), "schema_field", "batch_quote_not_in_input"),
        ({**findings_of(), "read_receipts": {"B1": True, "B2": False}}, "schema_field", "batch_read_coverage_incomplete"),
        ({**findings_of(), "read_receipts": {"B1": True}}, "schema_field", None),
        ({**findings_of(), "read_receipts": {"B1": True, "B2": "true"}}, "schema_field", None),
        ({"read_receipts": {"B1": True, "B2": True}, "findings": []}, "schema_field", None),
        ({**findings_of(), "extra": 1}, "schema_field", None),
    ],
    ids=["quote", "unread_input", "missing_receipt", "non_bool_receipt", "missing_field", "extra_field"],
)
def test_each_contract_violation_keeps_its_own_class(payload, category, code):
    model = reading_model()
    with pytest.raises(ValidationError) as raised:
        model.model_validate(payload)
    detail = batch_validation_detail(raised.value, model.model_json_schema())
    assert detail["category"] == category
    assert [row.get("protocol_code") for row in detail["validation_errors"]][0] == code


def test_broken_json_is_classified_as_invalid_json():
    model = reading_model()
    with pytest.raises(ValidationError) as raised:
        model.model_validate_json('{"read_receipts": ')
    assert batch_validation_detail(raised.value, model.model_json_schema())["category"] == "invalid_json"


def test_retry_prompt_does_not_carry_the_mismatch_diagnostic(monkeypatch):
    calls = []

    def provider(system, prompt, settings, schema, ignored):
        calls.append(prompt)
        result = output(prompt)
        if len(calls) == 1:
            result["findings"][0]["quote"] = "入力に存在しない合成の引用"
        return schema.model_validate(result)

    monkeypatch.setattr(oci, "_parse_text_once", provider)
    _, _, diagnostic = prepare([span(0, "合成の条件A。" * 600)], reader)
    assert "batch_quote_not_in_input" in calls[1]
    assert "quote_mismatch" not in calls[1]
    assert "unmatched_count" not in calls[1]
    failure = diagnostic["batches"][0]["validation_failures"][0]["validation_errors"][0]
    assert failure["quote_mismatch"]["reasons"] == {"quote_not_found": 1}


def test_failed_batch_summary_has_only_class_counts_and_capacity(monkeypatch):
    """#4090: 回答の診断には、失敗の類別・件数・容量だけを渡す（質問・引用・原文を渡さない）。"""
    secret = "入力に存在しない合成の引用"

    def provider(system, prompt, settings, schema, ignored):
        result = output(prompt)
        result["findings"][0]["quote"] = secret
        return schema.model_validate(result)

    monkeypatch.setattr(oci, "_parse_text_once", provider)
    failed: list[dict] = []
    with pytest.raises(ValidationError) as raised:
        batched_evidence.prepare_batched_evidence(
            [span(0, "合成の条件A。" * 600)],
            config(),
            question="合成の質問",
            render=lambda values: json.dumps([value["text"] for value in values], ensure_ascii=False),
            evidence_text=lambda values: json.dumps([value["text"] for value in values], ensure_ascii=False),
            system="指示",
            schema={"type": "object"},
            read=reader,
            failed_batches=failed,
        )

    assert len(failed) == 1
    summary = batch_failure_summary(failed[0], raised.value)
    assert summary["category"] == "batch_quote_not_in_input"
    assert summary["batch_index"] == 1
    assert summary["invalid_attempts"] == 2
    assert summary["input_count"] == 1
    assert summary["input_chars"] > 0
    assert summary["unmatched_count"] == 1
    assert summary["unmatched_reasons"] == {"quote_not_found": 1}
    serialized = json.dumps(summary, ensure_ascii=False)
    assert "合成" not in serialized


def test_a_failure_without_validation_detail_still_has_a_class():
    receipt = {"index": 2, "input_count": 3, "input_chars": 90, "status": "failed"}
    summary = batch_failure_summary(receipt, oci.LlmRunawayOutput("合成", "runaway_repetition", None))
    assert summary == {"category": "runaway_output", "batch_index": 2, "input_count": 3, "input_chars": 90, "invalid_attempts": 0}
