"""正式生成入口の直接原文と導出公開門を、同じ合成資料で確認する。"""

import re

from audit_fixtures import is_explanation_schema, synthetic_explanation

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer


def run(*, derived: bool, readback: str | None = None):
    calls = []
    text = "項目Aは12です。"

    def parse(system, prompt, config, schema, **options):
        calls.append(schema.__name__)
        if is_explanation_schema(schema):
            return synthetic_explanation(schema, prompt)
        ids = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)
        assert ids
        direct = (
            {"kind": "source", "basis": {"unit_ids": ids}}
            if readback is None
            else {"kind": "answer", "basis": {"unit_ids": ids}, "reasoning": "選択した原文を示す。", "result": readback}
        )
        parts = [{"request_id": "Q1", "requirement": "資料の値", "answer": direct}]
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
    settings = get_settings(environ={}, dotenv_path=None)
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
