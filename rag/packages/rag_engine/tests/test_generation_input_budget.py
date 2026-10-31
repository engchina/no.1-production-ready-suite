"""根拠ブロックと固定入力の予算を、本文の途中切断や必須根拠の除外なしに守る。"""

from dataclasses import replace

import pytest

from rag_engine.config import get_settings
from rag_engine.generation.input_budget import GenerationInputBudgetExceeded, ensure_model_input


def settings(**overrides):
    base = get_settings(environ={}, dotenv_path=None)
    providers = {key: replace(value, max_output_tokens=100) for key, value in base.llm_providers.items()}
    return replace(base, llm_providers=providers, **overrides)


def test_final_repair_input_checks_immutable_draft_and_feedback_too():
    config = settings(answer_max_input_tokens=400)
    ensure_model_input(config, "指示", "本文", {"type": "object"})
    with pytest.raises(GenerationInputBudgetExceeded) as raised:
        ensure_model_input(config, "指示", "草稿と指摘" * 200, {"type": "object"})
    assert raised.value.diagnostic["reason"] == "final_model_input_exceeds_limit"


def test_synthesis_does_not_send_fixed_input_that_cannot_fit():
    from test_grounded_answer import context, record

    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.answering import synthesize_grounded_answer

    def unused(*args, **kwargs):
        pytest.fail("入力の予算外ではモデルを呼ばない")

    config = settings(answer_max_input_tokens=100)
    with bind_dependencies(AnswerDependencies(unused, unused, unused, unused, unused)):
        response = synthesize_grounded_answer(
            "資料の内容は？", context(record(0, "合成の本文です。", "合成の説明")), config, image_prompt_mode="text_only"
        ).response
    trace = response.generation_trace
    assert trace["generation_input_budget"]["fits"] is False
    assert trace["llm_calls"] == 0
    assert trace["generation_failure"]["error"] == "GenerationInputBudgetExceeded"


def test_budget_notice_is_compatible_with_existing_published_language_pack():
    from rag_parser_core.language import build_pack, builtin_pack

    current = builtin_pack()
    saved = current.to_dict()
    saved["answer_labels"].pop("answer_evidence_budget_limited")
    loaded = build_pack(saved)
    assert loaded.ingest_hash == current.ingest_hash
    assert loaded.text("answer_labels", "answer_evidence_budget_limited").format(count=2)
