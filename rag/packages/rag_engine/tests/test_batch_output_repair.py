"""分割読取だけに適用する、上限内の契約修正と匿名の診断。"""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from pydantic import ValidationError
from test_batched_evidence import block, config, finding, prepare, span

from rag_engine.adapters import oci
from rag_engine.generation import batched_evidence
from rag_engine.generation.answering import _invalid_structured_output
from rag_engine.generation.batch_output_repair import (
    batch_failure_summary,
    batch_repair_prompt,
    batch_validation_detail,
    repair_feedback_upper_bound,
)
from rag_engine.generation.batched_evidence import BatchReading, _reading_model, reading_unit_counts
from rag_engine.generation.input_budget import model_input_measurement
from rag_engine.resources.deadline import Deadline, DeadlineExceeded, bind_deadline
from rag_engine.resources.structured_output_retry import bind_structured_output_repair, structured_output_prompt


def reader(system, prompt, schema):
    settings = config()
    provider = next(iter(settings.llm_providers.values()))
    return oci._parse_text_with_retry(system, prompt, settings, schema, provider)


def output(prompt):
    original = prompt.split("\n\n## Step 4:", 1)[0]
    values = json.loads(original)["evidence"]
    return {"findings": [finding(row) for row in values]}


@pytest.mark.parametrize("failure", ["schema", "range"])
def test_one_existing_retry_receives_targeted_feedback_and_keeps_all_inputs(monkeypatch, failure):
    calls = []

    def provider(system, prompt, settings, schema, ignored):
        calls.append(prompt)
        result = output(prompt)
        if len(calls) == 1:
            if failure == "schema":
                result.pop("findings")
            else:
                result["findings"][0]["start_line"] = result["findings"][0]["end_line"] = "B1-9999"
        elif len(calls) == 2:
            assert "validation_errors" in prompt
            assert ("findings" if failure == "schema" else "batch_range_invalid") in prompt
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
        return schema.model_validate({})

    monkeypatch.setattr(oci, "_parse_text_once", provider)
    monkeypatch.setattr(batched_evidence, "record_retrieval_stage", diagnostics.append)
    with pytest.raises(ValidationError):
        prepare([span(0, "合成の条件A。" * 600)], reader)
    assert len(calls) == 2 * len(diagnostics)
    assert all(len(row["validation_failures"]) == 2 for row in diagnostics)
    assert all(row["status"] == "failed" for row in diagnostics)
    assert structured_output_prompt("ordinary") == "ordinary"


def test_safe_errors_do_not_expose_input_message_context_or_unknown_keys():
    schema = _reading_model([span(0, "非公開の合成本文")])
    secret = "PRIVATE_SYNTHETIC_VALUE"
    with pytest.raises(ValidationError) as caught:
        schema.model_validate({"findings": [{"start_line": secret, "end_line": secret, "answer": secret}], "unanswered": [secret]})
    detail = batch_validation_detail(caught.value, schema.model_json_schema())
    saved = json.dumps(detail)
    assert secret not in saved
    assert "非公開" not in saved
    assert all(set(row) <= {"loc", "type", "protocol_code", "range_mismatch"} for row in detail["validation_errors"])


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


def test_a_batch_packed_to_the_limit_still_has_room_for_the_repair_feedback(monkeypatch):
    """#4229: 上限の近くまで詰めた組でも、修復の指摘の分が入力の予算に入り、回答全体を退避させない。"""
    settings = replace(config(answer_max_input_tokens=3000), evidence_budget_chars=200_000)
    delivered = []

    def provider(system, prompt, ignored_settings, schema, ignored):
        delivered.append((system, prompt))
        result = output(prompt)
        if "## Step 4:" not in prompt:
            result["findings"][0]["start_line"] = result["findings"][0]["end_line"] = "B1-9999"
        else:
            result["findings"] = []
        return schema.model_validate(result)

    monkeypatch.setattr(oci, "_parse_text_once", provider)
    _, _, diagnostic = batched_evidence.prepare_batched_evidence(
        [span(index, f"合成の条件{index}。" * 4) for index in range(200)],
        settings,
        question="合成の質問",
        render=block,
        evidence_text=block,
        system="指示",
        schema={"type": "object"},
        read=reader,
        unusable_output=_invalid_structured_output,
    )
    repairs = [(system, prompt) for system, prompt in delivered if "## Step 4:" in prompt]
    assert len(repairs) == diagnostic["batch_count"] > 1
    schema = BatchReading.model_json_schema()
    assert all(model_input_measurement(settings, system, prompt, schema)["fits"] for system, prompt in repairs)
    assert all(row["repair_input"]["retry_prompt_chars"] > row["repair_input"]["original_prompt_chars"] for row in diagnostic["batches"])


