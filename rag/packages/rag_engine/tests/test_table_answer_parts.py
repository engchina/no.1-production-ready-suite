"""表を求めた質問の答えは列名と行で受け、Markdown の表はコードが組み立てる（#4796）。

モデルに表の記法を書かせると、区切りの行の無い行や 1 段落に詰めた行になり、画面で表にならない。LLM は決定論の stub。
内容はすべて合成。

表は冒頭の回答に 1 回だけ、右端の出典の列（行ごとの出典行の番号）と表の下の出典行つきで出し、「資料をもとに導いた
結論」には題と上の表の案内だけを出す（#4812）。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from test_answer_flow_single_generation import _ids, _run, _source
from test_grounded_answer import context, record

from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answer_table import table_markdown
from rag_engine.generation.applications import CheckedApplication, render_lines, table_block_lines
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
    # 表は本文の組み立てが要約の直後に出す（#4812）。要約の文には表も題も入れない。
    assert draft.summary == ""


def test_markdown_escapes_cell_separators_and_fills_short_rows() -> None:
    table = AnswerTable.model_validate(
        {"columns": ["項目", "値", "備考"], "rows": [{"cells": ["A|B", "1行目\n2行目"], "item_indexes": [0]}]}
    )
    assert table_markdown(table).splitlines() == ["| 項目 | 値 | 備考 |", "| --- | --- | --- |", "| A\\|B | 1行目 2行目 |  |"]


def test_markdown_adds_the_source_column_with_marks_per_row() -> None:
    table = AnswerTable.model_validate(
        {"columns": ["項目", "値"], "rows": [{"cells": ["A", "1"], "item_indexes": [0]}, {"cells": ["B", "2"], "item_indexes": []}]}
    )
    assert table_markdown(table, source_column="出典", sources=["[1][2]"]).splitlines() == [
        "| 項目 | 値 | 出典 |",
        "| --- | --- | --- |",
        "| A | 1 | [1][2] |",
        "| B | 2 |  |",
    ]


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


def _entry(item: Any, index: int, page: int) -> Any:
    return type("Entry", (), {"span": {"evidence_id": "source-r", "page": page}, "quote_only": False, "item": item, "draft_index": index})()


def test_conclusion_lines_show_only_the_title_and_the_note_about_the_table_above() -> None:
    _, _, snapshot = catalog_case()
    draft = draft_of(table_part(two_rows(snapshot)))
    checked = CheckedApplication(draft.applications[0], entries=[_entry(draft.items[0], 0, 1)])
    cited: list[Any] = []
    lines = render_lines([checked], lambda span: f"根拠：synthetic.txt p.{span['page']}", cited.append)
    # 表と出典行は冒頭の回答に 1 回だけ出す。結論の節には表も出典行も重ねない。
    assert lines == ["・合成の処理 A の再試行の扱い", answer_labels().table_above_note]
    assert cited == []


def test_table_block_numbers_the_citation_lines_and_marks_each_row() -> None:
    application = GroundedApplication.model_validate(
        {
            "item_indexes": [0, 1, 2],
            "result": "合成の題",
            "table": {
                "columns": ["項目", "値"],
                "rows": [
                    {"cells": ["行 1", "値 1"], "item_indexes": [0]},
                    {"cells": ["行 2", "値 2"], "item_indexes": [1]},
                    {"cells": ["行 3", "値 3"], "item_indexes": [2, 0]},
                ],
            },
        }
    )
    entries = [_entry(None, index, page) for index, page in enumerate((3, 7, 9))]
    cited: list[list[Any]] = []
    lines = table_block_lines(
        CheckedApplication(application, entries=entries), lambda span: f"根拠：synthetic.txt p.{span['page']}", cited.append
    )
    column = answer_labels().table_source_column
    assert lines == [
        "合成の題",
        f"| 項目 | 値 | {column} |",
        "| --- | --- | --- |",
        "| 行 1 | 値 1 | [1] |",
        "| 行 2 | 値 2 | [2] |",
        "| 行 3 | 値 3 | [1][3] |",
        "根拠：synthetic.txt p.3",
        "根拠：synthetic.txt p.7",
        "根拠：synthetic.txt p.9",
    ]
    # 番号の n は表の下の n 番目の出典行。出典行ごとに、その行の規則を出典行の順に渡す。
    assert [[entry.draft_index for entry in items] for items in cited] == [[0], [1], [2]]


def test_rows_with_the_same_citation_line_share_one_number() -> None:
    _, _, snapshot = catalog_case()
    draft = draft_of(table_part(two_rows(snapshot)))
    application = draft.applications[0]
    entries = [_entry(draft.items[index], index, 1) for index in application.item_indexes]
    cited: list[list[Any]] = []
    lines = table_block_lines(CheckedApplication(application, entries=entries), lambda _span: "根拠：synthetic.txt p.1", cited.append)
    assert lines[3:] == [
        "| 失敗の判定 | 応答が 30 秒ない要求を失敗とする | [1] |",
        "| 再試行の回数 | 最大 5 回 | [1] |",
        "根拠：synthetic.txt p.1",
    ]
    assert [len(items) for items in cited] == [2]


def _table_parts(units: dict[str, str]) -> dict[str, Any]:
    first, second = units[FIRST], units[SECOND]
    part = table_part([(["失敗の判定", "応答が 30 秒ない要求を失敗とする"], [first]), (["再試行の回数", "最大 5 回"], [second])])
    part["request_id"] = _ids(QUESTION)[0]
    return {"question_type": ["fact"], "answer_parts": [part], "summary_part_indexes": [0], "confidence": "high"}


def test_answer_text_shows_the_table_once_with_the_source_column_and_citation_lines_below() -> None:
    ctx = context(
        record(0, FIRST, "合成の文書", page=1, source="synthetic.txt"), record(1, SECOND, "合成の文書", page=2, source="synthetic.txt")
    )
    response, calls = _run(QUESTION, ctx, _table_parts)
    assert calls == ["GroundedDraftWire"]
    text = response.answer_text
    labels = answer_labels()
    column = labels.table_source_column
    # 冒頭の回答の段落: 題・出典の列つきの表・行ごとの出典行（表の行の順に番号を振る）。
    head = text.split("\n\n", 1)[0].splitlines()
    assert head == [
        "合成の処理 A の再試行の扱い",
        f"| 項目 | 扱い | {column} |",
        "| --- | --- | --- |",
        "| 失敗の判定 | 応答が 30 秒ない要求を失敗とする | [1] |",
        "| 再試行の回数 | 最大 5 回 | [2] |",
        f"{labels.citation_prefix}synthetic.txt p.1",
        f"{labels.citation_prefix}synthetic.txt p.2",
    ]
    # 表は 1 回だけ。「資料をもとに導いた結論」には題と上の表の案内だけを出す。
    assert sum(line.startswith("| ---") for line in text.splitlines()) == 1
    applications = text.split(labels.applications_section, 1)[1].strip().split("\n\n", 1)[0]
    assert applications.splitlines() == ["・合成の処理 A の再試行の扱い", labels.table_above_note]
    # 本文の出典行の n 番目が citation_lines の n 番目（画面はこの対応で印から根拠を開く）。表の下の出典行が先頭。
    lines = response.generation_trace["citation_lines"]
    assert text.count(labels.citation_prefix) == len(lines)
    assert [row["page"] for row in lines[:2]] == [1, 2]


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
