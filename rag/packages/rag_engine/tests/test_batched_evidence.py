"""選択済み根拠の全文被覆と容量内の分割読取・統合を合成本文で確認する。"""

import json
import re
from collections import defaultdict
from dataclasses import replace

import pytest
from pydantic import ValidationError

from rag_engine.adapters.oci import LlmStreamOutputMissing
from rag_engine.config import get_settings
from rag_engine.generation.answering import _invalid_structured_output
from rag_engine.generation.batched_evidence import BatchReading, prepare_batched_evidence, verified_candidate
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
        unusable_output=_invalid_structured_output,
    )


def span(index, text):
    return {"evidence_id": f"E{index}", "source_id": f"p{index}", "source": f"p{index}", "text": text, "start": 0, "end": len(text)}


def body(value):
    """入力の本文から行頭の番号を除く（改行を含む単位の間は問わない）。"""
    return "".join(re.sub(r"^\[[A-Z][0-9]+\.[0-9]+\] ", "", line.split("| ", 1)[1]) for line in value["text"].split("\n"))


# 内容語の無い文。原文の照合に関係しないテストの回答候補に使う。
NO_TERM_ANSWER = "そのとおりです。"


def finding(value, answer=NO_TERM_ANSWER, first=1, last=None):
    ids = re.findall(r"(?m)^B\d+-\d+\| \[([A-Z][0-9]+\.[0-9]+)\] ", value["text"])
    if ids:
        selected = ids[first - 1 : first if last is None else last]
        return {"unit_ids": selected or ["unknown-unit"], "answer": answer}
    label = value["evidence_id"]
    return {"start_line": f"{label}-{first}", "end_line": f"{label}-{first if last is None else last}", "answer": answer}


def test_twelve_parents_and_oversized_parent_are_read_without_character_loss():
    inputs = [span(i, f"条件{i}。" + "補足本文。" * (5000 if i == 0 else 100)) for i in range(12)]
    seen = defaultdict(list)

    def read(system, prompt, schema):
        payload = json.loads(prompt)
        for source in payload["evidence"]:
            seen[source["source"]].append(body(source))
        return schema.model_validate(
            {
                "findings": [finding(s, answer=body(s).split("。", 1)[0]) for s in payload["evidence"] if body(s).startswith("条件")],
            }
        )

    selected, prompt, diagnostic = prepare(inputs, read)
    assert diagnostic["batch_count"] > 1
    assert diagnostic["read_source_count"] == 12
    assert diagnostic["excluded_span_count"] == 0
    for original in inputs:
        assert "".join(seen[original["source_id"]]) == original["text"]
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


@pytest.mark.parametrize("offset", [0, 700], ids=["origin", "offset"])
@pytest.mark.parametrize("overlapping", [False, True], ids=["single", "overlapping"])
def test_full_source_restoration_counts_replaced_excerpts_once(offset, overlapping):
    """全文へ戻すと消える抜粋の分を、補う容量の見積もりで二重に数えない（#5033）。"""
    inputs = [span(0, "A" * 1000 + "\n" + "Z" * 600), span(1, "B" * 1400 + "\n" + "Y" * 1800)]
    inputs[0].update(start=offset, end=offset + len(inputs[0]["text"]))

    def read(system, prompt, schema):
        findings = []
        for value in json.loads(prompt)["evidence"]:
            if value["source"] == "p0":
                findings.append(finding(value, last=3))
                if overlapping:
                    findings.append(finding(value, first=2, last=4))
            else:
                findings.append(finding(value, last=1 if overlapping else 4))
        return schema.model_validate({"findings": findings})

    selected, _, diagnostic = prepare(inputs, read)
    restored = [value for value in selected if value["source_id"] == "p0"]
    assert len(restored) == 1
    assert restored[0]["text"] == inputs[0]["text"]
    assert (restored[0]["start"], restored[0]["end"]) == (offset, offset + len(inputs[0]["text"]))
    assert restored[0]["evidence_id"] == inputs[0]["evidence_id"]
    assert len(block(selected)) <= config().evidence_budget_chars
    assert diagnostic["fits"] is True


