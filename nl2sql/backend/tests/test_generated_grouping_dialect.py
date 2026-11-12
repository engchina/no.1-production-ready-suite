"""生成 SQL の集約集合を保った Oracle 方言変換の回帰。"""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp

from app.features.nl2sql.oracle_dialect import OracleConversion, normalize_generated_grouping
from app.features.nl2sql.sql_semantics import parse_oracle_sql


@pytest.mark.parametrize("suffix,kind", [("ROLLUP", exp.Rollup), ("CUBE", exp.Cube)])
def test_suffix_is_normalized_without_losing_group_keys(
    suffix: str, kind: type[exp.Expression]
) -> None:
    raw = (
        f'SELECT "Group", category, SUM(value) FROM sample '
        f"WHERE note = 'WITH ROLLUP' GROUP BY \"Group\", category WITH {suffix}"
    )
    normalized = normalize_generated_grouping(raw)
    tree = sqlglot.parse_one(normalized, read=OracleConversion)
    grouping = tree.args["group"].expressions[0]
    assert isinstance(grouping, kind)
    assert [key.sql(dialect=OracleConversion) for key in grouping.expressions] == [
        '"Group"',
        "category",
    ]
    assert tree.args["where"].this.expression.this == "WITH ROLLUP"
    assert parse_oracle_sql(normalized).validation.is_valid
    assert normalize_generated_grouping(normalized) == normalized
    assert parse_oracle_sql(raw).graph is None
    assert parse_oracle_sql(raw).validation.findings[0].code == "SQL_GROUPING_DIALECT_UNSUPPORTED"


def test_nested_grouping_is_normalized() -> None:
    raw = (
        "WITH x AS (SELECT category,SUM(value) AS value FROM sample "
        "GROUP BY category WITH ROLLUP) SELECT SUM(value) FROM x"
    )
    normalized = normalize_generated_grouping(raw)
    assert parse_oracle_sql(normalized).validation.is_valid
    assert not any(
        group.args.get("rollup")
        for group in sqlglot.parse_one(normalized, read=OracleConversion).find_all(exp.Group)
    )


@pytest.mark.parametrize(
    "raw",
    [
        "SELECT category,SUM(value) FROM sample GROUP BY ROLLUP(category)",
        "SELECT category,SUM(value) FROM sample GROUP BY GROUPING SETS ((category),())",
        "SELECT 'GROUP BY category WITH ROLLUP' FROM dual",
        "SELECT category FROM sample GROUP BY category WITH ROLLUP; DELETE FROM sample",
        "SELECT category,SUM(value) FROM sample GROUP BY category WITH ROLLUP WITH CUBE",
    ],
)
def test_unrelated_or_ambiguous_sql_is_not_rewritten(raw: str) -> None:
    assert normalize_generated_grouping(raw) == raw
