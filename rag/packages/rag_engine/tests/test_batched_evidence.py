"""選択済み根拠の全文被覆と容量内の分割読取・統合を合成本文で確認する。"""

import json
import re
from collections import defaultdict
from dataclasses import replace

import pytest
from pydantic import ValidationError

from rag_engine.adapters.oci import LlmRunawayOutput, LlmStreamOutputMissing
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
    return "".join(re.sub(r"^\[U[0-9a-f]{24}\] ", "", line.split("| ", 1)[1]) for line in value["text"].split("\n"))


# 内容語の無い文。原文の照合に関係しないテストの回答候補に使う。
NO_TERM_ANSWER = "そのとおりです。"


def finding(value, answer=NO_TERM_ANSWER, first=1, last=None):
    ids = re.findall(r"(?m)^B\d+-\d+\| \[(U[0-9a-f]{24})\] ", value["text"])
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
    from rag_engine.generation.citation_wire import generation_draft_wire_model

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
        assert issubclass(schema, generation_draft_wire_model())
        generated.append(prompt)
        return schema.model_validate(
            {
                "response_kind": "answered",
                "answer_parts": [{"request_id": "Q1", "requirement": "合成の要求", "answer": {"kind": "gap", "text": "合成の不足"}}],
                "summary_part_indexes": [],
                "confidence": "low",
            }
        )

    def unused(*args, **kwargs):
        pytest.fail("不要な呼出し")

    settings = replace(config(), evidence_budget_chars=12000)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = answering.synthesize_grounded_answer(
            "各資料の条件を教えてください。",
            answering.AnswerContext(records=records, text="", independent_sources_merged=True),
            settings,
            image_prompt_mode="text_only",
        ).response

    assert "generation_failure" not in result.generation_trace
    assert generated
    assert batches > 0
    assert "採らない合成の回答" not in generated[0]
    assert "条件0は値0です。" in generated[0]


def _validation_error() -> ValidationError:
    try:
        BatchReading.model_validate({"findings": "合成の不正な値"})
    except ValidationError as exc:
        return exc
    raise AssertionError("合成の入力は型違反になる")


def test_stream_output_missing_in_single_generation_keeps_reference_and_failure_trace() -> None:
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
        assert getattr(schema, "_uses_typed_answer_parts", False)
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
    assert failure["stage"] == "answer.generate"
    assert failure["category"] == "stream_output_missing"
    assert failure["stream_trace"] == trace
    assert "batch_index" not in failure


UNREADABLE = "合成の読めない本文。"


def _raise_runaway(schema):
    raise LlmRunawayOutput("合成の反復", "runaway_repetition", None)


def _raise_output_missing(schema):
    raise LlmStreamOutputMissing("合成の本文の無い応答", None, {"events": []})


def _raise_invalid_schema(schema):
    return schema.model_validate({})


@pytest.mark.parametrize("unreadable", [_raise_runaway, _raise_output_missing, _raise_invalid_schema])
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
            _raise_runaway(None)
        return [finding(values[0], answer="合成の回答")]

    result, generated, _ = _synthesize(records, read, budget=6000)

    assert "generation_failure" not in result.generation_trace
    assert UNREADABLE in generated[0]


def test_batch_failure_summary_keeps_the_last_reason_and_shared_fields() -> None:
    """検証の失敗の後に別の理由で終わった組は最後の理由を残し、概要の項目は生成の経路と同じにする（#4197）。"""
    from rag_engine.generation.batch_output_repair import batch_failure_summary
    from rag_engine.generation.grounded import generation_failure_detail

    receipt = {"index": 2, "input_count": 3, "input_chars": 40, "validation_failures": [{"category": "invalid_json"}]}
    missing = batch_failure_summary(receipt, LlmStreamOutputMissing("合成", None, {"events": []}))
    assert (missing["category"], missing["stream_trace"], missing["invalid_attempts"]) == ("stream_output_missing", {"events": []}, 1)

    runaway = LlmRunawayOutput("合成", "runaway_repetition", None)
    summary = batch_failure_summary({"index": 1, "input_count": 1, "input_chars": 1}, runaway)
    assert summary["cutoff_reason"] == generation_failure_detail(runaway)["cutoff_reason"] == "runaway_repetition"

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
    from rag_engine.generation.citation_wire import generation_draft_wire_model

    generated, calls = [], []

    def model(system, prompt, settings, schema, **kwargs):
        calls.append(schema.__name__)
        if issubclass(schema, BatchReading):
            return schema(findings=read_findings(json.loads(prompt)["evidence"]))
        assert issubclass(schema, generation_draft_wire_model())
        generated.append(prompt)
        return schema.model_validate(
            {
                "response_kind": "answered",
                "answer_parts": [{"request_id": "Q1", "requirement": "合成の要求", "answer": {"kind": "gap", "text": "合成の不足"}}],
                "summary_part_indexes": [],
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
def test_budget_selection_preserves_available_conditions_and_reports_omissions(layout, findings):
    """予算内の原則・例外は残し、予算外の原文を未読のまま明示する。分批読取での救済はしない。"""
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

    assert PRINCIPLE in generated[0]
    if layout == "other_batch":
        assert EXCEPTION not in generated[0]
    else:
        assert EXCEPTION in generated[0]
    assert "generation_failure" not in result.generation_trace
    assert calls == ["GroundedDraftWire"]
    budget = result.generation_trace["generation_input_budget"]
    assert budget["batch_count"] == 0
    assert budget["excluded_span_count"] > 0
    assert "省略" in result.answer_text


def test_higher_rerank_score_is_filled_first_and_the_budget_is_kept():
    """#4113: 足す順は原質問の rerank の点数の順。入力の予算を超えない。"""
    records = [_record(index, f"合成の記載{index}です。" + "補足本文。" * 300, score=index / 10) for index in range(8)]

    result, generated, _ = _synthesize(records, lambda values: [], budget=6000)

    budget = result.generation_trace["generation_input_budget"]
    assert budget["fits"]
    assert budget["rendered_chars"] <= 6000
    assert budget["batch_count"] == 0
    assert "合成の記載7です。" in generated[0]
    assert "合成の記載0です。" not in generated[0]


def test_oversized_high_ranked_source_is_selected_within_budget_without_reader():
    records = [_record(0, "大きな記載。" + "補足本文。" * 3000, score=0.9), _record(1, "小さな記載です。" + "補足。" * 100, score=0.5)]
    records += [_record(index, f"合成の記載{index}。" + "補足本文。" * 900, score=0.1) for index in range(2, 6)]

    _, generated, _ = _synthesize(records, lambda values: [], budget=6000)

    assert "大きな記載。" in generated[0]
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
