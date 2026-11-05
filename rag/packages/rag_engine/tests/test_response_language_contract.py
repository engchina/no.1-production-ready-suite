"""回答言語の原質問との形式的束縛と、表示だけの隔離を検証する。"""

import json

import pytest

from rag_engine.generation.response_language import response_language_contract
from rag_engine.models.audit_wire import audit_wire_schema, decode_audit_wire
from rag_engine.models.llm import ApplicationAudit, GroundedAudit, GroundedFactAudit, ResponseLanguage


def test_only_the_contiguous_language_request_is_bound():
    question = "合計を教えてください。Explain in English."
    assert response_language_contract(question, ResponseLanguage(selection={"kind": "explicit", "quote": "English"})) == {
        "status": "confirmed",
        "language": "English",
        "instruction": "English",
    }
    for invented in ["ja", "Japanese", "ja-JP", "English and Japanese", ""]:
        if not invented:
            continue
        assert response_language_contract(question, ResponseLanguage(selection={"kind": "explicit", "quote": invented}))["language"] is None


def test_default_does_not_classify_question_or_sql_language():
    assert response_language_contract("SELECT x FROM y", ResponseLanguage())["language"] == "ja"


@pytest.mark.parametrize("canonical", [ApplicationAudit, GroundedAudit, GroundedFactAudit])
def test_audit_language_is_a_single_fixed_value_for_every_mode(canonical):
    schema = audit_wire_schema(canonical, (), application_indexes=(0,), output_language="en")
    assert schema.model_json_schema()["properties"]["output_language"]["const"] == "en"
    raw = {
        "output_language": "ja",
        "summary_supported": True,
        "application_reviews": {"A0": {"valid": True, "reason": "原文対応"}},
        "application_presentations": {"A0": {"display_reasoning": "日本語の説明", "equivalent": True}},
    }
    decoded = decode_audit_wire(json.dumps(raw), schema)
    assert decoded.audit.application_reviews[0].valid
    assert decoded.audit.output_language is None
    assert any(row["code"] == "output_language_mismatch" for row in decoded.validation["errors"])


def test_generation_cannot_invent_a_language_name_not_in_the_question():

    from rag_engine.generation.response_language import generation_language_schema
    from rag_engine.models.llm import ApplicationDraft

    schema = generation_language_schema(ApplicationDraft, "合計を教えてください。Explain in English.")
    raw = {"applications": [], "summary": "", "response_language": {"selection": {"kind": "explicit", "quote": "English."}}}
    assert schema.model_validate(raw).response_language.selection.quote == "English."
    for label in ["en", "ja", "Japanese", "Japanese and English"]:
        raw["response_language"]["selection"]["quote"] = label
        parsed = schema.model_validate(raw)
        assert parsed.response_language.invalid
        assert parsed.summary == ""
        assert response_language_contract("合計を教えてください。Explain in English.", parsed.response_language)["language"] is None
