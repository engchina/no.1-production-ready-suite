"""PowerPoint のファイル（.pptx）を作る Control Plane のツール `pptx_create`（#5837）。

- 渡し方: スキルが `control-plane:pptx_create` を宣言した業務 Agent の Run にだけ渡る（業務 Agent・
  Run に暗黙に足さない。不変条件 1）。実行は `tool_registry.invoke`（ツール権限・監査）を通る。
  Run の中に成果物を作るだけで外部へ書かないので、既定では承認が要らない（`xlsx_create`・
  `docx_create` と同じく副作用なしの宣言。管理者はツール権限で承認・拒否に変えられる）。
- 引数: モデルは構造化した内容（ファイルの題と、ファイルに出る順のスライド）を渡す。スライドは
  1 つの形の object で、`type`（表紙・章の扉・要点の箇条書き・表・左右の対比・グラフ）ごとに使う
  項目が決まる（`_SLIDE_FIELDS`。使わない項目に値があれば、黙って捨てずに引数の誤りにする）。
- 表とグラフの値は 2 つの出所のどちらか（ほかの形式のツールと同じ。`file_tool_support`）。
  - `source`: その Run で取得済みの表（NL2SQL の結果）の参照。値は保存した step の結果からここで
    写す（モデルは値を書かない）。無い参照・範囲外は引数の誤りとして返す。
  - `rows`: モデルが書いた小さい表。
  グラフの値は、必ずこの表から取る（どの列を項目・系列にするかだけを指定する。`chart_spec`）。
- 生成の本体は `pptx_presentation.build_presentation`（Run に依らない）。体裁は 1 つに決まっていて、
  引数で変えられない。保存は `artifact_file_store.save_tool_file`（参照だけを返す。大きさ・形式の
  検査もそこ）。結果・イベント・ログにファイルの中身を載せない。
- 上限: 1 つの Run で作れるファイルは `MAX_FILES_PER_RUN` 個（形式を問わない）。内容の上限は
  `pptx_presentation` と `chart_spec`。1 枚に収まる量の上限（題・項目の文字数と数、表の行と列）は、
  schema の制約ではなく生成の本体が確かめる（誤りに、スライドを分けるなどの直し方を書くため）。

ツールの層（`tools.py`）からは import しない（Run の module を読むため）。`tool_registry` への登録は
組み込み Runtime（`builtin_runtime`）がする。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.features.agent.chart_spec import (
    CHART_SCHEMA,
    CHART_TYPES,
    MAX_CHART_CATEGORIES,
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
from app.features.agent.pptx_presentation import (
    BULLET_ITEM_MAX_CHARS,
    COMPARISON_HEADING_MAX_CHARS,
    COMPARISON_ITEM_MAX_CHARS,
    CONTINUED_SUFFIX,
    MAX_BULLET_ITEMS,
    MAX_COMPARISON_ITEMS,
    MAX_SLIDES,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    MAX_TABLE_ROWS_PER_SLIDE,
    NOTES_MAX_CHARS,
    PPTX_MIME_TYPE,
    SLIDE_TYPES,
    SUBTITLE_MAX_CHARS,
    TITLE_MAX_CHARS,
    BulletItem,
    BulletsSlide,
    ChartSlide,
    ComparisonColumn,
    ComparisonSlide,
    CoverSlide,
    PptxSpecError,
    PresentationContent,
    SectionSlide,
    Slide,
    TableSlide,
    build_presentation,
)
from app.features.agent.table_columns import SpecIssue
from app.features.agent.tools import ToolDefinition, ToolHandler, ToolInvocationContext

PPTX_CREATE_TOOL = "pptx_create"
FILE_TITLE_MAX_CHARS = 100

# スライドの種類ごとの、要る項目と、書いてもよい項目（`title`・`notes` はどの種類でも使う）。
_SLIDE_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "cover": ((), ("subtitle",)),
    "section": ((), ()),
    "bullets": (("items",), ()),
    "table": (("table",), ()),
    "comparison": (("left", "right"), ()),
    "chart": (("table", "chart"), ()),
}
# 種類で分かれる項目と、使わないときの値の書き方。
_UNUSED_VALUES: dict[str, str] = {
    "subtitle": "null",
    "items": "空の配列",
    "left": "null",
    "right": "null",
    "table": "null",
    "chart": "null",
}


def _item_schema(max_chars: int) -> dict[str, Any]:
    """箇条書きの 1 つの項目の schema（`max_chars` は 1 つの項目の文字数の上限）。"""
    return {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "minLength": 1,
                "description": (
                    f"項目の文（{max_chars} 文字まで）。行頭の記号は自動で付くので書かない"
                ),
            },
            "children": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "description": (
                    f"この項目の 1 段下の項目の文（上から。1 つ {max_chars} 文字まで）。"
                    "無ければ空の配列"
                ),
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    }


_TABLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "columns": {
            "type": "array",
            "minItems": 1,
            "items": TEXT_COLUMN_SCHEMA,
            "description": f"表の列（左から。{MAX_TABLE_COLUMNS} 列まで）",
        },
        "source": source_schema("表"),
        "rows": {
            "type": "array",
            "items": {
                "type": "array",
                "items": {"type": ["string", "number", "null"]},
            },
            "description": (
                "source が null の表の行（1 行が columns と同じ数・順の値の配列。値の無い cell は "
                f"null）。table のスライドは {MAX_TABLE_ROWS_PER_SLIDE} 行まで、chart のスライドは "
                f"{MAX_CHART_CATEGORIES} 行まで。source を使う表では空の配列"
            ),
        },
    },
    "required": ["columns"],
    "additionalProperties": False,
}
_COMPARISON_COLUMN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "heading": {
            "type": ["string", "null"],
            "description": (
                f"この列の小見出し（{COMPARISON_HEADING_MAX_CHARS} 文字まで）。置かないなら null"
            ),
        },
        "items": {
            "type": "array",
            "items": _item_schema(COMPARISON_ITEM_MAX_CHARS),
            "description": (
                f"この列の項目（上から。1 段下の項目を含めて {MAX_COMPARISON_ITEMS} 項目まで）"
            ),
        },
    },
    "required": ["heading", "items"],
    "additionalProperties": False,
}
_SLIDE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": list(SLIDE_TYPES),
            "description": (
                "スライドの種類。cover=表紙（題と副題） / section=章の扉（章の題だけ） / "
                "bullets=要点の箇条書き / table=表 / comparison=左右 2 列の対比 / chart=グラフ"
            ),
        },
        "title": {
            "type": "string",
            "minLength": 1,
            "description": (
                f"スライドの題（{TITLE_MAX_CHARS} 文字まで）。cover は資料の題、section は章の題"
            ),
        },
        "subtitle": {
            "type": ["string", "null"],
            "description": (
                f"cover の題の下に置く文（日付・作成元など。{SUBTITLE_MAX_CHARS} 文字まで）。"
                "置かないなら null。ほかの種類では null"
            ),
        },
        "items": {
            "type": "array",
            "items": _item_schema(BULLET_ITEM_MAX_CHARS),
            "description": (
                f"bullets の項目（上から。1 段下の項目を含めて {MAX_BULLET_ITEMS} 項目まで）。"
                "ほかの種類では空の配列"
            ),
        },
        "left": {
            "anyOf": [_COMPARISON_COLUMN_SCHEMA, {"type": "null"}],
            "description": "comparison の左の列。ほかの種類では null",
        },
        "right": {
            "anyOf": [_COMPARISON_COLUMN_SCHEMA, {"type": "null"}],
            "description": "comparison の右の列。ほかの種類では null",
        },
        "table": {
            "anyOf": [_TABLE_SCHEMA, {"type": "null"}],
            "description": (
                "table のスライドに出す表か、chart のグラフの値を取る表（グラフの表はスライドに"
                "出ず、グラフのデータになる）。table で source の表が "
                f"{MAX_TABLE_ROWS_PER_SLIDE} 行を超えるときは、題に「{CONTINUED_SUFFIX}」を付けた"
                f"次のスライドへ続く（合わせて {MAX_TABLE_ROWS} 行まで）。ほかの種類では null"
            ),
        },
        "chart": {
            "anyOf": [CHART_SCHEMA, {"type": "null"}],
            "description": (
                "chart のグラフの指定（種類と、table のどの列を項目・系列にするか）。色・凡例・"
                "値のラベル・軸の体裁は種類ごとに決まっている。ほかの種類では null"
            ),
        },
        "notes": {
            "type": ["string", "null"],
            "maxLength": NOTES_MAX_CHARS,
            "description": (
                "発表者ノート（スライドには出ない。装飾の無い文で、改行は段落の区切りになる）。"
                "置かないなら null"
            ),
        },
    },
    "required": ["type", "title"],
    "additionalProperties": False,
}
PPTX_CREATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "minLength": 1,
            "maxLength": FILE_TITLE_MAX_CHARS,
            "description": "ファイルの題（拡張子なし）。ファイル名になる",
        },
        "slides": {
            "type": "array",
            "minItems": 1,
            "items": _SLIDE_SCHEMA,
            "description": (
                f"スライド（ファイルに出る順。表の続きのスライドを含めて {MAX_SLIDES} 枚まで）"
            ),
        },
    },
    "required": ["title", "slides"],
    "additionalProperties": False,
}
PPTX_CREATE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file": FILE_OUTPUT_SCHEMA,
        "slides": {
            "type": "object",
            "description": "引数のスライドの、種類ごとの数",
            "properties": {name: {"type": "integer"} for name in SLIDE_TYPES},
        },
        "slide_count": {
            "type": "integer",
            "description": "ファイルのスライドの数（表の続きのスライドを含む）",
        },
        "continued_slides": {
            "type": "integer",
            "description": "表が 1 枚に収まらずに、自動で足した続きのスライドの数",
        },
        "tables": {
            "type": "array",
            "description": (
                "表ごとの行の数（見出しの行を除く）・列の数・使ったスライドの数（ファイルに出る順）"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "rows": {"type": "integer"},
                    "columns": {"type": "integer"},
                    "slides": {"type": "integer"},
                },
            },
        },
        "charts": {
            "type": "array",
            "description": "グラフごとの種類・系列の数・項目の数（ファイルに出る順）",
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

PPTX_CREATE_DEFINITION = ToolDefinition(
    name=PPTX_CREATE_TOOL,
    title="PowerPoint のファイルの作成",
    description=(
        "PowerPoint のファイル（.pptx）を新しく作る。利用者が PowerPoint・スライド・発表の資料の"
        "ファイルでの出力を求めたときに使う。\n\n"
        "## Step 1: 内容を引数にまとめる\n"
        "- スライドは、ファイルに出る順に slides で渡す。表とグラフの値は、取得済みの表の参照"
        "（source）か、引数に書いた行（rows）のどちらかで渡す。\n"
        "- 作れるのは、表紙・章の扉・要点の箇条書き・表・左右の対比・グラフ（"
        + "・".join(CHART_TYPES.values())
        + "）のスライドと、発表者ノート。体裁（16:9・字体・色・配置・スライド番号）は決まって"
        "いて、変えられない。1 枚に入る量は決まっていて、収まらない内容は文字を小さくせずに誤りに"
        "なる。画像・図形・SmartArt・アニメーションと画面の切り替え・マスターとテンプレートの選択・"
        "文字の装飾（太字・色・リンクなど）・動画と音声・ほかの種類のグラフ（積み上げ・2 軸・"
        "散布図など）と、既存のファイルの編集はできない。\n\n"
        "## Step 2: 結果を確かめる\n"
        "作ったファイルは利用者の画面に表示され、利用者がダウンロードできる。結果の file.file_name "
        f"がファイル名。1 回の実行で作れるファイルは {MAX_FILES_PER_RUN} 個まで。"
    ),
    input_schema=PPTX_CREATE_INPUT_SCHEMA,
    output_schema=PPTX_CREATE_OUTPUT_SCHEMA,
    # Run の成果物を作るだけで、外部へ書かない（`xlsx_create` と同じ宣言。既定は承認なし）。
    side_effects=False,
    timeout_seconds=30.0,
    audit_tags=["output", "file"],
)


def _items(raw: Sequence[Mapping[str, Any]]) -> list[BulletItem]:
    return [
        BulletItem(text=item["text"], children=tuple(item.get("children") or ())) for item in raw
    ]


def _comparison_column(raw: Mapping[str, Any]) -> ComparisonColumn:
    return ComparisonColumn(items=_items(raw.get("items") or []), heading=raw.get("heading"))


def _content(arguments: Mapping[str, Any], run: Any) -> PresentationContent:
    """引数を生成の内容にする（参照した表の値を写す）。誤りは `ExternalToolError`。"""
    issues: list[SpecIssue] = []
    index = run_data_index(run, "pptx.source_unavailable")
    slides: list[Slide] = []
    for slide_index, raw in enumerate(arguments.get("slides") or []):
        at = f"slides.{slide_index}"
        required, optional = _SLIDE_FIELDS[str(raw.get("type"))]
        wrong = field_issues(
            at,
            raw,
            unit="スライド",
            target="スライド",
            required=required,
            optional=optional,
            unused_values=_UNUSED_VALUES,
        )
        if wrong:
            issues.extend(wrong)
            continue
        kind, title, notes = raw["type"], raw["title"], raw.get("notes")
        if kind == "cover":
            slides.append(CoverSlide(title=title, subtitle=raw.get("subtitle"), notes=notes))
        elif kind == "section":
            slides.append(SectionSlide(title=title, notes=notes))
        elif kind == "bullets":
            slides.append(BulletsSlide(title=title, items=_items(raw["items"]), notes=notes))
        elif kind == "comparison":
            slides.append(
                ComparisonSlide(
                    title=title,
                    left=_comparison_column(raw["left"]),
                    right=_comparison_column(raw["right"]),
                    notes=notes,
                )
            )
        else:
            table = raw["table"]
            written = len(table.get("rows") or [])
            if (
                kind == "table"
                and table.get("source") is None
                and (written > MAX_TABLE_ROWS_PER_SLIDE)
            ):
                issues.append(
                    SpecIssue(
                        f"{at}.table.rows",
                        "too_many",
                        f"rows に書ける表は {MAX_TABLE_ROWS_PER_SLIDE} 行までです。行を絞るか、"
                        "取得済みの表を source で参照してください。",
                    )
                )
                continue
            resolved = text_table(f"{at}.table", table, index, "表", issues)
            if resolved is None:
                continue
            columns, rows = resolved
            if kind == "table":
                slides.append(TableSlide(title=title, columns=columns, rows=rows, notes=notes))
            else:
                slides.append(
                    ChartSlide(
                        title=title,
                        columns=columns,
                        rows=rows,
                        chart=chart_spec(raw["chart"]),
                        notes=notes,
                    )
                )
    if issues:
        raise invalid_content(issues)
    return PresentationContent(title=str(arguments.get("title") or ""), slides=slides)


def _create_pptx(arguments: dict[str, Any], context: ToolInvocationContext) -> dict[str, Any]:
    def build(run: Any) -> tuple[str, bytes, dict[str, Any]]:
        content = _content(arguments, run)
        built = build_presentation(content)
        summary = {
            "slides": dict(built.slides),
            "slide_count": built.slide_count,
            "continued_slides": built.continued_slides,
            "tables": list(built.tables),
            "charts": list(built.charts),
        }
        return content.title, built.data, summary

    return create_run_file(
        context.run_id, mime_type=PPTX_MIME_TYPE, spec_errors=(PptxSpecError,), build=build
    )


def pptx_create_tool() -> tuple[ToolDefinition, ToolHandler]:
    """`tool_registry` に登録する `pptx_create`（定義と handler）。"""
    return PPTX_CREATE_DEFINITION, _create_pptx


__all__ = [
    "FILE_LIMIT_CODE",
    "MAX_FILES_PER_RUN",
    "PPTX_CREATE_DEFINITION",
    "PPTX_CREATE_INPUT_SCHEMA",
    "PPTX_CREATE_TOOL",
    "pptx_create_tool",
]