@pytest.mark.parametrize("failure", ["missing", "invalid_range", "exception"])
def test_failed_batch_does_not_become_success(failure):
    def read(system, prompt, schema):
        if failure == "exception":
            raise TimeoutError("synthetic")
        values = json.loads(prompt)["evidence"]
        return schema.model_validate(
            {
                **({} if failure == "missing" else {"findings": [finding(values[0], first=9999)] if failure == "invalid_range" else []}),
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
        return schema(findings=[])

    _, _, receipt = prepare([original], read, answer_max_input_tokens=2300)
    assert receipt["batch_count"] > 1
    assert receipt["batches"][0]["ranges"][0]["start"] == 900
    assert receipt["batches"][-1]["ranges"][-1]["end"] == original["end"]
    assert sum(range_row["end"] - range_row["start"] for batch in receipt["batches"] for range_row in batch["ranges"]) == len(
        original["text"]
    )
    assert "".join(value["text"].replace("\n", "") for value in seen).count("本文の条件です。") == 500
    assert not any("start" in value or "source_id" in value for value in seen)


def test_map_results_are_reduced_again_when_they_cannot_fit_together():
    inputs = [span(i, f"条件{i}です。" + "補足原文。" * 200) for i in range(12)]
    reduction_calls = []

    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        reducing = any(value.get("batch_answer") for value in values)
        reduction_calls.append(reducing)
        return schema(
            findings=[
                finding(value, answer=body(value).split("。", 1)[0], last=1 if reducing else min(2, value["text"].count("\n") + 1))
                for value in values
            ],
        )

    selected, _, diagnostic = prepare(inputs, read)
    assert any(reduction_calls)
    assert diagnostic["fits"]
    assert {value["source_id"] for value in selected} == {value["source_id"] for value in inputs}


def test_nonshrinking_map_results_do_not_silently_discard_sources():
    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        return schema(
            findings=[finding(value, answer=body(value)[:100], last=value["text"].count("\n") + 1) for value in values],
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
        unusable_output=_invalid_structured_output,
    )
    assert selected == inputs
    assert diagnostic["batch_count"] == 0


def test_concurrent_readers_keep_inputs_separate():
    from concurrent.futures import ThreadPoolExecutor

    def run(prefix):
        def read(system, prompt, schema):
            values = json.loads(prompt)["evidence"]
            assert all(value["source"].startswith(prefix) for value in values)
            return schema(findings=[])

        values = [{**span(i, "合成の本文。" * 300), "source_id": f"{prefix}-{i}", "source": f"{prefix}-{i}"} for i in range(3)]
        return prepare(values, read)[2]["read_source_count"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(run, ["left", "right"])) == [3, 3]


@pytest.mark.parametrize(
    ("invalid_selection", "error_type"),
    [
        ({"unit_ids": ["unknown"]}, "literal_error"),
        ({"unit_ids": []}, "too_short"),
        ({"start_line": "B1-1", "end_line": "B1-9999"}, "extra_forbidden"),
    ],
    ids=["unknown_unit", "no_unit", "free_integer_range"],
)
def test_invalid_unit_selection_uses_existing_structured_output_retry(invalid_selection, error_type):
    from rag_engine.adapters.oci import _parse_with_retry

    calls = 0
    batches = 0
    reasons = []

    def read(system, prompt, schema):
        nonlocal batches
        batches += 1
        values = json.loads(prompt)["evidence"]

        def operation():
            nonlocal calls
            calls += 1
            row = finding(values[0])
            if calls == 1:
                row = {**invalid_selection, "answer": "合成の回答"}
            try:
                return schema.model_validate({"findings": [row]})
            except ValidationError as exc:
                reasons.append({entry["type"] for entry in exc.errors()})
                raise

        return _parse_with_retry(operation, "synthetic", config())

    inputs = [span(i, "合成の本文。" * 200) for i in range(4)]
    _, _, diagnostic = prepare(inputs, read)
    assert calls == batches + 1
    assert len(reasons) == 1 and error_type in reasons[0]
    assert diagnostic["read_source_count"] == 4


def test_code_cuts_the_verbatim_original_from_the_selected_lines():
    """#4090: 原文と座標はコードが番号から切り出す。モデルの出力に原文の欄は無い。"""
    text = "第一の条件です。\n第二の条件です。\n第三の条件です。\n" + "補足本文。" * 1500
    original = {**span(0, text), "start": 300, "end": 300 + len(text)}

    def read(system, prompt, schema):
        assert set(schema.model_json_schema()["$defs"]["UnitBatchFinding"]["properties"]) == {"unit_ids", "answer"}
        values = json.loads(prompt)["evidence"]
        first_input = body(values[0]).startswith("第一の条件です。第二の条件です。")
        return schema(findings=[finding(values[0], first=2, last=3)] if first_input else [])

    selected, _, diagnostic = prepare([original], read)
    assert selected[0]["text"] == "第二の条件です。\n第三の条件です。"
    assert selected[0]["start"] == 300 + text.index("第二")
    assert selected[0]["end"] == selected[0]["start"] + len(selected[0]["text"])
    assert selected[0]["batch_answer"] == NO_TERM_ANSWER
    assert diagnostic["batches"][0]["selected_range_chars"] == [len(selected[0]["text"])]


def test_a_long_line_without_a_break_is_split_into_selectable_units():
    from rag_engine.generation.batched_evidence import READING_UNIT_MAX_CHARS, reading_units

    text = "あ" * (READING_UNIT_MAX_CHARS * 2 + 10)
    units = reading_units({"text": text})
    assert [end - start for start, end in units] == [READING_UNIT_MAX_CHARS, READING_UNIT_MAX_CHARS, 10]
    assert "".join(text[start:end] for start, end in units) == text


def test_rejected_unit_selection_keeps_its_batch_input_and_other_readings_reach_generation():
    """#4090: 1 件の範囲の不合格で組全体を退避にせず、原文を根拠に最終生成へ進む。不合格の answer は渡さない。"""
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation import answering

    records = [
        answering.AnswerRecord(
            id=f"p{i}",
            engine="synthetic",
            engine_label="Synthetic",
            page=1,
            seq_no=1,
            category="Chunk",
            text=f"条件{i}は値{i}です。\n" + "補足。" * 1500,
            source=f"document-{i}.txt",
        )
        for i in range(4)
    ]
    generated: list[str] = []
    batches = 0

    def model(system, prompt, settings, schema, **kwargs):
        nonlocal batches
        if issubclass(schema, BatchReading):
            batches += 1
            values = json.loads(prompt)["evidence"]
            return schema.model_validate(
                {
                    "findings": [{"unit_ids": ["unknown"], "answer": "採らない合成の回答"}]
                    if batches == 1
                    else [finding(row) for row in values]
                }
            )
        assert getattr(schema, "_uses_full_answer", False)
        generated.append(prompt)
        return schema.model_validate(
            {
                "response_kind": "answered",
                "answer_body": "合成の不足",
                "confidence": "low",
            }
        )

    def unused(*args, **kwargs):
        pytest.fail("不要な呼出し")

    settings = replace(config(), evidence_budget_chars=12000)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = answering.synthesize_grounded_answer(
            "各資料の条件を教えてください。", answering.AnswerContext(records=records, text=""), settings, image_prompt_mode="text_only"
        ).response

    assert "generation_failure" not in result.generation_trace
    assert generated
    assert "採らない合成の回答" not in generated[0]
    assert "条件0は値0です。" in generated[0]


def _validation_error() -> ValidationError:
    try:
        BatchReading.model_validate({"findings": "合成の不正な値"})
    except ValidationError as exc:
        return exc
    raise AssertionError("合成の入力は型違反になる")


def test_stream_output_missing_in_batch_read_falls_back_to_quotes_and_keeps_trace() -> None:
    """分割読取の完了の event に本文が無くても問の全体を失敗にせず、理由と event の並びを診断へ残す（#4197）。"""
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

    trace = {"events": [{"type": "response.completed", "output_index": None, "content_index": None}], "final_output_types": []}

    def model(system, prompt, settings, schema, **kwargs):
        assert issubclass(schema, BatchReading)
        raise LlmStreamOutputMissing("合成の本文の無い応答", None, trace)

    def unused(*args, **kwargs):
        pytest.fail("不要な呼出し")

    settings = replace(config(), evidence_budget_chars=12000)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = answering.synthesize_grounded_answer(
            "各資料の条件を教えてください。", answering.AnswerContext(records=records, text=""), settings, image_prompt_mode="text_only"
        ).response
    assert result.answer_text.startswith(grounded.answer_labels().neutral_summary)
    assert result.envelope["outcome"] == "conditional"
    failure = result.generation_trace["generation_failure"]
    assert failure["error"] == "LlmStreamOutputMissing"
    assert failure["stage"] == "evidence.batch_read"
    assert failure["category"] == "stream_output_missing"
    assert failure["stream_trace"] == trace
    assert failure["batch_index"] >= 1
    assert failure["invalid_attempts"] == 0


UNREADABLE = "合成の読めない本文。"


def _raise_output_missing(schema):
    raise LlmStreamOutputMissing("合成の本文の無い応答", None, {"events": []})


def _raise_invalid_schema(schema):
    return schema.model_validate({})


@pytest.mark.parametrize("unreadable", [_raise_output_missing, _raise_invalid_schema])
def test_one_unreadable_batch_keeps_its_inputs_and_the_other_batches_are_used(unreadable):
    """#4229: 1 つの組の出力を使えなくても、その組の原文を残し、ほかの組の読取結果と合わせて続ける。"""
    inputs = [span(0, UNREADABLE * 300), span(1, "合成の読める本文。" * 300)]

    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        if UNREADABLE in values[0]["text"]:
            unreadable(schema)
        return schema(findings=[finding(values[0])])

    selected, _, diagnostic = prepare(inputs, read)

    assert [value["text"] for value in selected if value["source_id"] == "p0"] == [inputs[0]["text"]]
    assert [value["batch_answer"] for value in selected if value["source_id"] == "p1"] == [NO_TERM_ANSWER]
    failed, completed = diagnostic["batches"]
    assert (failed["status"], failed["retained_input_count"]) == ("failed", 1)
    assert completed["status"] == "completed"
    assert diagnostic["read_source_count"] == 1
    assert diagnostic["excluded_span_count"] == 0


def test_a_failure_outside_the_output_contract_still_stops_the_reading():
    """#4229: 出力の不合格ではない失敗（実装の例外など）は、組の原文を残さずに送出する。"""

    def read(system, prompt, schema):
        raise KeyError("合成の実装の例外")

    with pytest.raises(KeyError):
        prepare([span(0, "合成の本文。" * 1000)], read)


def test_unreadable_batch_does_not_fall_back_the_whole_answer():
    """#4229: 1 つの組の暴走で、問の全体を原文の提示へ退避させない。"""
    records = [_record(0, UNREADABLE + FILLER[:2500], score=0.9), _record(1, "合成の読める本文。" + FILLER[:6000], score=0.5)]

    def read(values):
        if any(UNREADABLE in value["text"] for value in values):
            _raise_output_missing(None)
        return [finding(values[0], answer="合成の回答")]

    result, generated, _ = _synthesize(records, read, budget=6000)

    assert "generation_failure" not in result.generation_trace
    assert UNREADABLE in generated[0]


def test_batch_failure_summary_keeps_the_last_reason_and_shared_fields() -> None:
    """検証の失敗の後に別の理由で終わった組は最後の理由を残し、概要の項目は生成の経路と同じにする（#4197）。"""
    from rag_engine.generation.batch_output_repair import batch_failure_summary

    receipt = {"index": 2, "input_count": 3, "input_chars": 40, "validation_failures": [{"category": "invalid_json"}]}
    missing = batch_failure_summary(receipt, LlmStreamOutputMissing("合成", None, {"events": []}))
    assert (missing["category"], missing["stream_trace"], missing["invalid_attempts"]) == ("stream_output_missing", {"events": []}, 1)

    invalid = _validation_error()
    with_attempt = {"index": 1, "input_count": 1, "input_chars": 1, "validation_failures": [{"category": "schema_field"}]}
    assert batch_failure_summary(with_attempt, invalid)["category"] == "schema_field"


def test_batch_local_ids_preserve_original_sources_and_coordinates():
    original = {**span(0, "固有の条件。\n" + "補足。" * 2000), "evidence_id": "E" + "0123456789abcdef" * 4, "start": 700}
    original["end"] = original["start"] + len(original["text"])

    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        assert [value["evidence_id"] for value in values] == [f"B{index}" for index in range(1, len(values) + 1)]
        findings = [finding(value, answer="固有の条件。") for value in values if body(value).startswith("固有の条件。")]
        return schema(findings=findings)

    selected, _, diagnostic = prepare([original], read)
    assert diagnostic["read_source_count"] == 1
    assert len(selected) == 1
    assert selected[0]["source_id"] == original["source_id"]
    assert selected[0]["start"] == 700
    assert selected[0]["end"] == 700 + len("固有の条件。")
    assert selected[0]["evidence_id"].startswith("E")


# --- 読取の選択が少ないときの原文の補い（#4113）

PRINCIPLE = "原則として合成の申請は係長が承認する。"
EXCEPTION = "ただし合成の金額が上限を超える場合は部長が承認する。"
UNRELATED = "合成の備品の置き場所は倉庫である。"


def _synthesize(records, read_findings, *, budget=12000):
    """読取は ``read_findings`` を返す。最終生成の入力と呼び出しの種類を記録する。"""
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation import answering

    generated, calls = [], []

    def model(system, prompt, settings, schema, **kwargs):
        calls.append(schema.__name__)
        if issubclass(schema, BatchReading):
            return schema(findings=read_findings(json.loads(prompt)["evidence"]))
        assert getattr(schema, "_uses_full_answer", False)
        generated.append(prompt)
        return schema.model_validate(
            {
                "response_kind": "answered",
                "answer_body": "合成の不足",
                "confidence": "low",
            }
        )

    def unused(*args, **kwargs):
        pytest.fail("不要な呼出し")

    settings = replace(config(), evidence_budget_chars=budget)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = answering.synthesize_grounded_answer(
            "合成の申請の承認者は？", answering.AnswerContext(records=records, text=""), settings, image_prompt_mode="text_only"
        ).response
    return result, generated, calls


def _record(index, text, score=None):
    from rag_engine.generation import answering

    return answering.AnswerRecord(
        id=f"p{index}",
        engine="synthetic",
        engine_label="Synthetic",
        page=1,
        seq_no=index + 1,
        category="Chunk",
        text=text,
        source=f"document-{index}.txt",
        metadata={"rerank": {"relevance_score": score, "question_relevance_score": score}} if score is not None else {},
    )


FILLER = "\n" + "合成の補足の本文である。" * 700


@pytest.mark.parametrize(
    "layout",
    ["same_paragraph", "other_paragraph", "other_batch"],
)
@pytest.mark.parametrize("findings", ["none", "only_exception"])
def test_principle_and_exception_reach_the_final_generation(layout, findings):
    """#4113・#5098: 上限を超えると分割読取へ進み、読取が原則・例外を選ばなくても最終生成の入力に残る（同じ段落・別の段落・別の組）。"""
    if layout == "same_paragraph":
        texts = [PRINCIPLE + EXCEPTION + FILLER[:1000], "補足" + FILLER[:1000]]
    elif layout == "other_paragraph":
        texts = [PRINCIPLE + "\n\n" + "補足の段落である。" * 30 + "\n\n" + EXCEPTION, "補足" + FILLER[:1000]]
    else:
        texts = [PRINCIPLE + FILLER[:3000], "補足" + FILLER[:9000], EXCEPTION + FILLER[:3000]]
    records = [_record(index, text, score=1.0 - index / 10) for index, text in enumerate(texts)]
    # 全体が予算を超えて分割読取へ進むように、点数の低い大きな原文を足す。
    records += [_record(9, "補足" + FILLER, score=0.01)]

    def read(values):
        if findings == "none":
            return []
        return [finding(value, answer="合成の回答") for value in values if EXCEPTION in value["text"]][:1]

    result, generated, calls = _synthesize(records, read, budget=9000)

    assert PRINCIPLE in generated[0] and EXCEPTION in generated[0]
    assert "generation_failure" not in result.generation_trace
    # 読取の組の数と最終生成のほかに呼び出しは増えない。最終生成は 1 回。
    assert calls.count("FullAnswerWire") == 1
    assert all(name in {"BatchReading", "FullAnswerWire"} for name in calls)
    budget = result.generation_trace["generation_input_budget"]
    assert budget["selection"] == "batched_reading"
    assert budget["excluded_span_count"] == 0
    assert result.generation_trace["llm_calls"] == len(calls) == budget["batch_count"] + 1
    reduction = budget["reduction"]
    assert reduction["program_added"]["count"] >= 1
    assert reduction["final"]["count"] >= 2
    assert reduction["before_reduction"]["count"] >= reduction["final"]["count"]
    assert "省略" not in result.answer_text


def test_over_limit_answer_cites_only_units_of_the_final_input():
    """#5098: 上限を超えると組ごとに読み、最終生成は 1 回。回答候補は根拠の後に添え、引用の確認は最終生成の入力の単位だけに掛かる。"""
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation import answering

    records = [_record(0, PRINCIPLE + FILLER[:2000], score=0.9), _record(1, "補足" + FILLER, score=0.5)]
    calls, generated = [], []

    def model(system, prompt, settings, schema, **kwargs):
        calls.append(schema.__name__)
        if issubclass(schema, BatchReading):
            values = json.loads(prompt)["evidence"]
            return schema(findings=[finding(value, answer=PRINCIPLE) for value in values if body(value).startswith(PRINCIPLE)])
        generated.append(prompt)
        unit = re.search(r"(?m)^\d+\| \[(E[0-9]+\.[0-9]+)\] " + re.escape(PRINCIPLE), prompt).group(1)
        # 読取の番号（B1-1）は最終生成の単位ではないので、本文に書いても引用にならない。
        return schema.model_validate(
            {"answer_body": f"係長が承認します。[{unit}]\n部長が承認します。[B1-1]", "response_kind": "answered", "confidence": "high"}
        )

    def unused(*args, **kwargs):
        pytest.fail("不要な呼出し")

    settings = replace(config(), evidence_budget_chars=9000)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = answering.synthesize_grounded_answer(
            "合成の申請の承認者は？", answering.AnswerContext(records=records, text=""), settings, image_prompt_mode="text_only"
        ).response

    assert calls.count("FullAnswerWire") == 1 and calls.count("BatchReading") >= 2
    assert result.generation_trace["llm_calls"] == len(calls)
    assert "- 回答候補: " in generated[0]
    assert "回答候補（evidence_id は根拠のラベル）:\nBEGIN_UNTRUSTED_RETRIEVED_CONTEXT\n" in generated[0]
    lines = result.generation_trace["citation_lines"]
    assert len(lines) == 1
    assert lines[0]["source_ids"] == ["p0"]
    assert "generation_failure" not in result.generation_trace


def test_higher_rerank_score_is_filled_first_and_the_budget_is_kept():
    """#4113: 足す順は原質問の rerank の点数の順。入力の予算を超えない。"""
    records = [_record(index, f"合成の記載{index}です。" + "補足本文。" * 300, score=index / 10) for index in range(8)]

    result, generated, _ = _synthesize(records, lambda values: [], budget=6000)

    budget = result.generation_trace["generation_input_budget"]
    assert budget["fits"]
    assert budget["rendered_chars"] <= 6000
    assert budget["reduction"]["model_selected"]["count"] == 0
    assert "合成の記載7です。" in generated[0]
    assert "合成の記載0です。" not in generated[0]


def test_an_oversized_source_is_skipped_and_the_next_rank_is_filled():
    records = [_record(0, "大きな記載。" + "補足本文。" * 3000, score=0.9), _record(1, "小さな記載です。" + "補足。" * 100, score=0.5)]
    records += [_record(index, f"合成の記載{index}。" + "補足本文。" * 900, score=0.1) for index in range(2, 6)]

    _, generated, _ = _synthesize(records, lambda values: [], budget=6000)

    assert "小さな記載です。" in generated[0]
    assert "大きな記載。" not in generated[0]
    assert len(generated) == 1


def test_filled_source_keeps_the_reading_answer_and_does_not_duplicate_the_excerpt():
    selected, prompt, diagnostic = prepare(
        [span(index, f"合成の条件{index}。\n" + "補足本文。" * 200) for index in range(5)],
        lambda system, payload, schema: schema(findings=[finding(json.loads(payload)["evidence"][0], answer="合成の条件です。")]),
    )
    assert "合成の条件です。" in prompt
    assert diagnostic["reduction"]["program_added"]["count"] >= 1
    assert len({value["source_id"] for value in selected}) == len(selected)
    assert any(value.get("batch_answer") == "合成の条件です。" and value["text"].startswith("合成の条件") for value in selected)


def test_truly_missing_evidence_stays_a_gap():
    """#4113: 原文を足しても、根拠が無ければ生成は不足のまま（条件を推測しない）。"""
    records = [_record(index, UNRELATED + FILLER, score=0.5) for index in range(3)]

    result, generated, _ = _synthesize(records, lambda values: [], budget=9000)

    assert PRINCIPLE not in generated[0]
    assert all(item["kind"] != "rule" for item in result.envelope["items"])


def test_short_input_is_not_filled_and_has_no_reduction_record():
    """#4113: 分割読取をしない普通の回答の入力・呼び出しは変えない。"""
    inputs = [span(0, "条件Aです。")]
    selected, prompt, diagnostic = prepare(inputs, lambda *args: pytest.fail("短い入力に読取を追加しない"))
    assert selected == inputs
    assert "reduction" not in diagnostic


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("条件Aは30日です。", "条件Aは30日です。"),
        ("条件Ａは３０日です。", "条件Ａは３０日です。"),
        ("条件Aは60日です。", ""),
        ("条件Aは例外です。", ""),
        ("条件Aは30日です。ただし特例は除きます。", "条件Aは30日です。"),
        ("ただし特例は除きます。\n条件Aは30日です。", "条件Aは30日です。"),
    ],
    ids=["all-terms", "width-variant", "other-number", "other-word", "drops-only-the-sentence", "drops-the-first-line"],
)
def test_verified_candidate_keeps_only_sentences_whose_terms_are_in_the_source(answer, expected):
    assert verified_candidate(answer, "条件Aは30日です。") == expected


