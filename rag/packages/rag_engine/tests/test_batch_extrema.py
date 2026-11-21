"""分割の境界をまたぐ行の照合、網羅、同順位を合成原文で確認する。"""

import json
from dataclasses import replace

import pytest
from test_batched_evidence import block, config, span

from rag_engine.generation.batch_extrema import BatchExtrema, BatchPlan, DocumentFormat, calculate
from rag_engine.generation.document_inputs import FIRST_CHUNK
from rag_engine.generation.evidence_selection import select_evidence
from rag_engine.generation.full_answer_wire import render_full_answer

QUESTION = "1–5 の行を2行ずつ、極値と理由を示す。"
KEY = ("資料A", "版A")
FMT = DocumentFormat(document=1, template="R{row} = {value};")
PLAN = BatchPlan(operation="max", first_row=1, last_row=5, group_size=2, document_count=1, documents=[FMT], remaining_request="")


def piece(i, text):
    return {**span(i, text), "document_scope": KEY, "source": "合成A"}


@pytest.mark.parametrize(("mode", "expected"), [("max", [2]), ("min", [1])])
def test_extrema_take_all_rows_across_batches_with_exact_citations(mode, expected):
    a, b = piece(1, "R1 = -2.5;\nR3 = 42;"), piece(2, "R2 = -1.8;\nR4 = 42;\nR5 = 0;")
    result = calculate(QUESTION, PLAN.model_copy(update={"operation": mode}), [KEY], [[b], [a, b]])
    assert result and [group["winners"] for group in result.groups] == [expected, [3, 4], [5]]
    assert all(len(group["compared"]) == len(range(group["range"][0], group["range"][1] + 1)) for group in result.groups)
    assert result.answer.draft._response_kind == "answered"
    rendered = render_full_answer(result.answer.draft, result.answer.checked)
    assert len(rendered.citation_lines) == 4
    assert f"R{expected[0]} =" in result.answer.checked[0].item.quote
    assert len(result.answer.checked) == len(result.spans)


def test_missing_and_conflicting_groups_stay_local():
    batches = [[piece(1, "R1 = 10;\nR2 = 1;\nR3 = 2;\nR5 = -3;")], [piece(2, "R1 = -6;")]]
    result = calculate(QUESTION, PLAN, [KEY], batches)
    assert result and [(g["missing_or_conflicting"], g["winners"]) for g in result.groups] == [([1], []), ([4], []), ([], [5])]
    assert result.answer.draft._response_kind == "conditional"


def test_numeric_fields_must_be_in_source_and_scope():
    original = piece(1, "R1 = -2;")
    other_version = {**piece(2, "R2 = 999;"), "document_scope": ["資料A", "版B"]}
    inserted = {**piece(3, "R2 = 100;"), "non_source_text_ranges": [[5, 8]]}
    result = calculate(QUESTION, PLAN, [KEY], [[original, other_version, inserted]])
    assert result and not any(g["winners"] for g in result.groups)
    assert result.answer.draft._response_kind == "insufficient_evidence"
    for changes in ({"first_row": 9}, {"document_count": 2}, {"remaining_request": "追加された条件"}):
        assert calculate(QUESTION, PLAN.model_copy(update=changes), [KEY], [[original]]) is None


@pytest.mark.parametrize("kind", ["fit", "ordinary", "calculation", "mixed"])
def test_plan_runs_only_once_after_input_overflows(kind):
    head = {**piece(0, "R1 = 1;"), FIRST_CHUNK: True}
    chunks = [piece(i, f"R{i} = {i};\n" + "補足。" * 450) for i in range(2, 6)]
    settings = replace(config(), evidence_budget_chars=10000 if kind == "fit" else 2700)
    prompts = []

    def plan(system, prompt):
        assert json.loads(prompt)["question"] == QUESTION
        assert json.loads(prompt)["documents"][0]["first_chunk"] == [head["text"]]
        return PLAN.model_copy(
            update={"operation": "ordinary" if kind == "ordinary" else "max", "remaining_request": "理由" if kind == "mixed" else ""}
        )

    extrema = BatchExtrema(QUESTION, settings, None, plan)

    def read(system, prompt, schema):
        prompts.append(json.loads(prompt)["question"])
        return schema.model_validate({"findings": []})

    select_evidence(
        [head, *chunks],
        settings,
        question=QUESTION,
        render=block,
        evidence_text=block,
        tag=list,
        system="生成",
        schema_for=lambda _: {},
        read=read,
        unusable_output=lambda _: False,
        plan_batches=extrema.prepare,
    )
    assert extrema.calls == (kind != "fit")
    assert bool(extrema.result) == (kind in {"mixed", "calculation"})
    assert bool(prompts) == (kind in {"ordinary", "mixed"})
    if kind == "mixed":
        assert all(q == "理由" for q in prompts)


