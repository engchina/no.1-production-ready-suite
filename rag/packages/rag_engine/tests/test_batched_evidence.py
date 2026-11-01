"""選択済み根拠の全文被覆と容量内の分割読取・統合を合成本文で確認する。"""

import json
from collections import defaultdict
from dataclasses import replace

import pytest

from rag_engine.config import get_settings
from rag_engine.generation.batched_evidence import BatchReading, prepare_batched_evidence
from rag_engine.generation.input_budget import GenerationInputBudgetExceeded


def config(**kwargs):
    base = get_settings(environ={}, dotenv_path=None)
    providers = {key: replace(value, max_output_tokens=100) for key, value in base.llm_providers.items()}
    return replace(base, llm_providers=providers, evidence_budget_chars=3500, **kwargs)


def block(spans):
    return json.dumps([{"id": span["evidence_id"], "text": span["text"]} for span in spans], ensure_ascii=False)


def prepare(spans, read, **kwargs):
    return prepare_batched_evidence(
        spans,
        config(**kwargs),
        question="各資料の条件を比較する",
        render=block,
        evidence_text=block,
        system="指示",
        schema={"type": "object"},
        read=read,
    )


def span(index, text):
    return {"evidence_id": f"E{index}", "source_id": f"p{index}", "text": text, "start": 0, "end": len(text)}


def test_twelve_parents_and_oversized_parent_are_read_without_character_loss():
    inputs = [span(i, f"条件{i}。" + "補足本文。" * (5000 if i == 0 else 100)) for i in range(12)]
    seen = defaultdict(list)

    def read(system, prompt, schema):
        payload = json.loads(prompt)
        for source in payload["evidence"]:
            seen[source["source_id"]].append((source["start"], source["text"]))
        return schema.model_validate(
            {
                "read_receipts": {s["evidence_id"]: True for s in payload["evidence"]},
                "findings": [
                    {"answer": s["text"].split("。", 1)[0], "evidence_id": s["evidence_id"], "quote": s["text"].split("。", 1)[0] + "。"}
                    for s in payload["evidence"]
                    if s["text"].startswith("条件")
                ],
                "unanswered": [],
            }
        )

    selected, prompt, diagnostic = prepare(inputs, read)
    assert diagnostic["batch_count"] > 1
    assert diagnostic["read_source_count"] == 12
    assert diagnostic["excluded_span_count"] == 0
    for original in inputs:
        assert "".join(text for _, text in sorted(seen[original["source_id"]])) == original["text"]
    assert {s["source_id"] for s in selected} == {s["source_id"] for s in inputs}
    assert all(s["text"] in inputs[int(s["source_id"][1:])]["text"] for s in selected)
    assert len(block(selected)) <= 3500
    assert "条件11" in prompt


def test_short_input_uses_one_normal_generation_without_reader():
    inputs = [span(0, "条件Aです。")]
    selected, prompt, diagnostic = prepare(inputs, lambda *args: pytest.fail("短い入力に読取を追加しない"))
    assert selected == inputs
    assert diagnostic["batch_count"] == 0
    assert prompt == block(inputs)


@pytest.mark.parametrize("failure", ["missing", "invented_quote", "exception"])
def test_failed_or_unread_batch_does_not_become_success(failure):
    def read(system, prompt, schema):
        if failure == "exception":
            raise TimeoutError("synthetic")
        values = json.loads(prompt)["evidence"]
        return schema.model_validate(
            {
                "read_receipts": {} if failure == "missing" else {s["evidence_id"]: True for s in values},
                "findings": [{"answer": "合成の回答", "evidence_id": values[0]["evidence_id"], "quote": "存在しない引用"}]
                if failure == "invented_quote"
                else [],
                "unanswered": [],
            }
        )

    with pytest.raises((ValueError, TimeoutError)):
        prepare([span(0, "本文。" * 2000)], read)


def test_fixed_input_too_large_never_calls_reader():
    with pytest.raises(GenerationInputBudgetExceeded):
        prepare([span(0, "本文。" * 2000)], lambda *args: pytest.fail("入力超過"), answer_max_input_tokens=100)


