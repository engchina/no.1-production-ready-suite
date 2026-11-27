"""グラフの指定（種類と、表のどの列を項目・系列にするか）の型・上限・検証（#5837）。

ファイルの形式（PowerPoint・Excel・Word）にも Run にも依らない。描画（体裁・色・凡例・データ
ラベル）は 3 つの形式で共通の `chart_drawing` がし、形式ごとに置く所は `pptx_presentation`・
`docx_chart`・`xlsx_chart` が持つ。

- グラフの値は、必ず表（列の定義と行の値）から取る。指定するのは種類・項目の列・系列の列だけで、
  グラフ用の値を別に書く欄は無い（表の値を打ち直さない）。
- 種類は 4 つ（縦棒・横棒・折れ線・円）。系列にできるのは数値の列だけ。
- 上限（系列・項目の数、項目と系列の名前の長さ）を超える指定は、切り捨てずに誤りにする。
- 空の値: 縦棒・横棒・折れ線は、空の値を空のまま描く（棒を置かない・線をつながない）。円は割合を
  示すので、空の値・負の値・合計が 0 の表は誤りにする。項目の名前が空の行と、値がすべて空の系列は、
  どの種類でも誤りにする。数値として読めない値は、列の型で読む所（呼ぶ側）が誤りにする。
- 誤りは値を含まない場所・種類・直し方（`SpecIssue`）で返す。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.features.agent.table_columns import SpecIssue

# グラフの種類（引数の `type` の値と、その呼び名）。
CHART_TYPES: dict[str, str] = {
    "column": "縦棒（集合）",
    "bar": "横棒（集合）",
    "line": "折れ線",
    "pie": "円",
}
PIE = "pie"
# 系列（値の列）と項目（表の行）の数の上限。円は系列が 1 つで、項目の上限が小さい。
MAX_CHART_SERIES = 5
MAX_CHART_CATEGORIES = 20
MAX_PIE_CATEGORIES = 8
# 項目・系列の名前（軸の目盛り・凡例に出る）の文字数の上限。
MAX_CHART_LABEL_CHARS = 30


@dataclass(frozen=True, slots=True)
class ChartSpec:
    # `CHART_TYPES` の key。
    type: str
    # 項目の名前にする列と、系列にする列（表の列の番号。1 始まり）。
    category_column: int
    series_columns: Sequence[int]


@dataclass(frozen=True, slots=True)
class ChartSeries:
    # 系列の名前（列の見出し）。
    name: str
    # 項目ごとの値（空は None）。
    values: tuple[Decimal | None, ...]
    # 列の書式の名前（`table_columns.NUMBER_FORMATS` の key）。None は既定。
    format: str | None


@dataclass(frozen=True, slots=True)
class ChartData:
    """グラフに描く値（表から取り出したもの）。"""

    type: str
    categories: tuple[str, ...]
    series: tuple[ChartSeries, ...]


# 引数の `chart` の schema（strict。表は同じ階層の別の項目で渡す）。
CHART_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": list(CHART_TYPES),
            "description": "グラフの種類。"
            + " / ".join(f"{name}={label}" for name, label in CHART_TYPES.items()),
        },
        "category_column": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "項目（軸の目盛り・円の区分）の名前にする列の番号（表の columns の左から。"
                "1 始まり）。表の 1 行が 1 つの項目になる。項目は "
                f"{MAX_CHART_CATEGORIES} 個まで（{PIE} は {MAX_PIE_CATEGORIES} 個まで）"
            ),
        },
        "series_columns": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
            "description": (
                "系列（値）にする列の番号（表の columns の左から。1 始まり）。type が number の"
                f"列だけを選べる。{MAX_CHART_SERIES} 個まで（{PIE} は 1 個）。系列の名前は列の"
                "見出し、数値の表示は列の format になる"
            ),
        },
    },
    "required": ["type", "category_column", "series_columns"],
    "additionalProperties": False,
}


def chart_issues(
    at: str, spec: ChartSpec, column_types: Sequence[str], rows: int, *, rows_at: str
) -> list[SpecIssue]:
    """グラフの指定の誤り（種類・列の選び方・系列と項目の数）。

    `at` はグラフの指定の場所、`rows_at` は表の行の場所。`column_types` は表の列の型（左から）、
    `rows` は表の行の数。
    """
    issues: list[SpecIssue] = []
    if spec.type not in CHART_TYPES:
        return [
            SpecIssue(
                f"{at}.type", "enum", f"グラフの種類は {' / '.join(CHART_TYPES)} のどれかです。"
            )
        ]
    pie = spec.type == PIE
    width = len(column_types)
    out_of_range = f"表の列の番号（1〜{width}）を書いてください。"
    if not 1 <= spec.category_column <= width:
        issues.append(SpecIssue(f"{at}.category_column", "range", out_of_range))
    limit = 1 if pie else MAX_CHART_SERIES
    if not 1 <= len(spec.series_columns) <= limit:
        issues.append(
            SpecIssue(
                f"{at}.series_columns",
                "count",
                "円のグラフの系列は 1 つです。複数の系列を比べるときは、縦棒・横棒・折れ線に"
                "してください。"
                if pie
                else f"系列は 1〜{MAX_CHART_SERIES} 個にしてください。系列を絞るか、グラフを"
                "分けてください。",
            )
        )
    seen: set[int] = set()
    for index, column in enumerate(spec.series_columns):
        series_at = f"{at}.series_columns.{index}"
        if not 1 <= column <= width:
            issues.append(SpecIssue(series_at, "range", out_of_range))
        elif column == spec.category_column:
            issues.append(
                SpecIssue(series_at, "conflict", "項目の列（category_column）は系列にできません。")
            )
        elif column in seen:
            issues.append(SpecIssue(series_at, "duplicate", "同じ列を 2 回選べません。"))
        elif column_types[column - 1] != "number":
            issues.append(
                SpecIssue(
                    series_at,
                    "not_number",
                    "系列にできるのは、type が number の列だけです。数値の列を選んでください。",
                )
            )
        seen.add(column)
    if rows < 1:
        issues.append(SpecIssue(rows_at, "required", "グラフにする表に、行が 1 つ以上要ります。"))
    elif pie and rows > MAX_PIE_CATEGORIES:
        issues.append(
            SpecIssue(
                rows_at,
                "too_many",
                f"円のグラフの項目（表の行）は {MAX_PIE_CATEGORIES} 個までです。行を絞るか、"
                "縦棒・横棒のグラフにしてください。",
            )
        )
    elif rows > MAX_CHART_CATEGORIES:
        issues.append(
            SpecIssue(
                rows_at,
                "too_many",
                f"グラフの項目（表の行）は {MAX_CHART_CATEGORIES} 個までです。行を絞るか、"
                "表にしてください。",
            )
        )
    return issues


def chart_data(
    spec: ChartSpec,
    headers: Sequence[str],
    formats: Sequence[str | None],
    texts: Sequence[Sequence[str]],
    values: Sequence[Sequence[Any]],
) -> ChartData:
    """表（列ごとの見出し・書式・表示の文字・型で読んだ値）から、グラフに描く値を取り出す。

    `chart_issues` が通った指定で呼ぶ。項目の名前は項目の列の表示の文字、系列の値は系列の列の
    数値（`Decimal`。空は None）。
    """
    return ChartData(
        type=spec.type,
        categories=tuple(texts[spec.category_column - 1]),
        series=tuple(
            ChartSeries(
                name=headers[column - 1],
                values=tuple(values[column - 1]),
                format=formats[column - 1],
            )
            for column in spec.series_columns
        ),
    )


def chart_data_issues(spec: ChartSpec, data: ChartData, *, columns_at: str) -> list[SpecIssue]:
    """グラフに描く値の誤り（名前の長さ・空の値・円の値）。`columns_at` は表の列の場所。"""
    issues: list[SpecIssue] = []
    category_at = f"{columns_at}.{spec.category_column - 1}"
    for row, label in enumerate(data.categories):
        if not label.strip():
            issues.append(
                SpecIssue(
                    category_at,
                    "required",
                    f"項目の列の {row + 1} 行目が空です。項目の名前の無い行を外してください。",
                )
            )
            break
        if len(label) > MAX_CHART_LABEL_CHARS:
            issues.append(
                SpecIssue(
                    category_at,
                    "too_long",
                    f"項目の列の {row + 1} 行目は、項目の名前として長すぎます（"
                    f"{MAX_CHART_LABEL_CHARS} 文字まで）。短い名前の列を項目にしてください。",
                )
            )
            break
    for column, series in zip(spec.series_columns, data.series, strict=True):
        series_at = f"{columns_at}.{column - 1}"
        if len(series.name) > MAX_CHART_LABEL_CHARS:
            issues.append(
                SpecIssue(
                    f"{series_at}.header",
                    "too_long",
                    f"系列にする列の見出しは {MAX_CHART_LABEL_CHARS} 文字までです。",
                )
            )
        present = [value for value in series.values if value is not None]
        if not present:
            issues.append(
                SpecIssue(
                    series_at,
                    "empty",
                    "この列の値がすべて空です。値のある列を系列にしてください。",
                )
            )
        elif spec.type == PIE and (
            len(present) < len(series.values) or min(present) < 0 or not any(present)
        ):
            issues.append(
                SpecIssue(
                    series_at,
                    "pie_values",
                    "円のグラフの値は、空の値・負の値を含められず、合計が 0 より大きい必要が"
                    "あります。行を絞るか、縦棒・横棒のグラフにしてください。",
                )
            )
    return issues


__all__ = [
    "CHART_SCHEMA",
    "CHART_TYPES",
    "MAX_CHART_CATEGORIES",
    "MAX_CHART_LABEL_CHARS",
    "MAX_CHART_SERIES",
    "MAX_PIE_CATEGORIES",
    "PIE",
    "ChartData",
    "ChartSeries",
    "ChartSpec",
    "chart_data",
    "chart_data_issues",
    "chart_issues",
]