@pytest.mark.parametrize("mode", ["only", "explanation", "missing_explanation"])
def test_answer_entry_returns_computed_rows_with_citations_and_separate_explanation(mode):
    from test_batched_evidence import finding, reading
    from test_generation_document_background import deps, first_chunk, record

    from rag_engine.dependencies import bind_dependencies
    from rag_engine.generation.answer_models import AnswerContext
    from rag_engine.generation.answering import synthesize_grounded_answer
    from rag_engine.retrieval.metadata_context import document_context_key

    base = record()
    reason = "比較前に検査したためです。"
    head = first_chunk(base, text="R1 = 5;\n" + ("記録の先頭。" if mode == "missing_explanation" else reason))
    records = [
        replace(base, id=f"id{i}", chunk_uid=f"id{i}", chunk_id=f"id{i}", seq_no=i, chunk_seq=i, text=f"R{i} = {i};\n" + "補足資料。" * 360)
        for i in range(2, 6)
    ]
    calls = []

    def model(system, user, settings, schema, **kwargs):
        calls.append(schema.__name__)
        if schema is BatchPlan:
            return PLAN.model_copy(update={"remaining_request": "" if mode == "only" else "理由"})
        if schema.__name__ == "BatchReading":
            values = json.loads(user)["evidence"]
            assert json.loads(user)["question"] == "理由"
            assert getattr(schema, "_select_only", False)
            return reading(schema, findings=[finding(values[0], first=2), finding(values[-1])] if mode == "explanation" else [])
        assert "理由" in user
        if mode == "missing_explanation":
            return schema.model_validate(
                {"answer_body": "資料に理由がありません。", "response_kind": "insufficient_evidence", "confidence": "low"}
            )
        assert reason in user
        claims = json.loads(user.split("原文で照合済みの局所回答と引用番号\nBEGIN_UNTRUSTED_RETRIEVED_CONTEXT\n")[1].split("\n")[0])
        refs = [claim["references"][0] for claim in claims if claim["answer"] == reason]
        assert len(refs) == 1
        return schema.model_validate({"answer_body": f"{reason}[{refs[0]}]", "response_kind": "answered", "confidence": "high"})

    scope = document_context_key(base)
    io = replace(deps(lambda **kw: [{"document_scope": list(scope), "first_chunk": head}]), parse_text=model)
    with bind_dependencies(io):
        result = synthesize_grounded_answer(QUESTION, AnswerContext(records, ""), config(), image_prompt_mode="text_only")
    response = result.response
    assert response.question_type == ("calculation",)
    assert "| 1–2 | 5 |" in response.answer_text and "| 5–5 | 5 |" in response.answer_text
    assert response.cited_evidence and len(response.generation_trace["generation_input_budget"]["extrema_groups"]) == 3
    assert calls.count("BatchPlan") == 1
    if mode == "only":
        assert calls == ["BatchPlan"]
    if mode == "explanation":
        assert reason in response.answer_text
        assert response.generation_trace["generation_input_budget"]["partial_answer_synthesis"]
        assert any(reason == fact["quote"] for fact in response.evidence_facts)
        assert response.answer_text.count("| 1–2 | 5 |") == 1
    if mode == "missing_explanation":
        assert "確認できませんでした" in response.answer_text


@pytest.mark.parametrize("template", ["R{row} = -{value};", "R0{row} = {value};", "R{row} = {value}."])
def test_format_cannot_turn_part_of_a_number_into_a_complete_measurement(template):
    original = piece(1, "R01 = -2.45;")
    plan = PLAN.model_copy(update={"documents": [DocumentFormat(document=1, template=template)]})
    assert calculate(QUESTION, plan, [KEY], [[original]]) is None
