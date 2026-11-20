"""全文を読む段階と、引用番号で局所回答を統合する段階を検査する。"""

import json

import pytest
from test_batched_evidence import block, body, config, finding, span

from rag_engine.generation.batched_evidence import prepare_batched_evidence
from rag_engine.generation.document_inputs import FIRST_CHUNK
from rag_engine.generation.grounded import run_round, tag_spans
from rag_engine.generation.input_budget import GenerationInputBudgetExceeded
from rag_engine.generation.partial_answers import SummaryClaim, SummaryClaims, combine_partial_answers


def test_full_chunks_in_local_calls_and_only_verified_answers_in_final_call():
    scope = {"document_scope": ["合成文書", "版1"]}
    head = {**span(0, "同じ版の先頭。"), **scope, FIRST_CHUNK: True}
    chunks = [{**span(i, f"条件{i}。" + "追加の記載。" * 200), **scope} for i in range(1, 6)]
    seen = []

    def read(system, prompt, schema):
        group = json.loads(prompt)["evidence"]
        assert body(group[0]) == head["text"]
        assert sum(body(item) == head["text"] for item in group) == 1
        for item in group[1:]:
            assert body(item) == next(chunk["text"] for chunk in chunks if chunk["source"] == item["source"])
        seen.extend(item["source"] for item in group[1:])
        return schema.model_validate({"findings": [finding(item, answer=body(item).split("。")[0]) for item in group[1:]]})

    chosen, prompt, detail = prepare_batched_evidence(
        [*chunks, head],
        config(),
        question="どんな条件か",
        render=block,
        evidence_text=block,
        system="生成",
        schema={},
        read=read,
        tag=tag_spans,
        summarize=lambda _: pytest.fail("全局所回答が入る"),
        unusable_output=lambda _: False,
    )
    assert detail["partial_answer_synthesis"]
    assert all(seen.count(chunk["source"]) == 2 for chunk in chunks)
    assert "追加の記載" not in prompt and head["text"] not in prompt
    assert len(chosen) == len(chunks)
    assert all(source["label"] in prompt and source["text"].startswith("条件") for source in chosen)

    def final_model(system, user, settings, schema, **options):
        assert user == prompt
        return schema.model_validate({"answer_body": "条件1です。[E1]", "response_kind": "answered", "confidence": "high"})

    result = run_round(
        "どんな条件か",
        chosen,
        config(),
        prompt=prompt,
        partial_answer_synthesis=True,
        image_paths=[],
        provider_id=None,
        parse_text=final_model,
        parse_images=lambda *args: pytest.fail("画像を送らない"),
    )
    assert result.verified
    assert result.checked[0].span["source_id"] == chosen[0]["source_id"]
    assert result.checked[0].item.quote == chosen[0]["text"]


def test_minimum_group_fails_before_reading():
    scope = {"document_scope": ["合成文書", "版1"]}
    with pytest.raises(GenerationInputBudgetExceeded) as error:
        prepare_batched_evidence(
            [{**span(0, "先" * 1800), **scope, FIRST_CHUNK: True}, {**span(1, "文" * 1800), **scope}],
            config(),
            question="何か",
            render=block,
            evidence_text=block,
            system="生成",
            schema={},
            read=lambda *args: pytest.fail("超過した組を送信しない"),
            unusable_output=lambda _: False,
        )
    assert error.value.diagnostic["reason"] == "complete_chunk_group_exceeds_limit"
    assert error.value.diagnostic["source_ids"] == ["p0", "p1"]


def test_multiple_summary_levels_preserve_all_citations():
    claims = [SummaryClaim(answer="資料の記載。" * 30, references=[f"E{i}"]) for i in range(4)]

    def summarize(prompt):
        items = json.loads(prompt.split("BEGIN_UNTRUSTED_RETRIEVED_CONTEXT\n")[1].split("\nEND_UNTRUSTED")[0])
        return SummaryClaims(claims=[SummaryClaim(answer="合成の回答", references=[ref for item in items for ref in item["references"]])])

    block_text, measured, calls = combine_partial_answers(
        claims,
        question="質問",
        local_fits=lambda prompt: len(prompt) < 505,
        final_measure=lambda data: {"fits": len(data) < 220},
        summarize=summarize,
    )
    assert calls >= 2 and measured["fits"]
    assert all(ref in block_text for claim in claims for ref in claim.references)


def test_file_without_a_versioned_head_is_reported_without_calling_the_model():
    from test_generation_document_background import deps, record

    from rag_engine.dependencies import bind_dependencies
    from rag_engine.generation.answer_models import AnswerContext
    from rag_engine.generation.answering import synthesize_grounded_answer

    item = record()
    with bind_dependencies(deps(lambda **kw: [])):
        result = synthesize_grounded_answer("質問", AnswerContext([item], item.text), config(), image_prompt_mode="text_only")
    assert result.response.generation_trace["llm_calls"] == 0
    assert item.source in result.response.answer_text
    assert "同じ版の先頭チャンク" in result.response.answer_text
