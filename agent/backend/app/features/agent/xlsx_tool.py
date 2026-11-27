"""Excel のファイル（.xlsx）を作る Control Plane のツール `xlsx_create`（#5837）。

- 渡し方: スキルが `control-plane:xlsx_create` を宣言した業務 Agent の Run にだけ渡る（業務 Agent・
  Run に暗黙に足さない。不変条件 1）。実行は `tool_registry.invoke`（ツール権限・監査）を通る。
  Run の中に成果物を作るだけで外部へ書かないので、既定では承認が要らない（`record_output` と同じく
  副作用なしの宣言。管理者はツール権限で承認・拒否に変えられる）。
- 引数: モデルは構造化した内容（題・シート・列・集計・グラフ）を渡す。表の値は 2 つの出所の
  どちらか。
  - `source`: その Run で取得済みの表（NL2SQL の結果）の参照。回答の表の参照（`answer_data`）と同じ
    番号（`T1`）と行・列の指定で、値は保存した step の結果からここで写す（モデルは値を書かない）。
    無い参照・範囲外は引数の誤りとして返す。
  - `rows`: モデルが書いた小さい表。
  グラフ（`chart`）は、そのシートの表のどの列を項目・系列にするかだけを指定する（指定の形・上限・
  検証は PowerPoint のツールと同じ `chart_spec`）。グラフは表の cell を参照し、値を別に持たない。
- 生成の本体は `xlsx_workbook.build_workbook`（Run に依らない）。保存は
  `artifact_file_store.save_tool_file`（参照だけを返す。大きさ・形式の検査もそこ）。結果・イベント・
  ログにファイルの中身を載せない。
- 上限: 1 つの Run で作れるファイルは `MAX_FILES_PER_RUN` 個。内容の上限は `xlsx_workbook` と
  `chart_spec`。
- 表の参照の解決・ファイルの数の上限・内容の誤りの返し方は、Word のツール（`docx_tool`）と共通
  （`file_tool_support`）。

ツールの層（`tools.py`）からは import しない（Run の module を読むため）。`tool_registry` への登録は
組み込み Runtime（`builtin_runtime`）がする。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.features.agent.chart_spec import CHART_SCHEMA, CHART_TYPES
from app.features.agent.file_tool_support import (
    FILE_LIMIT_CODE,
    FILE_OUTPUT_SCHEMA,
    FORMAT_NAMES_HELP,
    MAX_FILES_PER_RUN,
    chart_spec,
    column_header,
    create_run_file,
    invalid_content,
    run_data_index,
    source_schema,
    table_values,
)
from app.features.agent.tools import ToolDefinition, ToolHandler, ToolInvocationContext
from app.features.agent.xlsx_workbook import (
    DATE_FORMATS,
    MAX_COLUMNS,
    MAX_NOTES,
    MAX_SHEETS,
    MAX_SUMMARIES,
    NUMBER_FORMATS,
    SHEET_NAME_MAX_CHARS,
    SUMMARY_COLUMN_TYPES,
    WIDTH_MAX,
    WIDTH_MIN,
    XLSX_MIME_TYPE,
    Column,
    Sheet,
    SpecIssue,
    Summary,
    XlsxSpecError,
    build_workbook,
)

XLSX_CREATE_TOOL = "xlsx_create"
# モデルが引数に書く表（`rows`）の行の数。大きい表は取得済みの表の参照（`source`）で渡す。
MAX_WRITTEN_ROWS = 200
TITLE_MAX_CHARS = 100
HEADING_MAX_CHARS = 200
NOTE_MAX_CHARS = 500
HEADER_MAX_CHARS = 100

_FORMAT_HELP = (
    FORMAT_NAMES_HELP + "。percent・percent1 は値を 100 倍して表示する（0.15 が 15%）。null は、"
    "数値が Excel の標準の表示、日付が date（時刻のある値を含む列は datetime）。文字の列は null"
)

_COLUMN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "header": {
            "type": ["string", "null"],
            "maxLength": HEADER_MAX_CHARS,
            "description": (
                "列の見出し。source を使うシートでは null にすると、参照した表の列名になる"
            ),
        },
        "type": {
            "type": "string",
            "enum": ["text", "number", "date"],
            "description": (
                "列の値の型。text=文字 / number=数値 / date=日付・日時（rows に書くときは "
                "YYYY-MM-DD か ISO 8601 の日時の文字列）"
            ),
        },
        "format": {
            "type": ["string", "null"],
            "enum": [*NUMBER_FORMATS, *DATE_FORMATS, None],
            "description": f"表示の書式。{_FORMAT_HELP}",
        },
        "width": {
            "type": ["number", "null"],
            "minimum": WIDTH_MIN,
            "maximum": WIDTH_MAX,
            "description": "列の幅（半角の文字数の目安）。null は内容から決める",
        },
    },
    "required": ["type"],
    "additionalProperties": False,
}
_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "function": {
            "type": "string",
            "enum": list(SUMMARY_COLUMN_TYPES),
            "description": (
                "集計の種類。sum=合計 / average=平均 / count=値のある cell の数 / min=最小 / "
                "max=最大。sum と average は数値の列、min と max は数値・日付の列に使える"
            ),
        },
        "columns": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_COLUMNS,
            "items": {"type": "integer", "minimum": 1, "maximum": MAX_COLUMNS},
            "description": "集計する列（シートの columns の 1 始まりの位置）",
        },
        "label": {
            "type": ["string", "null"],
            "maxLength": HEADER_MAX_CHARS,
            "description": "行の見出し（集計しない最初の列に入る）。置かないなら null",
        },
    },
    "required": ["function", "columns"],
    "additionalProperties": False,
}
_SHEET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": SHEET_NAME_MAX_CHARS,
            "description": (
                "シート名。Excel で使えない文字（: \\ / ? * [ ]）は _ に、同じ名前は連番を付けて"
                "直す（直した名前は結果の sheets に返る）"
            ),
        },
        "heading": {
            "type": ["string", "null"],
            "maxLength": HEADING_MAX_CHARS,
            "description": "表の上に置く題。置かないなら null",
        },
        "notes": {
            "type": "array",
            "maxItems": MAX_NOTES,
            "items": {"type": "string", "maxLength": NOTE_MAX_CHARS},
            "description": "題の下に置く説明の文（1 要素が 1 行）。無ければ空の配列",
        },
        "columns": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_COLUMNS,
            "items": _COLUMN_SCHEMA,
            "description": "表の列（左から）",
        },
        "source": source_schema("シート"),
        "rows": {
            "type": "array",
            "maxItems": MAX_WRITTEN_ROWS,
            "items": {
                "type": "array",
                "maxItems": MAX_COLUMNS,
                "items": {"type": ["string", "number", "null"]},
            },
            "description": (
                "source が null のシートの表の行（1 行が columns と同じ数・順の値の配列。値の無い "
                "cell は null）。source を使うシートでは空の配列"
            ),
        },
        "summaries": {
            "type": "array",
            "maxItems": MAX_SUMMARIES,
            "items": _SUMMARY_SCHEMA,
            "description": (
                "表の下に足す集計の行（1 要素が 1 行。Excel の数式で入る）。無ければ空の配列"
            ),
        },
        "chart": {
            "anyOf": [CHART_SCHEMA, {"type": "null"}],
            "description": (
                "表の右に置くグラフの指定（種類と、このシートの columns のどの列を項目・系列に"
                "するか）。グラフは表の cell を参照し、集計の行は含まない。色・凡例・値のラベル・"
                "軸の体裁は種類ごとに決まっている。行の多い表のシートには付けず、グラフにする"
                "行と列だけの表を別のシートに置いて、そのシートに付ける。置かないなら null"
            ),
        },
    },
    "required": ["name", "columns"],
    "additionalProperties": False,
}
XLSX_CREATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "minLength": 1,
            "maxLength": TITLE_MAX_CHARS,
            "description": "ファイルの題（拡張子なし）。ファイル名になる",
        },
        "sheets": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_SHEETS,
            "items": _SHEET_SCHEMA,
            "description": (
                "シート（左から）。1 つのシートに 1 つの表を置く（グラフは 1 つのシートに 1 つ"
                "まで）"
            ),
        },
    },
    "required": ["title", "sheets"],
    "additionalProperties": False,
}
XLSX_CREATE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file": FILE_OUTPUT_SCHEMA,
        "sheets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "rows": {"type": "integer"},
                    "columns": {"type": "integer"},
                    "summary_rows": {"type": "integer"},
                },
            },
        },
        "charts": {
            "type": "array",
            "description": (
                "グラフごとの、置いたシートの名前・種類・系列の数・項目の数（シートの順）"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "sheet": {"type": "string"},
                    "type": {"type": "string", "enum": list(CHART_TYPES)},
                    "series": {"type": "integer"},
                    "categories": {"type": "integer"},
                },
            },
        },
    },
}

XLSX_CREATE_DEFINITION = ToolDefinition(
    name=XLSX_CREATE_TOOL,
    title="Excel のファイルの作成",
    description=(
        "Excel のファイル（.xlsx）を新しく作る。利用者が Excel・表計算のファイルでの出力を求めた"
        "ときに使う。\n\n"
        "## Step 1: 内容を引数にまとめる\n"
        "- 1 つのシートに 1 つの表を置く。表の値は、取得済みの表の参照（source）か、引数に書いた"
        "行（rows）のどちらかで渡す。\n"
        "- 作れるのは、表・題と説明の文・数値と日付の書式・列の幅・集計の行・グラフ（"
        + "・".join(CHART_TYPES.values())
        + "）。字体と表・グラフの体裁は決まっていて、変えられない。画像・ピボット・マクロ・"
        "条件付き書式・ほかの種類のグラフ（積み上げ・2 軸・散布図など）と、既存のファイルの編集は"
        "できない。\n\n"
        "## Step 2: 結果を確かめる\n"
        "作ったファイルは利用者の画面に表示され、利用者がダウンロードできる。結果の file.file_name "
        f"がファイル名。1 回の実行で作れるファイルは {MAX_FILES_PER_RUN} 個まで。"
    ),
    input_schema=XLSX_CREATE_INPUT_SCHEMA,
    output_schema=XLSX_CREATE_OUTPUT_SCHEMA,
    # Run の成果物を作るだけで、外部へ書かない（`record_output` と同じ宣言。既定は承認なし）。
    side_effects=False,
    timeout_seconds=30.0,
    audit_tags=["output", "file"],
)


def _sheets(arguments: Mapping[str, Any], run: Any) -> list[Sheet]:
    """引数を生成の内容にする（参照した表の値を写す）。誤りは `ExternalToolError`。"""
    issues: list[SpecIssue] = []
    index = run_data_index(run, "xlsx.source_unavailable")
    sheets: list[Sheet] = []
    for sheet_index, raw in enumerate(arguments.get("sheets") or []):
        at = f"sheets.{sheet_index}"
        values = table_values(at, raw, index, "シート", issues)
        if values is None:
            continue
        names, rows = values
        columns = [
            Column(
                header=column_header(
                    f"{at}.columns.{column_index}",
                    column,
                    names[column_index] if names else None,
                    "シート",
                    issues,
                ),
                type=column["type"],
                format=column.get("format"),
                width=column.get("width"),
            )
            for column_index, column in enumerate(raw.get("columns") or [])
        ]
        chart = raw.get("chart")
        sheets.append(
            Sheet(
                name=str(raw.get("name") or ""),
                heading=raw.get("heading"),
                notes=tuple(raw.get("notes") or ()),
                columns=columns,
                rows=rows,
                summaries=tuple(
                    Summary(
                        function=item["function"],
                        columns=tuple(item.get("columns") or ()),
                        label=item.get("label"),
                    )
                    for item in raw.get("summaries") or ()
                ),
                chart=None if chart is None else chart_spec(chart),
            )
        )
    if issues:
        raise invalid_content(issues)
    return sheets


def _create_xlsx(arguments: dict[str, Any], context: ToolInvocationContext) -> dict[str, Any]:
    def build(run: Any) -> tuple[str, bytes, dict[str, Any]]:
        built = build_workbook(_sheets(arguments, run))
        summary = {"sheets": list(built.sheets), "charts": list(built.charts)}
        return str(arguments.get("title") or ""), built.data, summary

    return create_run_file(
        context.run_id, mime_type=XLSX_MIME_TYPE, spec_errors=(XlsxSpecError,), build=build
    )


def xlsx_create_tool() -> tuple[ToolDefinition, ToolHandler]:
    """`tool_registry` に登録する `xlsx_create`（定義と handler）。"""
    return XLSX_CREATE_DEFINITION, _create_xlsx


__all__ = [
    "FILE_LIMIT_CODE",
    "MAX_FILES_PER_RUN",
    "MAX_WRITTEN_ROWS",
    "XLSX_CREATE_DEFINITION",
    "XLSX_CREATE_INPUT_SCHEMA",
    "XLSX_CREATE_TOOL",
    "xlsx_create_tool",
]