def test_candidate_sentence_missing_from_the_source_does_not_reach_the_final_input():
    """#4234: 選んだ原文に無い例外の文を最終生成の入力へ渡さない。原文に有る文と原文の範囲は残す。"""
    selected, prompt, _ = prepare(
        [span(index, f"合成の条件{index}。\n" + "補足本文。" * 200) for index in range(5)],
        lambda system, payload, schema: schema(
            findings=[finding(json.loads(payload)["evidence"][0], answer="合成の条件です。ただし特例は除外する。")]
        ),
    )
    assert "合成の条件です。" in prompt
    assert "特例" not in prompt
    assert all("特例" not in str(value.get("batch_answer") or "") for value in selected)


def _deadline_reader(calls):
    """各組の入力の出典を記録し、各入力の最初の単位を回答候補にする読取のスタブ。"""

    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        calls.append([value["source"] for value in values])
        return schema.model_validate({"findings": [finding(value) for value in values]})

    return read


def _remaining(monkeypatch, values):
    """分割読取が各組の前に見る回答の期限の残り（秒）を、呼ばれた順に返す（最後の値を繰り返す）。"""
    import rag_engine.generation.batched_evidence as module

    queue = list(values)
    seen = []

    def remaining():
        value = queue.pop(0) if len(queue) > 1 else queue[0]
        seen.append(value)
        return value

    monkeypatch.setattr(module, "remaining_seconds", remaining)
    return seen


