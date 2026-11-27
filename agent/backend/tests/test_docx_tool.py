"""Word のファイルを作るツール `docx_create` と、組み込みのスキル「Word 作成」（#5837）。

- 参照した表（その Run の NL2SQL の結果）の値は、モデルの引数に値が無くても、保存した結果のまま
  表とグラフに入る。無い参照・範囲外・型に合わない値・種類に合わない項目・描けないグラフの指定は
  引数の誤りで、ファイルを作らない。
- 結果・step・イベント・モデルへの入力に、ファイルの中身（バイト列・base64）が載らない。
- 1 つの Run で作れるファイルの数の上限は、Excel のファイルと合わせて数える。
- スキルを選んだ業務 Agent の Run にだけツールが渡り、既定では承認待ちにならない。

モデルは SDK の `ScriptedModel`、NL2SQL は契約どおりの fake の MCP（`mcp_support`）、保存先は
memory。値は合成。
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import zipfile
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import anyio
import pytest
from agents import FunctionTool
from agents.testing import ScriptedModel, assistant_message, function_call
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.table import Table
from mcp_support import DEFAULT_OUTPUTS, fake_product_mcp
from openpyxl import load_workbook  # type: ignore[import-untyped]
from pptx.chart.chart import Chart
from pptx.oxml import parse_xml
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime, file_tool_support
from app.features.agent.artifact_file_store import FILE_ARTIFACT_KIND, FILE_RUN_CLOSED
from app.features.agent.builtin_runtime import UNDECLARED_CALL_NOT_DECLARED, ModelTarget
from app.features.agent.chart_spec import (
    CHART_SCHEMA,
    CHART_TYPES,
    MAX_CHART_CATEGORIES,
    MAX_PIE_CATEGORIES,
)
from app.features.agent.config import runtime_config_store
from app.features.agent.docx_document import BLOCK_TYPES, DOCX_MIME_TYPE, MAX_TABLE_ROWS
from app.features.agent.docx_tool import (
    DOCX_CREATE_INPUT_SCHEMA,
    DOCX_CREATE_TOOL,
    FILE_LIMIT_CODE,
    HEADING_MAX_CHARS,
    MAX_FILES_PER_RUN,
    MAX_WRITTEN_ROWS,
)
from app.features.agent.mcp_server import FILES_DOWNLOAD_MESSAGE, run_output
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    RunState,
    RunStatus,
    runtime_repository,
)
from app.features.agent.skills import (
    DOCX_CREATE_SKILL_ID,
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
# 合成の NL2SQL の結果（数値・小数・日時・NULL・桁の多い番号・`=` で始まる文字）。
COLUMNS = ["LABEL", "AMOUNT", "RATE", "CREATED_AT", "CODE"]
ROWS: list[dict[str, Any]] = [
    {
        "LABEL": "record-1",
        "AMOUNT": 1200,
        "RATE": 0.25,
        "CREATED_AT": "2026-01-05T00:00:00",
        "CODE": "12345678901234567890",
    },
    {
        "LABEL": "=1+1",
        "AMOUNT": 30,
        "RATE": 0.5,
        "CREATED_AT": "2026-02-10T18:30:00",
        "CODE": "0000000000000000000001",
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
        "caption": None,
        "columns": [_column("text", header="名前")],
        "source": None,
        "rows": [],
    }
    return {**base, **changes}


def _block(block_type: str, **changes: Any) -> dict[str, Any]:
    """モデルに見せる schema（strict）どおり、すべての項目を持つブロック。"""
    base: dict[str, Any] = {
        "type": block_type,
        "text": None,
        "level": None,
        "items": [],
        "table": None,
        "chart": None,
    }
    return {**base, **changes}


def _table_block(**changes: Any) -> dict[str, Any]:
    return _block("table", table=_table(**changes))


def _chart(chart_type: str = "column", category: int = 1, *series: int) -> dict[str, Any]:
    return {
        "type": chart_type,
        "category_column": category,
        "series_columns": list(series or (2,)),
    }


def _chart_block(chart: dict[str, Any] | None = None, **table: Any) -> dict[str, Any]:
    """グラフのブロック（既定は、名前と数値の 2 列を書いた表の、縦棒のグラフ）。"""
    written = {
        "columns": [_column("text", header="名前"), _column("number", header="数量")],
        "rows": [["a", 1], ["b", 2]],
    }
    return _block("chart", table=_table(**(table or written)), chart=chart or _chart())


PARAGRAPH = _block("paragraph", text="合成の段落。")


def _arguments(*blocks: dict[str, Any], title: str = "合成の文書") -> dict[str, Any]:
    return {"title": title, "subtitle": None, "blocks": list(blocks)}


def _invoke(run_id: str | None, *blocks: dict[str, Any], title: str = "合成の文書") -> ToolResult:
    return tool_registry.invoke(
        ToolCall(name=DOCX_CREATE_TOOL, arguments=_arguments(*blocks, title=title)),
        context=ToolInvocationContext(run_id=run_id),
    )


def _files(run_id: str) -> list[Any]:
    return [item for item in runtime_repository.get_run(run_id).artifacts if item.kind == "file"]


def _document(run_id: str, artifact_id: str) -> tuple[Any, bytes]:
    data = runtime_repository.read_artifact_file(run_id, artifact_id).data
    return Document(io.BytesIO(data)), data


def _cell_texts(table: Table) -> list[list[str]]:
    return [[cell.text for cell in row.cells] for row in table.rows]


def _chart_parts(data: bytes, number: int = 1) -> tuple[Any, Any]:
    """文書の中のグラフ（python-pptx で読んだもの）と、その値のブックの sheet。"""
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        chart = Chart(parse_xml(package.read(f"word/charts/chart{number}.xml")), None)
        workbook = package.read(f"word/embeddings/Microsoft_Excel_Sheet{number}.xlsx")
    return chart, load_workbook(io.BytesIO(workbook)).active


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


@pytest.fixture
def run_id() -> Iterator[str]:
    """NL2SQL の結果を 1 つ持つ、実行中の Run（結果は表の参照 `T1` として登録される）。"""
    run = RunState(
        id=f"run5837d-{uuid4().hex[:8]}",
        goal="合成の質問",
        agent_id="default",
        runtime_id="builtin",
        status=RunStatus.RUNNING,
    )
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - 実行中の Run を直接置く
        repository._runs[run.id] = run  # noqa: SLF001
    step_id, _ = runtime_repository.start_builtin_tool_step(
        run.id, ToolCall(name="nl2sql__nl2sql_query", arguments={})
    )
    runtime_repository.finish_builtin_tool_step(
        run.id,
        step_id,
        ToolResult(name="nl2sql__nl2sql_query", success=True, output=NL2SQL_OUTPUT),
    )
    try:
        yield run.id
    finally:
        _forget_run(run.id)


# ---- 表の参照（値はモデルから受け取らない） -------------------------------------------------


def test_referenced_values_are_copied_from_the_stored_result(run_id: str) -> None:
    arguments = _table_block(
        caption="合成の表",
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

    result = _invoke(
        run_id, _block("heading", text="1. 概要", level=1), arguments, title="合成の報告"
    )

    assert result.success, result.error
    assert result.policy_decision == ToolPolicyDecision.ALLOW and not result.approval_required
    [artifact] = _files(run_id)
    document, data = _document(run_id, artifact.id)
    [table] = document.tables
    texts = _cell_texts(table)
    assert texts[0] == ["金額（円）", "LABEL", "CREATED_AT", "RATE", "番号"]
    # 値は保存した結果のまま（数値・日付は書式を当てた文字、文字はそのまま）。
    assert texts[1:] == [
        ["30", "=1+1", "2026-02-10 18:30", "50.0%", "0000000000000000000001"],
        ["1,200", "record-1", "2026-01-05 00:00", "25.0%", "12345678901234567890"],
        ["", "record-3", "", "", ""],
    ]
    stored = [ROWS[position] for position in (1, 0, 2)]
    assert [row[1] for row in texts[1:]] == [row["LABEL"] for row in stored]
    assert [row[4] for row in texts[1:]] == [row["CODE"] or "" for row in stored]
    assert [Decimal(row[0].replace(",", "")) if row[0] else None for row in texts[1:]] == [
        None if row["AMOUNT"] is None else Decimal(row["AMOUNT"]) for row in stored
    ]
    # 数値・日付の列は右寄せ。
    right = WD_ALIGN_PARAGRAPH.RIGHT
    assert [cell.paragraphs[0].alignment for cell in table.rows[1].cells] == [
        right,
        None,
        right,
        right,
        None,
    ]
    # 結果は成果物の参照と要約だけ（中身を載せない）。
    assert result.output == {
        "file": {
            "artifact_id": artifact.id,
            "file_name": "合成の報告.docx",
            "mime_type": DOCX_MIME_TYPE,
            "size_bytes": len(data),
            "sha256": artifact.content["sha256"],
        },
        "blocks": {
            "heading": 1,
            "paragraph": 0,
            "bullets": 0,
            "numbered": 0,
            "table": 1,
            "chart": 0,
            "page_break": 0,
        },
        "tables": [{"rows": 3, "columns": 5}],
        "charts": [],
    }
    encoded = base64.b64encode(data).decode("ascii")
    recorded = result.model_dump_json() + runtime_repository.get_run(run_id).model_dump_json()
    assert encoded[:64] not in recorded


def test_chart_values_are_copied_from_the_stored_result(run_id: str) -> None:
    arguments = _chart_block(
        _chart("column", 1, 2, 3),
        caption="合成の図",
        columns=[
            _column("text"),
            _column("number", header="金額（円）", format="integer"),
            _column("number", format="percent1"),
        ],
        source=_source(rows="2,1", columns="1-3"),
    )
    # モデルの引数に、グラフの値は 1 つも無い（どの列を項目・系列にするかだけ）。
    assert all(str(value) not in json.dumps(arguments) for value in (1200, 0.25, "record-1"))

    result = _invoke(run_id, arguments, title="合成の報告")

    assert result.success, result.error
    assert result.output is not None
    assert result.output["blocks"]["chart"] == 1 and result.output["tables"] == []
    assert result.output["charts"] == [{"type": "column", "series": 2, "categories": 2}]
    document, data = _document(run_id, _files(run_id)[0].id)
    # グラフの値を取る表は文書に出ない。グラフは、題の下に Word のグラフとして入る。
    assert document.tables == []
    assert [item.text for item in document.paragraphs if item.style.name == "Caption"] == [
        "合成の図"
    ]
    chart, sheet = _chart_parts(data)
    [plot] = chart.plots
    # 項目・系列の名前と値は、保存した結果のまま（行は書いた順）。
    stored = [ROWS[1], ROWS[0]]
    assert list(plot.categories) == [row["LABEL"] for row in stored] == ["=1+1", "record-1"]
    assert [item.name for item in plot.series] == ["金額（円）", "RATE"]
    assert [list(item.values) for item in plot.series] == [
        [float(row["AMOUNT"]) for row in stored],
        [float(row["RATE"]) for row in stored],
    ]
    # 値のブック（利用者が「データの編集」で開く表）も同じ値。`=` で始まる名前は文字のまま。
    assert [list(row) for row in sheet.iter_rows(values_only=True)] == [
        [None, "金額（円）", "RATE"],
        ["=1+1", 30, 0.5],
        ["record-1", 1200, 0.25],
    ]
    assert sheet.cell(row=2, column=1).data_type == "s"
    encoded = base64.b64encode(data).decode("ascii")
    recorded = result.model_dump_json() + runtime_repository.get_run(run_id).model_dump_json()
    assert encoded[:64] not in recorded


def test_all_block_types_and_both_table_sources_work_together(run_id: str) -> None:
    result = tool_registry.invoke(
        ToolCall(
            name=DOCX_CREATE_TOOL,
            arguments={
                "title": "合成の報告",
                "subtitle": "2026-11-26　部門 1",
                "blocks": [
                    _block("heading", text="1. 概要", level=1),
                    _block("paragraph", text="合成の段落。"),
                    _block(
                        "bullets",
                        items=[
                            {"text": "項目 A", "children": ["項目 A-1"]},
                            {"text": "項目 B", "children": []},
                        ],
                    ),
                    _block("numbered", items=[{"text": "手順 1", "children": []}]),
                    _table_block(
                        columns=[_column("text"), _column("number")],
                        source=_source(table="[[table:T1]]", rows="1", columns="1-2"),
                    ),
                    _block("page_break"),
                    _block("heading", text="1.1 補足", level=2),
                    _table_block(
                        caption="合成の説明",
                        columns=[_column("text", header="項目"), _column("number", header="値")],
                        rows=[["a", 1], ["b", 2.5], ["c", None]],
                    ),
                    _chart_block(
                        _chart("line"),
                        caption="合成の図",
                        columns=[_column("text"), _column("number")],
                        source=_source(rows="1-2", columns="1-2"),
                    ),
                    _chart_block(_chart("pie")),
                ],
            },
        ),
        context=ToolInvocationContext(run_id=run_id),
    )

    assert result.success, result.error
    assert result.output is not None
    assert result.output["blocks"] == {
        "heading": 2,
        "paragraph": 1,
        "bullets": 1,
        "numbered": 1,
        "table": 2,
        "chart": 2,
        "page_break": 1,
    }
    assert result.output["tables"] == [{"rows": 1, "columns": 2}, {"rows": 3, "columns": 2}]
    assert result.output["charts"] == [
        {"type": "line", "series": 1, "categories": 2},
        {"type": "pie", "series": 1, "categories": 2},
    ]
    document, _ = _document(run_id, _files(run_id)[0].id)
    texts = [(item.style.name, item.text) for item in document.paragraphs if item.text]
    assert texts == [
        ("Title", "合成の報告"),
        ("Subtitle", "2026-11-26　部門 1"),
        ("Heading 1", "1. 概要"),
        ("Normal", "合成の段落。"),
        ("List Paragraph", "項目 A"),
        ("List Paragraph", "項目 A-1"),
        ("List Paragraph", "項目 B"),
        ("List Paragraph", "手順 1"),
        ("Heading 2", "1.1 補足"),
        ("Caption", "合成の説明"),
        ("Caption", "合成の図"),
    ]
    # グラフの値を取る表は、文書の表にならない。
    referenced, written = document.tables
    assert _cell_texts(referenced) == [["LABEL", "AMOUNT"], ["record-1", "1200"]]
    assert _cell_texts(written) == [["項目", "値"], ["a", "1"], ["b", "2.5"], ["c", ""]]
    # 改ページの後の見出しは、新しいページから始まる。
    [heading] = [item for item in document.paragraphs if item.text == "1.1 補足"]
    assert heading.paragraph_format.page_break_before is True


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        (
            _table_block(source=_source(table="T9")),
            ("blocks.0.table.source.table", "unknown_table"),
        ),
        (
            # cell の参照は表の参照ではない。
            _table_block(source=_source(table="T1:R1:C1")),
            ("blocks.0.table.source.table", "unknown_table"),
        ),
        (
            _table_block(source=_source(rows="4", columns="1")),
            ("blocks.0.table.source.rows", "selection"),
        ),
        (
            _table_block(source=_source(rows="1", columns="6")),
            ("blocks.0.table.source.columns", "selection"),
        ),
        (
            _table_block(source=_source(rows="1,1", columns="1")),
            ("blocks.0.table.source.rows", "selection"),
        ),
        (
            # 選んだ列の数と columns の数が違う。
            _table_block(source=_source(columns="1,2")),
            ("blocks.0.table.columns", "count"),
        ),
        (
            _table_block(source=_source(columns="1"), rows=[["x"]]),
            ("blocks.0.table.rows", "conflict"),
        ),
        (
            # 参照しない表は、列の見出しが要る。
            _table_block(columns=[_column("text")], rows=[["x"]]),
            ("blocks.0.table.columns.0.header", "required"),
        ),
        (
            # 参照した値が列の型に合わない（文字の列を数値にした）。
            _table_block(columns=[_column("number")], source=_source(columns="1")),
            ("blocks.0.table.columns.0.type", "not_number"),
        ),
        (
            _table_block(columns=[_column("date")], source=_source(columns="2")),
            ("blocks.0.table.columns.0.type", "not_date"),
        ),
        (
            _table_block(columns=[_column("text", header="名前", format="integer")]),
            ("blocks.0.table.columns.0.format", "format"),
        ),
        # ブロックの種類に合わない項目（要る項目が無い・使わない項目に値がある）。
        (_block("heading", text="見出し"), ("blocks.0.level", "required")),
        (_block("heading", level=1), ("blocks.0.text", "required")),
        (_block("paragraph", text=""), ("blocks.0.text", "required")),
        (_block("bullets"), ("blocks.0.items", "required")),
        (_block("table"), ("blocks.0.table", "required")),
        (
            _block("paragraph", text="文", items=[{"text": "項目", "children": []}]),
            ("blocks.0.items", "conflict"),
        ),
        (_block("paragraph", text="文", level=2), ("blocks.0.level", "conflict")),
        (_block("page_break", text="文"), ("blocks.0.text", "conflict")),
        (
            _block("numbered", items=[{"text": "項目", "children": []}], table=_table()),
            ("blocks.0.table", "conflict"),
        ),
        (
            _block("heading", text="x" * (HEADING_MAX_CHARS + 1), level=1),
            ("blocks.0.text", "too_long"),
        ),
        (
            _block("bullets", items=[{"text": "項目", "children": [" "]}]),
            ("blocks.0.items.0.children.0", "required"),
        ),
        # グラフ: 値を取る表とグラフの指定の両方が要る。ほかの種類では指定を書けない。
        (_block("chart", table=_table(rows=[["a"]])), ("blocks.0.chart", "required")),
        (_block("chart", chart=_chart()), ("blocks.0.table", "required")),
        (
            _block("table", table=_table(rows=[["a"]]), chart=_chart()),
            ("blocks.0.chart", "conflict"),
        ),
        (_block("paragraph", text="文", chart=_chart()), ("blocks.0.chart", "conflict")),
        (
            _chart_block(columns=[_column("text"), _column("number")], source=_source(table="T9")),
            ("blocks.0.table.source.table", "unknown_table"),
        ),
        (
            # 系列にできるのは数値の列だけ（文字の列を系列にした）。
            _chart_block(columns=[_column("text"), _column("text")], source=_source(columns="1,5")),
            ("blocks.0.chart.series_columns.0", "not_number"),
        ),
        (_chart_block(_chart("column", 1, 3)), ("blocks.0.chart.series_columns.0", "range")),
        (
            # 参照した値に空の値がある表は、円のグラフにできない。
            _chart_block(
                _chart("pie"),
                columns=[_column("text"), _column("number")],
                source=_source(columns="1,2"),
            ),
            ("blocks.0.table.columns.1", "pie_values"),
        ),
        (
            _chart_block(
                columns=[_column("text", header="名前"), _column("number", header="数量")],
                rows=[[f"item-{index}", index] for index in range(MAX_CHART_CATEGORIES + 1)],
            ),
            ("blocks.0.table.rows", "too_many"),
        ),
        (
            _chart_block(
                _chart("pie"),
                columns=[_column("text", header="名前"), _column("number", header="数量")],
                rows=[[f"item-{index}", index + 1] for index in range(MAX_PIE_CATEGORIES + 1)],
            ),
            ("blocks.0.table.rows", "too_many"),
        ),
    ],
    ids=[
        "unknown-table",
        "cell-reference",
        "row-out-of-range",
        "column-out-of-range",
        "duplicate-row",
        "column-count",
        "source-and-rows",
        "header-required",
        "text-as-number",
        "number-as-date",
        "format-for-type",
        "heading-without-level",
        "heading-without-text",
        "empty-paragraph",
        "list-without-items",
        "table-without-table",
        "paragraph-with-items",
        "paragraph-with-level",
        "page-break-with-text",
        "list-with-table",
        "long-heading",
        "blank-child-item",
        "chart-without-chart",
        "chart-without-table",
        "table-with-chart",
        "paragraph-with-chart",
        "chart-unknown-table",
        "chart-text-series",
        "chart-series-out-of-range",
        "chart-pie-with-empty-value",
        "chart-too-many-categories",
        "chart-too-many-pie-categories",
    ],
)
def test_bad_references_values_and_block_fields_are_argument_errors(
    run_id: str, block: dict[str, Any], expected: tuple[str, str]
) -> None:
    result = _invoke(run_id, block)

    assert _argument_errors(result) == [expected]
    # 成功にせず、ファイルも作らない。誤りの文に結果の値を写さない。
    assert _files(run_id) == []
    text = json.dumps(result.error_details, ensure_ascii=False)
    assert all(str(row["LABEL"]) not in text for row in ROWS)


def test_referenced_table_over_the_row_limit_points_to_a_spreadsheet(run_id: str) -> None:
    """大きい表の参照は切り捨てず、Excel のファイルにすることを促す誤りにする。"""
    rows = [{"LABEL": f"record-{index}"} for index in range(MAX_TABLE_ROWS + 1)]
    step_id, _ = runtime_repository.start_builtin_tool_step(
        run_id, ToolCall(name="nl2sql__nl2sql_query", arguments={})
    )
    runtime_repository.finish_builtin_tool_step(
        run_id,
        step_id,
        ToolResult(
            name="nl2sql__nl2sql_query",
            success=True,
            output={"status": "done", "columns": ["LABEL"], "rows": rows},
        ),
    )

    result = _invoke(run_id, _table_block(columns=[_column("text")], source=_source(table="T2")))

    assert _argument_errors(result) == [("blocks.0.table.rows", "too_many")]
    [error] = result.error_details["argument_errors"]
    assert "Excel のファイル" in error["message"]
    assert _files(run_id) == []
    # 行を絞れば作れる。
    assert _invoke(
        run_id,
        _table_block(columns=[_column("text")], source=_source(table="T2", rows="1-20")),
    ).success


def test_schema_violations_are_rejected_before_the_handler(run_id: str) -> None:
    item = {"text": "項目", "children": []}
    for arguments in (
        {**_arguments(PARAGRAPH), "title": ""},
        _arguments(),
        {"title": "t", "blocks": [PARAGRAPH]},
        _arguments(_block("quote", text="文")),
        _arguments(_block("heading", text="見出し", level=4)),
        _arguments(_block("paragraph", text="文", bold=True)),
        _arguments(_block("bullets", items=[{**item, "children": [""]}])),
        _arguments(_block("bullets", items=[{**item, "children": [["x"]]}])),
        _arguments(_table_block(columns=[_column("money", header="a")])),
        _arguments(_table_block(columns=[_column("number", header="a", format="￥")])),
        _arguments(_table_block(columns=[{**_column("text", header="a"), "width": 10}])),
        _arguments(_table_block(rows=[[True]])),
        _arguments(_table_block(rows=[["x"]] * (MAX_WRITTEN_ROWS + 1))),
        _arguments(_table_block(source={"table": "T1"})),
        _arguments(_table_block(image="a.png")),
        _arguments(_chart_block(_chart("area"))),
        _arguments(_chart_block({**_chart(), "category_column": 0})),
        _arguments(_chart_block({**_chart(), "series_columns": ["2"]})),
        # グラフ用の値を書く欄・体裁の指定は無い。
        _arguments(_chart_block({**_chart(), "values": [[1, 2]]})),
        _arguments(_chart_block({**_chart(), "title": "題", "colors": ["FF0000"]})),
    ):
        result = tool_registry.invoke(
            ToolCall(name=DOCX_CREATE_TOOL, arguments=arguments),
            context=ToolInvocationContext(run_id=run_id),
        )
        assert (result.success, result.error_code) == (False, INVALID_ARGUMENTS_CODE), arguments
        assert result.audit_metadata["argument_validation"] == "failed"
    assert _files(run_id) == []


def test_input_schema_is_strict_for_the_model() -> None:
    """モデルには strict の schema で渡す（root の項目はすべて必須。追加の項目は不可）。"""
    assert builtin_runtime._strict_schema(DOCX_CREATE_INPUT_SCHEMA)  # noqa: SLF001

    def objects(node: Any) -> Iterator[dict[str, Any]]:
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for value in node.values():
                yield from objects(value)
        elif isinstance(node, list):
            for value in node:
                yield from objects(value)

    for node in objects(DOCX_CREATE_INPUT_SCHEMA):
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


def test_chart_argument_is_the_shared_chart_schema() -> None:
    """グラフの指定は `pptx_create` と同じ形・同じ上限（`chart_spec` の 1 つの schema）。"""
    block = DOCX_CREATE_INPUT_SCHEMA["properties"]["blocks"]["items"]["properties"]
    assert block["type"]["enum"] == list(BLOCK_TYPES)
    assert block["chart"]["anyOf"] == [CHART_SCHEMA, {"type": "null"}]
    assert CHART_SCHEMA["properties"]["type"]["enum"] == list(CHART_TYPES)
    # グラフの値を書く欄は無い（値は table から取る）。
    assert set(CHART_SCHEMA["properties"]) == {"type", "category_column", "series_columns"}
    # 作れるグラフの種類の呼び名は、ツールの説明に正本から入る。
    definition = tool_registry.get(DOCX_CREATE_TOOL)
    assert definition is not None
    assert all(label in definition.description for label in CHART_TYPES.values())
    assert set(definition.output_schema["properties"]) == {"file", "blocks", "tables", "charts"}


def test_files_per_run_are_limited_together_with_spreadsheets(
    run_id: str, monkeypatch: MonkeyPatch
) -> None:
    assert MAX_FILES_PER_RUN == 10
    monkeypatch.setattr(file_tool_support, "MAX_FILES_PER_RUN", 2)
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
    assert spreadsheet.success, spreadsheet.error

    results = [_invoke(run_id, PARAGRAPH, title=f"file-{index}") for index in (1, 2)]

    assert [result.success for result in results] == [True, False]
    assert results[1].error_code == FILE_LIMIT_CODE
    assert [item.name for item in _files(run_id)] == ["file-0.xlsx", "file-1.docx"]


def test_mcp_run_output_lists_the_file_without_its_content(run_id: str) -> None:
    """外部の MCP への Run の応答には、ファイルの名前・形式・大きさだけが載る（版は変えない）。"""
    assert _invoke(run_id, PARAGRAPH, title="合成の報告").success
    [artifact] = _files(run_id)
    _, data = _document(run_id, artifact.id)

    output = run_output(runtime_repository.get_run(run_id))

    assert [item.model_dump() for item in output.files] == [
        {"file_name": "合成の報告.docx", "mime_type": DOCX_MIME_TYPE, "size_bytes": len(data)}
    ]
    assert output.message.endswith(FILES_DOWNLOAD_MESSAGE)
    text = output.model_dump_json()
    assert base64.b64encode(data).decode("ascii")[:64] not in text and artifact.id not in text


def test_tool_fails_outside_a_run_and_after_the_run_ended(run_id: str) -> None:
    assert _invoke(None, PARAGRAPH).error_code == FILE_RUN_CLOSED
    assert _invoke("run_missing", PARAGRAPH).error_code == FILE_RUN_CLOSED
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - Run を終わった状態にする
        repository._runs[run_id].status = RunStatus.COMPLETED  # noqa: SLF001

    result = _invoke(run_id, PARAGRAPH)

    assert (result.success, result.error_code) == (False, FILE_RUN_CLOSED)
    assert _files(run_id) == []


# ---- ツール権限（既定は承認なし。管理者が変えられる） ---------------------------------------


def test_default_policy_allows_and_the_admin_can_change_it(run_id: str) -> None:
    definition = tool_registry.get(DOCX_CREATE_TOOL)
    assert definition is not None and not definition.side_effects
    for mode in ("approval", "deny"):
        assert ToolPolicy(default_mode=mode).decide(definition) == ToolPolicyDecision.ALLOW
    # ツール権限の画面の一覧に、Control Plane のツールとして出る。
    [row] = [
        tool
        for tool in tool_policy_catalog(ToolInvocationContext()).tools
        if tool.name == DOCX_CREATE_TOOL
    ]
    assert (row.server_id, row.default_decision) == (CONTROL_PLANE_SOURCE, ToolPolicyDecision.ALLOW)
    call = ToolCall(name=DOCX_CREATE_TOOL, arguments=_arguments(PARAGRAPH))
    context = ToolInvocationContext(run_id=run_id)

    asked = tool_registry.invoke(call, policy=ToolPolicy(ask={DOCX_CREATE_TOOL}), context=context)
    denied = tool_registry.invoke(
        call, policy=ToolPolicy(deny={DOCX_CREATE_TOOL}), context=context, force=True
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
    agent_id = f"agent5837d-{uuid4().hex[:8]}"
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
        RunCreateRequest(goal="合成の値を Word のファイルにして", agent_id=agent_id),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return runtime_repository.get_run(run.id)


DOCX_CALL = _arguments(
    _block("heading", text="1. 一覧", level=1),
    _block("paragraph", text="合成の値の一覧です。"),
    _table_block(
        columns=[_column("text"), _column("number", format="integer")],
        source=_source(columns="1,2"),
    ),
    title="合成の一覧",
)


def test_builtin_skill_declares_only_the_docx_tool() -> None:
    skill = skill_registry.get(DOCX_CREATE_SKILL_ID)
    assert skill is not None and skill.source == "builtin" and skill.name == "Word 作成"
    assert [(item.server_id, item.tool_names) for item in skill.mcp_requirements] == [
        ("control-plane", [DOCX_CREATE_TOOL])
    ]
    assert builtin_runtime.agent_tool_names([DOCX_CREATE_SKILL_ID]) == [DOCX_CREATE_TOOL]
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
        [function_call(DOCX_CREATE_TOOL, DOCX_CALL, call_id="call-docx")],
        [assistant_message("合成の値は表のとおりです。Word のファイルは作れません。")],
    )

    run = _run(agent)

    assert run.status == RunStatus.COMPLETED, run.events[-1].model_dump_json()
    assert all(DOCX_CREATE_TOOL not in {tool.name for tool in call.tools} for call in model.calls)
    # 宣言の外の呼び出しは実行せず、理由を残す。
    undeclared = [
        event.payload["undeclared_tool_call"]
        for event in run.events
        if "undeclared_tool_call" in event.payload
    ]
    assert [(item["tool_name"], item["reason"]) for item in undeclared] == [
        (DOCX_CREATE_TOOL, UNDECLARED_CALL_NOT_DECLARED)
    ]
    assert [item for item in run.artifacts if item.kind == FILE_ARTIFACT_KIND] == []


def test_agent_with_the_skill_creates_a_file_from_the_run_data_without_approval(
    monkeypatch: MonkeyPatch, agent: str
) -> None:
    runtime_repository.create_agent(
        AgentProfile(
            id=agent,
            name="データと Word の業務 Agent",
            skill_ids=[STRUCTURED_DATA_QUERY_SKILL_ID, DOCX_CREATE_SKILL_ID],
        )
    )
    model = _script(
        monkeypatch,
        [function_call("nl2sql__nl2sql_query", {"question": "合成の値"}, call_id="call-data")],
        [function_call(DOCX_CREATE_TOOL, DOCX_CALL, call_id="call-docx")],
        [assistant_message("ファイル「合成の一覧.docx」を作りました。表に 3 行あります。")],
    )

    run = _run(agent)

    # 既定のツール権限で、承認待ちにならずに完了する。
    assert run.status == RunStatus.COMPLETED, run.events[-1].model_dump_json()
    assert run.approvals == []
    [tool] = [
        item
        for item in model.calls[0].tools
        if isinstance(item, FunctionTool) and item.name == DOCX_CREATE_TOOL
    ]
    # モデルには strict の schema で渡り、入れ子の object の項目もすべて必須になる。
    assert tool.strict_json_schema is True
    block_schema = tool.params_json_schema["properties"]["blocks"]["items"]
    assert sorted(block_schema["required"]) == [
        "chart",
        "items",
        "level",
        "table",
        "text",
        "type",
    ]
    # スキルの指示がモデルに渡る。
    assert "## Step 3: 表とグラフの値の渡し方を選ぶ" in str(model.calls[0].system_instructions)
    [artifact] = [item for item in run.artifacts if item.kind == FILE_ARTIFACT_KIND]
    assert artifact.content["file_name"] == "合成の一覧.docx"
    document, data = _document(run.id, artifact.id)
    # 値は NL2SQL の結果と一致する（モデルの引数に値は無い）。
    [table] = document.tables
    assert _cell_texts(table) == [
        ["LABEL", "AMOUNT"],
        ["record-1", "1,200"],
        ["=1+1", "30"],
        ["record-3", ""],
    ]
    # step・イベント・モデルへの入力に、ファイルの中身は載らない（参照だけ）。
    [step] = [
        item for item in run.steps if item.tool_call and item.tool_call.name == DOCX_CREATE_TOOL
    ]
    assert step.tool_result is not None and step.tool_result.success
    assert set(step.tool_result.output or {}) == {"file", "blocks", "tables", "charts"}
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
        AgentProfile(id=agent, name="Word の業務 Agent", skill_ids=[DOCX_CREATE_SKILL_ID])
    )
    written = _arguments(PARAGRAPH)
    runtime_config_store.patch_tool_policy(deny=[DOCX_CREATE_TOOL])
    denied_model = _script(monkeypatch, [assistant_message("Word のファイルは作れません。")])

    denied = _run(agent)

    assert denied.status == RunStatus.COMPLETED, denied.events[-1].model_dump_json()
    assert DOCX_CREATE_TOOL not in {tool.name for tool in denied_model.calls[0].tools}

    runtime_config_store.patch_tool_policy(ask=[DOCX_CREATE_TOOL], deny=[])
    _script(monkeypatch, [function_call(DOCX_CREATE_TOOL, written, call_id="call-ask")])

    asked = _run(agent)

    assert asked.status == RunStatus.WAITING_APPROVAL
    assert [item for item in asked.artifacts if item.kind == FILE_ARTIFACT_KIND] == []
