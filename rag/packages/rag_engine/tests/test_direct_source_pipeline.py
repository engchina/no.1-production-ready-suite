"""正式生成入口の直接原文と導出公開門を、同じ合成資料で確認する。"""

import re
from dataclasses import replace

import pytest

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.models.llm import ApplicationAudit


def run(*, derived: bool, valid: bool = True):
    calls = []
    text = "項目Aは12です。"

    def parse(system, prompt, config, schema, **options):
        calls.append(schema.__name__)
        if schema is ApplicationAudit:
            return schema(application_reviews=[{"index": 0, "valid": valid, "reason": "合成の公開門判定"}], summary_supported=valid)
        ids = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)
        assert ids
        parts = [{"request_id": "Q1", "requirement": "資料の値", "answer": {"kind": "source", "basis": {"unit_ids": ids}}}]
        if derived:
            parts.append(
                {
                    "request_id": "Q1",
                    "requirement": "2件分の計算",
                    "answer": {"kind": "answer", "basis": {"unit_ids": ids}, "reasoning": "12×2=24", "result": "24です。"},
                }
            )
        return schema.model_validate(
            {
                "answer_parts": parts,
                "summary_part_indexes": list(range(len(parts))),
                "request_coverage": [
                    {"request_id": "Q1", "evidence_scope": [{"evidence_id": "E1", "target": "項目A", "relation": "direct", "link": None}]}
                ],
                "confidence": "high",
            }
        )

    def unexpected(*args, **kwargs):
        raise AssertionError("追加の検索・画像呼出しは不要")

    record = AnswerRecord(
        id="source-a",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source="synthetic.txt",
        chunk_level="parent",
        chunk_id="source-a",
        source_run_id="synthetic",
        metadata={"layout": {"native_text_ranges": [{"start": 0, "end": len(text)}]}},
    )
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0, llm_retries=0)
    with bind_dependencies(AnswerDependencies(unexpected, unexpected, parse, unexpected, unexpected)):
        response = synthesize_grounded_answer(
            "項目Aの値と2件分を教えてください。" if derived else "項目Aの値を教えてください。",
            AnswerContext(records=[record], text=text),
            settings,
            image_prompt_mode="text_only",
        ).response
    return response, calls


def test_source_formal_flow_publishes_original_without_derivation_call():
    response, calls = run(derived=False)
    assert calls == ["GroundedDraftWire"]
    assert "項目Aは12です。" in response.answer_text
    assert not response.generation_trace["applications"]
    draft = response.generation_trace["rounds"][0]["draft"]
    assert draft["answer_parts"][0]["result_kind"] == "direct"
    assert draft["summary_item_indexes"] == [0]
    assert draft["summary_application_indexes"] == []


@pytest.mark.parametrize("valid", [True, False])
def test_mixed_direct_and_derived_keep_gate_and_final_summary_dependencies(valid):
    response, calls = run(derived=True, valid=valid)
    assert calls == ["GroundedDraftWire", "ApplicationAudit"]
    assert "項目Aは12です。" in response.answer_text
    assert response.generation_trace["applications"][0]["published"] is valid
    if valid:
        assert "24です。" in response.answer_text
    else:
        assert "24です。" not in response.answer_text
    draft = response.generation_trace["rounds"][0]["draft"]
    assert draft["summary_item_indexes"] == [0]
    assert draft["summary_application_indexes"] == [0]
    assert [part["result_kind"] for part in draft["answer_parts"]] == ["direct", "derived"]
