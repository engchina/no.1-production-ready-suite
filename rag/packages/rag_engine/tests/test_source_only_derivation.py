"""導出が要る種類を宣言した出力の要点がすべて source 型のとき、原文の列挙を答えにしない（#4586）。

録画応答（``fixtures/source_only_derivation_outputs.json``。合成の判定表と区分の規則）を、テストの原文単位 ID と
根拠の label に置き換えて再生する。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from rag_engine.adapters.oci import LlmRunawayOutput
from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answer_parts_wire import DERIVATION_QUESTION_TYPES, source_only_derivation
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft
from rag_engine.generation.grounded import generation_output_model

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "source_only_derivation_outputs.json").read_text(encoding="utf-8"))
QUESTION = "属性A〜Hは有です。属性Iは無です。確定した合計点と区分を教えてください。"
POINTS = zip("ABCDEFGHI", (2, 3, 1, 4, 2, 1, 3, 2, 5), strict=True)
TABLE = "| 行ID | 条件 | 点数 |\n" + "\n".join(
    f"| A{number} | 属性{name}=有 | {points} |" for number, (name, points) in enumerate(POINTS, 1)
)
RULE = "合計点の区分は、0〜9点は区分A、10〜19点は区分B、20〜29点は区分Cです。"
DERIVED = "合計点は18点、区分はBです。"


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
    """プロンプトの番号付きの原文から、fixture の置き換えの記号 → 今回の原文単位 ID・根拠の label の対応を作る。"""
    mapping: dict[str, str] = {}
    label = ""
    for line in prompt.splitlines():
        if found := re.match(r"\[(E\d+)\]", line):
            label = found.group(1)
        if not (found := re.match(r"\d+\| \[(U[0-9a-f]{24})\] (.*)", line)):
            continue
        unit, text = found.groups()
        if row := re.match(r"\| A(\d) \|", text):
            mapping["{row:" + row.group(1) + "}"] = unit
            mapping["{E:table}"] = label
        elif text.startswith("| 行ID"):
            mapping["{header}"] = unit
        elif text == RULE:
            mapping["{rule}"] = unit
            mapping["{E:rule}"] = label
    return mapping


def _replay(name: str, prompt: str) -> dict[str, Any]:
    text = json.dumps(FIXTURE["outputs"][name], ensure_ascii=False)
    for key, value in _placeholders(prompt).items():
        text = text.replace(key, value)
    assert "{" + "row:" not in text and "{E:" not in text
    output = json.loads(text)
    # 生成の schema は要求ごとに計画を 1 件だけ受ける（#4587）。同じ要求の計画を繰り返した録画は
    # test_request_coverage_cardinality が拒まれることを確かめ、ここでは要求ごとの最初の計画で再生する。
    first: dict[str, Any] = {}
    for plan in output["request_coverage"]:
        first.setdefault(plan["request_id"], plan)
    output["request_coverage"] = list(first.values())
    return output


def _derivation(prompt: str, requirement: str) -> dict[str, Any]:
    mapping = _placeholders(prompt)
    rows = [mapping[f"{{row:{number}}}"] for number in range(1, 10)]
    return {
        "answer_parts": [
            {
                "request_id": "Q1",
                "requirement": requirement,
                "answer": {
                    "kind": "answer",
                    "basis": {"unit_ids": [*rows, mapping["{rule}"]]},
                    "reasoning": "2+3+1+4+2+1+3+2=18。属性Iは無なので加算しない。18点は10〜19点の区分B。",
                    "result": DERIVED,
                },
            }
        ],
        "question_type": ["calculation", "classification"],
        "summary_part_indexes": [0],
        "request_coverage": [
            {
                "request_id": "Q1",
                "evidence_scope": [
                    {"evidence_id": mapping["{E:table}"], "target": "判定表", "relation": "direct", "link": None},
                    {"evidence_id": mapping["{E:rule}"], "target": "区分の規則", "relation": "direct", "link": None},
                ],
            }
        ],
        "confidence": "high",
    }


# 作り直しでモデルが書いた識別子の形の文（実モデルの確認で不足の文に出た形の合成）。
IDENTIFIER_REQUIREMENT = "calculation_and_classification"


def _first_requirement(name: str) -> str:
    return FIXTURE["outputs"][name]["answer_parts"][0]["requirement"]


def run(name: str, *, repair: str = "derive"):
    calls: list[tuple[str, str]] = []

    def parse(system, prompt, config, schema, **options):
        calls.append((options.get("purpose") or "", schema.__name__))
        if schema.__name__ == "ApplicationExplanation":
            return schema.model_validate({"explanation": "表の行の点数を合計し、区分の規則に当てはめた。"})
        assert schema.__name__ == "GroundedDraftWire", schema.__name__
        if "source_only_requests" not in prompt:
            return schema.model_validate(_replay(name, prompt))
        # 作り直しの契約は source 型を受けない。
        with pytest.raises(ValidationError):
            schema.model_validate(_replay(name, prompt))
        # 作り直しの要点の requirement は、1 回目の出力の同じ要求の requirement の写しだけを受ける。
        with pytest.raises(ValidationError):
            schema.model_validate(_derivation(prompt, IDENTIFIER_REQUIREMENT))
        if repair == "invalid":
            raise LlmRunawayOutput("合成の反復", "runaway_repetition", None)
        return schema.model_validate(_derivation(prompt, _first_requirement(name)))

    def unexpected(*args, **kwargs):
        raise AssertionError("追加の検索・画像の呼び出しは不要")

    records = [_record("table-a", TABLE, "synthetic-table.md"), _record("rule-a", RULE, "synthetic-rule.md")]
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0, llm_retries=0)

    with bind_dependencies(AnswerDependencies(unexpected, unexpected, parse, unexpected, unexpected)):
        response = synthesize_grounded_answer(
            QUESTION,
            AnswerContext(records=records, text=TABLE + "\n" + RULE),
            settings,
            image_prompt_mode="text_only",
        ).response
    return response, [schema for _purpose, schema in calls]


@pytest.mark.parametrize("name", sorted(FIXTURE["outputs"]))
def test_recorded_source_only_outputs_contradict_their_declared_derivation(name):
    """録画の出力は、導出が要る種類を宣言しながら要点がすべて source 型。"""
    output = FIXTURE["outputs"][name]
    assert set(output["question_type"]) & set(DERIVATION_QUESTION_TYPES)
    assert {part["answer"]["kind"] for part in output["answer_parts"]} == {"source"}


@pytest.mark.parametrize("name", sorted(FIXTURE["outputs"]))
def test_source_only_derivation_is_regenerated_without_source_and_publishes_the_derivation(name):
    response, calls = run(name)
    assert calls[:2] == ["GroundedDraftWire", "GroundedDraftWire"]
    assert calls[2:] == ["ApplicationExplanation"]
    assert DERIVED in response.answer_text
    trace = response.generation_trace
    assert trace["applications"][0]["published"] is True
    round_ = trace["rounds"][0]
    assert round_["timing"]["answer_part_role_repair"]["outcome"] == "repaired"
    assert round_["timing"]["answer_part_role_repair"]["requests"] == ["Q1"]
    assert round_["timing"]["generation_calls"] == 2
    assert [part["result_kind"] for part in round_["draft"]["answer_parts"]] == ["derived"]
    assert [part["text"] for part in round_["draft"]["answer_parts"]] == [_first_requirement(name)]


@pytest.mark.parametrize("name", sorted(FIXTURE["outputs"]))
def test_failed_regeneration_leaves_a_gap_instead_of_the_row_listing_as_the_answer(name):
    response, calls = run(name, repair="invalid")
    assert calls[:2] == ["GroundedDraftWire", "GroundedDraftWire"]
    round_ = response.generation_trace["rounds"][0]
    assert round_["timing"]["answer_part_role_repair"]["outcome"] == "underived"
    draft = round_["draft"]
    assert not draft["applications"]
    assert {part["result_kind"] for part in draft["answer_parts"]} == {"derived"}
    # 原文の列挙を要約（冒頭の答え）にしない。要点は欠けた要点として不足の記載になる。
    assert draft["summary"] == "" and draft["summary_item_indexes"] == []
    assert "根拠付きの説明を十分に示せていません" in response.answer_text
    assert not response.answer_text.startswith("| A1")


def _single_unit_case(question_type: list[str]):
    text = "項目Aは12です。"
    span = {"label": "E1", "evidence_id": "source-a", "source_id": "s-a", "source": "synthetic.txt", "text": text}
    snapshot = EvidenceUnitSnapshot.create(span, [(0, len(text))], "explicit_units_v5")
    catalog = SentUnitCatalog([snapshot])
    model = generation_output_model("項目Aの値を教えてください。", unit_catalog=catalog)
    value = model.model_validate(
        {
            "answer_parts": [
                {
                    "request_id": "Q1",
                    "requirement": "項目Aの値",
                    "answer": {"kind": "source", "basis": {"unit_ids": list(snapshot.unit_ids)}},
                }
            ],
            "question_type": question_type,
            "summary_part_indexes": [0],
            "confidence": "high",
        }
    )
    return value, catalog


@pytest.mark.parametrize("question_type", [["fact"], [], ["definition", "comparison"]])
def test_direct_fact_source_stays_direct(question_type):
    """原文の記載で答えられる種類（または宣言なし）の source 型は、従来どおり直接の記載（#4448）。"""
    value, catalog = _single_unit_case(question_type)
    assert source_only_derivation(value) == []
    draft = canonical_generation_draft(value, unit_catalog=catalog)
    assert draft.answer_parts[0].result_kind == "direct"
    assert draft.summary == "項目Aは12です。"


def test_mixed_source_and_derivation_is_not_a_contradiction():
    value, _catalog = _single_unit_case(["calculation"])
    raw = value.model_dump(exclude_unset=True)
    basis = raw["answer_parts"][0]["answer"]["basis"]
    raw["answer_parts"].append(
        {
            "request_id": "Q1",
            "requirement": "2件分",
            "answer": {"kind": "answer", "basis": basis, "reasoning": "12×2=24", "result": "24です。"},
        }
    )
    assert source_only_derivation(type(value).model_validate(raw)) == []
    raw["answer_parts"][1] = {"request_id": "Q1", "requirement": "2件分", "answer": {"kind": "gap", "text": "件数の資料が必要です。"}}
    assert source_only_derivation(type(value).model_validate(raw)) == []


def test_question_type_is_a_closed_vocabulary():
    value, _catalog = _single_unit_case(["fact"])
    raw = value.model_dump(exclude_unset=True)
    raw["question_type"] = ["price_calculation"]
    with pytest.raises(ValidationError):
        type(value).model_validate(raw)