DEADLINE_INPUTS = [span(i, f"条件{i}。" + "補足本文。" * 120) for i in range(12)]


def test_batches_are_all_read_while_the_deadline_leaves_the_final_generation(monkeypatch):
    """期限の残りが最後の生成の見積もり + 確定の分以上なら、全部の組を読む（#5204）。"""
    seen = _remaining(monkeypatch, [600.0])
    calls = []
    selected, _, diagnostic = prepare(DEADLINE_INPUTS, _deadline_reader(calls))
    assert len(calls) == diagnostic["batch_count"] > 1 and len(seen) == len(calls)
    assert "stop_reason" not in diagnostic and diagnostic["excluded_span_count"] == 0
    assert diagnostic["read_source_count"] == 12


def test_remaining_batches_are_not_read_when_only_the_final_generation_fits(monkeypatch):
    """1 組を読んだ後に残りが 120 + 30 秒を切ったら、次の組から読まず、読んだ組と入る分の入力で確定する（#5204）。"""
    _remaining(monkeypatch, [600.0, 149.0])
    calls = []
    selected, prompt, diagnostic = prepare(DEADLINE_INPUTS, _deadline_reader(calls))
    assert len(calls) == diagnostic["batch_count"] == 1
    assert diagnostic["stop_reason"] == "deadline"
    stop = diagnostic["deadline"]
    assert (stop["remaining_seconds"], stop["reserve_seconds"], stop["completed_batch_count"]) == (149.0, 150.0, 1)
    assert stop["skipped_batch_count"] >= 1 and stop["pass_index"] == 1
    # 読んだ組の出典は最終生成に入り、入力の上限を超えない。入らなかった出典の数を回答の未反映の案内に渡す。
    assert set(calls[0]) <= {value["source"] for value in selected}
    assert diagnostic["fits"] and len(block(selected)) <= 3500
    assert diagnostic["excluded_span_count"] == 12 - len({value["source_id"] for value in selected})
    assert diagnostic["excluded_span_count"] > 0


