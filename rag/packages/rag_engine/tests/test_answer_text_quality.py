"""文字の復旧が回答単位・送達した原文・既存の再試行枠を越えないことを確認する。"""

import json

import pytest
from test_answer_parts_wire import case

from rag_engine.generation.answer_text_quality import AllAnswerPartsFailed, repair_answer_parts
from rag_engine.resources.llm_retry_budget import LlmRetryBudget, llm_retry_scope


def run(raw, schema, read, *, budget=None, prompt="合成の原質問と原文"):
    calls = []

    def parse(prompt, output_model):
        calls.append(prompt)
        return output_model.model_validate(read(json.loads(prompt.rsplit("\n", 1)[-1])["targets"]))

    result, diagnostic = repair_answer_parts(
        schema.model_validate(raw),
        original_system="## Step 1: 原文から回答する",
        original_prompt=prompt,
        budget=budget or LlmRetryBudget(),
        read=parse,
        record=lambda *args: None,
    )
    return result, diagnostic, calls


def test_valid_parts_and_original_replacement_character_do_not_call_model():
    raw, schema, _ = case()
    for prompt in ["合成の原文", "原文にある�を引用する"]:
        if "�" in prompt:
            raw["answer_parts"][1]["answer"]["reasoning"] += "�"
        result, diagnostic, calls = run(raw, schema, lambda _: pytest.fail("不要な再生成"), prompt=prompt)
        assert result.model_dump() == schema.model_validate(raw).model_dump()
        assert diagnostic == {} and calls == []


def test_repair_only_the_affected_part_and_keep_clean_part_exactly():
    raw, schema, _ = case()
    clean = dict(raw["answer_parts"][0])
    raw["answer_parts"][1]["answer"]["reasoning"] = "説明�"

    def read(targets):
        assert len(targets) == 1 and targets[0]["index"] == 1
        targets[0]["part"]["answer"]["reasoning"] = "原文の値から導く。"
        return {"items": targets}

    result, diagnostic, calls = run(raw, schema, read)
    assert result.answer_parts[0].model_dump(exclude_unset=True) == clean
    assert "�" not in result.model_dump_json()
    assert diagnostic["repaired_indexes"] == [1] and diagnostic["failed_indexes"] == []
    assert len(calls) == 1 and calls[0].startswith("合成の原質問と原文")


@pytest.mark.parametrize("change", ["wrong_index", "request", "requirement", "citation", "still_corrupt"])
def test_invalid_repair_keeps_the_other_part_and_reports_technical_partial_failure(change):
    raw, schema, _ = case()
    raw["answer_parts"][1]["answer"]["result"] = "壊れた�結果"

    def read(targets):
        row = targets[0]
        row["part"]["answer"]["result"] = "正常な結果"
        if change == "wrong_index":
            row["index"] = 0
        elif change == "request":
            row["part"]["request_id"] = "別の要求"
        elif change == "requirement":
            row["part"]["requirement"] = "別の要求内容"
        elif change == "citation":
            row["part"]["answer"]["basis"]["unit_ids"] = ["not-sent"]
        else:
            row["part"]["answer"]["result"] = "まだ�"
        return {"items": targets}

    result, diagnostic, calls = run(raw, schema, read)
    assert len(result.answer_parts) == 1
    assert result.answer_parts[0].model_dump(exclude_unset=True) == raw["answer_parts"][0]
    assert result.response_kind == "conditional"
    assert result.summary_part_indexes == []
    assert all(part.answer.kind != "gap" for part in result.answer_parts)
    assert diagnostic["status"] == "partial_failure" and diagnostic["failed_indexes"] == [1]
    assert len(calls) == 1


def test_all_failed_parts_are_not_an_empty_success():
    raw, schema, _ = case()
    for part in raw["answer_parts"]:
        part["answer"]["reasoning"] = "説明�"
    with pytest.raises(AllAnswerPartsFailed) as error:
        run(raw, schema, lambda targets: {"items": targets})
    assert error.value.diagnostic["failed_indexes"] == [0, 1]


def test_exhausted_transport_budget_prevents_a_new_unit_repair_call():
    raw, schema, _ = case()
    raw["answer_parts"][1]["answer"]["reasoning"] = "説明�"
    budget = LlmRetryBudget(max_retries=1)
    assert budget.take()
    result, diagnostic, calls = run(raw, schema, lambda _: pytest.fail("枠を使い切った"), budget=budget)
    assert len(result.answer_parts) == 1 and calls == []
    assert diagnostic["failed_indexes"] == [1]


def test_nested_retry_scopes_share_budget_and_restore_on_exit():
    with llm_retry_scope(1) as outer:
        assert outer.take()
        with llm_retry_scope(5) as inner:
            assert inner is outer and not inner.take()
    with llm_retry_scope(1) as independent:
        assert independent is not outer and independent.take()


