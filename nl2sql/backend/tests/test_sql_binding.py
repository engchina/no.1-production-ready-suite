"""派生表と物理列、および Oracle の分析関数の実行前検査。"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.nl2sql.models import SchemaColumn, SchemaTable
from app.features.nl2sql.sql_binding import column_binding_violations
from app.features.nl2sql.sql_semantics import parse_oracle_sql

TABLES = [
    SchemaTable(
        owner="APP",
        table_name="TABLE_A",
        logical_name="Table A",
        columns=[
            SchemaColumn(column_name=name, logical_name=name, data_type="NUMBER")
            for name in ("ID", "AMOUNT")
        ],
    )
]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT t.MISSING FROM APP.TABLE_A t",
        "WITH x AS (SELECT ID FROM APP.TABLE_A) SELECT AMOUNT FROM x",
        "SELECT d.AMOUNT FROM (SELECT ID FROM APP.TABLE_A) d",
        "WITH x(k) AS (SELECT ID FROM APP.TABLE_A) SELECT ID FROM x",
        "SELECT ID FROM APP.TABLE_A a JOIN APP.TABLE_A b ON a.ID=b.ID",
    ],
)
def test_invalid_bindings(sql: str) -> None:
    assert column_binding_violations(sql, TABLES, owner="APP")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM APP.TABLE_A",
        "SELECT a.ID FROM APP.TABLE_A a JOIN APP.TABLE_A b ON a.ID=b.ID",
        "WITH x(k) AS (SELECT ID FROM APP.TABLE_A) SELECT k FROM x",
        'WITH x AS (SELECT ID AS "mixed" FROM APP.TABLE_A) SELECT "mixed" FROM x',
        "SELECT x.ID FROM (SELECT * FROM APP.TABLE_A) x",
        "SELECT AMOUNT AS total FROM APP.TABLE_A ORDER BY total",
        "SELECT a.ID FROM APP.TABLE_A a WHERE EXISTS (SELECT 1 FROM APP.TABLE_A b WHERE b.ID=a.ID)",
        "SELECT 1 FROM DUAL",
        "SELECT ID FROM TABLE_A UNION ALL SELECT ID FROM TABLE_A",
    ],
)
def test_valid_bindings(sql: str) -> None:
    assert not column_binding_violations(sql, TABLES, owner="APP")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ID FROM TABLE_A WHERE RANK() OVER (ORDER BY AMOUNT) <= 2",
        "SELECT ID FROM TABLE_A GROUP BY RANK() OVER (ORDER BY AMOUNT)",
        "SELECT COUNT(*) FROM TABLE_A HAVING RANK() OVER (ORDER BY COUNT(*)) <= 2",
        "SELECT SUM(ROW_NUMBER() OVER (ORDER BY ID)) FROM TABLE_A",
    ],
)
def test_window_in_forbidden_position_is_blocked(sql: str) -> None:
    analysis = parse_oracle_sql(sql)
    assert analysis.graph is None
    assert analysis.validation.findings[0].code == "SQL_WINDOW_PLACEMENT_INVALID"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT RANK() OVER (ORDER BY AMOUNT) AS rn FROM TABLE_A",
        "SELECT * FROM (SELECT ID, RANK() OVER (ORDER BY AMOUNT) rn FROM TABLE_A) WHERE rn<=2",
        "SELECT ID FROM TABLE_A ORDER BY RANK() OVER (ORDER BY AMOUNT)",
        "SELECT ID FROM TABLE_A WHERE ID IN (SELECT RANK() OVER (ORDER BY AMOUNT) FROM TABLE_A)",
    ],
)
def test_valid_window_locations(sql: str) -> None:
    assert parse_oracle_sql(sql).graph is not None


def test_quoted_physical_columns_remain_case_sensitive() -> None:
    tables = [
        SchemaTable(
            owner="APP",
            table_name="Mixed",
            logical_name="表A",
            columns=[SchemaColumn(column_name="Amount", logical_name="値", data_type="NUMBER")],
        )
    ]
    assert not column_binding_violations(
        'SELECT a."Amount" FROM APP."Mixed" a', tables, owner="APP"
    )
    assert column_binding_violations('SELECT a.AMOUNT FROM APP."Mixed" a', tables, owner="APP")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ROWNUM,ROWID FROM APP.TABLE_A",
        "SELECT ID FROM APP.TABLE_A WHERE ROWNUM<=10",
        "SELECT LEVEL FROM DUAL CONNECT BY LEVEL<=5",
    ],
)
def test_oracle_pseudocolumns_are_not_treated_as_missing_columns(sql: str) -> None:
    assert not column_binding_violations(sql, TABLES, owner="APP")


@pytest.mark.parametrize(
    "bad",
    [
        "WITH x AS (SELECT ID FROM APP.TABLE_A) SELECT AMOUNT FROM x",
        "SELECT MISSING FROM APP.TABLE_A",
        "SELECT ID FROM APP.TABLE_A WHERE RANK() OVER (ORDER BY AMOUNT)<=2",
    ],
)
def test_generated_invalid_sql_is_sent_back_before_execution(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    from app.features.nl2sql.models import (
        AllowedObjects,
        Nl2SqlEngine,
        Nl2SqlProfile,
        SchemaCatalog,
    )
    from app.features.nl2sql.service import GeneratedSql, Nl2SqlService
    from app.features.nl2sql.store import MemoryNl2SqlStore

    service = Nl2SqlService(store=MemoryNl2SqlStore())
    monkeypatch.setattr(service, "_current_schema_owner", lambda: "APP")
    calls = []

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        calls.append(kwargs)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=bad if len(calls) == 1 else "SELECT ID FROM APP.TABLE_A",
            explanation="",
            engine_meta={},
            schema_catalog=SchemaCatalog(refreshed_at="2026-01-01", tables=TABLES),
        )

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    result = service._generate_sql(
        Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
        "IDを表示",
        Nl2SqlProfile(id="p", name="p"),
        AllowedObjects(),
        10,
        [],
    )
    assert result.generated_sql == "SELECT ID FROM APP.TABLE_A"
    assert len(calls) == 2 and calls[1]["generation_feedback"]["rejected_sql"] == bad