def test_preserve_records_keeps_full_body_even_when_total_exceeds_budget():
    from rag_engine.generation.answering import AnswerContext, AnswerRecord, _grounded_spans

    records = [
        AnswerRecord(
            id=f"p{i}",
            engine="synthetic",
            engine_label="Synthetic",
            page=1,
            seq_no=1,
            category="Chunk",
            text=f"条件{i}。" + "補足。" * 9000,
            source=f"document-{i}.txt",
        )
        for i in range(12)
    ]
    selected = _grounded_spans("全資料の条件は？", AnswerContext(records=records, text=""), (), None, budget=80000, preserve_records=True)
    for record in records:
        own = sorted((s for s in selected if s["source_id"] == record.id), key=lambda s: s["start"])
        assert "".join(s["text"] for s in own) == record.text


def test_partition_respects_model_tokens_and_preserves_nonzero_parent_offsets():
    seen = []
    original = span(0, "本文の条件です。\n" * 500)
    original.update(start=900, end=900 + len(original["text"]))

    def read(system, prompt, schema):
        from rag_engine.generation.input_budget import model_input_measurement

        assert model_input_measurement(config(answer_max_input_tokens=2300), system, prompt, schema.model_json_schema())["fits"]
        values = json.loads(prompt)["evidence"]
        seen.extend(values)
        return schema(read_receipts={s["evidence_id"]: True for s in values}, findings=[], unanswered=[])

    _, _, receipt = prepare([original], read, answer_max_input_tokens=2300)
    assert receipt["batch_count"] > 1
    assert "".join(value["text"] for value in seen) == original["text"]
    assert seen[0]["start"] == 900
    assert seen[-1]["end"] == original["end"]


def test_synthesis_reads_all_twelve_parents_before_final_answer(monkeypatch):
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation import answering, grounded
    from rag_engine.models.llm import GroundedCitation, GroundedDraft, GroundedItem, RequestScopePlan

    records = [
        answering.AnswerRecord(
            id=f"p{i}",
            engine="synthetic",
            engine_label="Synthetic",
            page=1,
            seq_no=1,
            category="Chunk",
            text=f"条件{i}は値{i}です。\n" + "補足。" * (9000 if i == 0 else 1000),
            source=f"document-{i}.txt",
        )
        for i in range(12)
    ]
    captured = []
    seen = set()
    final_spans = []
    original_round = grounded.run_round

    def round_capture(question, spans, *args, **kwargs):
        final_spans[:] = spans
        return original_round(question, spans, *args, **kwargs)

    monkeypatch.setattr(grounded, "run_round", round_capture)

    def model(system, prompt, settings, schema, **kwargs):
        captured.append(schema)
        if issubclass(schema, BatchReading):
            inputs = json.loads(prompt)["evidence"]
            seen.update(s["source_id"] for s in inputs)
            return schema(
                read_receipts={s["evidence_id"]: True for s in inputs},
                findings=[
                    {
                        "answer": s["text"].split("。", 1)[0] + "。",
                        "evidence_id": s["evidence_id"],
                        "quote": s["text"].split("。", 1)[0] + "。",
                    }
                    for s in inputs
                    if s["text"].startswith("条件")
                ],
                unanswered=[],
            )
        assert seen == {r.id for r in records}
        if schema is RequestScopePlan:
            return schema(request_coverage=[])
        assert "分割読取の回答候補" in prompt
        return GroundedDraft(
            items=[
                GroundedItem(kind="rule", text=s["text"], citations=[GroundedCitation(evidence_id=s["label"], quote=s["text"])])
                for s in final_spans
            ],
            summary="",
            confidence="medium",
        )

    def unused(*args, **kwargs):
        pytest.fail("検索や画像の追加呼出はしない")

    settings = replace(config(), evidence_budget_chars=12000, answer_audit_mode="none", answer_max_correction_rounds=0)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        response = answering.synthesize_grounded_answer(
            "各資料の条件を教えてください。", answering.AnswerContext(records=records, text=""), settings, image_prompt_mode="text_only"
        ).response
    for i in range(12):
        assert f"条件{i}は値{i}です。" in response.answer_text
    diagnostic = response.generation_trace["generation_input_budget"]
    assert diagnostic["read_source_count"] == 12
    assert diagnostic["excluded_span_count"] == 0
    assert response.generation_trace["llm_calls"] == len(captured)