def test_no_batch_is_read_when_the_deadline_is_already_short(monkeypatch):
    """最初の組の前から残りが足りなければ LLM を呼ばず、入力を読取の優先の順に入る分だけ最終生成へ渡す（#5204）。"""
    _remaining(monkeypatch, [100.0])
    selected, _, diagnostic = prepare(DEADLINE_INPUTS, lambda *args: pytest.fail("期限の残りが足りない組を読まない"))
    assert diagnostic["batch_count"] == 0 and diagnostic["stop_reason"] == "deadline"
    assert diagnostic["deadline"]["completed_batch_count"] == 0
    assert selected and diagnostic["fits"] and len(block(selected)) <= 3500
    assert diagnostic["excluded_span_count"] == 12 - len({value["source_id"] for value in selected}) > 0


@pytest.mark.parametrize(("left", "reads"), [(149.9, False), (150.0, True)], ids=["after-search", "exact"])
def test_shortest_answer_limit_reads_no_batch_after_any_elapsed_time(monkeypatch, left, reads):
    """回答の上限の最小 150 秒は予約（既定 120 + 30 秒）と同じ（#5280）。境界ちょうどなら読むが、検索などで時間を使った
    後の残り（150 秒未満）では組を読まず、入る分だけで最終生成する。"""
    _remaining(monkeypatch, [left])
    calls = []
    _, _, diagnostic = prepare(DEADLINE_INPUTS, _deadline_reader(calls))
    assert (len(calls) > 0) is reads
    assert (diagnostic.get("stop_reason") == "deadline") is (not reads)


