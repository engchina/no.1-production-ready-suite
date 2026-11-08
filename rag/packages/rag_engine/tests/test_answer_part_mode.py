"""要点の役割（mode）を答えの型より先に決め、導出の要点では source 型を選べなくする（#4611。#4586 の規則を schema で）。

記載の要点の source 型は、質問全体の宣言（question_type）と食い違っても直接の記載のまま（#4631）。
"""

import pytest
from openai.lib._parsing._responses import type_to_text_format_param
from pydantic import ValidationError

from rag_engine.generation.answer_parts_wire import answer_parts_model
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model

TEXT = "単価は300円です。\n数量は4個です。"


def catalog() -> tuple[SentUnitCatalog, EvidenceUnitSnapshot]:
    span = {"label": "E1", "evidence_id": "source-a", "source_id": "s-a", "source": "synthetic.txt", "text": TEXT}
    snapshot = EvidenceUnitSnapshot.create(span, [(0, 9), (10, len(TEXT))], "explicit_units_v5")
    return SentUnitCatalog([snapshot]), snapshot


def ref(node: dict) -> str:
    return node["$ref"].rsplit("/", 1)[1]


def test_strict_schema_puts_the_mode_before_the_answer_and_derivation_has_no_source():
    units, _ = catalog()
    strict = type_to_text_format_param(answer_parts_model(generation_draft_wire_model(), units))["schema"]
    defs = strict["$defs"]
    assert [ref(node) for node in strict["properties"]["answer_parts"]["items"]["anyOf"]] == ["DerivationAnswerPart", "StatedAnswerPart"]
    for name in ("DerivationAnswerPart", "StatedAnswerPart"):
        assert list(defs[name]["properties"]) == defs[name]["required"] == ["request_id", "requirement", "mode", "answer"]
        assert "default" not in defs[name]["properties"]["mode"]
    assert [ref(node) for node in defs["DerivationAnswerPart"]["properties"]["answer"]["anyOf"]] == ["SupportedAnswerPart", "GapAnswerPart"]
    assert [ref(node) for node in defs["StatedAnswerPart"]["properties"]["answer"]["anyOf"]] == [
        "SourceAnswerPart",
        "SupportedAnswerPart",
        "GapAnswerPart",
    ]


def raw(*parts: dict) -> dict:
    return {"answer_parts": list(parts), "question_type": ["calculation"], "summary_part_indexes": [0], "confidence": "high"}


def test_derivation_part_rejects_a_source_answer_and_keeps_derivations():
    units, snapshot = catalog()
    model = answer_parts_model(generation_draft_wire_model(), units)
    source = {"kind": "source", "basis": {"unit_ids": list(snapshot.unit_ids)}}
    with pytest.raises(ValidationError):
        model.model_validate(raw({"request_id": "Q1", "requirement": "合計", "mode": "derivation", "answer": source}))
    derived = {"kind": "answer", "basis": {"unit_ids": list(snapshot.unit_ids)}, "reasoning": "300 × 4", "result": "1200円"}
    value = model.model_validate(raw({"request_id": "Q1", "requirement": "合計", "mode": "derivation", "answer": derived}))
    draft = canonical_generation_draft(value, unit_catalog=units)
    assert [part.result_kind for part in draft.answer_parts] == ["derived"]
    assert draft.applications[0].result == "1200円"


def test_stated_part_and_parts_without_mode_keep_the_source_answer_under_a_derivation_declaration():
    """記載の要点は source 型を選べ、質問全体を calculation と宣言しても直接の記載のまま（#4631）。

    mode の無い出力（録画）は記載の要点として読む。
    """
    units, snapshot = catalog()
    model = answer_parts_model(generation_draft_wire_model(), units)
    source = {"kind": "source", "basis": {"unit_ids": list(snapshot.unit_ids)}}
    for part in (
        {"request_id": "Q1", "requirement": "単価", "mode": "stated", "answer": source},
        {"request_id": "Q1", "requirement": "単価", "answer": source},
    ):
        value = model.model_validate(raw(part))
        assert [entry.mode for entry in value.answer_parts] == ["stated"]
        draft = canonical_generation_draft(value, unit_catalog=units)
        assert [entry.result_kind for entry in draft.answer_parts] == ["direct"]
        assert draft.question_type == ["calculation"]
        assert draft.summary == "単価は300円です。\n数量は4個です。"
