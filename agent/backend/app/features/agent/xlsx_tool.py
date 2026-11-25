"""Excel のファイル（.xlsx）を作る Control Plane のツール `xlsx_create`（#5837）。

- 渡し方: スキルが `control-plane:xlsx_create` を宣言した業務 Agent の Run にだけ渡る（業務 Agent・
  Run に暗黙に足さない。不変条件 1）。実行は `tool_registry.invoke`（ツール権限・監査）を通る。
  Run の中に成果物を作るだけで外部へ書かないので、既定では承認が要らない（`record_output` と同じく
  副作用なしの宣言。管理者はツール権限で承認・拒否に変えられる）。
- 引数: モデルは構造化した内容（題・シート・列・集計）を渡す。表の値は 2 つの出所のどちらか。
  - `source`: その Run で取得済みの表（NL2SQL の結果）の参照。回答の表の参照（`answer_data`）と同じ
    番号（`T1`）と行・列の指定で、値は保存した step の結果からここで写す（モデルは値を書かない）。
    無い参照・範囲外は引数の誤りとして返す。
  - `rows`: モデルが書いた小さい表。
- 生成の本体は `xlsx_workbook.build_workbook`（Run に依らない）。保存は
  `artifact_file_store.save_tool_file`（参照だけを返す。大きさ・形式の検査もそこ）。結果・イベント・
  ログにファイルの中身を載せない。
- 上限: 1 つの Run で作れるファイルは `MAX_FILES_PER_RUN` 個。内容の上限は `xlsx_workbook`。

ツールの層（`tools.py`）からは import しない（Run の module を読むため）。`tool_registry` への登録は
組み込み Runtime（`builtin_runtime`）がする。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from threading import Lock
from typing import Any

from app.features.agent.answer_data import DataIndex, DataReferenceError, data_index
from app.features.agent.answer_data_projection import TableProjectionError, selected_positions
from app.features.agent.artifact_file_store import (
    FILE_ARTIFACT_KIND,
    FILE_RUN_CLOSED,
    save_tool_file,
)
from app.features.agent.tool_arguments import INVALID_ARGUMENTS_CODE
from app.features.agent.tools import (
    ExternalToolError,
    ToolDefinition,
    ToolHandler,
    ToolInvocationContext,
)
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
# 1 つの Run で作れるファイルの数（形式を問わず、その Run のファイルの成果物の数）。
MAX_FILES_PER_RUN = 10
# モデルが引数に書く表（`rows`）の行の数。大きい表は取得済みの表の参照（`source`）で渡す。
MAX_WRITTEN_ROWS = 200
TITLE_MAX_CHARS = 100
HEADING_MAX_CHARS = 200
NOTE_MAX_CHARS = 500
HEADER_MAX_CHARS = 100
FILE_LIMIT_CODE = "artifact.file_limit"
INVALID_CONTENT_MESSAGE = (
    "引数の内容に誤りがあるため、ファイルを作っていません。"
    "argument_errors の場所と直し方に従って引数を直し、呼び直してください。"
)

_FORMAT_HELP = (
    "数値の列は "
    + " / ".join(f"{name}（{code}）" for name, code in NUMBER_FORMATS.items())
    + "、日付の列は "
    + " / ".join(f"{name}（{code}）" for name, code in DATE_FORMATS.items())
    + "。percent・percent1 は値を 100 倍して表示する（0.15 が 15%）。null は、数値が Excel の"
    "標準の表示、日付が date（時刻のある値を含む列は datetime）。文字の列は null"
)
_SELECTOR_HELP = "* はすべて、1-8 は範囲、1,3,5 は列挙（書いた順に並ぶ）。番号は 1 始まり"

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
_SOURCE_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "table": {
            "type": "string",
            "description": "表の番号。取得した結果の answer_data.table が [[table:T1]] なら T1",
        },
        "rows": {
            "type": "string",
            "description": f"写す行。{_SELECTOR_HELP}",
        },
        "columns": {
            "type": "string",
            "description": (
                f"写す列。{_SELECTOR_HELP}（answer_data.column_refs の番号）。"
                "選んだ列の数と順が、シートの columns と対応する"
            ),
        },
    },
    "required": ["table", "rows", "columns"],
    "additionalProperties": False,
}
_SOURCE_SCHEMA: dict[str, Any] = {
    "anyOf": [_SOURCE_OBJECT_SCHEMA, {"type": "null"}],
    "description": (
        "この実行で取得済みの表の参照（値はこのツールが取得した結果から写す）。"
        "参照しないシートは null"
    ),
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
        "source": _SOURCE_SCHEMA,
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
            "description": "シート（左から）。1 つのシートに 1 つの表を置く",
        },
    },
    "required": ["title", "sheets"],
    "additionalProperties": False,
}
XLSX_CREATE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file": {
            "type": "object",
            "description": "作ったファイル（中身は含まない）",
            "properties": {
                "artifact_id": {"type": "string"},
                "file_name": {"type": "string"},
                "mime_type": {"type": "string"},
                "size_bytes": {"type": "integer"},
                "sha256": {"type": "string"},
            },
        },
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
        "- 作れるのは、表・題と説明の文・数値と日付の書式・列の幅・集計の行。グラフ・画像・"
        "ピボット・マクロ・条件付き書式と、既存のファイルの編集はできない。\n\n"
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

# ファイルの数の確認と保存を 1 つずつにする（並行の呼び出しで上限を超えない）。
_SAVE_LOCK = Lock()


def _invalid(issues: Sequence[SpecIssue]) -> ExternalToolError:
    return ExternalToolError(
        INVALID_ARGUMENTS_CODE,
        INVALID_CONTENT_MESSAGE,
        {"argument_errors": [item.as_dict() for item in issues]},
    )


def _source_table(
    at: str, source: Mapping[str, Any], index: DataIndex, issues: list[SpecIssue]
) -> tuple[list[str], list[list[Any]]] | None:
    """参照した表の、選んだ列の名前と行の値（保存した結果の値のまま）。誤りなら None。"""
    alias = str(source.get("table") or "").strip().removeprefix("[[table:").removesuffix("]]")
    table = index.tables.get(index.aliases.get(alias, ""))
    if table is None:
        issues.append(
            SpecIssue(
                f"{at}.table",
                "unknown_table",
                "この実行に無い表の参照です。取得した結果の answer_data.table の番号（T1 など）を"
                "書いてください。",
            )
        )
        return None
    names, rows = table
    selected: dict[str, list[int]] = {}
    for key, count in (("rows", len(rows)), ("columns", len(names))):
        try:
            selected[key] = selected_positions(str(source.get(key) or ""), count)
        except TableProjectionError as exc:
            issues.append(SpecIssue(f"{at}.{key}", "selection", str(exc)))
    if len(selected) != 2:
        return None
    columns = [names[position] for position in selected["columns"]]
    return columns, [
        [rows[position].get(column) for column in columns] for position in selected["rows"]
    ]


def _sheets(arguments: Mapping[str, Any], run: Any) -> list[Sheet]:
    """引数を生成の内容にする（参照した表の値を写す）。誤りは `ExternalToolError`。"""
    issues: list[SpecIssue] = []
    index: DataIndex | None = None
    sheets: list[Sheet] = []
    for sheet_index, raw in enumerate(arguments.get("sheets") or []):
        at = f"sheets.{sheet_index}"
        raw_columns = list(raw.get("columns") or [])
        source = raw.get("source")
        rows: list[list[Any]] = [list(row) for row in raw.get("rows") or []]
        names: list[str] | None = None
        if source is not None:
            if rows:
                issues.append(
                    SpecIssue(
                        f"{at}.rows",
                        "conflict",
                        "source を使うシートでは rows を空の配列にしてください。",
                    )
                )
            if index is None:
                try:
                    index = data_index(run.steps, run.metadata)
                except DataReferenceError as exc:
                    raise ExternalToolError("xlsx.source_unavailable", str(exc)) from exc
            resolved = _source_table(f"{at}.source", source, index, issues)
            if resolved is None:
                continue
            names, rows = resolved
            if len(names) != len(raw_columns):
                issues.append(
                    SpecIssue(
                        f"{at}.columns",
                        "count",
                        f"columns の数を、source.columns で選んだ列の数（{len(names)}）に"
                        "そろえてください。",
                    )
                )
                continue
        columns: list[Column] = []
        for column_index, column in enumerate(raw_columns):
            header = column.get("header") or (names[column_index] if names else None)
            if header is None:
                issues.append(
                    SpecIssue(
                        f"{at}.columns.{column_index}.header",
                        "required",
                        "source を使わないシートでは、列の見出しを書いてください。",
                    )
                )
                header = ""
            columns.append(
                Column(
                    header=header,
                    type=column["type"],
                    format=column.get("format"),
                    width=column.get("width"),
                )
            )
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
            )
        )
    if issues:
        raise _invalid(issues)
    return sheets


def _check_file_count(run: Any) -> None:
    count = sum(1 for artifact in run.artifacts if artifact.kind == FILE_ARTIFACT_KIND)
    if count >= MAX_FILES_PER_RUN:
        raise ExternalToolError(
            FILE_LIMIT_CODE,
            f"1 回の実行で作れるファイルは {MAX_FILES_PER_RUN} 個までです。"
            "これ以上は作らず、作ったファイルで回答してください。",
        )


def _create_xlsx(arguments: dict[str, Any], context: ToolInvocationContext) -> dict[str, Any]:
    from app.features.agent.runtime import runtime_repository

    run_id = context.run_id
    try:
        run = runtime_repository.get_run(run_id) if run_id else None
    except KeyError:
        run = None
    if run is None or not run_id:
        raise ExternalToolError(FILE_RUN_CLOSED, "実行の外ではファイルを作れません。")
    _check_file_count(run)
    sheets = _sheets(arguments, run)
    try:
        built = build_workbook(sheets)
    except XlsxSpecError as exc:
        raise _invalid(exc.issues) from exc
    with _SAVE_LOCK:
        _check_file_count(runtime_repository.get_run(run_id))
        reference = save_tool_file(
            run_id,
            file_name=str(arguments.get("title") or ""),
            mime_type=XLSX_MIME_TYPE,
            data=built.data,
        )
    return {"file": reference, "sheets": list(built.sheets)}


def xlsx_create_tool() -> tuple[ToolDefinition, ToolHandler]:
    """`tool_registry` に登録する `xlsx_create`（定義と handler）。"""
    return XLSX_CREATE_DEFINITION, _create_xlsx


__all__ = [
    "MAX_FILES_PER_RUN",
    "MAX_WRITTEN_ROWS",
    "XLSX_CREATE_DEFINITION",
    "XLSX_CREATE_INPUT_SCHEMA",
    "XLSX_CREATE_TOOL",
    "xlsx_create_tool",
]
