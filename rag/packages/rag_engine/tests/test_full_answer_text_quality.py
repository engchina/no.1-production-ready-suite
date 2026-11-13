"""完成本文でも、破損した表示単位だけを共有の再試行枠で復旧する。"""

import json
from copy import deepcopy

import pytest
from test_full_answer_wire import catalog

from rag_engine.generation.answer_text_quality import AllAnswerPartsFailed, repair_answer_parts
from rag_engine.generation.full_answer_text_quality import body_blocks
from rag_engine.generation.full_answer_wire import full_answer_model
from rag_engine.resources.llm_retry_budget import LlmRetryBudget


def wire(body):
    units, uid = catalog()
    return full_answer_model(units).model_validate(
        {"answer_body": body, "response_kind": "answered", "confidence": "high", "citations": [{"unit_ids": [uid]}]}
    )


def repair(value, replacement, *, budget=None, prompt="合成の質問と原文"):
    calls, records = [], []

    def read(prompt, schema):
        targets = json.loads(prompt.rsplit("\n", 1)[-1])["targets"]
        calls.append(deepcopy(targets))
        for target in targets:
            target["part"] = replacement
        return schema.model_validate({"items": targets})

    result, diagnostic = repair_answer_parts(
        value,
        original_prompt=prompt,
        original_system="## Step 1: 回答する",
        budget=budget or LlmRetryBudget(max_retries=1),
        read=read,
        record=lambda *args: records.append(args),
    )
    return result, diagnostic, calls, records


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_repairs_only_damaged_paragraph_and_keeps_separators_citations_and_clean_body(ending):
    prefix, suffix = "# 見出し" + ending * 2, "正常な末尾です。"
    value = wire(prefix + "説明�[C1]" + ending * 2 + suffix)
    result, diagnostic, calls, records = repair(value, "入力電圧は12 Vです。[C1]")
    assert result.answer_body == prefix + "入力電圧は12 Vです。[C1]" + ending * 2 + suffix
    assert result.citations == value.citations
    assert calls == [[{"field": "answer_body", "index": 1, "part": "説明�[C1]" + ending * 2}]]
    assert diagnostic["repaired_units"] == [["answer_body", 1]]
    assert diagnostic["status"] == "recovered" and len(records) == 1


@pytest.mark.parametrize("replacement", ["まだ�[C1]", "別の引用[C2]", "引用を外す", ""])
def test_failure_keeps_clean_paragraph_and_reports_a_technical_partial_failure(replacement):
    value = wire("正常な段落\n\n破損�[C1]")
    result, diagnostic, calls, _ = repair(value, replacement)
    assert result.answer_body == "正常な段落\n\n"
    assert result.citations == value.citations
    assert result.response_kind == "conditional"
    assert diagnostic["status"] == "partial_failure" and diagnostic["failed_units"] == [["answer_body", 1]]
    assert len(calls) == 1


def test_all_failed_is_not_an_empty_success():
    with pytest.raises(AllAnswerPartsFailed) as caught:
        repair(wire("破損�[C1]"), "破損�[C1]")
    assert caught.value.diagnostic["failed_units"] == [["answer_body", 0]]


def test_shared_exhausted_budget_does_not_call_model():
    budget = LlmRetryBudget(max_retries=1)
    assert budget.take()
    result, diagnostic, calls, _ = repair(wire("正常\n\n破損�[C1]"), "復旧[C1]", budget=budget)
    assert calls == [] and diagnostic["attempts"] == []
    assert result.answer_body == "正常\n\n"


@pytest.mark.parametrize("body,prompt", [("正常[C1]", "合成原文"), ("原文の�[C1]", "原文に�がある")])
def test_clean_body_and_original_replacement_character_need_no_repair(body, prompt):
    value = wire(body)
    result, diagnostic, calls, _ = repair(value, "不要な変更", prompt=prompt)
    assert result is value and diagnostic == {} and calls == []


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_code_fence_keeps_blank_lines_in_one_display_unit(fence):
    body = "前段\n\n" + fence + "text\n破損�\n\nコード\n" + fence + "\n\n末尾"
    blocks = body_blocks(body)
    assert len(blocks) == 3 and "コード" in blocks[1] and "破損�" in blocks[1]
    assert "".join(blocks) == body