@pytest.mark.parametrize(("left", "reads"), [(89.0, False), (90.0, True)], ids=["below", "boundary"])
def test_batch_deadline_reserve_follows_the_generation_round_setting(monkeypatch, left, reads):
    """予約は設定の生成の 1 回の見積もり + 確定の 30 秒（設定 60 秒なら 90 秒が境界。#5204）。"""
    _remaining(monkeypatch, [left])
    calls = []
    _, _, diagnostic = prepare(DEADLINE_INPUTS, _deadline_reader(calls), answer_generation_round_estimate_seconds=60.0)
    assert (len(calls) > 0) is reads
    assert ("stop_reason" in diagnostic) is (not reads)


def test_deadline_in_a_later_pass_keeps_the_first_pass_readings(monkeypatch):
    """2 回目の読取の途中で期限に届いたら、1 回目の読取の結果（回答候補つきの範囲）を最終生成へ残す（#5204）。"""
    inputs = [span(i, f"条件{i}です。" + "補足原文。" * 200) for i in range(12)]
    passes = []

    def read(system, prompt, schema):
        values = json.loads(prompt)["evidence"]
        reducing = any(value.get("batch_answer") for value in values)
        passes.append(reducing)
        return schema(
            findings=[
                finding(value, answer=body(value).split("。", 1)[0], last=1 if reducing else min(2, value["text"].count("\n") + 1))
                for value in values
            ],
        )

    # 期限なしで読んだときの 1 回目の読取の組の数。その次（2 回目の最初の組）の前で残りを足りなくする。
    first_count = _passes_of(inputs, read).index(True)
    passes.clear()
    _remaining(monkeypatch, [600.0] * first_count + [10.0])
    selected, _, diagnostic = prepare(inputs, read)
    assert passes and not any(passes)
    assert diagnostic["stop_reason"] == "deadline" and diagnostic["deadline"]["pass_index"] == 2
    assert diagnostic["fits"] and any(value.get("batch_answer") for value in selected)


