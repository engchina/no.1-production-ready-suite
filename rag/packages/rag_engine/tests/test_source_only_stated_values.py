"""作り直しで返った答えの数字が、引用した 1 つの原文の単位に書かれていれば、直接の記載として値を答える（#4631）。

録画応答（``fixtures/source_only_value_replay_outputs.json``。合成の判定表と区分の規則）を、テストの原文単位 ID に置き換えて
再生する。質問全体を calculation と宣言して要点がすべて source 型の出力は、導出だけの契約で作り直される。作り直しの答えの
数字をコードが確かめ、語の意味は読まない。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from application_verdict_stub import audit_reply, is_application_schema
from audit_fixtures import synthetic_explanation

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import application_explanation as explanation
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "source_only_value_replay_outputs.json").read_text(encoding="utf-8"))["outputs"]
QUESTION = "行A4の点数と、区分Bになる合計点の範囲を教えてください。"
POINTS = zip("ABCDEFGHI", (2, 3, 1, 4, 2, 1, 3, 2, 5), strict=True)
TABLE = "| 行ID | 条件 | 点数 |\n" + "\n".join(
    f"| A{number} | 属性{name}=有 | {points} |" for number, (name, points) in enumerate(POINTS, 1)
)
ROW_A4 = "| A4 | 属性D=有 | 4 |"
RULE = "合計点の区分は、0〜9点は区分A、10〜19点は区分B、20〜29点は区分Cです。"
MISSING = "根拠付きの説明を十分に示せていません"
ROW_REQUIREMENT = "行A4の点数"
RANGE_REQUIREMENT = "区分Bになる合計点の範囲"


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
        elif text.startswith("| 行ID"):
            continue
        elif text == RULE:
            mapping["{rule}"] = unit
        else:
            mapping["{only}"] = unit
    text = json.dumps(output, ensure_ascii=False)
    for key, value in mapping.items():
        text = text.replace(key, value)
    assert "{row:" not in text and "{rule}" not in text and "{only}" not in text
    return json.loads(text)


def _source(requirement: str, *units: str) -> dict[str, Any]:
    return {
        "request_id": "Q1",
        "requirement": requirement,
        "mode": "stated",
        "answer": {"kind": "source", "basis": {"unit_ids": list(units)}},
    }


def _derived(requirement: str, result: str, *units: str) -> dict[str, Any]:
    return {
        "request_id": "Q1",
        "requirement": requirement,
        "answer": {"kind": "answer", "basis": {"unit_ids": list(units)}, "reasoning": "原文の値から導く。", "result": result},
    }


def _draft(*parts: dict[str, Any]) -> dict[str, Any]:
    return {
        "response_language": {"selection": {"kind": "default"}},
        "question_type": ["calculation"],
        "answer_parts": list(parts),
        "external_data_required": False,
        "external_data_items": [],
        "reference_materials": [],
        "confidence": "high",
        "summary_part_indexes": [0],
    }


def run(first: dict[str, Any], repair: dict[str, Any], *, texts: tuple[str, ...] = (TABLE, RULE), undetermined: bool = False):
    calls: list[str] = []

    def parse(system, prompt, config, schema, **options):
        name = schema.__name__
        if name == "RowConditionType":
            calls.append("row")
            raise ValueError("合成の不受理（行の判定を not_determined にする）")
        if explanation.is_explanation_schema(schema):
            return synthetic_explanation(schema, prompt)
        if is_application_schema(schema):
            return audit_reply(schema, [{"index": 0, "valid": True}], prompt=prompt)
        assert name == "GroundedDraftWire", name
        if "source_only_requests" in prompt:
            calls.append("repair")
            return schema.model_validate(_bind(repair, prompt))
        calls.append("generate")
        return schema.model_validate(_bind(first, prompt))

    def unexpected(*args, **kwargs):
        raise AssertionError("追加の検索・画像の呼び出しは不要")

    records = [_record(f"record-{index}", text, f"synthetic-{index}.md") for index, text in enumerate(texts)]
    environ = (
        {
            "OCI_ENTERPRISE_AI_DEFAULT_MODEL": "answer-model",
            "RAG_ENGINE_ROW_CONDITION_MODEL": "row-model",
            "RAG_ENGINE_ROW_CONDITION_ENDPOINT": "https://row.invalid/v1",
            "RAG_ENGINE_ROW_CONDITION_API_KEY": "synthetic-row",
        }
        if undetermined
        else {}
    )
    settings = replace(
        get_settings(environ=environ, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0, llm_retries=0
    )
    with bind_dependencies(AnswerDependencies(unexpected, unexpected, parse, unexpected, unexpected)):
        response = synthesize_grounded_answer(
            QUESTION, AnswerContext(records=records, text="\n".join(texts)), settings, image_prompt_mode="text_only"
        ).response
    return response, calls


def kinds(response) -> list[str]:
    return [part["result_kind"] for part in response.generation_trace["rounds"][0]["draft"]["answer_parts"]]


def test_recorded_repair_of_stated_values_answers_the_values():
    """録画の作り直し（「4点」「10点〜19点」）は、数字が引用した 1 つの単位にあるので値を答える。"""
    response, calls = run(RECORDED["mr1_multi_request"], RECORDED["mr1_multi_request_repair"])
    assert calls == ["generate", "repair"]
    assert kinds(response) == ["direct", "direct"]
    assert response.generation_trace["rounds"][0]["timing"]["answer_part_role_repair"]["outcome"] == "repaired"
    assert not response.generation_trace["rounds"][0]["draft"]["applications"]
    assert ROW_A4 in response.answer_text and RULE in response.answer_text
    assert MISSING not in response.answer_text


def test_stated_values_are_not_held_by_an_undetermined_table():
    """表の行の判定が not_determined でも、直接の記載になった値は保留にならない（#4631 の症状）。"""
    response, calls = run(RECORDED["mr1_multi_request"], RECORDED["mr1_multi_request_repair"], undetermined=True)
    assert "row" in calls
    assert [table["table_result"] for table in response.generation_trace["row_conditions"]["tables"]] == ["not_determined"]
    assert kinds(response) == ["direct", "direct"]
    assert ROW_A4 in response.answer_text and MISSING not in response.answer_text


