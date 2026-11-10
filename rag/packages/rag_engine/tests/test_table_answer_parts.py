"""表を求めた質問の答えは列名と行で受け、Markdown の表はコードが組み立てる（#4796）。

モデルに表の記法を書かせると、区切りの行の無い行や 1 段落に詰めた行になり、画面で表にならない。LLM は決定論の stub。
内容はすべて合成。
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import ValidationError
from test_answer_flow_single_generation import _ids, _run, _source
from test_grounded_answer import context, record

from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answer_table import table_markdown
from rag_engine.generation.applications import CheckedApplication, render_lines
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft
from rag_engine.generation.grounded import generation_output_model
from rag_engine.models.llm import AnswerTable, GroundedApplication

FIRST = "合成の処理 A は応答が 30 秒ない要求を失敗とする。"
SECOND = "失敗した要求は最大 5 回まで再試行する。"
TEXT = f"{FIRST}\n{SECOND}"
QUESTION = "合成の処理 A の再試行の扱いを表で出して。"
TABLE_LINES = [
    "| 項目 | 扱い |",
    "| --- | --- |",
    "| 失敗の判定 | 応答が 30 秒ない要求を失敗とする |",
    "| 再試行の回数 | 最大 5 回 |",
]


def catalog_case() -> tuple[Any, SentUnitCatalog, EvidenceUnitSnapshot]:
    span = {"label": "E1", "evidence_id": "source-r", "source_id": "s-r", "source": "synthetic.txt", "text": TEXT}
    snapshot = EvidenceUnitSnapshot.create(span, [(0, len(FIRST)), (len(FIRST) + 1, len(TEXT))], "explicit_units_v5")
    catalog = SentUnitCatalog([snapshot])
    return generation_output_model(QUESTION, unit_catalog=catalog), catalog, snapshot


def table_part(rows: list[tuple[list[str], list[str]]], columns: tuple[str, ...] = ("項目", "扱い")) -> dict[str, Any]:
    return {
        "request_id": "Q1",
        "requirement": "再試行の扱いを表で示す",
        "answer": {
            "kind": "table",
            "title": "合成の処理 A の再試行の扱い",
            "columns": list(columns),
            "rows": [{"cells": cells, "basis": {"unit_ids": unit_ids}} for cells, unit_ids in rows],
        },
    }


def draft_of(part: dict[str, Any]) -> Any:
    model, catalog, _ = catalog_case()
    raw = {"answer_parts": [part], "summary_part_indexes": [0], "confidence": "medium"}
    return canonical_generation_draft(model.model_validate(raw), unit_catalog=catalog)


def two_rows(snapshot: EvidenceUnitSnapshot) -> list[tuple[list[str], list[str]]]:
    first, second = snapshot.unit_ids
    return [(["失敗の判定", "応答が 30 秒ない要求を失敗とする"], [first]), (["再試行の回数", "最大 5 回"], [second])]


def test_the_generation_schema_offers_a_table_with_columns_and_a_basis_per_row() -> None:
    model, _, _ = catalog_case()
    schema = model.model_json_schema()
    table = schema["$defs"]["TableAnswerPart"]
    assert set(table["required"]) == {"kind", "title", "columns", "rows"}
    row = schema["$defs"][table["properties"]["rows"]["items"]["$ref"].split("/")[-1]]
    assert set(row["required"]) == {"cells", "basis"}
    # 表の記法（区切りの行・| の位置）はモデルに書かせない。
    assert "Markdown" not in str(table)


def test_table_part_becomes_a_derived_conclusion_whose_markdown_is_built_by_code() -> None:
    _, _, snapshot = catalog_case()
    draft = draft_of(table_part(two_rows(snapshot)))
    part = draft.answer_parts[0]
    assert part.result_kind == "derived" and part.application_indexes == [0]
    application = draft.applications[0]
    assert application.result == "合成の処理 A の再試行の扱い"
    assert application.reasoning == ""
    assert application.table is not None
    assert application.table.columns == ["項目", "扱い"]
    # 根拠は行ごと: 行の item は、その行が選んだ原文の単位だけ。
    rows = application.table.rows
    assert [[draft.items[index].text for index in row.item_indexes] for row in rows] == [[FIRST], [SECOND]]
    assert application.item_indexes == [rows[0].item_indexes[0], rows[1].item_indexes[0]]
    # 冒頭の要約は題と、コードが組み立てた GFM の表（行ごとに改行し、区切りの行を持つ）。
    assert draft.summary.splitlines() == ["合成の処理 A の再試行の扱い", *TABLE_LINES]


def test_markdown_escapes_cell_separators_and_fills_short_rows() -> None:
    table = AnswerTable.model_validate(
        {"columns": ["項目", "値", "備考"], "rows": [{"cells": ["A|B", "1行目\n2行目"], "item_indexes": [0]}]}
    )
    assert table_markdown(table).splitlines() == ["| 項目 | 値 | 備考 |", "| --- | --- | --- |", "| A\\|B | 1行目 2行目 |  |"]


def test_row_with_more_cells_than_columns_is_rejected_with_how_to_fix() -> None:
    model, _, snapshot = catalog_case()
    part = table_part([(["失敗の判定", "30 秒", "余分"], [snapshot.unit_ids[0]])])
    with pytest.raises(ValidationError, match="1 行目の cells が 3 個で、columns の 2 個より多い"):
        model.model_validate({"answer_parts": [part], "summary_part_indexes": [0], "confidence": "medium"})


def test_row_basis_must_be_a_sent_unit() -> None:
    _, _, snapshot = catalog_case()
    part = table_part([(["失敗の判定", "30 秒"], ["U" + "0" * 24])])
    with pytest.raises(ValidationError):
        draft_of(part)


def test_only_a_table_conclusion_may_omit_the_reasoning() -> None:
    with pytest.raises(ValidationError, match="reasoning"):
        GroundedApplication.model_validate({"item_indexes": [0], "result": "結論"})


def test_conclusion_lines_put_the_table_after_the_title_and_before_the_citation() -> None:
    _, _, snapshot = catalog_case()
    draft = draft_of(table_part(two_rows(snapshot)))
    entry = type("Entry", (), {"span": {"evidence_id": "source-r"}, "quote_only": False, "item": draft.items[0]})()
    checked = CheckedApplication(draft.applications[0], entries=[entry])
    lines = render_lines([checked], lambda _span: "根拠：synthetic.txt p.1", lambda _items: None)
    assert lines == ["・合成の処理 A の再試行の扱い", *TABLE_LINES, "根拠：synthetic.txt p.1"]


def _table_parts(units: dict[str, str]) -> dict[str, Any]:
    first, second = units[FIRST], units[SECOND]
    part = table_part([(["失敗の判定", "応答が 30 秒ない要求を失敗とする"], [first]), (["再試行の回数", "最大 5 回"], [second])])
    part["request_id"] = _ids(QUESTION)[0]
    return {"question_type": ["fact"], "answer_parts": [part], "summary_part_indexes": [0], "confidence": "high"}


def test_answer_text_shows_the_table_in_the_summary_and_the_conclusion() -> None:
    ctx = context(record(0, FIRST, "合成の文書", source="synthetic.txt"), record(1, SECOND, "合成の文書", source="synthetic.txt"))
    response, calls = _run(QUESTION, ctx, _table_parts)
    assert calls == ["GroundedDraftWire"]
    text = response.answer_text
    lines = text.splitlines()
    # 冒頭の要約と「資料をもとに導いた結論」の両方に、行ごとに改行した表が入る（1 段落に詰めない）。
    start = lines.index(TABLE_LINES[0])
    assert lines[start : start + len(TABLE_LINES)] == TABLE_LINES
    applications = text.split(answer_labels().applications_section, 1)[1]
    assert "\n".join(["・合成の処理 A の再試行の扱い", *TABLE_LINES]) in applications
    assert re.search(r"\| 再試行の回数 \| 最大 5 回 \|\n" + re.escape(answer_labels().citation_prefix), applications)


def test_a_question_without_a_table_request_keeps_the_answer_shape() -> None:
    question = "合成の処理 A の再試行の回数は？"
    response, _ = _run(
        question,
        context(record(0, SECOND, "合成の文書", source="synthetic.txt")),
        lambda units: {
            "answer_parts": [_source(_ids(question)[0], "再試行の回数", units[SECOND])],
            "summary_part_indexes": [0],
            "confidence": "high",
        },
    )
    assert "| --- |" not in response.answer_text
    assert SECOND in response.answer_text