def test_map_results_are_reduced_again_when_they_cannot_fit_together():
    inputs = [span(i, f"条件{i}です。" + "補足原文。" * 200) for i in range(12)]
    reduction_calls = []

    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        reducing = any(value.get("batch_answer") for value in values)
        reduction_calls.append(reducing)
        return schema(
            read_receipts={value["evidence_id"]: True for value in values},
            findings=[
                {
                    "answer": value["text"].split("。", 1)[0],
                    "evidence_id": value["evidence_id"],
                    "quote": value["text"].split("。", 1)[0] + "。" if reducing else value["text"][:300],
                }
                for value in values
            ],
            unanswered=[],
        )

    selected, _, diagnostic = prepare(inputs, read)
    assert any(reduction_calls)
    assert diagnostic["fits"]
    assert {value["source_id"] for value in selected} == {value["source_id"] for value in inputs}


def test_nonshrinking_map_results_do_not_silently_discard_sources():
    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        return schema(
            read_receipts={value["evidence_id"]: True for value in values},
            findings=[{"answer": value["text"], "evidence_id": value["evidence_id"], "quote": value["text"]} for value in values],
            unanswered=[],
        )

    with pytest.raises(GenerationInputBudgetExceeded) as raised:
        prepare([span(i, "合成の本文。" * 200) for i in range(12)], read)
    assert raised.value.diagnostic["reason"] == "batch_reduction_did_not_shrink"
    assert raised.value.diagnostic["batch_count"] > 0


def test_parent_larger_than_old_24000_gate_can_use_single_generation():
    inputs = [span(0, "合成本文。" * 5000)]
    selected, _, diagnostic = prepare_batched_evidence(
        inputs,
        replace(config(), evidence_budget_chars=80000),
        question="条件は？",
        render=block,
        evidence_text=block,
        system="指示",
        schema={"type": "object"},
        read=lambda *args: pytest.fail("容量内なら分割しない"),
    )
    assert selected == inputs
    assert diagnostic["batch_count"] == 0


@pytest.mark.parametrize("failure", ["missing", "unread", "unknown"])
def test_reader_schema_binds_receipt_to_all_input_ids(failure):
    from pydantic import ValidationError

    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        ids = [value["evidence_id"] for value in values]
        field = schema.model_json_schema()["$defs"]["BatchReadReceipts"]
        assert field["required"] == list(field["properties"]) == ids
        invalid = {key: True for key in ids}
        if failure == "missing":
            invalid.pop(ids[-1])
        elif failure == "unknown":
            invalid["unknown"] = True
        else:
            invalid[ids[-1]] = False
        with pytest.raises(ValidationError):
            schema.model_validate({"read_receipts": invalid, "findings": [], "unanswered": []})
        return schema(read_receipts=dict.fromkeys(ids, True), findings=[], unanswered=[])

    prepare([span(i, "合成の本文。" * 40) for i in range(20)], read)


def test_incomplete_receipt_uses_existing_structured_output_retry():
    from rag_engine.adapters.oci import _parse_with_retry

    calls = 0
    batches = 0

    def read(system, prompt, schema):
        nonlocal batches
        batches += 1
        ids = [value["evidence_id"] for value in json.loads(prompt)["evidence"]]

        def operation():
            nonlocal calls
            calls += 1
            return schema.model_validate(
                {"read_receipts": {} if calls == 1 else dict.fromkeys(ids, True), "findings": [], "unanswered": []}
            )

        return _parse_with_retry(operation, "synthetic", config(), retries=0)

    _, _, diagnostic = prepare([span(0, "合成の本文。" * 2000)], read)
    assert calls == batches + 1
    assert diagnostic["read_source_count"] == 1