def _rejected(model, payload):
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)
    return caught.value


def test_the_reserved_feedback_is_not_shorter_than_any_real_repair():
    """#4229: 組を分けるときに確保する指摘の上限は、実際の不合格の指摘のどの形より短くならない。"""
    values = [span(index, "合成の条件。" * 20) for index in range(5)]
    model = _reading_model(values)
    schema = model.model_json_schema()
    bad_field = {"start_line": 1, "end_line": 2, "answer": 3, "unknown": 4}
    failures = {
        "many_schema_errors": _rejected(model, {"findings": [bad_field] * 50, "unknown": 1}),
        "wrong_root_type": _rejected(model, {"findings": "合成の不正な値"}),
        "every_input_out_of_range": _rejected(
            model, {"findings": [{"start_line": f"B{n}-999", "end_line": f"B{n}-999", "answer": "合成"} for n in range(1, 6)]}
        ),
        "unknown_input": _rejected(model, {"findings": [{"start_line": "B9-1", "end_line": "B9-1", "answer": "合成"}]}),
    }
    upper_bound = batch_repair_prompt("合成の入力", repair_feedback_upper_bound(reading_unit_counts(values)))
    for name, failure in failures.items():
        real = batch_repair_prompt("合成の入力", batch_validation_detail(failure, schema))
        assert len(json.dumps(real, ensure_ascii=False)) <= len(json.dumps(upper_bound, ensure_ascii=False)), name


def test_cancellation_after_first_rejection_prevents_retry(monkeypatch):
    calls = []
    deadline = Deadline()

    def provider(system, prompt, settings, schema, ignored):
        calls.append(prompt)
        deadline.cancel()
        return schema.model_validate({})

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


def reading_model():
    return _reading_model([{"text": "第一の行です。\n第二の行です。\n第三の行です。"}, {"text": "別の入力の行です。"}])


def findings_of(*rows: tuple[str, str]) -> dict:
    return {"findings": [{"start_line": start, "end_line": end, "answer": "合成の回答"} for start, end in rows]}


def mismatch_detail(*rows: tuple[str, str]) -> dict:
    model = reading_model()
    with pytest.raises(ValidationError) as raised:
        model.model_validate(findings_of(*rows))
    detail = batch_validation_detail(raised.value, model.model_json_schema())
    assert json.dumps(detail, ensure_ascii=False).count("合成") == 0
    row = detail["validation_errors"][0]
    assert row["protocol_code"] == "batch_range_invalid"
    return row["range_mismatch"]


def test_valid_ranges_pass_the_check():
    reading_model().model_validate(findings_of(("B1-1", "B1-3"), ("B2-1", "B2-1"), (" B1-2 ", "B1-2")))


@pytest.mark.parametrize(
    ("row", "reason", "retained"),
    [
        (("B9-1", "B9-1"), "evidence_id_unknown", []),
        (("B1-4", "B1-4"), "range_out_of_bounds", ["B1"]),
        (("B1-0", "B1-1"), "line_label_invalid", ["B1"]),
        (("B1-3", "B1-1"), "range_inverted", ["B1"]),
        (("B1-1", "B2-1"), "range_across_inputs", []),
        (("第一の行です。", "B1-1"), "line_label_invalid", []),
    ],
    ids=["unknown_id", "out_of_bounds", "zero", "inverted", "across_inputs", "not_a_label"],
)
def test_range_mismatch_is_classified_without_contents(row, reason, retained):
    mismatch = mismatch_detail(row)
    assert mismatch["unmatched_count"] == 1
    assert mismatch["reasons"] == {reason: 1}
    assert mismatch["samples"][0]["reason"] == reason
    assert mismatch["retained_evidence_ids"] == retained
    assert "第一" not in json.dumps(mismatch, ensure_ascii=False)


