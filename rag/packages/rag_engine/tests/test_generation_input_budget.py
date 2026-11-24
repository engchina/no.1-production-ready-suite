"""根拠ブロックと固定入力の予算を、本文の途中切断や必須根拠の除外なしに守る。"""

from dataclasses import replace

import pytest

from rag_engine.config import get_settings
from rag_engine.generation.input_budget import GenerationInputBudgetExceeded, ensure_model_input, model_input_measurement


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


def test_budget_notice_is_required_in_saved_language_pack():
    import pytest
    from rag_parser_core.language import LanguagePackError, build_pack, builtin_pack

    current = builtin_pack()
    saved = current.to_dict()
    saved["answer_labels"].pop("answer_evidence_budget_limited")
    with pytest.raises(LanguagePackError):
        build_pack(saved)
    assert "answer_evidence_budget_limited" not in saved["answer_labels"]


def test_large_capacity_keeps_input_room_for_online_generation():

    base = get_settings(environ={}, dotenv_path=None)
    providers = {key: replace(value, max_output_tokens=500_000) for key, value in base.llm_providers.items()}
    config = replace(base, llm_providers=providers, answer_max_input_tokens=500_000, online_max_output_tokens=16_384)
    diagnostic = model_input_measurement(config, "指示", "合成の本文", {"type": "object"})
    assert diagnostic["output_reserved_tokens"] == 16_384
    assert diagnostic["available_input_tokens"] > 0
    assert diagnostic["fits"]
    assert diagnostic["available_input_tokens"] + diagnostic["output_reserved_tokens"] == 500_000
    assert diagnostic["estimated_input_tokens"] + diagnostic["output_reserved_tokens"] <= 500_000
    assert model_input_measurement(settings(answer_max_input_tokens=400), "指示", "本文", {})["output_reserved_tokens"] == 100


def test_planning_includes_final_language_contract_and_sdk_wrappers():
    from pr_backend_core.generation_input import complete_input_measurement

    from rag_engine.adapters.oci import _response_wire_request
    from rag_engine.generation.citation_unit_ids import SentUnitCatalog
    from rag_engine.generation.grounded import final_answer_model
    from rag_engine.generation.paragraph_repair import ParagraphRepair

    config = settings(answer_max_input_tokens=96_000)
    provider = config.llm_providers[config.default_answer_llm]
    question = "資料の記載を説明してください。"
    units = SentUnitCatalog(())
    schema = final_answer_model(question, guided=False, unit_catalog=units)
    validated = ParagraphRepair(units).model(schema)
    prompt = '資料の "引用" と改行\n'
    measured = model_input_measurement(config, "指示", prompt, schema.model_json_schema())
    actual = _response_wire_request(
        {
            "input": [{"role": "system", "content": "指示"}, {"role": "user", "content": prompt}],
            "model": provider.model,
            "temperature": 0.0,
            "text_format": validated,
            "max_output_tokens": measured["output_reserved_tokens"],
            **({"reasoning": {"effort": provider.reasoning_effort}} if provider.reasoning_effort else {}),
        }
    )
    assert "BoundExplicitResponseLanguage" in actual["text"]["format"]["schema"]["$defs"]
    assert (
        measured["complete_input_estimated_tokens"]
        == complete_input_measurement({**actual, "stream": True}, limit_tokens=96_000).estimated_tokens
    )


def test_japanese_body_and_template_are_jointly_limited():
    from pr_backend_core.generation_input import GENERATION_INPUT_LIMIT

    measured = model_input_measurement(settings(answer_max_input_tokens=500_000), "指示", "合成本文。" * 14_000, {"type": "object"})
    assert measured["estimated_input_tokens"] < measured["available_input_tokens"]
    assert measured["complete_input_estimated_tokens"] > measured["complete_input_limit_tokens"] == GENERATION_INPUT_LIMIT
    assert not measured["fits"]


def test_oversize_final_request_never_reaches_client_or_retry(monkeypatch):
    from rag_engine.adapters import oci
    from rag_engine.models.llm import TextOutput

    config = settings(answer_max_input_tokens=256_000)
    provider = config.llm_providers[config.default_answer_llm]
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *_: pytest.fail("過大な要求の送信"))
    with pytest.raises(GenerationInputBudgetExceeded) as raised, oci.bind_output_cap(128):
        oci._openai_parsed([{"role": "user", "content": "x" * 200_000}], config, TextOutput, provider)
    assert raised.value.diagnostic["complete_input_limit_tokens"] == 256_000
