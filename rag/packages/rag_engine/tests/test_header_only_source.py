"""表の見出しの行だけを答えの原文にした source 型の要点を、表の値の答えとして受けない（#4592）。

録画応答（``fixtures/header_only_source_outputs.json``。合成の判定表への直接の値の質問）を、テストの原文単位 ID と
根拠の label に置き換えて再生する。見出しの行かは、表の chunk の分割が記録した列名（``table_row_tree_column_keys``）と
一致した行だけで決まる。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from application_verdict_stub import APPLICATION_SCHEMAS, audit_reply

from rag_engine.adapters.oci import LlmRunawayOutput
from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answer_parts_wire import header_only_source_parts
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft
from rag_engine.generation.grounded import generation_output_model
from rag_engine.retrieval.evidence_selection import evidence_spans
from rag_engine.retrieval.table_header_lines import TABLE_HEADER_LINES_KEY, recorded_header_line

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "header_only_source_outputs.json").read_text(encoding="utf-8"))
QUESTION = "行A4の点数を教えてください。"
HEADER = "| 行ID | 条件 | 点数 |"
POINTS = zip("ABCDEFGHI", (2, 3, 1, 4, 2, 1, 3, 2, 5), strict=True)
ROWS = [f"| A{number} | 属性{name}=有 | {points} |" for number, (name, points) in enumerate(POINTS, 1)]
TABLE = "\n".join([HEADER, *ROWS])
ROW_A4 = ROWS[3]
# 表の chunk の分割の記録（rag_pipeline_core の _table_row_tree_metadata の形）。
TABLE_METADATA = {"content_kind": "table", "table_row_tree_column_keys": json.dumps(["行ID", "条件", "点数"], ensure_ascii=False)}


def _record(identity: str, text: str, metadata: dict[str, Any] | None = None) -> AnswerRecord:
    return AnswerRecord(
        id=identity,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source="synthetic-table.md",
        chunk_level="parent",
        chunk_id=identity,
        source_run_id="synthetic",
        metadata={"layout": {"native_text_ranges": [{"start": 0, "end": len(text)}]}, **(metadata or {})},
    )


def _units(prompt: str) -> tuple[dict[str, str], str]:
    """プロンプトの番号付きの原文から、行の原文 → 原文単位 ID と、表の根拠の label を作る。"""
    mapping: dict[str, str] = {}
    label = table_label = ""
    for line in prompt.splitlines():
        if found := re.match(r"\[(E\d+)\]", line):
            label = found.group(1)
        if found := re.match(r"\d+\| \[(U[0-9a-f]{24})\] (.*)", line):
            mapping[found.group(2)] = found.group(1)
            if found.group(2) == HEADER:
                table_label = label
    return mapping, table_label


def _replay(prompt: str) -> dict[str, Any]:
    mapping, label = _units(prompt)
    text = json.dumps(FIXTURE["outputs"]["direct_value_header_only"], ensure_ascii=False)
    return json.loads(text.replace("{header}", mapping[HEADER]).replace("{E:table}", label))


def _row_answer(prompt: str) -> dict[str, Any]:
    output = _replay(prompt)
    mapping, _label = _units(prompt)
    output["answer_parts"][0]["answer"]["basis"]["unit_ids"] = [mapping[HEADER], mapping[ROW_A4]]
    output["request_coverage"][0]["evidence_scope"][0]["link"] = {"unit_ids": [mapping[ROW_A4]]}
    return output


def run(*, repair: str = "row", metadata: dict[str, Any] | None = TABLE_METADATA):
    calls: list[str] = []
    prompts: list[str] = []

    def parse(system, prompt, config, schema, **options):
        calls.append(schema.__name__)
        if schema in APPLICATION_SCHEMAS:
            return audit_reply(schema, [{"index": 0, "valid": True}], prompt=prompt, summary_supported=True)
        assert schema.__name__ == "GroundedDraftWire", schema.__name__
        prompts.append(prompt)
        if "header_only_requests" not in prompt:
            return schema.model_validate(_replay(prompt))
        if repair == "invalid":
            raise LlmRunawayOutput("合成の反復", "runaway_repetition", None)
        return schema.model_validate(_row_answer(prompt) if repair == "row" else _replay(prompt))

    def unexpected(*args, **kwargs):
        raise AssertionError("追加の検索・画像の呼び出しは不要")

    records = [_record("table-a", TABLE, metadata)]
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0, llm_retries=0)
    with bind_dependencies(AnswerDependencies(unexpected, unexpected, parse, unexpected, unexpected)):
        response = synthesize_grounded_answer(
            QUESTION, AnswerContext(records=records, text=TABLE), settings, image_prompt_mode="text_only"
        ).response
    return response, calls, prompts


def test_recorded_output_selects_only_the_header_row():
    output = FIXTURE["outputs"]["direct_value_header_only"]
    assert [part["answer"] for part in output["answer_parts"]] == [{"kind": "source", "basis": {"unit_ids": ["{header}"]}}]


def test_header_only_source_is_regenerated_once_and_the_data_row_is_shown():
    response, calls, prompts = run()
    assert calls[:2] == ["GroundedDraftWire", "GroundedDraftWire"]
    assert '"header_only_requests": ["Q1"]' in prompts[1]
    assert "## Step 6: 表の見出しの行だけを示した要点を作り直す" in prompts[1]
    timing = response.generation_trace["rounds"][0]["timing"]
    assert timing["header_only_source_repair"] == {"requests": ["Q1"], "outcome": "repaired"}
    assert timing["generation_calls"] == 2
    assert ROW_A4 in response.answer_text
    assert "確認できません" not in response.answer_text


@pytest.mark.parametrize("repair", ["header_again", "invalid"])
def test_header_only_source_left_after_regeneration_is_not_an_answer(repair):
    response, calls, _prompts = run(repair=repair)
    assert calls[:2] == ["GroundedDraftWire", "GroundedDraftWire"]
    round_ = response.generation_trace["rounds"][0]
    assert round_["timing"]["header_only_source_repair"]["outcome"] == "unaccepted"
    draft = round_["draft"]
    # 見出しの行を確認できた事項・冒頭の答えにしない。要点は欠けた要点として不足の記載になる。
    assert HEADER not in [item["text"] for item in draft["items"]]
    assert draft["summary"] == "" and draft["summary_item_indexes"] == []
    assert [part["result_kind"] for part in draft["answer_parts"]] == ["derived"]
    assert "根拠付きの説明を十分に示せていません" in response.answer_text
    assert not response.answer_text.startswith(HEADER)


def test_without_recorded_table_header_the_selection_is_not_judged():
    """表の chunk の分割の記録が無い根拠では、見出しの行を推測しない（作り直さない）。"""
    response, calls, prompts = run(metadata={})
    assert calls[0] == "GroundedDraftWire" and calls.count("GroundedDraftWire") == 1
    assert "header_only_source_repair" not in response.generation_trace["rounds"][0]["timing"]
    # 記録した見出しの行は根拠の属性で、生成の入力（根拠ブロック）は変えない。
    assert run()[2][0] == prompts[0]


def _catalog(span: dict[str, Any]) -> tuple[SentUnitCatalog, EvidenceUnitSnapshot]:
    text = span["text"]
    units, position = [], 0
    for line in text.split("\n"):
        units.append((position, position + len(line)))
        position += len(line) + 1
    snapshot = EvidenceUnitSnapshot.create(span, units, "explicit_units_v5")
    return SentUnitCatalog([snapshot]), snapshot


def _value(catalog: SentUnitCatalog, unit_ids: list[str], kind: str = "source"):
    model = generation_output_model(QUESTION, unit_catalog=catalog)
    answer: dict[str, Any] = {"kind": kind, "basis": {"unit_ids": unit_ids}}
    if kind == "answer":
        answer.update(reasoning="表の行の点数。", result="4")
    return model.model_validate(
        {
            "answer_parts": [{"request_id": "Q1", "requirement": "行A4の点数", "answer": answer}],
            "question_type": ["fact"],
            "summary_part_indexes": [0],
            "confidence": "high",
        }
    )


SPAN = {
    "label": "E1",
    "evidence_id": "table-a",
    "source_id": "s-a",
    "source": "synthetic.md",
    "text": TABLE,
    TABLE_HEADER_LINES_KEY: [HEADER],
}


def test_header_and_data_rows_stay_direct():
    catalog, snapshot = _catalog(SPAN)
    value = _value(catalog, [snapshot.unit_ids[0], snapshot.unit_ids[4]])
    assert header_only_source_parts(value, catalog) == []
    draft = canonical_generation_draft(value, unit_catalog=catalog)
    assert draft.answer_parts[0].result_kind == "direct"
    assert draft.summary == HEADER + "\n" + ROW_A4


def test_header_only_source_is_withheld_in_the_canonical_draft():
    catalog, snapshot = _catalog(SPAN)
    value = _value(catalog, [snapshot.unit_ids[0]])
    assert header_only_source_parts(value, catalog) == ["Q1"]
    draft = canonical_generation_draft(value, unit_catalog=catalog)
    assert draft.items == [] and draft.summary == ""
    assert [(part.result_kind, part.item_indexes, part.application_indexes) for part in draft.answer_parts] == [("derived", [], [])]


def test_derivation_over_the_header_row_is_left_to_the_derivation_review():
    """導出（answer 型）の basis は対象外（公開は導出の審査が決める）。"""
    catalog, snapshot = _catalog(SPAN)
    assert header_only_source_parts(_value(catalog, [snapshot.unit_ids[0]], kind="answer"), catalog) == []


def test_unrecorded_span_has_no_header_rows():
    span = {key: value for key, value in SPAN.items() if key != TABLE_HEADER_LINES_KEY}
    catalog, snapshot = _catalog(span)
    assert header_only_source_parts(_value(catalog, [snapshot.unit_ids[0]]), catalog) == []


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (["行ID", "条件", "点数"], HEADER),
        (["行ID", "条件"], None),  # 記録と行のセルが一致しない
        (["行ID", "条件", "点数_2"], None),
    ],
    ids=["recorded", "column_count_mismatch", "renamed_key"],
)
def test_recorded_header_line_requires_the_recorded_column_keys(keys, expected):
    record = _record("table-a", TABLE, {"table_row_tree_column_keys": json.dumps(keys, ensure_ascii=False)})
    assert recorded_header_line(record) == expected


def test_evidence_spans_carry_only_recorded_header_rows():
    with_header = evidence_spans(QUESTION, [_record("table-a", TABLE, TABLE_METADATA)])
    assert [span.get(TABLE_HEADER_LINES_KEY) for span in with_header] == [[HEADER]]
    without = evidence_spans(QUESTION, [_record("table-a", TABLE)])
    assert [TABLE_HEADER_LINES_KEY in span for span in without] == [False]