def _passes_of(inputs, read):
    """期限なしで読んだときの、各読取が 2 回目以降の読取か（回答候補つきの入力を読むか）。"""
    seen = []

    def spy(system, prompt, schema):
        seen.append(any(value.get("batch_answer") for value in json.loads(prompt)["evidence"]))
        return read(system, prompt, schema)

    prepare(inputs, spy)
    return seen


def test_reading_answer_rule_is_sent_once_from_the_schema():
    """読取の回答候補の書き方は項目の説明にだけ置き、読取の指示に重ねない（#5250）。"""
    from openai.lib._parsing._responses import type_to_text_format_param
    from pr_backend_core.response_schema import with_schema_field_guide

    from rag_engine.generation.batched_evidence import BATCH_READING_PROMPT, READING_ANSWER_DESCRIPTION, _reading_model

    text = "合成の規則 A は 3 日とする。\nただし、例外 B では 5 日とする。"
    value = {"source_id": "doc#0", "text": text, "source": "合成の資料 A", "page": 1}
    schema = type_to_text_format_param(_reading_model([value]))["schema"]
    guided, _stripped = with_schema_field_guide(BATCH_READING_PROMPT, schema)
    rule = "条件、例外、否定、数値、比較対象の違いを保つ"
    assert rule in READING_ANSWER_DESCRIPTION
    assert "条件、例外、否定、数値、比較対象の違い" not in BATCH_READING_PROMPT
    assert "answer" not in BATCH_READING_PROMPT
    assert guided.count(rule) == 1


