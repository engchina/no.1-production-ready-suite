"""回答の種類は同じ生成の宣言であり、意味の検査や本文の公開の門ではない。"""

from typing import get_args

import pytest
from pydantic import ValidationError
from test_answer_flow_single_generation import ROW, _ids, _run, _source, context, record
from test_answer_parts_wire import case

from rag_engine.generation.answer_envelope import AnswerOutcome
from rag_engine.generation.citation_wire import canonical_generation_draft
from rag_engine.models.llm import GroundedDraft


@pytest.mark.parametrize("kind", get_args(AnswerOutcome))
def test_declared_kind_preserves_reference_answer_and_does_not_repeat_generation(kind):
    question = "項目Aの保存年数を教えてください。"
    response, calls = _run(
        question,
        context(record(0, ROW, "合成資料", source="synthetic.md")),
        lambda units: {
            "response_kind": kind,
            "answer_parts": [_source(_ids(question)[0], "合成の要求", units[ROW])],
            "summary_part_indexes": [0],
            "confidence": "high",
            "reference_materials": ["合成の補足資料"],
        },
    )
    assert response.envelope["outcome"] == kind
    assert ROW in response.answer_text
    assert calls == ["GroundedDraftWire"]


def test_new_generation_requires_an_explicit_existing_kind():
    raw, schema, catalog = case()
    assert "response_kind" in schema.model_json_schema()["required"]
    raw.pop("response_kind")
    with pytest.raises(ValidationError):
        schema.model_validate(raw)
    raw["response_kind"] = "invented-kind"
    with pytest.raises(ValidationError):
        schema.model_validate(raw)
    raw["response_kind"] = "insufficient_evidence"
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft._response_kind == "insufficient_evidence"
    assert "response_kind" not in draft.model_dump()
    assert GroundedDraft.model_validate({"items": [], "summary": "", "confidence": "low"})._response_kind is None


def test_configured_secondary_model_does_not_add_another_generation(monkeypatch):
    from types import SimpleNamespace

    from rag_engine.generation import answering

    calls = []
    result = object()

    def generate(*args, **options):
        calls.append(options.get("answer_llm_provider"))
        return result

    monkeypatch.setattr(answering, "synthesize_grounded_answer", generate)
    returned = answering.synthesize_with_model_cascade(
        "合成の質問",
        object(),
        SimpleNamespace(escalation_llm="secondary"),
        answer_llm_provider="primary",
        image_prompt_mode="text_only",
    )
    assert returned is result
    assert calls == ["primary"]


@pytest.mark.parametrize(
    "mixed,declared,expected",
    [
        (False, "answered", "insufficient_evidence"),
        (True, "answered", "conditional"),
        (False, "conditional", "insufficient_evidence"),
        (False, "needs_environment_data", "needs_environment_data"),
    ],
)
def test_declared_answered_does_not_disagree_with_its_own_explicit_gap_parts(mixed, declared, expected):
    from test_answer_flow_single_generation import _gap

    question = "項目Aと項目Cの値を教えてください。"
    gap = "項目Cは資料に記載されていません。"

    def parts(units):
        selected = [_gap(_ids(question)[0], "合成の不足", gap)]
        if mixed:
            selected.append(_source(_ids(question)[0], "合成の回答", units[ROW]))
        return {"response_kind": declared, "answer_parts": selected, "summary_part_indexes": [], "confidence": "high"}

    response, calls = _run(question, context(record(0, ROW, "合成資料", source="synthetic.md")), parts)
    assert response.envelope["outcome"] == expected
    assert gap in response.answer_text
    assert response.generation_trace["response_kind"] == {
        "declared": declared,
        "recorded": expected,
        "source": "typed_answer_parts" if declared != expected else "declared",
    }
    assert calls == ["GroundedDraftWire"]
