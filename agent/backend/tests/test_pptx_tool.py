"""PowerPoint のファイルを作るツール `pptx_create` と、組み込みのスキル（#5837）。

- 参照した表（その Run の NL2SQL の結果）の値は、モデルの引数に値が無くても、保存した結果のまま
  表とグラフに入る。無い参照・範囲外・型に合わない値・種類に合わない項目・1 枚に収まらない内容は
  引数の誤りで、ファイルを作らない。
- 結果・step・イベント・モデルへの入力に、ファイルの中身（バイト列・base64）が載らない。
- 1 つの Run で作れるファイルの数の上限は、Excel・Word のファイルと合わせて数える。
- スキルを選んだ業務 Agent の Run にだけツールが渡り、既定では承認待ちにならない。

モデルは SDK の `ScriptedModel`、NL2SQL は契約どおりの fake の MCP（`mcp_support`）、保存先は
memory。値は合成。
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import anyio
import pytest
from agents import FunctionTool
from agents.testing import ScriptedModel, assistant_message, function_call
from mcp_support import DEFAULT_OUTPUTS, fake_product_mcp
from pptx import Presentation
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.text import PP_ALIGN
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime, pptx_tool
from app.features.agent.artifact_file_store import FILE_ARTIFACT_KIND, FILE_RUN_CLOSED
from app.features.agent.builtin_runtime import UNDECLARED_CALL_NOT_DECLARED, ModelTarget
from app.features.agent.chart_spec import MAX_CHART_CATEGORIES, MAX_PIE_CATEGORIES
from app.features.agent.config import runtime_config_store
from app.features.agent.docx_tool import DOCX_CREATE_TOOL
from app.features.agent.mcp_server import FILES_DOWNLOAD_MESSAGE, run_output
from app.features.agent.pptx_presentation import (
    BULLET_ITEM_MAX_CHARS,
    COMPARISON_ITEM_MAX_CHARS,
    CONTINUED_SUFFIX,
    MAX_BULLET_ITEMS,
    MAX_TABLE_ROWS,
    MAX_TABLE_ROWS_PER_SLIDE,
    PPTX_MIME_TYPE,
    TITLE_MAX_CHARS,
)
from app.features.agent.pptx_tool import (
    FILE_LIMIT_CODE,
    MAX_FILES_PER_RUN,
    PPTX_CREATE_INPUT_SCHEMA,
    PPTX_CREATE_TOOL,
)
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    RunState,
    RunStatus,
    runtime_repository,
)
from app.features.agent.skills import (
    PPTX_CREATE_SKILL_ID,
    STRUCTURED_DATA_QUERY_SKILL_ID,
    skill_registry,
)
from app.features.agent.tool_arguments import INVALID_ARGUMENTS_CODE
from app.features.agent.tool_policy_catalog import CONTROL_PLANE_SOURCE, tool_policy_catalog
from app.features.agent.tools import (
    ToolCall,
    ToolInvocationContext,
    ToolPolicy,
    ToolPolicyDecision,
    ToolResult,
    tool_registry,
)
from app.features.agent.xlsx_tool import XLSX_CREATE_TOOL

USER_UUID = "11111111-2222-3333-4444-555555555837"
# 合成の NL2SQL の結果（数値・小数・日時・NULL・`=` で始まる文字）。
COLUMNS = ["LABEL", "AMOUNT", "RATE", "CREATED_AT", "CODE"]
ROWS: list[dict[str, Any]] = [
    {
        "LABEL": "record-1",
        "AMOUNT": 1200,
        "RATE": 0.25,
        "CREATED_AT": "2026-01-05T00:00:00",
        "CODE": "0001",
    },
    {
        "LABEL": "=1+1",
        "AMOUNT": 30,
        "RATE": 0.5,
        "CREATED_AT": "2026-02-10T18:30:00",
        "CODE": "0002",
    },
    {"LABEL": "record-3", "AMOUNT": None, "RATE": None, "CREATED_AT": None, "CODE": None},
]
NL2SQL_OUTPUT: dict[str, Any] = {"status": "done", "columns": COLUMNS, "rows": ROWS}


def _column(
    column_type: str, *, header: str | None = None, format: str | None = None
) -> dict[str, Any]:
    return {"header": header, "type": column_type, "format": format}


def _source(table: str = "T1", rows: str = "*", columns: str = "*") -> dict[str, str]:
    return {"table": table, "rows": rows, "columns": columns}


def _table(**changes: Any) -> dict[str, Any]:
    """モデルに見せる schema（strict）どおり、すべての項目を持つ表。"""
    base: dict[str, Any] = {
        "columns": [_column("text", header="名前")],
        "source": None,
        "rows": [],
    }
    return {**base, **changes}


def _slide(slide_type: str, title: str = "合成の題", **changes: Any) -> dict[str, Any]:
    """モデルに見せる schema（strict）どおり、すべての項目を持つスライド。"""
    base: dict[str, Any] = {
        "type": slide_type,
        "title": title,
        "subtitle": None,
        "items": [],
        "left": None,
        "right": None,
        "table": None,
        "chart": None,
        "notes": None,
    }
    return {**base, **changes}


def _item(text: str = "項目", *children: str) -> dict[str, Any]:
    return {"text": text, "children": list(children)}


def _side(*items: dict[str, Any], heading: str | None = None) -> dict[str, Any]:
    return {"heading": heading, "items": list(items)}


def _chart(chart_type: str, category: int, *series: int) -> dict[str, Any]:
    return {"type": chart_type, "category_column": category, "series_columns": list(series)}


def _table_slide(title: str = "合成の表", **changes: Any) -> dict[str, Any]:
    return _slide("table", title, table=_table(**changes))


def _chart_slide(chart: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return _slide("chart", "合成のグラフ", table=_table(**changes), chart=chart)


BULLETS = _slide("bullets", "合成の要点", items=[_item("項目 A", "項目 A-1"), _item("項目 B")])


def _arguments(*slides: dict[str, Any], title: str = "合成の資料") -> dict[str, Any]:
    return {"title": title, "slides": list(slides)}


def _invoke(run_id: str | None, *slides: dict[str, Any], title: str = "合成の資料") -> ToolResult:
    return tool_registry.invoke(
        ToolCall(name=PPTX_CREATE_TOOL, arguments=_arguments(*slides, title=title)),
        context=ToolInvocationContext(run_id=run_id),
    )


def _files(run_id: str) -> list[Any]:
    return [item for item in runtime_repository.get_run(run_id).artifacts if item.kind == "file"]


def _presentation(run_id: str, artifact_id: str) -> tuple[Any, bytes]:
    data = runtime_repository.read_artifact_file(run_id, artifact_id).data
    return Presentation(io.BytesIO(data)), data


def _slide_table(slide: Any) -> Any:
    [frame] = [shape for shape in slide.shapes if shape.has_table]
    return frame.table


def _slide_chart(slide: Any) -> Any:
    [frame] = [shape for shape in slide.shapes if shape.has_chart]
    return frame.chart


def _cell_texts(table: Any) -> list[list[str]]:
    return [[cell.text for cell in row.cells] for row in table.rows]


def _titles(presentation: Any) -> list[str]:
    return [str(slide.shapes.title.text_frame.text) for slide in presentation.slides]


def _argument_errors(result: ToolResult) -> list[tuple[str, str]]:
    assert (result.success, result.error_code) == (False, INVALID_ARGUMENTS_CODE), result
    return [(item["argument"], item["code"]) for item in result.error_details["argument_errors"]]


def _forget_run(run_id: str) -> None:
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - テストの後始末
        repository._runs.pop(run_id, None)  # noqa: SLF001
        repository._artifact_files = {  # noqa: SLF001
            key: data
            for key, data in repository._artifact_files.items()  # noqa: SLF001
            if key[0] != run_id
        }


def _add_result(run_id: str, columns: list[str], rows: list[dict[str, Any]]) -> None:
    """Run に、NL2SQL の結果を 1 つ足す（次の番号の表の参照として登録される）。"""
    step_id, _ = runtime_repository.start_builtin_tool_step(
        run_id, ToolCall(name="nl2sql__nl2sql_query", arguments={})
    )
    runtime_repository.finish_builtin_tool_step(
        run_id,
        step_id,
        ToolResult(
            name="nl2sql__nl2sql_query",
            success=True,
            output={"status": "done", "columns": columns, "rows": rows},
        ),
    )


@pytest.fixture
def run_id() -> Iterator[str]:
    """NL2SQL の結果を 1 つ持つ、実行中の Run（結果は表の参照 `T1` として登録される）。"""
    run = RunState(
        id=f"run5837p-{uuid4().hex[:8]}",
        goal="合成の質問",
        agent_id="default",
        runtime_id="builtin",
        status=RunStatus.RUNNING,
    )
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - 実行中の Run を直接置く
        repository._runs[run.id] = run  # noqa: SLF001
    _add_result(run.id, COLUMNS, ROWS)
    try:
        yield run.id
    finally:
        _forget_run(run.id)


# ---- 表の参照（値はモデルから受け取らない） -------------------------------------------------


def test_referenced_values_are_copied_from_the_stored_result(run_id: str) -> None:
    arguments = _table_slide(
        # 見出しが null の列は、参照した表の列名になる。
        columns=[
            _column("number", header="金額（円）", format="integer"),
            _column("text"),
            _column("date"),
            _column("number", format="percent1"),
            _column("text", header="番号"),
        ],
        # 行・列は書いた順に並ぶ（回答の表の参照と同じ指定）。
        source=_source(rows="2,1,3", columns="2,1,4,3,5"),
    )
    # モデルの引数に、表の値は 1 つも無い。
    assert all(str(value) not in json.dumps(arguments) for value in (1200, 0.25, "record-1"))

    result = _invoke(run_id, _slide("cover", "合成の報告"), arguments, title="合成の報告")

    assert result.success, result.error
    assert result.policy_decision == ToolPolicyDecision.ALLOW and not result.approval_required
    [artifact] = _files(run_id)
    presentation, data = _presentation(run_id, artifact.id)
    table = _slide_table(presentation.slides[1])
    texts = _cell_texts(table)
    assert texts[0] == ["金額（円）", "LABEL", "CREATED_AT", "RATE", "番号"]
    # 値は保存した結果のまま（数値・日付は書式を当てた文字、文字はそのまま）。
    assert texts[1:] == [
        ["30", "=1+1", "2026-02-10 18:30", "50.0%", "0002"],
        ["1,200", "record-1", "2026-01-05 00:00", "25.0%", "0001"],
        ["", "record-3", "", "", ""],
    ]
    stored = [ROWS[position] for position in (1, 0, 2)]
    assert [row[1] for row in texts[1:]] == [row["LABEL"] for row in stored]
    assert [row[4] for row in texts[1:]] == [row["CODE"] or "" for row in stored]
    # 数値・日付の列は右寄せ。
    right, left = PP_ALIGN.RIGHT, PP_ALIGN.LEFT
    assert [cell.text_frame.paragraphs[0].alignment for cell in table.rows[1].cells] == [
        right,
        left,
        right,
        right,
        left,
    ]
    # 結果は成果物の参照と要約だけ（中身を載せない）。
    assert result.output == {
        "file": {
            "artifact_id": artifact.id,
            "file_name": "合成の報告.pptx",
            "mime_type": PPTX_MIME_TYPE,
            "size_bytes": len(data),
            "sha256": artifact.content["sha256"],
        },
        "slides": {
            "cover": 1,
            "section": 0,
            "bullets": 0,
            "table": 1,
            "comparison": 0,
            "chart": 0,
        },
        "slide_count": 2,
        "continued_slides": 0,
        "tables": [{"rows": 3, "columns": 5, "slides": 1}],
        "charts": [],
    }
    encoded = base64.b64encode(data).decode("ascii")
    recorded = result.model_dump_json() + runtime_repository.get_run(run_id).model_dump_json()
    assert encoded[:64] not in recorded


def test_chart_values_are_copied_from_the_stored_result(run_id: str) -> None:
    """グラフの値は、参照した表から取る（モデルはどの列を使うかだけを書く）。"""
    arguments = _chart_slide(
        _chart("column", 1, 2, 3),
        columns=[_column("text"), _column("number", format="integer"), _column("number")],
        source=_source(rows="1-2", columns="1,2,3"),
    )
    assert all(str(value) not in json.dumps(arguments) for value in (1200, 0.25, "record-1"))

    result = _invoke(run_id, arguments)

    assert result.success, result.error
    assert result.output is not None
    assert result.output["charts"] == [{"type": "column", "series": 2, "categories": 2}]
    assert (result.output["slides"]["chart"], result.output["tables"]) == (1, [])
    presentation, _ = _presentation(run_id, _files(run_id)[0].id)
    chart = _slide_chart(presentation.slides[0])
    # PowerPoint のグラフとして入り、項目と系列の値は NL2SQL の結果と一致する。
    assert chart.chart_type == XL_CHART_TYPE.COLUMN_CLUSTERED
    [plot] = chart.plots
    assert list(plot.categories) == [row["LABEL"] for row in ROWS[:2]]
    assert [(series.name, list(series.values)) for series in plot.series] == [
        ("AMOUNT", [float(row["AMOUNT"]) for row in ROWS[:2]]),
        ("RATE", [float(row["RATE"]) for row in ROWS[:2]]),
    ]
    # グラフの表は、スライドには出ない。
    assert not [shape for shape in presentation.slides[0].shapes if shape.has_table]


def test_all_slide_types_both_table_sources_and_notes_work_together(run_id: str) -> None:
    result = _invoke(
        run_id,
        _slide("cover", "合成の報告", subtitle="2026-11-26　部門 1"),
        _slide("section", "1. 概要", notes="章の扉のノート。"),
        BULLETS,
        _slide(
            "comparison",
            "案 A と案 B",
            left=_side(_item("早い", "3 か月"), heading="案 A"),
            right=_side(_item("安い")),
        ),
        _table_slide(
            columns=[_column("text"), _column("number")], source=_source(rows="1", columns="1-2")
        ),
        _table_slide(
            "書いた表",
            columns=[_column("text", header="項目"), _column("number", header="値")],
            rows=[["a", 1], ["b", 2.5], ["c", None]],
        ),
        _chart_slide(
            _chart("pie", 1, 2),
            columns=[_column("text", header="項目"), _column("number", header="値")],
            rows=[["a", 1], ["b", 3]],
        ),
        title="合成の報告",
    )

    assert result.success, result.error
    assert result.output is not None
    assert result.output["slides"] == {
        "cover": 1,
        "section": 1,
        "bullets": 1,
        "table": 2,
        "comparison": 1,
        "chart": 1,
    }
    assert (result.output["slide_count"], result.output["continued_slides"]) == (7, 0)
    assert result.output["tables"] == [
        {"rows": 1, "columns": 2, "slides": 1},
        {"rows": 3, "columns": 2, "slides": 1},
    ]
    assert result.output["charts"] == [{"type": "pie", "series": 1, "categories": 2}]
    presentation, _ = _presentation(run_id, _files(run_id)[0].id)
    assert _titles(presentation) == [
        "合成の報告",
        "1. 概要",
        "合成の要点",
        "案 A と案 B",
        "合成の表",
        "書いた表",
        "合成のグラフ",
    ]
    slides = list(presentation.slides)
    assert [
        (paragraph.level, paragraph.text)
        for paragraph in slides[2].placeholders[1].text_frame.paragraphs
    ] == [(0, "項目 A"), (1, "項目 A-1"), (0, "項目 B")]
    assert [
        [paragraph.text for paragraph in slides[3].placeholders[index].text_frame.paragraphs]
        for index in (1, 2)
    ] == [["案 A", "早い", "3 か月"], ["安い"]]
    assert _cell_texts(_slide_table(slides[4])) == [["LABEL", "AMOUNT"], ["record-1", "1200"]]
    assert _cell_texts(_slide_table(slides[5])) == [
        ["項目", "値"],
        ["a", "1"],
        ["b", "2.5"],
        ["c", ""],
    ]
    assert list(_slide_chart(slides[6]).plots[0].series[0].values) == [1.0, 3.0]
    # 発表者ノートは、書いたスライドにだけ入る。
    assert [slide.has_notes_slide for slide in slides] == [False, True, *[False] * 5]
    assert slides[1].notes_slide.notes_text_frame.text == "章の扉のノート。"


def test_referenced_table_continues_on_the_next_slides(run_id: str) -> None:
    """参照した表が 1 枚の行の上限を超えるときは、次のスライドへ自動で続ける。"""
    count = MAX_TABLE_ROWS_PER_SLIDE * 2 + 3
    _add_result(
        run_id, ["LABEL", "AMOUNT"], [{"LABEL": f"r-{n}", "AMOUNT": n} for n in range(count)]
    )
    table = _table_slide(
        "合成の一覧",
        columns=[_column("text"), _column("number", format="integer")],
        source=_source(table="T2"),
    )

    result = _invoke(run_id, table, BULLETS)

    assert result.success, result.error
    assert result.output is not None
    assert (result.output["slide_count"], result.output["continued_slides"]) == (4, 2)
    assert result.output["tables"] == [{"rows": count, "columns": 2, "slides": 3}]
    presentation, _ = _presentation(run_id, _files(run_id)[0].id)
    assert _titles(presentation) == [
        "合成の一覧",
        "合成の一覧" + CONTINUED_SUFFIX,
        "合成の一覧" + CONTINUED_SUFFIX,
        "合成の要点",
    ]
    tables = [_cell_texts(_slide_table(slide)) for slide in list(presentation.slides)[:3]]
    # 見出しの行は毎回付き、値は参照した表の順のまますべて入る。
    assert all(rows[0] == ["LABEL", "AMOUNT"] for rows in tables)
    assert [row for rows in tables for row in rows[1:]] == [
        [f"r-{n}", str(n)] for n in range(count)
    ]


def test_referenced_table_over_the_total_row_limit_points_to_a_spreadsheet(run_id: str) -> None:
    """続きを含めた行の上限を超える表は切り捨てず、Excel のファイルにすることを促す誤りにする。"""
    rows = [{"LABEL": f"record-{index}"} for index in range(MAX_TABLE_ROWS + 1)]
    _add_result(run_id, ["LABEL"], rows)

    result = _invoke(run_id, _table_slide(columns=[_column("text")], source=_source(table="T2")))

    assert _argument_errors(result) == [("slides.0.table.rows", "too_many")]
    [error] = result.error_details["argument_errors"]
    assert "行を絞るか、Excel のファイルにしてください" in error["message"]
    assert _files(run_id) == []
    # 行を絞れば作れる。
    assert _invoke(
        run_id, _table_slide(columns=[_column("text")], source=_source(table="T2", rows="1-20"))
    ).success


WRITTEN_TABLE = {"columns": [_column("text", header="名前"), _column("number", header="値")]}


@pytest.mark.parametrize(
    ("slide", "expected"),
    [
        (
            _table_slide(source=_source(table="T9")),
            ("slides.0.table.source.table", "unknown_table"),
        ),
        (
            # cell の参照は表の参照ではない。
            _table_slide(source=_source(table="T1:R1:C1")),
            ("slides.0.table.source.table", "unknown_table"),
        ),
        (
            _table_slide(source=_source(rows="4", columns="1")),
            ("slides.0.table.source.rows", "selection"),
        ),
        (
            _table_slide(source=_source(rows="1", columns="6")),
            ("slides.0.table.source.columns", "selection"),
        ),
        (
            # 選んだ列の数と columns の数が違う。
            _table_slide(source=_source(columns="1,2")),
            ("slides.0.table.columns", "count"),
        ),
        (
            _table_slide(source=_source(columns="1"), rows=[["x"]]),
            ("slides.0.table.rows", "conflict"),
        ),
        (
            # 参照しない表は、列の見出しが要る。
            _table_slide(columns=[_column("text")], rows=[["x"]]),
            ("slides.0.table.columns.0.header", "required"),
        ),
        (
            # 参照した値が列の型に合わない（文字の列を数値にした）。
            _table_slide(columns=[_column("number")], source=_source(columns="1")),
            ("slides.0.table.columns.0.type", "not_number"),
        ),
        (
            _table_slide(columns=[_column("date")], source=_source(columns="2")),
            ("slides.0.table.columns.0.type", "not_date"),
        ),
        (
            _table_slide(columns=[_column("text", header="名前", format="integer")]),
            ("slides.0.table.columns.0.format", "format"),
        ),
        (
            # モデルが書く表は、1 枚ぶんの行まで（続きのスライドにしない）。
            _table_slide(rows=[["x"]] * (MAX_TABLE_ROWS_PER_SLIDE + 1)),
            ("slides.0.table.rows", "too_many"),
        ),
        # スライドの種類に合わない項目（要る項目が無い・使わない項目に値がある）。
        (_slide("bullets"), ("slides.0.items", "required")),
        (_slide("table"), ("slides.0.table", "required")),
        (_slide("comparison", left=_side(_item())), ("slides.0.right", "required")),
        (_slide("chart", table=_table()), ("slides.0.chart", "required")),
        (_slide("chart", chart=_chart("bar", 1, 2)), ("slides.0.table", "required")),
        (_slide("section", subtitle="副題"), ("slides.0.subtitle", "conflict")),
        (_slide("cover", items=[_item()]), ("slides.0.items", "conflict")),
        (_slide("bullets", items=[_item()], table=_table()), ("slides.0.table", "conflict")),
        (
            _slide("table", table=_table(), chart=_chart("bar", 1, 2)),
            ("slides.0.chart", "conflict"),
        ),
        (_slide("bullets", items=[_item()], left=_side(_item())), ("slides.0.left", "conflict")),
        # 1 枚に収まらない内容（文字を小さくせずに誤りにする）。
        (_slide("section", "題" * (TITLE_MAX_CHARS + 1)), ("slides.0.title", "too_long")),
        (
            _slide("bullets", items=[_item()] * (MAX_BULLET_ITEMS + 1)),
            ("slides.0.items", "too_many"),
        ),
        (
            _slide("bullets", items=[_item("文" * (BULLET_ITEM_MAX_CHARS + 1))]),
            ("slides.0.items.0.text", "too_long"),
        ),
        (
            _slide(
                "comparison",
                left=_side(_item("文" * (COMPARISON_ITEM_MAX_CHARS + 1))),
                right=_side(_item()),
            ),
            ("slides.0.left.items.0.text", "too_long"),
        ),
        (
            _slide("bullets", items=[_item("項目", " ")]),
            ("slides.0.items.0.children.0", "required"),
        ),
        # グラフ: 列の選び方・系列にできる列・上限・空の値。
        (
            _chart_slide(_chart("bar", 1, 2), **WRITTEN_TABLE, rows=[["a", "多い"]]),
            ("slides.0.table.columns.1.type", "not_number"),
        ),
        (
            _chart_slide(_chart("bar", 2, 1), **WRITTEN_TABLE, rows=[["a", 1]]),
            ("slides.0.chart.series_columns.0", "not_number"),
        ),
        (
            _chart_slide(_chart("line", 1, 3), **WRITTEN_TABLE, rows=[["a", 1]]),
            ("slides.0.chart.series_columns.0", "range"),
        ),
        (
            _chart_slide(_chart("pie", 1, 2), **WRITTEN_TABLE, rows=[["a", 1], ["b", None]]),
            ("slides.0.table.columns.1", "pie_values"),
        ),
        (
            _chart_slide(
                _chart("column", 1, 2),
                **WRITTEN_TABLE,
                rows=[[f"項目 {n}", n] for n in range(MAX_CHART_CATEGORIES + 1)],
            ),
            ("slides.0.table.rows", "too_many"),
        ),
        (
            _chart_slide(
                _chart("pie", 1, 2),
                **WRITTEN_TABLE,
                rows=[[f"項目 {n}", n + 1] for n in range(MAX_PIE_CATEGORIES + 1)],
            ),
            ("slides.0.table.rows", "too_many"),
        ),
        (
            # 参照した表の、値がすべて空の列は系列にできない。
            _chart_slide(
                _chart("line", 1, 2),
                columns=[_column("text"), _column("number")],
                source=_source(rows="3", columns="1,2"),
            ),
            ("slides.0.table.columns.1", "empty"),
        ),
    ],
    ids=[
        "unknown-table",
        "cell-reference",
        "row-out-of-range",
        "column-out-of-range",
        "column-count",
        "source-and-rows",
        "header-required",
        "text-as-number",
        "number-as-date",
        "format-for-type",
        "too-many-written-rows",
        "bullets-without-items",
        "table-without-table",
        "comparison-without-right",
        "chart-without-chart",
        "chart-without-table",
        "section-with-subtitle",
        "cover-with-items",
        "bullets-with-table",
        "table-with-chart",
        "bullets-with-left",
        "long-title",
        "too-many-items",
        "long-item",
        "long-comparison-item",
        "blank-child-item",
        "chart-not-a-number",
        "chart-text-series",
        "chart-series-out-of-range",
        "pie-empty-value",
        "chart-too-many-categories",
        "pie-too-many-categories",
        "chart-empty-series",
    ],
)
def test_bad_references_values_and_slide_fields_are_argument_errors(
    run_id: str, slide: dict[str, Any], expected: tuple[str, str]
) -> None:
    result = _invoke(run_id, slide)

    assert _argument_errors(result) == [expected]
    # 成功にせず、ファイルも作らない。誤りの文に結果の値を写さない。
    assert _files(run_id) == []
    text = json.dumps(result.error_details, ensure_ascii=False)
    assert all(str(row["LABEL"]) not in text for row in ROWS)
    assert "多い" not in text


def test_fit_errors_say_how_to_fix(run_id: str) -> None:
    result = _invoke(
        run_id,
        _slide("bullets", items=[_item()] * (MAX_BULLET_ITEMS + 1)),
        _table_slide(rows=[["x"]] * (MAX_TABLE_ROWS_PER_SLIDE + 1)),
    )

    messages = {
        item["argument"]: item["message"] for item in result.error_details["argument_errors"]
    }
    assert "source で参照" in messages["slides.1.table.rows"]
    # 生成の本体の誤り（1 枚に入る量）は、引数の誤りを直してから返る。
    result = _invoke(run_id, _slide("bullets", items=[_item()] * (MAX_BULLET_ITEMS + 1)))
    [error] = result.error_details["argument_errors"]
    assert f"{MAX_BULLET_ITEMS} 項目まで" in error["message"]
    assert "スライドを分けてください" in error["message"]


def test_schema_violations_are_rejected_before_the_handler(run_id: str) -> None:
    for arguments in (
        {**_arguments(BULLETS), "title": ""},
        _arguments(),
        {"title": "t"},
        _arguments({key: value for key, value in BULLETS.items() if key != "title"}),
        _arguments(_slide("quote")),
        _arguments(_slide("section", "")),
        _arguments({**_slide("section"), "layout": "dark"}),
        _arguments({**_slide("section"), "font_size": 40}),
        _arguments(_slide("bullets", items=[{**_item(), "bold": True}])),
        _arguments(_slide("bullets", items=[_item("項目", "")])),
        _arguments(_slide("comparison", left={"items": [_item()]}, right=_side(_item()))),
        _arguments(_table_slide(columns=[_column("money", header="a")])),
        _arguments(_table_slide(columns=[_column("number", header="a", format="￥")])),
        _arguments(_table_slide(rows=[[True]])),
        _arguments(_table_slide(source={"table": "T1"})),
        _arguments(_table_slide(image="a.png")),
        # グラフ: 4 つ以外の種類・値を書く欄・体裁の指定は、引数に無い。
        _arguments(_chart_slide(_chart("stacked", 1, 2))),
        _arguments(_chart_slide({**_chart("bar", 1, 2), "values": [1, 2]})),
        _arguments(_chart_slide({**_chart("bar", 1, 2), "colors": ["FF0000"]})),
        _arguments(_chart_slide(_chart("bar", 0, 2))),
        _arguments(_chart_slide({"type": "bar", "category_column": "名前", "series_columns": [2]})),
    ):
        result = tool_registry.invoke(
            ToolCall(name=PPTX_CREATE_TOOL, arguments=arguments),
            context=ToolInvocationContext(run_id=run_id),
        )
        assert (result.success, result.error_code) == (False, INVALID_ARGUMENTS_CODE), arguments
        assert result.audit_metadata["argument_validation"] == "failed"
    assert _files(run_id) == []


def test_input_schema_is_strict_for_the_model() -> None:
    """モデルには strict の schema で渡す（root の項目はすべて必須。追加の項目は不可）。"""
    assert builtin_runtime._strict_schema(PPTX_CREATE_INPUT_SCHEMA)  # noqa: SLF001

    def objects(node: Any) -> Iterator[dict[str, Any]]:
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for value in node.values():
                yield from objects(value)
        elif isinstance(node, list):
            for value in node:
                yield from objects(value)

    for node in objects(PPTX_CREATE_INPUT_SCHEMA):
        assert node["additionalProperties"] is False
        # 必須でない項目は、null か空の配列で「無い」を表せる。
        for name, schema in node["properties"].items():
            if name in node["required"]:
                continue
            types = schema.get("type")
            nullable = "null" in (types if isinstance(types, list) else [types]) or any(
                option == {"type": "null"} for option in schema.get("anyOf", [])
            )
            assert nullable or types == "array", name
    # 体裁（字体・色・字の大きさ・配置）を変える項目は無い。
    slide = PPTX_CREATE_INPUT_SCHEMA["properties"]["slides"]["items"]
    assert sorted(slide["properties"]) == [
        "chart",
        "items",
        "left",
        "notes",
        "right",
        "subtitle",
        "table",
        "title",
        "type",
    ]


def test_files_per_run_are_limited_together_with_other_formats(
    run_id: str, monkeypatch: MonkeyPatch
) -> None:
    assert MAX_FILES_PER_RUN == 10
    monkeypatch.setattr(pptx_tool, "MAX_FILES_PER_RUN", 3)
    # 形式を問わず、その Run のファイルの成果物の数で数える。
    sheet = {
        "name": "合成の表",
        "heading": None,
        "notes": [],
        "columns": [{"header": "名前", "type": "text", "format": None, "width": None}],
        "source": None,
        "rows": [["x"]],
        "summaries": [],
    }
    spreadsheet = tool_registry.invoke(
        ToolCall(name=XLSX_CREATE_TOOL, arguments={"title": "file-0", "sheets": [sheet]}),
        context=ToolInvocationContext(run_id=run_id),
    )
    paragraph: dict[str, Any] = {
        "type": "paragraph",
        "text": "合成の段落。",
        "level": None,
        "items": [],
        "table": None,
    }
    document = tool_registry.invoke(
        ToolCall(
            name=DOCX_CREATE_TOOL,
            arguments={"title": "file-1", "subtitle": None, "blocks": [paragraph]},
        ),
        context=ToolInvocationContext(run_id=run_id),
    )
    assert spreadsheet.success and document.success, (spreadsheet.error, document.error)

    results = [_invoke(run_id, BULLETS, title=f"file-{index}") for index in (2, 3)]

    assert [result.success for result in results] == [True, False]
    assert results[1].error_code == FILE_LIMIT_CODE
    assert [item.name for item in _files(run_id)] == ["file-0.xlsx", "file-1.docx", "file-2.pptx"]


def test_mcp_run_output_lists_the_file_without_its_content(run_id: str) -> None:
    """外部の MCP への Run の応答には、ファイルの名前・形式・大きさだけが載る（版は変えない）。"""
    assert _invoke(run_id, BULLETS, title="合成の報告").success
    [artifact] = _files(run_id)
    _, data = _presentation(run_id, artifact.id)

    output = run_output(runtime_repository.get_run(run_id))

    assert [item.model_dump() for item in output.files] == [
        {"file_name": "合成の報告.pptx", "mime_type": PPTX_MIME_TYPE, "size_bytes": len(data)}
    ]
    assert output.message.endswith(FILES_DOWNLOAD_MESSAGE)
    text = output.model_dump_json()
    assert base64.b64encode(data).decode("ascii")[:64] not in text and artifact.id not in text


def test_tool_fails_outside_a_run_and_after_the_run_ended(run_id: str) -> None:
    assert _invoke(None, BULLETS).error_code == FILE_RUN_CLOSED
    assert _invoke("run_missing", BULLETS).error_code == FILE_RUN_CLOSED
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - Run を終わった状態にする
        repository._runs[run_id].status = RunStatus.COMPLETED  # noqa: SLF001

    result = _invoke(run_id, BULLETS)

    assert (result.success, result.error_code) == (False, FILE_RUN_CLOSED)
    assert _files(run_id) == []


# ---- ツール権限（既定は承認なし。管理者が変えられる） ---------------------------------------


def test_default_policy_allows_and_the_admin_can_change_it(run_id: str) -> None:
    definition = tool_registry.get(PPTX_CREATE_TOOL)
    assert definition is not None and not definition.side_effects
    for mode in ("approval", "deny"):
        assert ToolPolicy(default_mode=mode).decide(definition) == ToolPolicyDecision.ALLOW
    # ツール権限の画面の一覧に、Control Plane のツールとして出る。
    [row] = [
        tool
        for tool in tool_policy_catalog(ToolInvocationContext()).tools
        if tool.name == PPTX_CREATE_TOOL
    ]
    assert (row.server_id, row.default_decision) == (CONTROL_PLANE_SOURCE, ToolPolicyDecision.ALLOW)
    call = ToolCall(name=PPTX_CREATE_TOOL, arguments=_arguments(BULLETS))
    context = ToolInvocationContext(run_id=run_id)

    asked = tool_registry.invoke(call, policy=ToolPolicy(ask={PPTX_CREATE_TOOL}), context=context)
    denied = tool_registry.invoke(
        call, policy=ToolPolicy(deny={PPTX_CREATE_TOOL}), context=context, force=True
    )

    assert (asked.success, asked.approval_required) == (False, True)
    assert (denied.success, denied.policy_decision) == (False, ToolPolicyDecision.DENY)
    assert _files(run_id) == []


# ---- スキルの宣言からだけ渡る（組み込み Runtime） -------------------------------------------


def _script(monkeypatch: MonkeyPatch, *steps: Any) -> ScriptedModel:
    model = ScriptedModel(list(steps))
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": ModelTarget(
            model_id=model_id or "test-model",
            endpoint="https://oci.example",
            project_ocid="",
            api_key="k",
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    return model


@pytest.fixture
def agent(monkeypatch: MonkeyPatch) -> Iterator[str]:
    """NL2SQL を fake にした、業務 Agent の ID（スキルは各テストが決める）。"""
    fake_product_mcp(
        monkeypatch,
        outputs={
            "nl2sql_query": {
                **DEFAULT_OUTPUTS["nl2sql_query"],
                "columns": COLUMNS,
                "rows": ROWS,
                "returned_count": len(ROWS),
                "total": len(ROWS),
            }
        },
    )
    agent_id = f"agent5837p-{uuid4().hex[:8]}"
    original = runtime_config_store.get_tool_policy()
    try:
        yield agent_id
    finally:
        runtime_config_store.patch_tool_policy(
            default_mode=original.default_mode,
            allow=sorted(original.allow),
            ask=sorted(original.ask),
            deny=sorted(original.deny),
        )
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            run_ids = [
                run_id
                for run_id, run in repository._runs.items()  # noqa: SLF001
                if run.agent_id == agent_id
            ]
        for run_id in run_ids:
            _forget_run(run_id)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(agent_id)


def _run(agent_id: str) -> RunState:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="合成の値を PowerPoint のファイルにして", agent_id=agent_id),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return runtime_repository.get_run(run.id)


DATA_COLUMNS = [_column("text"), _column("number", format="integer")]
PPTX_CALL = _arguments(
    _slide("cover", "合成の一覧"),
    _table_slide("合成の値の一覧", columns=DATA_COLUMNS, source=_source(columns="1,2")),
    _chart_slide(
        _chart("bar", 1, 2), columns=DATA_COLUMNS, source=_source(rows="1-2", columns="1,2")
    ),
    title="合成の一覧",
)


def test_builtin_skill_declares_only_the_pptx_tool() -> None:
    skill = skill_registry.get(PPTX_CREATE_SKILL_ID)
    assert skill is not None and skill.source == "builtin" and skill.name == "PowerPoint 作成"
    assert [(item.server_id, item.tool_names) for item in skill.mcp_requirements] == [
        ("control-plane", [PPTX_CREATE_TOOL])
    ]
    assert builtin_runtime.agent_tool_names([PPTX_CREATE_SKILL_ID]) == [PPTX_CREATE_TOOL]
    assert builtin_runtime.agent_tool_names([STRUCTURED_DATA_QUERY_SKILL_ID]) == []


def test_agent_without_the_skill_does_not_get_the_tool(
    monkeypatch: MonkeyPatch, agent: str
) -> None:
    runtime_repository.create_agent(
        AgentProfile(
            id=agent, name="データ照会の業務 Agent", skill_ids=[STRUCTURED_DATA_QUERY_SKILL_ID]
        )
    )
    model = _script(
        monkeypatch,
        [function_call("nl2sql__nl2sql_query", {"question": "合成の値"}, call_id="call-data")],
        [function_call(PPTX_CREATE_TOOL, PPTX_CALL, call_id="call-pptx")],
        [assistant_message("合成の値は表のとおりです。PowerPoint のファイルは作れません。")],
    )

    run = _run(agent)

    assert run.status == RunStatus.COMPLETED, run.events[-1].model_dump_json()
    assert all(PPTX_CREATE_TOOL not in {tool.name for tool in call.tools} for call in model.calls)
    # 宣言の外の呼び出しは実行せず、理由を残す。
    undeclared = [
        event.payload["undeclared_tool_call"]
        for event in run.events
        if "undeclared_tool_call" in event.payload
    ]
    assert [(item["tool_name"], item["reason"]) for item in undeclared] == [
        (PPTX_CREATE_TOOL, UNDECLARED_CALL_NOT_DECLARED)
    ]
    assert [item for item in run.artifacts if item.kind == FILE_ARTIFACT_KIND] == []


def test_agent_with_the_skill_creates_a_file_from_the_run_data_without_approval(
    monkeypatch: MonkeyPatch, agent: str
) -> None:
    runtime_repository.create_agent(
        AgentProfile(
            id=agent,
            name="データと PowerPoint の業務 Agent",
            skill_ids=[STRUCTURED_DATA_QUERY_SKILL_ID, PPTX_CREATE_SKILL_ID],
        )
    )
    model = _script(
        monkeypatch,
        [function_call("nl2sql__nl2sql_query", {"question": "合成の値"}, call_id="call-data")],
        [function_call(PPTX_CREATE_TOOL, PPTX_CALL, call_id="call-pptx")],
        [assistant_message("ファイル「合成の一覧.pptx」を作りました。スライドは 3 枚です。")],
    )

    run = _run(agent)

    # 既定のツール権限で、承認待ちにならずに完了する。
    assert run.status == RunStatus.COMPLETED, run.events[-1].model_dump_json()
    assert run.approvals == []
    [tool] = [
        item
        for item in model.calls[0].tools
        if isinstance(item, FunctionTool) and item.name == PPTX_CREATE_TOOL
    ]
    # モデルには strict の schema で渡り、入れ子の object の項目もすべて必須になる。
    assert tool.strict_json_schema is True
    slide_schema = tool.params_json_schema["properties"]["slides"]["items"]
    assert sorted(slide_schema["required"]) == sorted(slide_schema["properties"])
    # スキルの指示がモデルに渡る。
    assert "## Step 3: 表とグラフの値の渡し方を選ぶ" in str(model.calls[0].system_instructions)
    [artifact] = [item for item in run.artifacts if item.kind == FILE_ARTIFACT_KIND]
    assert artifact.content["file_name"] == "合成の一覧.pptx"
    presentation, data = _presentation(run.id, artifact.id)
    # 表とグラフの値は NL2SQL の結果と一致する（モデルの引数に値は無い）。
    assert _cell_texts(_slide_table(presentation.slides[1])) == [
        ["LABEL", "AMOUNT"],
        ["record-1", "1,200"],
        ["=1+1", "30"],
        ["record-3", ""],
    ]
    [plot] = _slide_chart(presentation.slides[2]).plots
    assert list(plot.categories) == ["record-1", "=1+1"]
    assert [list(series.values) for series in plot.series] == [[1200.0, 30.0]]
    # step・イベント・モデルへの入力に、ファイルの中身は載らない（参照だけ）。
    [step] = [
        item for item in run.steps if item.tool_call and item.tool_call.name == PPTX_CREATE_TOOL
    ]
    assert step.tool_result is not None and step.tool_result.success
    assert set(step.tool_result.output or {}) == {
        "file",
        "slides",
        "slide_count",
        "continued_slides",
        "tables",
        "charts",
    }
    encoded = base64.b64encode(data).decode("ascii")
    assert encoded[:64] not in run.model_dump_json()
    model_input = str(model.calls[2].input)
    assert encoded[:64] not in model_input and artifact.id in model_input
    model.assert_complete()


def test_admin_deny_removes_the_tool_and_ask_waits_for_approval(
    monkeypatch: MonkeyPatch, agent: str
) -> None:
    """管理者がツール権限を変えれば、拒否のツールは渡らず、承認のツールは承認待ちになる。"""
    runtime_repository.create_agent(
        AgentProfile(id=agent, name="PowerPoint の業務 Agent", skill_ids=[PPTX_CREATE_SKILL_ID])
    )
    written = _arguments(BULLETS)
    runtime_config_store.patch_tool_policy(deny=[PPTX_CREATE_TOOL])
    denied_model = _script(monkeypatch, [assistant_message("PowerPoint のファイルは作れません。")])

    denied = _run(agent)

    assert denied.status == RunStatus.COMPLETED, denied.events[-1].model_dump_json()
    assert PPTX_CREATE_TOOL not in {tool.name for tool in denied_model.calls[0].tools}

    runtime_config_store.patch_tool_policy(ask=[PPTX_CREATE_TOOL], deny=[])
    _script(monkeypatch, [function_call(PPTX_CREATE_TOOL, written, call_id="call-ask")])

    asked = _run(agent)

    assert asked.status == RunStatus.WAITING_APPROVAL
    assert [item for item in asked.artifacts if item.kind == FILE_ARTIFACT_KIND] == []
