"""Word のファイル（.docx）を作る Control Plane のツール `docx_create`（#5837）。

- 渡し方: スキルが `control-plane:docx_create` を宣言した業務 Agent の Run にだけ渡る（業務 Agent・
  Run に暗黙に足さない。不変条件 1）。実行は `tool_registry.invoke`（ツール権限・監査）を通る。
  Run の中に成果物を作るだけで外部へ書かないので、既定では承認が要らない（`xlsx_create` と同じく
  副作用なしの宣言。管理者はツール権限で承認・拒否に変えられる）。
- 引数: モデルは構造化した内容（題・副題と、上から順のブロック）を渡す。ブロックは 1 つの形の
  object で、`type`（見出し・段落・箇条書き・番号付きのリスト・表・グラフ・改ページ）ごとに使う
  項目が決まる（`_BLOCK_FIELDS`。使わない項目に値があれば、黙って捨てずに引数の誤りにする）。
- 表とグラフの値は 2 つの出所のどちらか（`xlsx_create` と同じ。`file_tool_support`）。
  - `source`: その Run で取得済みの表（NL2SQL の結果）の参照。値は保存した step の結果からここで
    写す（モデルは値を書かない）。無い参照・範囲外は引数の誤りとして返す。
  - `rows`: モデルが書いた小さい表。
  グラフの値は、必ずこの表から取る（どの列を項目・系列にするかだけを指定する。`chart_spec`。
  `pptx_create` のグラフのスライドと同じ形）。
- 生成の本体は `docx_document.build_document`（Run に依らない）。体裁は 1 つに決まっていて、引数で
  変えられない。保存は `artifact_file_store.save_tool_file`（参照だけを返す。大きさ・形式の検査も
  そこ）。結果・イベント・ログにファイルの中身を載せない。
- 上限: 1 つの Run で作れるファイルは `MAX_FILES_PER_RUN` 個（形式を問わない）。内容の上限は
  `docx_document` と `chart_spec`。

ツールの層（`tools.py`）からは import しない（Run の module を読むため）。`tool_registry` への登録は
組み込み Runtime（`builtin_runtime`）がする。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.features.agent.answer_data import DataIndex
from app.features.agent.chart_spec import (
    CHART_SCHEMA,
    CHART_TYPES,
    MAX_CHART_CATEGORIES,
)
from app.features.agent.docx_document import (
    BLOCK_TYPES,
    DOCX_MIME_TYPE,
    MAX_BLOCKS,
    MAX_HEADING_LEVEL,
    MAX_LIST_ITEMS,
    MAX_TABLE_COLUMNS,
    Block,
    ChartBlock,
    DocumentContent,
    DocxSpecError,
    HeadingBlock,
    ListBlock,
    ListItem,
    PageBreakBlock,
    ParagraphBlock,
    TableBlock,
    build_document,
)
from app.features.agent.file_tool_support import (
    FILE_LIMIT_CODE,
    FILE_OUTPUT_SCHEMA,
    MAX_FILES_PER_RUN,
    TEXT_COLUMN_SCHEMA,
    chart_spec,
    create_run_file,
    field_issues,
    invalid_content,
    run_data_index,
    source_schema,
    text_table,
)
from app.features.agent.table_columns import SpecIssue
from app.features.agent.tools import ToolDefinition, ToolHandler, ToolInvocationContext

DOCX_CREATE_TOOL = "docx_create"
# モデルが引数に書く表（`rows`）の行の数。大きい表は取得済みの表の参照（`source`）で渡す。
MAX_WRITTEN_ROWS = 100
TITLE_MAX_CHARS = 100
SUBTITLE_MAX_CHARS = 200
HEADING_MAX_CHARS = 200
PARAGRAPH_MAX_CHARS = 4000
ITEM_MAX_CHARS = 500
CAPTION_MAX_CHARS = 200

# ブロックの種類ごとの、使う項目。
_BLOCK_FIELDS: dict[str, tuple[str, ...]] = {
    "heading": ("text", "level"),
    "paragraph": ("text",),
    "bullets": ("items",),
    "numbered": ("items",),
    "table": ("table",),
    "chart": ("table", "chart"),
    "page_break": (),
}
# 種類で分かれる項目と、使わないときの値の書き方。
_UNUSED_VALUES: dict[str, str] = {
    "text": "null",
    "level": "null",
    "items": "空の配列",
    "table": "null",
    "chart": "null",
}

_TABLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "caption": {
            "type": ["string", "null"],
            "maxLength": CAPTION_MAX_CHARS,
            "description": "表・グラフの上に置く題。置かないなら null",
        },
        "columns": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_TABLE_COLUMNS,
            "items": TEXT_COLUMN_SCHEMA,
            "description": "表の列（左から）",
        },
        "source": source_schema("表"),
        "rows": {
            "type": "array",
            "maxItems": MAX_WRITTEN_ROWS,
            "items": {
                "type": "array",
                "maxItems": MAX_TABLE_COLUMNS,
                "items": {"type": ["string", "number", "null"]},
            },
            "description": (
                "source が null の表の行（1 行が columns と同じ数・順の値の配列。値の無い cell は "
                f"null）。chart のブロックは {MAX_CHART_CATEGORIES} 行まで。source を使う表では"
                "空の配列"
            ),
        },
    },
    "required": ["columns"],
    "additionalProperties": False,
}
_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "minLength": 1,
            "maxLength": ITEM_MAX_CHARS,
            "description": "項目の文。行頭の記号・番号は自動で付くので書かない",
        },
        "children": {
            "type": "array",
            "maxItems": MAX_LIST_ITEMS,
            "items": {"type": "string", "minLength": 1, "maxLength": ITEM_MAX_CHARS},
            "description": "この項目の 1 段下の項目の文（上から）。無ければ空の配列",
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}
_BLOCK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": list(BLOCK_TYPES),
            "description": (
                "ブロックの種類。heading=見出し / paragraph=段落 / bullets=箇条書き / "
                "numbered=番号付きのリスト / table=表 / chart=グラフ / page_break=改ページ（次の"
                "ブロックを新しいページから始める）"
            ),
        },
        "text": {
            "type": ["string", "null"],
            "maxLength": PARAGRAPH_MAX_CHARS,
            "description": (
                "heading の見出しの文（"
                f"{HEADING_MAX_CHARS} 文字まで）か、paragraph の段落の文。装飾の無い文で書く"
                "（文の中の改行は段落の区切りになる）。ほかの種類では null"
            ),
        },
        "level": {
            "type": ["integer", "null"],
            "minimum": 1,
            "maximum": MAX_HEADING_LEVEL,
            "description": "heading のレベル（1 が最も大きい見出し）。ほかの種類では null",
        },
        "items": {
            "type": "array",
            "maxItems": MAX_LIST_ITEMS,
            "items": _ITEM_SCHEMA,
            "description": "bullets・numbered の項目（上から）。ほかの種類では空の配列",
        },
        "table": {
            "anyOf": [_TABLE_SCHEMA, {"type": "null"}],
            "description": (
                "table の表か、chart のグラフの値を取る表（グラフの表は文書に出ず、グラフのデータに"
                "なる）。ほかの種類では null"
            ),
        },
        "chart": {
            "anyOf": [CHART_SCHEMA, {"type": "null"}],
            "description": (
                "chart のグラフの指定（種類と、table のどの列を項目・系列にするか）。大きさ・色・"
                "凡例・値のラベル・軸の体裁は種類ごとに決まっている。ほかの種類では null"
            ),
        },
    },
    "required": ["type"],
    "additionalProperties": False,
}
DOCX_CREATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "minLength": 1,
            "maxLength": TITLE_MAX_CHARS,
            "description": "文書の題（拡張子なし）。文書の先頭に入り、ファイル名にもなる",
        },
        "subtitle": {
            "type": ["string", "null"],
            "maxLength": SUBTITLE_MAX_CHARS,
            "description": "題の下に置く 1 行（日付・作成元など）。置かないなら null",
        },
        "blocks": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_BLOCKS,
            "items": _BLOCK_SCHEMA,
            "description": "本文（上から順のブロック）",
        },
    },
    "required": ["title", "subtitle", "blocks"],
    "additionalProperties": False,
}
DOCX_CREATE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file": FILE_OUTPUT_SCHEMA,
        "blocks": {
            "type": "object",
            "description": "ブロックの種類ごとの数",
            "properties": {name: {"type": "integer"} for name in BLOCK_TYPES},
        },
        "tables": {
            "type": "array",
            "description": "表ごとの行の数（見出しの行を除く）と列の数（文書に出る順）",
            "items": {
                "type": "object",
                "properties": {"rows": {"type": "integer"}, "columns": {"type": "integer"}},
            },
        },
        "charts": {
            "type": "array",
            "description": "グラフごとの種類・系列の数・項目の数（文書に出る順）",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(CHART_TYPES)},
                    "series": {"type": "integer"},
                    "categories": {"type": "integer"},
                },
            },
        },
    },
}

DOCX_CREATE_DEFINITION = ToolDefinition(
    name=DOCX_CREATE_TOOL,
    title="Word のファイルの作成",
    description=(
        "Word のファイル（.docx）を新しく作る。利用者が Word・文書のファイルでの出力を求めた"
        "ときに使う。\n\n"
        "## Step 1: 内容を引数にまとめる\n"
        "- 本文は、文書に出る順のブロック（blocks）で渡す。表とグラフの値は、取得済みの表の参照"
        "（source）か、引数に書いた行（rows）のどちらかで渡す。\n"
        "- 作れるのは、題と副題・見出し・段落・箇条書きと番号付きのリスト・表・グラフ（"
        + "・".join(CHART_TYPES.values())
        + "）・改ページ。体裁（A4 縦・余白・字体・ページ番号・グラフの大きさ）は決まっていて、"
        "変えられない。画像・図・ほかの種類のグラフ（積み上げ・2 軸・散布図など）・目次・"
        "ヘッダーとフッターの内容・脚注・コメント・変更履歴・段組み・横向き・文字の装飾（太字・"
        "色・リンクなど）・テンプレートと、既存のファイルの編集はできない。\n\n"
        "## Step 2: 結果を確かめる\n"
        "作ったファイルは利用者の画面に表示され、利用者がダウンロードできる。結果の file.file_name "
        f"がファイル名。1 回の実行で作れるファイルは {MAX_FILES_PER_RUN} 個まで。"
    ),
    input_schema=DOCX_CREATE_INPUT_SCHEMA,
    output_schema=DOCX_CREATE_OUTPUT_SCHEMA,
    # Run の成果物を作るだけで、外部へ書かない（`xlsx_create` と同じ宣言。既定は承認なし）。
    side_effects=False,
    timeout_seconds=30.0,
    audit_tags=["output", "file"],
)


def _table(
    at: str,
    raw: Mapping[str, Any],
    chart: Mapping[str, Any] | None,
    index: Callable[[], DataIndex],
    issues: list[SpecIssue],
) -> TableBlock | ChartBlock | None:
    """表の引数を生成の内容にする（参照した表の値を写す）。誤りなら None。

    `chart`（グラフの指定）があれば、その表から値を取るグラフのブロックにする。
    """
    resolved = text_table(at, raw, index, "表", issues)
    if resolved is None:
        return None
    columns, rows = resolved
    caption = raw.get("caption")
    if chart is None:
        return TableBlock(columns=columns, rows=rows, caption=caption)
    return ChartBlock(
        columns=columns,
        rows=rows,
        chart=chart_spec(chart),
        caption=caption,
    )


def _content(arguments: Mapping[str, Any], run: Any) -> DocumentContent:
    """引数を生成の内容にする（参照した表の値を写す）。誤りは `ExternalToolError`。"""
    issues: list[SpecIssue] = []
    index = run_data_index(run, "docx.source_unavailable")
    blocks: list[Block] = []
    for block_index, raw in enumerate(arguments.get("blocks") or []):
        at = f"blocks.{block_index}"
        wrong = field_issues(
            at,
            raw,
            unit="ブロック",
            target="文書",
            required=_BLOCK_FIELDS[str(raw.get("type"))],
            optional=(),
            unused_values=_UNUSED_VALUES,
        )
        if wrong:
            issues.extend(wrong)
            continue
        kind = raw["type"]
        if kind == "heading":
            if len(raw["text"]) > HEADING_MAX_CHARS:
                issues.append(
                    SpecIssue(
                        f"{at}.text",
                        "too_long",
                        f"見出しは {HEADING_MAX_CHARS} 文字までです。長い文は paragraph に"
                        "書いてください。",
                    )
                )
                continue
            blocks.append(HeadingBlock(level=raw["level"], text=raw["text"]))
        elif kind == "paragraph":
            blocks.append(ParagraphBlock(text=raw["text"]))
        elif kind in ("bullets", "numbered"):
            blocks.append(
                ListBlock(
                    ordered=kind == "numbered",
                    items=[
                        ListItem(text=item["text"], children=tuple(item.get("children") or ()))
                        for item in raw["items"]
                    ],
                )
            )
        elif kind in ("table", "chart"):
            chart = raw["chart"] if kind == "chart" else None
            table = _table(f"{at}.table", raw["table"], chart, index, issues)
            if table is not None:
                blocks.append(table)
        else:
            blocks.append(PageBreakBlock())
    if issues:
        raise invalid_content(issues)
    return DocumentContent(
        title=str(arguments.get("title") or ""),
        subtitle=arguments.get("subtitle"),
        blocks=blocks,
    )


def _create_docx(arguments: dict[str, Any], context: ToolInvocationContext) -> dict[str, Any]:
    def build(run: Any) -> tuple[str, bytes, dict[str, Any]]:
        content = _content(arguments, run)
        built = build_document(content)
        summary = {
            "blocks": dict(built.blocks),
            "tables": list(built.tables),
            "charts": list(built.charts),
        }
        return content.title, built.data, summary

    return create_run_file(
        context.run_id, mime_type=DOCX_MIME_TYPE, spec_errors=(DocxSpecError,), build=build
    )


def docx_create_tool() -> tuple[ToolDefinition, ToolHandler]:
    """`tool_registry` に登録する `docx_create`（定義と handler）。"""
    return DOCX_CREATE_DEFINITION, _create_docx


__all__ = [
    "DOCX_CREATE_DEFINITION",
    "DOCX_CREATE_INPUT_SCHEMA",
    "DOCX_CREATE_TOOL",
    "FILE_LIMIT_CODE",
    "MAX_FILES_PER_RUN",
    "MAX_WRITTEN_ROWS",
    "docx_create_tool",
]