def test_range_mismatch_keeps_counts_for_all_findings_and_a_bounded_sample():
    rows = [(f"B1-{number}", f"B1-{number}") for number in range(10, 30)]
    mismatch = mismatch_detail(("B1-1", "B1-1"), *rows)
    assert mismatch["unmatched_count"] == 20
    assert mismatch["reasons"] == {"range_out_of_bounds": 20}
    assert len(mismatch["samples"]) == 8


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (findings_of(("B1-9", "B1-9")), "batch_range_invalid"),
        ({}, None),
        ({**findings_of(), "extra": 1}, None),
        ({"findings": [{"start_line": "B1-1", "answer": "合成の回答"}]}, None),
    ],
    ids=["range", "missing_field", "extra_field", "missing_end"],
)
def test_each_contract_violation_keeps_its_own_class(payload, code):
    model = reading_model()
    with pytest.raises(ValidationError) as raised:
        model.model_validate(payload)
    detail = batch_validation_detail(raised.value, model.model_json_schema())
    assert detail["category"] == "schema_field"
    assert [row.get("protocol_code") for row in detail["validation_errors"]][0] == code


def test_broken_json_is_classified_as_invalid_json():
    model = reading_model()
    with pytest.raises(ValidationError) as raised:
        model.model_validate_json('{"findings": ')
    assert batch_validation_detail(raised.value, model.model_json_schema())["category"] == "invalid_json"


def test_retry_prompt_names_the_valid_lines_without_the_diagnostic(monkeypatch):
    calls = []

    def provider(system, prompt, settings, schema, ignored):
        calls.append(prompt)
        result = output(prompt)
        if len(calls) == 1:
            result["findings"][0]["start_line"] = result["findings"][0]["end_line"] = "B1-9999"
        return schema.model_validate(result)

    monkeypatch.setattr(oci, "_parse_text_once", provider)
    _, _, diagnostic = prepare([span(0, "合成の条件A。" * 600)], reader)
    feedback = calls[1].split("## Step 4:", 1)[1]
    assert "batch_range_invalid" in feedback
    assert "B1 の行は B1-1〜B1-" in feedback
    assert "range_mismatch" not in feedback
    assert "unmatched_count" not in feedback
    assert "合成" not in feedback
    failure = diagnostic["batches"][0]["validation_failures"][0]["validation_errors"][0]
    assert failure["range_mismatch"]["reasons"] == {"range_out_of_bounds": 1}


def test_failed_batch_summary_has_only_class_counts_and_capacity():
    """#4090: 回答の診断には、失敗の類別・件数・容量だけを渡す（質問・原文を渡さない）。"""
    model = reading_model()
    with pytest.raises(ValidationError) as raised:
        model.model_validate(findings_of(("B1-9", "B1-9")))
    detail = batch_validation_detail(raised.value, model.model_json_schema())
    receipt = {"index": 1, "input_count": 2, "input_chars": 120, "validation_failures": [detail, detail]}

    assert batch_failure_summary(receipt, raised.value) == {
        "category": "batch_range_invalid",
        "batch_index": 1,
        "input_count": 2,
        "input_chars": 120,
        "invalid_attempts": 2,
        "unmatched_count": 1,
        "unmatched_reasons": {"range_out_of_bounds": 1},
    }


def _contained(monkeypatch, bad_rows, inputs):
    """2 回とも ``bad_rows`` を返す読取で分割読取を動かす。"""
    prompts = []

    def provider(system, prompt, settings, schema, ignored):
        prompts.append(prompt)
        result = output(prompt)
        result["findings"] = [*result["findings"], *bad_rows]
        return schema.model_validate(result)

    monkeypatch.setattr(oci, "_parse_text_once", provider)
    return (*prepare(inputs, reader), prompts)


