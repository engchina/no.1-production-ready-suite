"""ファイルを作るツール（`xlsx_create`・`docx_create`・`pptx_create`・`markdown_create`）の共通の部品
（#5837・#5867）。

形式に依らない所だけを置く。生成の本体（`xlsx_workbook`・`docx_document`・`pptx_presentation`）は
Run に依らず、ここは Run を読む（ツールの層 `tools.py` からは import しない）。

- 表の参照（`source`）: その Run で取得済みの表（NL2SQL の結果）を、回答の表の参照
  （`answer_data`）と同じ番号（`T1`）と行・列の指定で選ぶ。値は保存した step の結果から写す
  （モデルは値を書かない）。無い参照・範囲外は引数の誤り（`SpecIssue`）にする。
- 1 つの Run で作れるファイルの数の上限（形式を問わず、その Run のファイルの成果物の数）と、
  数の確認から保存までを 1 つずつにする lock。
- 内容の誤りの返し方（`tool.invalid_arguments` と `argument_errors`。値を含まない）。
- 値を文字として入れる表（Word・PowerPoint）の、列の引数の schema と、引数から列の定義と行の値を
  決める所（`TEXT_COLUMN_SCHEMA`・`text_table`）。
- 種類で使う項目が決まる引数（Word のブロック・PowerPoint のスライド）の、要る項目の欠け・使わない
  項目の値の誤り（`field_issues`）と、グラフの指定（`chart_spec`）。
- ツールの handler の流れ（Run の確認・数の上限・内容の生成・保存。`create_run_file`）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from threading import Lock
from typing import Any, Protocol, cast

from app.features.agent.answer_data import DataIndex, DataReferenceError, data_index
from app.features.agent.answer_data_projection import TableProjectionError, selected_positions
from app.features.agent.artifact_file_store import (
    FILE_ARTIFACT_KIND,
    FILE_RUN_CLOSED,
    save_tool_file,
)
from app.features.agent.chart_spec import ChartSpec
from app.features.agent.table_columns import DATE_FORMATS, NUMBER_FORMATS, SpecIssue
from app.features.agent.table_text import Column
from app.features.agent.tool_arguments import INVALID_ARGUMENTS_CODE
from app.features.agent.tools import ExternalToolError

# 1 つの Run で作れるファイルの数（形式を問わず、その Run のファイルの成果物の数）。
MAX_FILES_PER_RUN = 10
FILE_LIMIT_CODE = "artifact.file_limit"
INVALID_CONTENT_MESSAGE = (
    "引数の内容に誤りがあるため、ファイルを作っていません。"
    "argument_errors の場所と直し方に従って引数を直し、呼び直してください。"
)

# 引数の説明に使う文（書式の名前の一覧、行・列の選び方）。
FORMAT_NAMES_HELP = (
    "数値の列は "
    + " / ".join(f"{name}（{code}）" for name, code in NUMBER_FORMATS.items())
    + "、日付の列は "
    + " / ".join(f"{name}（{code}）" for name, code in DATE_FORMATS.items())
)
SELECTOR_HELP = "* はすべて、1-8 は範囲、1,3,5 は列挙（書いた順に並ぶ）。番号は 1 始まり"

# 値を文字として入れる表（Word・PowerPoint）の、列の引数の schema。
HEADER_MAX_CHARS = 100
TEXT_FORMAT_HELP = (
    FORMAT_NAMES_HELP + "。percent・percent1 は値を 100 倍して表示する（0.15 が 15%）。null は、"
    "数値が値のままの表示、日付が date（時刻のある値を含む列は datetime）。文字の列は null"
)
TEXT_COLUMN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "header": {
            "type": ["string", "null"],
            "maxLength": HEADER_MAX_CHARS,
            "description": (
                "列の見出し。source を使う表では null にすると、参照した表の列名になる"
            ),
        },
        "type": {
            "type": "string",
            "enum": ["text", "number", "date"],
            "description": (
                "列の値の型。text=文字 / number=数値 / date=日付・日時（rows に書くときは "
                "YYYY-MM-DD か ISO 8601 の日時の文字列）。number と date の列は右寄せになる"
            ),
        },
        "format": {
            "type": ["string", "null"],
            "enum": [*NUMBER_FORMATS, *DATE_FORMATS, None],
            "description": f"表示の書式。{TEXT_FORMAT_HELP}",
        },
    },
    "required": ["type"],
    "additionalProperties": False,
}

# 作ったファイルの参照（結果の `file`。中身は含まない）。
FILE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "作ったファイル（中身は含まない）",
    "properties": {
        "artifact_id": {"type": "string"},
        "file_name": {"type": "string"},
        "mime_type": {"type": "string"},
        "size_bytes": {"type": "integer"},
        "sha256": {"type": "string"},
    },
}

# ファイルの数の確認と保存を 1 つずつにする（並行の呼び出しで上限を超えない。形式をまたいで 1 つ）。
_SAVE_LOCK = Lock()


def source_schema(owner: str) -> dict[str, Any]:
    """表の参照（`source`）の引数の schema。`owner` は表を置く単位の呼び名（シート・表）。"""
    return {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": (
                            "表の番号。取得した結果の answer_data.table が [[table:T1]] なら T1"
                        ),
                    },
                    "rows": {
                        "type": "string",
                        "description": f"写す行。{SELECTOR_HELP}",
                    },
                    "columns": {
                        "type": "string",
                        "description": (
                            f"写す列。{SELECTOR_HELP}（answer_data.column_refs の番号）。"
                            f"選んだ列の数と順が、{owner}の columns と対応する"
                        ),
                    },
                },
                "required": ["table", "rows", "columns"],
                "additionalProperties": False,
            },
            {"type": "null"},
        ],
        "description": (
            "この実行で取得済みの表の参照（値はこのツールが取得した結果から写す）。"
            f"参照しない{owner}は null"
        ),
    }


def invalid_content(issues: Sequence[SpecIssue]) -> ExternalToolError:
    """内容の誤り（ファイルは作らない。モデルが場所と直し方を読んで呼び直す）。"""
    return ExternalToolError(
        INVALID_ARGUMENTS_CODE,
        INVALID_CONTENT_MESSAGE,
        {"argument_errors": [item.as_dict() for item in issues]},
    )


def running_run(run_id: str | None) -> Any:
    """ツールを呼んだ Run（Run の外・無い Run は `ExternalToolError`）。"""
    from app.features.agent.runtime import runtime_repository

    try:
        run = runtime_repository.get_run(run_id) if run_id else None
    except KeyError:
        run = None
    if run is None:
        raise ExternalToolError(FILE_RUN_CLOSED, "実行の外ではファイルを作れません。")
    return run


def run_data_index(run: Any, unavailable_code: str) -> Callable[[], DataIndex]:
    """その Run で取得済みの表の索引を返す関数（最初に使うときに読む）。

    表を参照しない呼び出しでは読まない。読めなければ `unavailable_code` のエラー。
    """
    loaded: list[DataIndex] = []

    def index() -> DataIndex:
        if not loaded:
            try:
                loaded.append(data_index(run.steps, run.metadata))
            except DataReferenceError as exc:
                raise ExternalToolError(unavailable_code, str(exc)) from exc
        return loaded[0]

    return index


def source_table(
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


def table_values(
    at: str,
    raw: Mapping[str, Any],
    index: Callable[[], DataIndex],
    owner: str,
    issues: list[SpecIssue],
) -> tuple[list[str] | None, list[list[Any]]] | None:
    """表の引数（`columns`・`source`・`rows`）から、行の値を決める。誤りなら None。

    `source` があれば参照した表の値（と、選んだ列の名前）、無ければ引数に書いた `rows`（列の名前は
    None）。`owner` は表を置く単位の呼び名（シート・表）。
    """
    rows: list[list[Any]] = [list(row) for row in raw.get("rows") or []]
    source = raw.get("source")
    if source is None:
        return None, rows
    if rows:
        issues.append(
            SpecIssue(
                f"{at}.rows",
                "conflict",
                f"source を使う{owner}では rows を空の配列にしてください。",
            )
        )
    resolved = source_table(f"{at}.source", source, index(), issues)
    if resolved is None:
        return None
    names, rows = resolved
    if len(names) != len(raw.get("columns") or []):
        issues.append(
            SpecIssue(
                f"{at}.columns",
                "count",
                f"columns の数を、source.columns で選んだ列の数（{len(names)}）に"
                "そろえてください。",
            )
        )
        return None
    return names, rows


def column_header(
    at: str,
    column: Mapping[str, Any],
    name: str | None,
    owner: str,
    issues: list[SpecIssue],
) -> str:
    """列の見出し（引数に無ければ、参照した表の列名 `name`。どちらも無ければ誤り）。"""
    header = column.get("header") or name
    if header is None:
        issues.append(
            SpecIssue(
                f"{at}.header",
                "required",
                f"source を使わない{owner}では、列の見出しを書いてください。",
            )
        )
        return ""
    return str(header)


def text_table(
    at: str,
    raw: Mapping[str, Any],
    index: Callable[[], DataIndex],
    owner: str,
    issues: list[SpecIssue],
) -> tuple[list[Column], list[list[Any]]] | None:
    """値を文字として入れる表の引数から、列の定義と行の値を決める（参照した表の値を写す）。

    誤りなら None。列の見出しが引数に無い列は、参照した表の列名にする。
    """
    values = table_values(at, raw, index, owner, issues)
    if values is None:
        return None
    names, rows = values
    columns = [
        Column(
            header=column_header(
                f"{at}.columns.{column_index}",
                column,
                names[column_index] if names else None,
                owner,
                issues,
            ),
            type=column["type"],
            format=column.get("format"),
        )
        for column_index, column in enumerate(raw.get("columns") or [])
    ]
    return columns, rows


def chart_spec(raw: Mapping[str, Any]) -> ChartSpec:
    """グラフの引数（種類・項目の列・系列の列）を、形式に依らないグラフの指定にする。"""
    return ChartSpec(
        type=raw["type"],
        category_column=raw["category_column"],
        series_columns=tuple(raw["series_columns"]),
    )


def field_issues(
    at: str,
    raw: Mapping[str, Any],
    *,
    unit: str,
    target: str,
    required: Sequence[str],
    optional: Sequence[str],
    unused_values: Mapping[str, str],
) -> list[SpecIssue]:
    """種類に合わない項目（要る項目が無い・使わない項目に値がある）。

    `unit` は種類で分かれる単位（「ブロック」「スライド」）、`target` は作るもの（「文書」）。
    `unused_values` は種類で分かれる項目と、使わないときの値の書き方。
    """
    kind = str(raw.get("type"))
    issues: list[SpecIssue] = []
    for name, unused_value in unused_values.items():
        present = raw.get(name) not in (None, "", [])
        if name in required and not present:
            issues.append(
                SpecIssue(
                    f"{at}.{name}",
                    "required",
                    f"type が {kind} の{unit}では {name} を書いてください。",
                )
            )
        elif name not in required and name not in optional and present:
            issues.append(
                SpecIssue(
                    f"{at}.{name}",
                    "conflict",
                    f"type が {kind} の{unit}では {name} を{unused_value}にしてください"
                    f"（この項目の内容は{target}に入りません）。",
                )
            )
    return issues


def check_file_count(run: Any, limit: int) -> None:
    """その Run のファイルの成果物が `limit` 個に達していたら `ExternalToolError`。"""
    count = sum(1 for artifact in run.artifacts if artifact.kind == FILE_ARTIFACT_KIND)
    if count >= limit:
        raise ExternalToolError(
            FILE_LIMIT_CODE,
            f"1 回の実行で作れるファイルは {limit} 個までです。"
            "これ以上は作らず、作ったファイルで回答してください。",
        )


def save_run_file(
    run_id: str, limit: int, *, file_name: str, mime_type: str, data: bytes
) -> dict[str, Any]:
    """ファイルの数を確かめてから、Run の成果物として保存する（成果物の参照を返す）。"""
    from app.features.agent.runtime import runtime_repository

    with _SAVE_LOCK:
        check_file_count(runtime_repository.get_run(run_id), limit)
        return save_tool_file(run_id, file_name=file_name, mime_type=mime_type, data=data)


class _SpecError(Protocol):
    issues: Sequence[SpecIssue]


def create_run_file(
    run_id: str | None,
    *,
    mime_type: str,
    build: Callable[[Any], tuple[str, bytes, dict[str, Any]]],
    spec_errors: tuple[type[Exception], ...] = (),
) -> dict[str, Any]:
    """ファイルを作るツールの handler の流れ（Run の確認 → 数の上限 → 生成 → 保存）。

    `build(run)` はファイル名・中身・結果の要約を返す。`spec_errors`（生成の本体の内容の誤り。
    `issues` を持つ）は引数の誤りにする。上限は呼ぶたびに `MAX_FILES_PER_RUN` を読む（形式を
    またいで同じ値）。結果は成果物の参照 `file` と要約。
    """
    run = running_run(run_id)
    limit = MAX_FILES_PER_RUN
    check_file_count(run, limit)
    try:
        file_name, data, summary = build(run)
    except spec_errors as exc:
        error = cast(_SpecError, exc)
        raise invalid_content(error.issues) from exc
    reference = save_run_file(run.id, limit, file_name=file_name, mime_type=mime_type, data=data)
    return {"file": reference, **summary}


__all__ = [
    "FILE_LIMIT_CODE",
    "FILE_OUTPUT_SCHEMA",
    "FORMAT_NAMES_HELP",
    "HEADER_MAX_CHARS",
    "INVALID_CONTENT_MESSAGE",
    "MAX_FILES_PER_RUN",
    "SELECTOR_HELP",
    "TEXT_COLUMN_SCHEMA",
    "TEXT_FORMAT_HELP",
    "chart_spec",
    "check_file_count",
    "column_header",
    "create_run_file",
    "field_issues",
    "invalid_content",
    "run_data_index",
    "running_run",
    "save_run_file",
    "source_schema",
    "source_table",
    "table_values",
    "text_table",
]