@pytest.mark.parametrize("recover", [True, False])
def test_product_answer_retains_clean_part_and_discloses_partial_failure(recover):
    from test_answer_flow_single_generation import OTHER, ROW, _settings, _units, _unused
    from test_grounded_answer import context, record

    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.answering import synthesize_grounded_answer

    question = "合成の二つの事項を教えてください。"
    calls = []

    def model(system, prompt, settings, schema, **kwargs):
        calls.append(schema.__name__)
        if schema.__name__ == "SupportingTextRepair":
            targets = json.loads(prompt.rsplit("\n", 1)[-1])["targets"]
            assert [row["index"] for row in targets] == [1]
            if recover:
                targets[0]["part"] = "年1回の点検です。[C2]"
            return schema.model_validate({"items": targets})
        units = _units(prompt)
        return schema.model_validate(
            {
                "response_kind": "answered",
                "answer_body": "保存年数は5年です。[C1]\n\n説明�[C2]",
                "citations": [{"unit_ids": [units[ROW]]}, {"unit_ids": [units[OTHER]]}],
                "confidence": "high",
            }
        )

    with bind_dependencies(AnswerDependencies(_unused, _unused, model, _unused, _unused)):
        response = synthesize_grounded_answer(
            question,
            context(record(0, ROW + "\n" + OTHER, "合成資料", source="synthetic.md")),
            _settings(),
            image_prompt_mode="text_only",
        ).response
    assert calls[0] == "FullAnswerWire" and set(calls[1:]) == {"SupportingTextRepair"}
    assert "保存年数は5年です。" in response.answer_text and "�" not in response.answer_text
    diagnostic = response.generation_trace["generation_text_quality"]
    assert diagnostic["status"] == ("recovered" if recover else "partial_failure")
    if not recover:
        assert response.envelope["outcome"] == "conditional"
        assert response.answer_text.startswith("一部の回答")
        assert "生成できませんでした" in response.answer_text
        assert not response.envelope["gaps"]
        assert response.generation_trace["grounded_draft"]["items"]


def test_repair_cannot_reassign_the_part_to_another_subquestion():
    from rag_engine.generation.answer_parts_wire import answer_parts_model
    from rag_engine.generation.citation_wire import generation_draft_wire_model

    raw, _, catalog = case()
    raw["answer_parts"][0]["subquestion"] = "①"
    raw["answer_parts"][1]["subquestion"] = "②"
    raw["answer_parts"][1]["answer"]["result"] = "結果�"
    schema = answer_parts_model(generation_draft_wire_model(), catalog, subquestions=["①", "②"])

    def read(targets):
        targets[0]["part"]["subquestion"] = "①"
        targets[0]["part"]["answer"]["result"] = "修正した結果"
        return {"items": targets}

    result, diagnostic, calls = run(raw, schema, read)
    assert len(result.answer_parts) == 1 and result.answer_parts[0].subquestion == "①"
    assert diagnostic["failed_indexes"] == [1] and len(calls) == 1


def test_all_failed_product_output_keeps_input_receipt_and_has_a_specific_failure_code():
    from test_answer_flow_single_generation import ROW, _settings, _units, _unused
    from test_grounded_answer import context, record

    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.answering import synthesize_grounded_answer

    calls = []
    question = "合成の保存年数を教えてください。"

    def model(system, prompt, settings, schema, **kwargs):
        calls.append(schema.__name__)
        if schema.__name__ == "SupportingTextRepair":
            return schema.model_validate({"items": json.loads(prompt.rsplit("\n", 1)[-1])["targets"]})
        units = _units(prompt)
        return schema.model_validate(
            {
                "response_kind": "answered",
                "answer_body": "説明�[C1]",
                "citations": [{"unit_ids": [units[ROW]]}],
                "confidence": "high",
            }
        )

    with bind_dependencies(AnswerDependencies(_unused, _unused, model, _unused, _unused)):
        response = synthesize_grounded_answer(
            question, context(record(0, ROW, "合成資料", source="synthetic.md")), _settings(), image_prompt_mode="text_only"
        ).response
    assert calls[0] == "FullAnswerWire" and set(calls[1:]) == {"SupportingTextRepair"}
    assert response.generation_trace["generation_failure"]["category"] == "answer_part_text_quality"
    assert not response.generation_trace["escalation_signals"]
    assert ROW in response.answer_text and "�" not in response.answer_text
    assert response.generation_trace["provided_evidence_scope"]["evidence_items"]


@pytest.mark.parametrize("recover", [True, False])
@pytest.mark.parametrize("field", ["external_data_items", "reference_materials"])
def test_supplementary_display_retries_only_the_damaged_unit(field, recover):
    raw, schema, _ = case()
    raw[field] = (
        [{"text": "確認�事項", "who_can_answer": "system", "already_known": False, "decides": True}]
        if field == "external_data_items"
        else ["確認�資料"]
    )
    clean_parts = schema.model_validate(raw).answer_parts

    def read(targets):
        assert len(targets) == 1 and targets[0]["field"] == field and targets[0]["index"] == 0
        if recover:
            if field == "external_data_items":
                targets[0]["part"]["text"] = "確認する値と目的"
            else:
                targets[0]["part"] = "確認する資料と目的"
        return {"items": targets}

    result, diagnostic, calls = run(raw, schema, read)
    assert result.answer_parts == clean_parts
    assert "�" not in result.model_dump_json()
    assert len(getattr(result, field)) == int(recover)
    assert diagnostic["status"] == ("recovered" if recover else "partial_failure")
    assert diagnostic["failed_units"] == ([] if recover else [[field, 0]])
    assert len(calls) == 1


def test_supplementary_repair_keeps_confirmation_classification_and_shared_budget():
    raw, schema, _ = case()
    raw["external_data_items"] = [{"text": "確認�事項", "who_can_answer": "system", "already_known": False, "decides": True}]

    def read(targets):
        targets[0]["part"].update(text="正常な文", who_can_answer="user")
        return {"items": targets}

    result, diagnostic, calls = run(raw, schema, read)
    assert not result.external_data_items and diagnostic["status"] == "partial_failure"
    assert len(calls) == 1
    budget = LlmRetryBudget(max_retries=1)
    assert budget.take()
    result, diagnostic, calls = run(raw, schema, lambda _: pytest.fail("共通の枠を越えた"), budget=budget)
    assert not result.external_data_items and not calls
    assert diagnostic["failed_units"] == [["external_data_items", 0]]
