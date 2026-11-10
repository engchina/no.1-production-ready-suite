"""要点の役割（mode）を答えの型より先に決め、導出の要点では source 型を選べなくする（#4611。#4586 の規則を schema で）。"""

from openai.lib._parsing._responses import type_to_text_format_param

from rag_engine.generation.answer_parts_wire import answer_parts_model
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.citation_wire import generation_draft_wire_model

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
    # 表で示す答え（TableAnswerPart。#4796）はどちらの役割でも選べる。
    assert [ref(node) for node in defs["DerivationAnswerPart"]["properties"]["answer"]["anyOf"]] == [
        "SupportedAnswerPart",
        "TableAnswerPart",
        "GapAnswerPart",
    ]
    assert [ref(node) for node in defs["StatedAnswerPart"]["properties"]["answer"]["anyOf"]] == [
        "SourceAnswerPart",
        "SupportedAnswerPart",
        "TableAnswerPart",
        "GapAnswerPart",
    ]


def raw(*parts: dict) -> dict:
    return {
        "response_kind": "answered",
        "answer_parts": list(parts),
        "question_type": ["calculation"],
        "summary_part_indexes": [0],
        "confidence": "high",
    }


def test_regeneration_without_source_has_no_mode():
    """source 型を受けない作り直しの契約（#4586）は、役割を選ばせず導出か不足だけを受ける。"""
    units, _ = catalog()
    strict = type_to_text_format_param(answer_parts_model(generation_draft_wire_model(), units, direct_source=False))["schema"]
    part = strict["$defs"][ref(strict["properties"]["answer_parts"]["items"])]
    assert "mode" not in part["properties"]
