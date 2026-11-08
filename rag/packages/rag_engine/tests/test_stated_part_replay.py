"""質問全体の宣言（question_type）で、要点ごとに記載（mode=stated）と選んだ source 型の要点を導出に作り直さない（#4631）。

録画応答（``fixtures/stated_part_replay_outputs.json``。配備 17e41814af の既定のモデルの answer.generate、合成の判定表と
区分の規則）を、テストの原文単位 ID に置き換えてそのまま再生する。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from application_verdict_stub import APPLICATION_SCHEMAS, audit_reply
from pydantic import ValidationError

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answer_parts_wire import DERIVATION_QUESTION_TYPES
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.grounded import generation_output_model

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = json.loads((FIXTURES / "stated_part_replay_outputs.json").read_text(encoding="utf-8"))["outputs"]
# #4586 の録画（mode の無い世代。計算の質問で、質問全体を計算と宣言し、要点がすべて表の行・規則の source 型だった出力）。
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


def run(question: str, output: dict[str, Any]):
    calls: list[str] = []

    def parse(system, prompt, config, schema, **options):
        calls.append(schema.__name__)
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
    return response, calls


def test_recorded_multi_request_declares_calculation_but_states_every_part():
    output = RECORDED["mr1_multi_request"]
    assert output["question_type"] == ["calculation"]
    assert [(part["mode"], part["answer"]["kind"]) for part in output["answer_parts"]] == [("stated", "source"), ("stated", "source")]


def test_stated_parts_keep_the_recorded_values_without_regeneration():
    """要求が 2 つの質問で、どちらの要点も記載・source 型なら、質問全体の calculation の宣言で作り直さずに原文の値を答える。

    作り直すと、表の行の判定が決まらない題では作り直した導出が保留になり、値を答えなかった（配備 17e41814af）。
    """
    response, calls = run(MULTI_REQUEST_QUESTION, RECORDED["mr1_multi_request"])
    assert calls.count("GroundedDraftWire") == 1
    round_ = response.generation_trace["rounds"][0]
    assert round_["timing"]["generation_calls"] == 1
    assert "answer_part_role_repair" not in round_["timing"]
    draft = round_["draft"]
    assert [part["result_kind"] for part in draft["answer_parts"]] == ["direct", "direct"]
    assert draft["question_type"] == ["calculation"]
    assert ROW_A4 in response.answer_text and RULE in response.answer_text
    assert MISSING not in response.answer_text


@pytest.mark.parametrize("name", ["rk1_rows_known", "rk2_rows_known"])
def test_calculation_answered_with_a_derivation_publishes_it(name):
    """計算の質問で要点が answer 型（記載の役割でも）なら、作り直しなしに導出を公開する。"""
    output = RECORDED[name]
    assert [(part["mode"], part["answer"]["kind"]) for part in output["answer_parts"]] == [("stated", "answer")]
    response, calls = run(QUESTION, output)
    assert calls.count("GroundedDraftWire") == 1
    trace = response.generation_trace
    assert trace["applications"][0]["published"] is True
    assert [part["result_kind"] for part in trace["rounds"][0]["draft"]["answer_parts"]] == ["derived"]
    assert "18" in response.answer_text and "区分" in response.answer_text


def test_direct_value_stays_direct():
    response, calls = run(DIRECT_QUESTION, RECORDED["dv1_direct_value"])
    assert calls.count("GroundedDraftWire") == 1
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


@pytest.mark.parametrize("name", sorted(SOURCE_ONLY))
def test_source_only_listing_cannot_be_a_derivation_part(name):
    """#4586 の録画の要点を導出の役割（mode=derivation）にすると、行・規則の列挙（source 型）は契約が受けない。

    計算の質問で要点がすべて source 型になる出力は、要点の役割を答えの型より先に選ばせる schema で防ぐ（#4611）。
    """
    output = SOURCE_ONLY[name]
    assert set(output["question_type"]) & set(DERIVATION_QUESTION_TYPES)
    assert {part["answer"]["kind"] for part in output["answer_parts"]} == {"source"}
    catalog, mapping = _table_catalog()
    model = generation_output_model(QUESTION, unit_catalog=catalog)
    value = json.loads(json.dumps(output, ensure_ascii=False))
    value.pop("request_coverage", None)
    for part in value["answer_parts"]:
        part["mode"] = "derivation"
    text = json.dumps(value, ensure_ascii=False)
    for key, unit in mapping.items():
        text = text.replace(key, unit)
    with pytest.raises(ValidationError):
        model.model_validate(json.loads(text))