def _limit_full_sources(monkeypatch, marker, limit):
    """最終生成の入力の判定だけを、全文の原文（``marker`` を含む）の数が ``limit`` を超えると入らないことにする
    （字数は入るのに token の判定で入らない試行の合成）。読取の入力の判定は変えない。判定の結果を順に返す。"""
    import rag_engine.generation.batched_evidence as module

    original = module.model_input_measurement
    results = []

    def measure(settings, system, prompt, schema, **kwargs):
        measured = original(settings, system, prompt, schema, **kwargs)
        if system == module.BATCH_READING_PROMPT:
            return measured
        fits = bool(measured["fits"]) and prompt.count(marker) <= limit
        results.append(fits)
        return {**measured, "fits": fits}

    monkeypatch.setattr(module, "model_input_measurement", measure)
    return results


@pytest.mark.parametrize("limit", [1, 2, 3, 4], ids=lambda limit: f"limit-{limit}")
@pytest.mark.parametrize("path", ["fill_to_budget", "keep_unread"])
def test_returned_prompt_is_rendered_last_from_the_returned_sources(monkeypatch, path, limit):
    """#5317: 容量の二分探索の最後の試行が入らなくても、最後の描画は返す原文から行う。描画は引用の単位の視図を
    その原文で記録するので、最終生成の目録が返す原文と一致する。"""
    if path == "keep_unread":
        inputs, filler = DEADLINE_INPUTS, "補足本文。" * 120
        _remaining(monkeypatch, [600.0, 149.0])
        read = _deadline_reader([])
    else:
        inputs, filler = [span(index, f"合成の条件{index}。\n" + "補足本文。" * 200) for index in range(6)], "補足本文。" * 200

        def read(system, payload, schema):
            return schema(findings=[finding(json.loads(payload)["evidence"][0], answer="合成の条件です。")])

    results = _limit_full_sources(monkeypatch, filler, limit)
    rendered = []

    def render(values):
        rendered.append(list(values))
        return block(values)

    selected, prompt, diagnostic = prepare_batched_evidence(
        inputs,
        config(),
        question="各資料の条件を比較する",
        render=render,
        evidence_text=block,
        system="指示",
        schema={"type": "object"},
        read=read,
        unusable_output=_invalid_structured_output,
    )

    assert diagnostic["fits"] and results
    assert [id(value) for value in rendered[-1]] == [id(value) for value in selected]
    assert prompt.startswith(block(selected))


def test_last_unfit_trial_keeps_the_citations_of_the_final_generation(monkeypatch):
    """#5317: 原文を補う二分探索の最後の試行が入らなくても、最終生成の目録は送った根拠と一致し、本文の印は引用に結ばれて
    内部のラベルが回答に残らない。"""
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation import answering

    filler = "合成の補足の本文である。" * 700
    records = [_record(index, f"合成の記載{index}です。\n{filler}\n合成の末尾です。", score=1.0 - index / 10) for index in range(6)]
    results = _limit_full_sources(monkeypatch, "合成の末尾です。", 1)

    def model(system, prompt, settings, schema, **kwargs):
        if issubclass(schema, BatchReading):
            return schema(findings=[finding(value, answer="合成の記載です。") for value in json.loads(prompt)["evidence"][:1]])
        unit = re.search(r"\[(E[0-9]+\.[0-9]+)\] 合成の記載[0-9]+です。", prompt).group(1)
        return schema.model_validate({"answer_body": f"合成の記載があります。[{unit}]", "response_kind": "answered", "confidence": "high"})

    def unused(*args, **kwargs):
        pytest.fail("不要な呼出し")

    settings = replace(config(), evidence_budget_chars=30000)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = answering.synthesize_grounded_answer(
            "合成の申請の承認者は？", answering.AnswerContext(records=records, text=""), settings, image_prompt_mode="text_only"
        ).response

    trace = result.generation_trace
    assert trace["generation_input_budget"]["selection"] == "batched_reading"
    assert trace["generation_input_budget"]["reduction"]["program_added"]["count"] == 1
    # 補う件数の二分探索は、入らない試行で終わる。
    assert results[-2:] == [True, False]
    snapshot = trace["rounds"][0]["generation_citation_trace"]["input"]
    assert snapshot["snapshot_status"] == "recorded" and not snapshot["unrecorded_evidence_ids"]
    assert len(trace["citation_lines"]) == 1
    assert not re.search(r"\[E[0-9]+\.[0-9]+\]", result.answer_text)
