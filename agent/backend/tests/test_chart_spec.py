"""グラフの指定の型・上限・検証（`chart_spec`。#5837）。

ファイルの形式にも Run にも依らない（値は合成）。

- 種類・列の選び方（範囲・重複・項目の列との重なり・数値の列だけ）・系列と項目の数の上限。
- 表からグラフの値を取り出すこと（項目の名前は表示の文字、系列の値は数値。値を作らない）。
- 空の値の扱い: 縦棒・横棒・折れ線は空のまま、円は誤り。空の項目の名前・値の無い系列は誤り。
- 誤りの文に、表の値を載せないこと。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.features.agent.chart_spec import (
    CHART_SCHEMA,
    CHART_TYPES,
    MAX_CHART_CATEGORIES,
    MAX_CHART_LABEL_CHARS,
    MAX_CHART_SERIES,
    MAX_PIE_CATEGORIES,
    ChartData,
    ChartSeries,
    ChartSpec,
    chart_data,
    chart_data_issues,
    chart_issues,
)

# 表の列の型（左から。1 列目が項目の名前、2〜7 列目が数値、8 列目が日付）。
TYPES = ["text", "number", "number", "number", "number", "number", "number", "date"]


def _issues(
    spec: ChartSpec, rows: int = 3, types: list[str] | None = None
) -> list[tuple[str, str]]:
    found = chart_issues("chart", spec, types or TYPES, rows, rows_at="table.rows")
    return [(item.argument, item.code) for item in found]


def test_limits_and_types_are_the_decided_values() -> None:
    assert list(CHART_TYPES) == ["column", "bar", "line", "pie"]
    assert (MAX_CHART_SERIES, MAX_CHART_CATEGORIES, MAX_PIE_CATEGORIES) == (5, 20, 8)
    # 引数の schema は strict（すべての項目が必須・追加の項目なし）で、種類は定義から作る。
    assert CHART_SCHEMA["additionalProperties"] is False
    assert sorted(CHART_SCHEMA["required"]) == sorted(CHART_SCHEMA["properties"])
    assert CHART_SCHEMA["properties"]["type"]["enum"] == list(CHART_TYPES)
    # グラフ用の値を書く欄は無い（値は表から取る）。
    assert set(CHART_SCHEMA["properties"]) == {"type", "category_column", "series_columns"}


@pytest.mark.parametrize("chart_type", list(CHART_TYPES), ids=list(CHART_TYPES))
def test_valid_selection_has_no_issues(chart_type: str) -> None:
    series = [2] if chart_type == "pie" else [2, 3, 4, 5, 6]
    rows = MAX_PIE_CATEGORIES if chart_type == "pie" else MAX_CHART_CATEGORIES
    assert _issues(ChartSpec(chart_type, 1, series), rows) == []


@pytest.mark.parametrize(
    ("spec", "rows", "expected"),
    [
        (ChartSpec("area", 1, [2]), 3, ("chart.type", "enum")),
        (ChartSpec("column", 0, [2]), 3, ("chart.category_column", "range")),
        (ChartSpec("column", 9, [2]), 3, ("chart.category_column", "range")),
        (ChartSpec("column", 1, []), 3, ("chart.series_columns", "count")),
        (ChartSpec("column", 1, [2, 3, 4, 5, 6, 7]), 3, ("chart.series_columns", "count")),
        (ChartSpec("pie", 1, [2, 3]), 3, ("chart.series_columns", "count")),
        (ChartSpec("line", 1, [2, 9]), 3, ("chart.series_columns.1", "range")),
        (ChartSpec("line", 2, [2]), 3, ("chart.series_columns.0", "conflict")),
        (ChartSpec("bar", 1, [2, 2]), 3, ("chart.series_columns.1", "duplicate")),
        # 系列にできるのは数値の列だけ（文字・日付の列は選べない）。
        (ChartSpec("bar", 2, [1]), 3, ("chart.series_columns.0", "not_number")),
        (ChartSpec("bar", 1, [8]), 3, ("chart.series_columns.0", "not_number")),
        (ChartSpec("column", 1, [2]), 0, ("table.rows", "required")),
        (ChartSpec("column", 1, [2]), MAX_CHART_CATEGORIES + 1, ("table.rows", "too_many")),
        (ChartSpec("pie", 1, [2]), MAX_PIE_CATEGORIES + 1, ("table.rows", "too_many")),
    ],
    ids=[
        "unknown-type",
        "category-zero",
        "category-out-of-range",
        "no-series",
        "too-many-series",
        "pie-with-two-series",
        "series-out-of-range",
        "series-is-the-category",
        "duplicate-series",
        "text-series",
        "date-series",
        "no-rows",
        "too-many-categories",
        "too-many-pie-categories",
    ],
)
def test_bad_selection_is_an_issue(spec: ChartSpec, rows: int, expected: tuple[str, str]) -> None:
    assert _issues(spec, rows) == [expected]


def test_limit_messages_say_how_to_fix() -> None:
    [rows] = chart_issues(
        "chart", ChartSpec("pie", 1, [2]), TYPES, MAX_PIE_CATEGORIES + 1, rows_at="table.rows"
    )
    assert f"{MAX_PIE_CATEGORIES} 個まで" in rows.message and "行を絞る" in rows.message
    [series] = chart_issues("chart", ChartSpec("pie", 1, [2, 3]), TYPES, 3, rows_at="table.rows")
    assert "縦棒・横棒・折れ線" in series.message


HEADERS = ["品目", "数量（個）", "比率"]
FORMATS = [None, "integer", "percent1"]
TEXTS = [["品目 A", "品目 B", "品目 C"], ["1,200", "30", ""], ["25.0%", "50.0%", ""]]
VALUES: list[list[Any]] = [
    ["品目 A", "品目 B", "品目 C"],
    [Decimal(1200), Decimal(30), None],
    [Decimal("0.25"), Decimal("0.5"), None],
]


def _data(spec: ChartSpec, values: list[list[Any]] | None = None) -> ChartData:
    return chart_data(spec, HEADERS, FORMATS, TEXTS, values or VALUES)


def test_chart_values_are_taken_from_the_table() -> None:
    data = _data(ChartSpec("column", 1, [3, 2]))

    assert data.type == "column"
    assert data.categories == ("品目 A", "品目 B", "品目 C")
    # 系列は選んだ順。名前は列の見出し、値は表の数値のまま、表示形式は列の書式。
    assert data.series == (
        ChartSeries("比率", (Decimal("0.25"), Decimal("0.5"), None), "percent1"),
        ChartSeries("数量（個）", (Decimal(1200), Decimal(30), None), "integer"),
    )


@pytest.mark.parametrize("chart_type", ["column", "bar", "line"], ids=["column", "bar", "line"])
def test_empty_values_are_kept_empty_for_bars_and_lines(chart_type: str) -> None:
    spec = ChartSpec(chart_type, 1, [2])
    assert chart_data_issues(spec, _data(spec), columns_at="table.columns") == []


@pytest.mark.parametrize(
    ("spec", "values", "expected"),
    [
        # 円は割合を示すので、空の値・負の値・合計が 0 の表は描けない。
        (ChartSpec("pie", 1, [2]), VALUES, ("table.columns.1", "pie_values")),
        (
            ChartSpec("pie", 1, [2]),
            [VALUES[0], [Decimal(5), Decimal(-1), Decimal(2)], VALUES[2]],
            ("table.columns.1", "pie_values"),
        ),
        (
            ChartSpec("pie", 1, [2]),
            [VALUES[0], [Decimal(0), Decimal(0), Decimal(0)], VALUES[2]],
            ("table.columns.1", "pie_values"),
        ),
        # 値がすべて空の系列は、どの種類でも描けない。
        (
            ChartSpec("line", 1, [2, 3]),
            [VALUES[0], VALUES[1], [None, None, None]],
            ("table.columns.2", "empty"),
        ),
    ],
    ids=["pie-empty-value", "pie-negative-value", "pie-zero-total", "all-empty-series"],
)
def test_values_that_cannot_be_drawn_are_issues(
    spec: ChartSpec, values: list[list[Any]], expected: tuple[str, str]
) -> None:
    found = chart_data_issues(spec, _data(spec, values), columns_at="table.columns")
    assert [(item.argument, item.code) for item in found] == [expected]


def test_category_and_series_names_are_required_and_limited() -> None:
    spec = ChartSpec("bar", 1, [2])
    long_name = "名" * (MAX_CHART_LABEL_CHARS + 1)
    series = (ChartSeries("数量", (Decimal(1), Decimal(2)), None),)

    def issues(data: ChartData) -> list[tuple[str, str]]:
        found = chart_data_issues(spec, data, columns_at="table.columns")
        # 誤りの文に、項目の名前（表の値）を載せない。
        assert all(long_name not in item.message for item in found)
        return [(item.argument, item.code) for item in found]

    assert issues(ChartData("bar", ("品目 A", " "), series)) == [("table.columns.0", "required")]
    assert issues(ChartData("bar", ("品目 A", long_name), series)) == [
        ("table.columns.0", "too_long")
    ]
    assert issues(
        ChartData("bar", ("品目 A", "品目 B"), (ChartSeries(long_name, (Decimal(1), None), None),))
    ) == [("table.columns.1.header", "too_long")]
    # 上限ちょうどの名前は通る。
    limit = "名" * MAX_CHART_LABEL_CHARS
    assert issues(ChartData("bar", (limit, "品目 B"), series)) == []
