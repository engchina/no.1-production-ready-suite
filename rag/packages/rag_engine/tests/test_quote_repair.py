"""source 型だけの出力の作り直し（#4586）で、原文の 1 つの単位からの抜き出し（quote 型）を受け、値を答える（#4631）。

録画応答（``fixtures/quote_repair_replay_outputs.json``。配備 17e41814af の既定のモデルの answer.generate、合成の判定表と
区分の規則）を、テストの原文単位 ID に置き換えて再生する。要求が 2 つで値が原文にある題は、質問全体を calculation と
宣言して要点がすべて source 型だったので作り直しの対象になり、導出だけの契約では表の行の判定が決まらない題で答えが
保留になっていた。作り直しの出力は、抜き出しの型の合成（原文にある字句・無い字句）で与える。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from openai.lib._parsing._responses import type_to_text_format_param
from pydantic import ValidationError

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answer_parts_wire import answer_parts_model
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "quote_repair_replay_outputs.json").read_text(encoding="utf-8"))["outputs"]
QUESTION = "行A4の点数と、区分Bになる合計点の範囲を教えてください。"
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


def _bind(output: dict[str, Any], prompt: str) -> dict[str, Any]:
    """プロンプトの番号付きの原文から、fixture の記号を今回の原文単位 ID に置き換える。"""
    mapping: dict[str, str] = {}
    for line in prompt.splitlines():
        if not (found := re.match(r"\d+\| \[(U[0-9a-f]{24})\] (.*)", line)):
            continue
        unit, text = found.groups()
        if row := re.match(r"\| A(\d) \|", text):
            mapping["{row:" + row.group(1) + "}"] = unit
        elif text == RULE:
            mapping["{rule}"] = unit
    text = json.dumps(output, ensure_ascii=False)
    for key, value in mapping.items():
        text = text.replace(key, value)
    assert "{row:" not in text and "{rule}" not in text
    return json.loads(text)


def quoted_repair(rule_text: str = "10〜19点は区分B") -> dict[str, Any]:
    """録画の作り直し（導出だけの契約の出力）の要点を、抜き出しの型にした合成の作り直し。"""
    repair = json.loads(json.dumps(RECORDED["mr1_multi_request_repair"], ensure_ascii=False))
    quotes = [{"kind": "quote", "unit_id": "{row:4}", "text": "4"}, {"kind": "quote", "unit_id": "{rule}", "text": rule_text}]
    for part, quote in zip(repair["answer_parts"], quotes, strict=True):
        part["answer"] = quote
    return repair


def run(repair: dict[str, Any]):
    calls: list[str] = []

    def parse(system, prompt, config, schema, **options):
        calls.append("repair" if "source_only_requests" in prompt else "generate")
        assert schema.__name__ == "GroundedDraftWire", schema.__name__
        if "source_only_requests" in prompt:
            return schema.model_validate(_bind(repair, prompt))
        return schema.model_validate(_bind(RECORDED["mr1_multi_request"], prompt))

    def unexpected(*args, **kwargs):
        raise AssertionError("追加の検索・画像の呼び出しは不要")

    records = [_record("table-a", TABLE, "synthetic-table.md"), _record("rule-a", RULE, "synthetic-rule.md")]
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0, llm_retries=0)
    with bind_dependencies(AnswerDependencies(unexpected, unexpected, parse, unexpected, unexpected)):
        response = synthesize_grounded_answer(
            QUESTION, AnswerContext(records=records, text=TABLE + "\n" + RULE), settings, image_prompt_mode="text_only"
        ).response
    return response, calls


def test_recorded_output_states_every_part_under_a_calculation_declaration():
    output = RECORDED["mr1_multi_request"]
    assert output["question_type"] == ["calculation"]
    assert [(part["mode"], part["answer"]["kind"]) for part in output["answer_parts"]] == [("stated", "source"), ("stated", "source")]


def test_quoted_repair_answers_the_stated_values():
    """作り直しで、値が書かれた単位からの抜き出しを返した要点は、原文の直接の記載として値を答える。"""
    response, calls = run(quoted_repair())
    assert calls == ["generate", "repair"]
    round_ = response.generation_trace["rounds"][0]
    repair = round_["timing"]["answer_part_role_repair"]
    assert repair["outcome"] == "repaired" and repair["answer_kinds"] == ["quote", "quote"]
    draft = round_["draft"]
    assert [part["result_kind"] for part in draft["answer_parts"]] == ["direct", "direct"]
    assert not draft["applications"]
    assert ROW_A4 in response.answer_text and RULE in response.answer_text
    assert MISSING not in response.answer_text


def test_quote_missing_from_the_unit_is_an_invalid_repair():
    """抜き出しが単位の本文に無い作り直しは契約違反で、従来どおり導出の無い要点（不足）にする。"""
    response, _calls = run(quoted_repair("10点〜19点"))
    repair = response.generation_trace["rounds"][0]["timing"]["answer_part_role_repair"]
    assert repair["outcome"] == "underived" and repair["error"] == "ValidationError"
    assert MISSING in response.answer_text


def _catalog() -> tuple[SentUnitCatalog, EvidenceUnitSnapshot]:
    span = {"label": "E1", "evidence_id": "rule-a", "source_id": "s-r", "source": "synthetic-rule.md", "text": RULE}
    snapshot = EvidenceUnitSnapshot.create(span, [(0, len(RULE))], "explicit_units_v5")
    return SentUnitCatalog([snapshot]), snapshot


def ref(node: dict) -> str:
    return node["$ref"].rsplit("/", 1)[1]


def test_only_the_regeneration_contract_has_the_quote_answer():
    units, snapshot = _catalog()
    for direct_source, expected in ((True, False), (False, True)):
        strict = type_to_text_format_param(answer_parts_model(generation_draft_wire_model(), units, direct_source=direct_source))["schema"]
        assert ("QuoteAnswerPart" in strict["$defs"]) is expected
    quote = strict["$defs"]["QuoteAnswerPart"]
    assert list(quote["properties"]) == quote["required"] == ["kind", "unit_id", "text"]
    unit = quote["properties"]["unit_id"]
    assert unit.get("enum", [unit.get("const")]) == list(snapshot.unit_ids)
    part = strict["$defs"][ref(strict["properties"]["answer_parts"]["items"])]
    assert [ref(node) for node in part["properties"]["answer"]["anyOf"]] == ["SupportedAnswerPart", "QuoteAnswerPart", "GapAnswerPart"]


def _quote_output(unit_id: str, text: str) -> dict[str, Any]:
    return {
        "answer_parts": [{"request_id": "Q1", "requirement": "区分B", "answer": {"kind": "quote", "unit_id": unit_id, "text": text}}],
        "question_type": ["calculation"],
        "summary_part_indexes": [0],
        "confidence": "high",
    }


@pytest.mark.parametrize("text", ["10〜19点は区分B", "10〜19点 は 区分B", RULE])
def test_quote_is_checked_as_a_substring_ignoring_only_whitespace(text):
    units, snapshot = _catalog()
    model = answer_parts_model(generation_draft_wire_model(), units, direct_source=False)
    draft = canonical_generation_draft(model.model_validate(_quote_output(snapshot.unit_ids[0], text)), unit_catalog=units)
    assert [part.result_kind for part in draft.answer_parts] == ["direct"]
    assert draft.items[0].text == RULE
    assert draft.summary == RULE


@pytest.mark.parametrize("text", ["10点〜19点", "区分Bは10〜19点", "18点"])
def test_quote_not_written_in_the_unit_is_rejected(text):
    units, snapshot = _catalog()
    model = answer_parts_model(generation_draft_wire_model(), units, direct_source=False)
    with pytest.raises(ValidationError, match="quote 型の text"):
        model.model_validate(_quote_output(snapshot.unit_ids[0], text))


def test_quote_unit_must_be_a_sent_unit():
    units, _snapshot = _catalog()
    model = answer_parts_model(generation_draft_wire_model(), units, direct_source=False)
    with pytest.raises(ValidationError):
        model.model_validate(_quote_output("U" + "0" * 24, "区分B"))