def test_sum_is_still_held_by_an_undetermined_table():
    """足し算の答えは導出のままで、表の行の判定が not_determined なら従来どおり保留する。"""
    repair = _draft(_derived(ROW_REQUIREMENT, "合計点は18点です。", *[f"{{row:{number}}}" for number in range(1, 9)]))
    response, _calls = run(_draft(_source(ROW_REQUIREMENT, "{row:4}")), repair, undetermined=True)
    assert kinds(response) == ["derived"]
    assert "18点" not in response.answer_text and MISSING in response.answer_text


@pytest.mark.parametrize(
    ("text", "result"),
    [
        ("上限は１２０点です。", "120点"),
        ("上限は120点です。", "１２０点"),
        ("上限は1,200点です。", "1200点"),
        ("上限は1200点です。", "1,200点"),
        ("上限は１，２００点です。", "1200点"),
        ("対象は10～19点です。", "10点〜19点"),
        ("対象は10-19点です。", "10点から19点"),
        ("対象は10点以上19点以下です。", "10〜19点"),
        ("割合は1.5%です。", "1.50%"),
        ("開始日は2026/04/01です。", "2026年4月1日"),
    ],
)
def test_notation_differences_of_a_stated_value_are_the_same_number(text, result):
    response, calls = run(_draft(_source("上限", "{only}")), _draft(_derived("上限", result, "{only}")), texts=(text,))
    assert calls == ["generate", "repair"]
    assert kinds(response) == ["direct"]
    assert text in response.answer_text and result not in response.answer_text.replace(text, "")


@pytest.mark.parametrize(
    ("result", "units"),
    [
        pytest.param("合計点は18点です。", ("{row:4}",), id="sum-missing-from-the-cited-unit"),
        pytest.param("合計点は18点です。", tuple(f"{{row:{number}}}" for number in range(1, 9)), id="sum-of-the-cited-rows"),
        pytest.param("5点です。", ("{row:1}", "{row:2}", "{row:9}"), id="sum-equal-to-one-cited-row"),
        pytest.param("4点です。", ("{row:4}", "{row:5}"), id="adjacent-units-are-not-one-unit"),
        pytest.param("4点です。", ("{row:4}", "{rule}"), id="units-of-two-records"),
        pytest.param("区分Bです。", ("{rule}",), id="answer-without-digits"),
        pytest.param("14点です。", ("{row:4}",), id="digits-inside-another-number"),
    ],
)
def test_other_repaired_answers_stay_derivations(result, units):
    response, calls = run(_draft(_source(ROW_REQUIREMENT, "{row:4}")), _draft(_derived(ROW_REQUIREMENT, result, *units)))
    assert calls[:2] == ["generate", "repair"]
    assert kinds(response) == ["derived"]


def test_each_part_of_a_repair_is_checked_on_its_own():
    first = _draft(_source(ROW_REQUIREMENT, "{row:4}"), _source(RANGE_REQUIREMENT, "{rule}"))
    repair = _draft(
        _derived(ROW_REQUIREMENT, "4点", "{row:4}"),
        _derived(RANGE_REQUIREMENT, "合計点は18点で区分Bです。", *[f"{{row:{number}}}" for number in range(1, 9)], "{rule}"),
    )
    response, _calls = run(first, repair)
    assert kinds(response) == ["direct", "derived"]


def test_value_equal_to_the_only_cited_unit_is_a_known_limit():
    """既知の制約: 1 つの単位だけを引用し、合計がたまたまその単位の値と同じ答えは、直接の記載として扱う（形式では区別しない）。"""
    response, _calls = run(_draft(_source(ROW_REQUIREMENT, "{row:4}")), _draft(_derived(ROW_REQUIREMENT, "合計点は4点です。", "{row:4}")))
    assert kinds(response) == ["direct"]


def test_first_generation_answer_is_not_rewritten():
    """作り直しを通らない 1 回目の answer 型は、数字が 1 つの単位にあっても今までどおり導出のまま。"""
    first = _draft(_derived(ROW_REQUIREMENT, "4点", "{row:4}"))
    response, calls = run(first, first)
    assert calls == ["generate"]
    assert kinds(response) == ["derived"]