def test_invalid_range_after_the_retry_keeps_valid_findings_and_the_whole_input(monkeypatch):
    """#4090: 再試行の後も無効な範囲は採らず、有効な findings と、その入力の原文の全体で最終生成へ進む。"""
    inputs = [span(index, f"合成の条件{index}。" + "補足本文。" * 200) for index in range(5)]
    rows = [{"start_line": "B2-9999", "end_line": "B2-9999", "answer": "採らない合成の回答"}]

    selected, prompt, diagnostic, prompts = _contained(monkeypatch, rows, inputs)

    first_batch = diagnostic["batches"][0]
    assert first_batch["status"] == "completed"
    assert len(first_batch["validation_failures"]) == 2
    assert first_batch["range_unverified"] == {"unmatched_count": 1, "reasons": {"range_out_of_bounds": 1}, "retained_input_count": 1}
    # 組ごとの再試行は既存の 1 回だけ（モデルの呼び出しを増やさない。どの組にも番号 B2 の不合格がある）。
    assert len(prompts) == 2 * diagnostic["batch_count"]
    assert "採らない合成の回答" not in prompt
    whole = [value for value in selected if value["text"] == inputs[1]["text"]]
    assert len(whole) == 1
    # 有効な findings の回答候補は、残した入力でも保つ。
    assert whole[0]["batch_answer"] == "合成の回答"


def test_unknown_input_after_the_retry_adds_no_source(monkeypatch):
    """#4090: 入力に無い番号の範囲は、残す原文も無いので捨てる。"""
    inputs = [span(index, f"合成の条件{index}。" + "補足本文。" * 200) for index in range(5)]
    rows = [{"start_line": "B99-1", "end_line": "B99-1", "answer": "採らない合成の回答"}]

    _, prompt, diagnostic, _ = _contained(monkeypatch, rows, inputs)

    assert diagnostic["batches"][0]["range_unverified"] == {
        "unmatched_count": 1,
        "reasons": {"evidence_id_unknown": 1},
        "retained_input_count": 0,
    }
    assert "採らない合成の回答" not in prompt


def test_a_schema_violation_is_not_contained_and_still_fails(monkeypatch):
    """#4090: 絞るのは範囲の不合格だけ。schema の違反は従来どおり組の失敗にする。"""

    def provider(system, prompt, settings, schema, ignored):
        result = output(prompt)
        result["findings"][0]["start_line"] = "B1-9999"
        result["extra"] = 1
        return schema.model_validate(result)

    monkeypatch.setattr(oci, "_parse_text_once", provider)
    with pytest.raises(ValidationError):
        prepare([span(0, "合成の条件A。" * 600)], reader)


def test_when_every_range_is_rejected_and_the_inputs_do_not_fit_the_failure_is_the_range_not_the_budget(monkeypatch):
    """#4090: 残した原文が容量に入らなければ、容量の不足ではなく範囲の不合格として参考情報へ退避する。"""

    def provider(system, prompt, settings, schema, ignored):
        result = output(prompt)
        for row in result["findings"]:
            row["end_line"] = row["start_line"].split("-")[0] + "-9999"
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
            unusable_output=_invalid_structured_output,
            failed_batches=failed,
        )
    assert batch_failure_summary(failed[-1], raised.value)["category"] == "batch_range_invalid"


def test_a_failure_without_validation_detail_still_has_a_class():
    receipt = {"index": 2, "input_count": 3, "input_chars": 90, "status": "failed"}
    summary = batch_failure_summary(receipt, oci.LlmRunawayOutput("合成", "runaway_repetition", None))
    assert summary == {
        "category": "runaway_output",
        "cutoff_reason": "runaway_repetition",
        "batch_index": 2,
        "input_count": 3,
        "input_chars": 90,
        "invalid_attempts": 0,
    }