def test_concurrent_reader_schemas_keep_receipts_separate():
    from concurrent.futures import ThreadPoolExecutor

    def run(prefix):
        def read(system, prompt, schema):
            values = json.loads(prompt)["evidence"]
            ids = [value["evidence_id"] for value in values]
            assert schema.model_json_schema()["$defs"]["BatchReadReceipts"]["required"] == ids
            assert all(value["source_id"].startswith(prefix) for value in values)
            return schema(read_receipts=dict.fromkeys(ids, True), findings=[], unanswered=[])

        values = [{**span(i, "合成の本文。" * 300), "source_id": f"{prefix}-{i}"} for i in range(3)]
        return prepare(values, read)[2]["read_source_count"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(run, ["left", "right"])) == [3, 3]


@pytest.mark.parametrize("failure", ["unknown_source", "invented_quote", "noncontiguous_quote"])
def test_quote_validation_uses_existing_structured_output_retry(failure):
    from rag_engine.adapters.oci import _parse_with_retry

    calls = 0
    batches = 0

    def read(system, prompt, schema):
        nonlocal batches
        batches += 1
        values = json.loads(prompt)["evidence"]
        source = values[0]
        receipt = {value["evidence_id"]: True for value in values}
        valid_quote = source["text"][:5]

        def operation():
            nonlocal calls
            calls += 1
            first = calls == 1
            quote = ("存在しない原文" if failure == "invented_quote" else valid_quote + "…" + valid_quote) if first else valid_quote
            return schema.model_validate(
                {
                    "read_receipts": receipt,
                    "findings": [
                        {
                            "evidence_id": "unknown" if first and failure == "unknown_source" else source["evidence_id"],
                            "quote": quote,
                            "answer": "合成の回答",
                        }
                    ],
                    "unanswered": [],
                }
            )

        return _parse_with_retry(operation, "synthetic", config(), retries=0)

    _, _, diagnostic = prepare([span(0, "合成の本文。" * 2000)], read)
    assert calls == batches + 1
    assert diagnostic["read_source_count"] == 1


def test_invalid_batch_reading_returns_unverified_source_material_without_an_answer():
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation import answering, grounded

    records = [
        answering.AnswerRecord(
            id=f"p{i}",
            engine="synthetic",
            engine_label="Synthetic",
            page=1,
            seq_no=1,
            category="Chunk",
            text=f"条件{i}は値{i}です。\n" + "補足。" * 9000,
            source=f"document-{i}.txt",
        )
        for i in range(2)
    ]

    def model(system, prompt, settings, schema, **kwargs):
        assert issubclass(schema, BatchReading)
        return schema.model_validate({"read_receipts": {}, "findings": [], "unanswered": []})

    def unused(*args, **kwargs):
        pytest.fail("不要な呼出し")

    settings = replace(config(), evidence_budget_chars=12000, answer_audit_mode="none", answer_max_correction_rounds=0)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = answering.synthesize_grounded_answer(
            "各資料の条件を教えてください。", answering.AnswerContext(records=records, text=""), settings, image_prompt_mode="text_only"
        ).response
    assert result.answer_text.startswith(grounded.answer_labels().neutral_summary)
    assert result.envelope["outcome"] == "conditional"
    assert result.needs_human_review
    assert result.generation_trace["generation_failure"]["error"] == "ValidationError"
    assert all(item["applies"] == "quote_only" for item in result.envelope["items"])


def test_batch_local_ids_preserve_original_sources_and_coordinates():
    original = {**span(0, "固有の条件。" + "補足。" * 2000), "evidence_id": "E" + "0123456789abcdef" * 4, "start": 700}
    original["end"] = original["start"] + len(original["text"])

    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        assert [value["evidence_id"] for value in values] == [f"B{index}" for index in range(1, len(values) + 1)]
        findings = [
            {"evidence_id": value["evidence_id"], "quote": "固有の条件。", "answer": "固有の条件。"}
            for value in values
            if value["text"].startswith("固有の条件。")
        ]
        return schema(read_receipts={value["evidence_id"]: True for value in values}, findings=findings, unanswered=[])

    selected, _, diagnostic = prepare([original], read)
    assert diagnostic["read_source_count"] == 1
    assert len(selected) == 1
    assert selected[0]["source_id"] == original["source_id"]
    assert selected[0]["start"] == 700
    assert selected[0]["end"] == 700 + len("固有の条件。")
    assert selected[0]["evidence_id"].startswith("E")
