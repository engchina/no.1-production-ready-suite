"""要点ごとの要求の種類（requirement_type）で答えの型を決め、質問全体の宣言で記載の要点を導出にしない（#4631）。

録画応答（``fixtures/requirement_type_replay_outputs.json``・``fixtures/source_only_derivation_outputs.json``。合成の判定表と
区分の規則）を、テストの原文単位 ID に置き換えて再生する。録画は要点の役割（mode）と質問全体の宣言（question_type）の世代の
出力なので、要点ごとの種類（``requirement_type``）を与えて今の契約の形にする（``typed``）。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from application_verdict_stub import APPLICATION_SCHEMAS, audit_reply
from openai.lib._parsing._responses import type_to_text_format_param
from pydantic import ValidationError

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answer_parts_wire import DERIVATION_QUESTION_TYPES, STATED_QUESTION_TYPES, answer_parts_model
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.generation.grounded import generation_output_model

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = json.loads((FIXTURES / "requirement_type_replay_outputs.json").read_text(encoding="utf-8"))["outputs"]
# #4586 の録画（行の条件が分かっている計算の質問で、質問全体を計算と宣言し、要点がすべて source 型だった出力）。
SOURCE_ONLY = json.loads((FIXTURES / "source_only_derivation_outputs.json").read_text(encoding="utf-8"))["outputs"]
QUESTION = "属性A〜Hは有です。属性Iは無です。確定した合計点と区分を教えてください。"
MULTI_REQUEST_QUESTION = "行A4の点数と、区分Bになる合計点の範囲を教えてください。"
DIRECT_QUESTION = "行A4の点数を教えてください。"
POINTS = zip("ABCDEFGHI", (2, 3, 1, 4, 2, 1, 3, 2, 5), strict=True)
TABLE = "| 行ID | 条件 | 点数 |\n" + "\n".join(
    f"| A{number} | 属性{name}=有 | {points} |" for number, (name, points) in enumerate(POINTS, 1)
)
ROW_A4 = "| A4 | 属性D=有 | 4 |"
RULE = "合計点の区分は、0〜9点は区分A、10〜19点は区分B、20〜29点は区分Cです。"
MISSING = "根拠付きの説明を十分に示せていません"


def _record(identity: str, text: str, source: str) -> AnswerRecord:
    return AnswerRecord(
        id=identity,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source=source,
        chunk_level="parent",
        chunk_id=identity,
        source_run_id="synthetic",
        metadata={"layout": {"native_text_ranges": [{"start": 0, "end": len(text)}]}},
    )


def _placeholders(prompt: str) -> dict[str, str]:
    """プロンプトの番号付きの原文から、fixture の置き換えの記号 → 今回の原文単位 ID の対応を作る。"""
    mapping: dict[str, str] = {}
    for line in prompt.splitlines():
        if not (found := re.match(r"\d+\| \[(U[0-9a-f]{24})\] (.*)", line)):
            continue
        unit, text = found.groups()
        if row := re.match(r"\| A(\d) \|", text):
            mapping["{row:" + row.group(1) + "}"] = unit
        elif text.startswith("| 行ID"):
            mapping["{header}"] = unit
        elif text == RULE:
            mapping["{rule}"] = unit
    return mapping


def typed(output: dict[str, Any], types: list[str]) -> dict[str, Any]:
    """録画の出力を、要点ごとの種類を答える今の契約の形にする（役割と質問全体の宣言・計画の欄を外す）。"""
    value = json.loads(json.dumps(output, ensure_ascii=False))
    value.pop("question_type", None)
    value.pop("request_coverage", None)
    assert len(types) == len(value["answer_parts"])
    for part, kind in zip(value["answer_parts"], types, strict=True):
        part.pop("mode", None)
        part["requirement_type"] = kind
    return value


def _bind(output: dict[str, Any], prompt: str) -> dict[str, Any]:
    text = json.dumps(output, ensure_ascii=False)
    for key, value in _placeholders(prompt).items():
        text = text.replace(key, value)
    assert "{row:" not in text and "{rule}" not in text
    return json.loads(text)


def run(question: str, output: dict[str, Any]):
    calls: list[tuple[str, str]] = []

    def parse(system, prompt, config, schema, **options):
        calls.append((options.get("purpose") or "", schema.__name__))
        if schema in APPLICATION_SCHEMAS:
            return audit_reply(schema, [{"index": 0, "valid": True}], prompt=prompt, summary_supported=True)
        if schema.__name__ == "ApplicationExplanation":
            return schema.model_validate({"explanation": "表の行の点数を合計し、区分の規則に当てはめた。"})
        assert schema.__name__ == "GroundedDraftWire", schema.__name__
        return schema.model_validate(_bind(output, prompt))

    def unexpected(*args, **kwargs):
        raise AssertionError("追加の検索・画像の呼び出しは不要")

    records = [_record("table-a", TABLE, "synthetic-table.md"), _record("rule-a", RULE, "synthetic-rule.md")]
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0, llm_retries=0)
    with bind_dependencies(AnswerDependencies(unexpected, unexpected, parse, unexpected, unexpected)):
        response = synthesize_grounded_answer(
            question, AnswerContext(records=records, text=TABLE + "\n" + RULE), settings, image_prompt_mode="text_only"
        ).response
    return response, [schema for _purpose, schema in calls]


def test_stated_parts_keep_the_recorded_values_without_regeneration():
    """要求が 2 つの質問で、どちらの要点も記載の種類・source 型なら、作り直さずに原文の値を答える。

    録画（配備 17e41814af）は質問全体を calculation と宣言し、要点は 2 つとも mode=stated・source 型だった。質問全体の
    宣言だけで導出に作り直すと、表の行の判定が決まらない題では導出が保留になり、値を答えなかった。
    """
    response, calls = run(MULTI_REQUEST_QUESTION, typed(RECORDED["mr1_multi_request"], ["fact", "fact"]))
    assert calls.count("GroundedDraftWire") == 1
    round_ = response.generation_trace["rounds"][0]
    assert round_["timing"]["generation_calls"] == 1
    assert "answer_part_role_repair" not in round_["timing"]
    draft = round_["draft"]
    assert [part["result_kind"] for part in draft["answer_parts"]] == ["direct", "direct"]
    assert draft["question_type"] == ["fact"]
    assert ROW_A4 in response.answer_text and RULE in response.answer_text
    assert MISSING not in response.answer_text


@pytest.mark.parametrize("name", ["rk1_rows_known", "rk2_rows_known"])
def test_calculation_part_publishes_the_derivation(name):
    """行の条件が分かっている計算の要点は、導出の種類で answer 型を返し、作り直しなしに導出を公開する。"""
    response, calls = run(QUESTION, typed(RECORDED[name], ["calculation"]))
    assert calls[0] == "GroundedDraftWire" and calls.count("GroundedDraftWire") == 1
    trace = response.generation_trace
    assert trace["applications"][0]["published"] is True
    round_ = trace["rounds"][0]
    assert [part["result_kind"] for part in round_["draft"]["answer_parts"]] == ["derived"]
    assert round_["draft"]["question_type"] == ["calculation"]
    assert "18" in response.answer_text and "区分" in response.answer_text


def test_direct_value_part_stays_direct():
    response, _calls = run(DIRECT_QUESTION, typed(RECORDED["dv1_direct_value"], ["fact"]))
    draft = response.generation_trace["rounds"][0]["draft"]
    assert [part["result_kind"] for part in draft["answer_parts"]] == ["direct"]
    assert ROW_A4 in response.answer_text and MISSING not in response.answer_text


def _table_catalog() -> tuple[SentUnitCatalog, dict[str, str]]:
    table = {"label": "E1", "evidence_id": "table-a", "source_id": "s-t", "source": "synthetic-table.md", "text": TABLE}
    rule = {"label": "E2", "evidence_id": "rule-a", "source_id": "s-r", "source": "synthetic-rule.md", "text": RULE}
    lines, start = [], 0
    for line in TABLE.split("\n"):
        lines.append((start, start + len(line)))
        start += len(line) + 1
    table_units = EvidenceUnitSnapshot.create(table, lines, "explicit_units_v5")
    rule_units = EvidenceUnitSnapshot.create(rule, [(0, len(RULE))], "explicit_units_v5")
    mapping = {"{header}": table_units.unit_ids[0], "{rule}": rule_units.unit_ids[0]}
    mapping.update({f"{{row:{number}}}": table_units.unit_ids[number] for number in range(1, len(lines))})
    return SentUnitCatalog([table_units, rule_units]), mapping


def _replace(output: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    text = json.dumps(output, ensure_ascii=False)
    for key, value in mapping.items():
        text = text.replace(key, value)
    return json.loads(text)


@pytest.mark.parametrize("name", sorted(SOURCE_ONLY))
def test_source_only_listing_cannot_answer_a_derivation_part(name):
    """#4586 の録画（計算の質問で要点がすべて表の行・規則の source 型）は、要点を導出の種類にすると契約が受けない。

    導出の種類の要点の答えは answer 型か gap 型だけで、行の列挙を答えにする出力を作れない（作り直しの対象にもならない）。
    """
    output = SOURCE_ONLY[name]
    declared = [kind for kind in output["question_type"] if kind in DERIVATION_QUESTION_TYPES]
    assert declared and {part["answer"]["kind"] for part in output["answer_parts"]} == {"source"}
    catalog, mapping = _table_catalog()
    model = generation_output_model(QUESTION, unit_catalog=catalog)
    with pytest.raises(ValidationError):
        model.model_validate(_replace(typed(output, [declared[0]] * len(output["answer_parts"])), mapping))


def ref(node: dict) -> str:
    return node["$ref"].rsplit("/", 1)[1]


def test_strict_schema_types_each_part_before_the_answer_and_has_no_question_level_type():
    catalog, _mapping = _table_catalog()
    strict = type_to_text_format_param(answer_parts_model(generation_draft_wire_model(), catalog))["schema"]
    assert "question_type" not in strict["properties"]
    defs = strict["$defs"]
    assert [ref(node) for node in strict["properties"]["answer_parts"]["items"]["anyOf"]] == ["DerivationAnswerPart", "StatedAnswerPart"]
    for name, kinds in (("DerivationAnswerPart", DERIVATION_QUESTION_TYPES), ("StatedAnswerPart", STATED_QUESTION_TYPES)):
        properties = defs[name]["properties"]
        assert list(properties) == defs[name]["required"] == ["request_id", "requirement", "requirement_type", "answer"]
        assert properties["requirement_type"]["enum"] == list(kinds)
        assert "default" not in properties["requirement_type"]
    assert [ref(node) for node in defs["DerivationAnswerPart"]["properties"]["answer"]["anyOf"]] == ["SupportedAnswerPart", "GapAnswerPart"]
    assert [ref(node) for node in defs["StatedAnswerPart"]["properties"]["answer"]["anyOf"]] == [
        "SourceAnswerPart",
        "SupportedAnswerPart",
        "GapAnswerPart",
    ]


def test_question_type_is_composed_from_the_part_types_in_order():
    catalog, mapping = _table_catalog()
    model = answer_parts_model(generation_draft_wire_model(), catalog)
    source = {"kind": "source", "basis": {"unit_ids": [mapping["{row:4}"]]}}
    derived = {"kind": "answer", "basis": {"unit_ids": [mapping["{row:4}"], mapping["{row:5}"]]}, "reasoning": "4 + 2", "result": "6点"}
    value = model.model_validate(
        {
            "answer_parts": [
                {"request_id": "Q1", "requirement": "行A4の点数", "requirement_type": "fact", "answer": source},
                {"request_id": "Q1", "requirement": "行A4とA5の合計", "requirement_type": "calculation", "answer": derived},
                {"request_id": "Q1", "requirement": "行A4の条件", "requirement_type": "fact", "answer": source},
            ],
            "summary_part_indexes": [0],
            "confidence": "high",
        }
    )
    draft = canonical_generation_draft(value, unit_catalog=catalog)
    assert draft.question_type == ["fact", "calculation"]
    assert [part.result_kind for part in draft.answer_parts] == ["direct", "derived", "direct"]


def test_parts_without_a_type_read_as_stated_and_add_no_type():
    """種類の無い出力（録画）は記載の要点として読み、質問の種類に値を足さない。"""
    catalog, mapping = _table_catalog()
    model = answer_parts_model(generation_draft_wire_model(), catalog)
    value = model.model_validate(
        {
            "answer_parts": [
                {"request_id": "Q1", "requirement": "行A4の点数", "answer": {"kind": "source", "basis": {"unit_ids": [mapping["{row:4}"]]}}}
            ],
            "summary_part_indexes": [0],
            "confidence": "high",
        }
    )
    draft = canonical_generation_draft(value, unit_catalog=catalog)
    assert draft.question_type == []
    assert [part.result_kind for part in draft.answer_parts] == ["direct"]


def test_requirement_type_is_a_closed_vocabulary():
    catalog, mapping = _table_catalog()
    model = answer_parts_model(generation_draft_wire_model(), catalog)
    source = {"kind": "source", "basis": {"unit_ids": [mapping["{row:4}"]]}}
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "answer_parts": [{"request_id": "Q1", "requirement": "行A4の点数", "requirement_type": "lookup", "answer": source}],
                "confidence": "high",
            }
        )
