"""実 Oracle の非実行解析で、引用別名と疑似列を区別する。LLM は呼ばない。"""

from __future__ import annotations

import os

import pytest

from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter, OracleSelectPreparationError
from app.settings import get_settings

pytestmark = pytest.mark.skipif(
    os.getenv("NL2SQL_RUN_ORACLE_INTEGRATION") != "1",
    reason="NL2SQL_RUN_ORACLE_INTEGRATION=1 の明示指定が必要です。",
)


def test_oracle_prepare_alias_and_pseudocolumn() -> None:
    adapter = OracleNl2SqlAdapter(get_settings())
    with pytest.raises(OracleSelectPreparationError) as failure:
        adapter.prepare_select("SELECT 'A' AS LEVEL FROM DUAL")
    assert failure.value.code == 923
    assert failure.value.offset > 0
    assert failure.value.allows_generation_repair
    for sql in [
        "SELECT 'A' AS \"LEVEL\" FROM DUAL",
        "SELECT LEVEL FROM DUAL CONNECT BY LEVEL <= 2",
        "SELECT 1 AS A FROM DUAL UNION ALL SELECT 2 AS B FROM DUAL",
    ]:
        adapter.prepare_select(sql)


@pytest.mark.parametrize("literal", ["あ", "😀", "あ😀"])
def test_unicode_native_offset_maps_to_actual_token(literal: str) -> None:
    from app.features.nl2sql.oracle_sql_diagnostics import oracle_parse_diagnostic

    sql = f"SELECT '{literal}' AS LEVEL FROM DUAL"
    adapter = OracleNl2SqlAdapter(get_settings())
    with pytest.raises(OracleSelectPreparationError) as failure:
        adapter.prepare_select(sql)
    error = failure.value
    expected = sql.index("LEVEL")
    assert error.offset == len(sql[:expected].encode("utf-8"))
    diagnostic = oracle_parse_diagnostic(
        sql, code=error.code, offset=error.offset, offset_basis=error.offset_basis
    )
    location = diagnostic["location"]
    assert location is not None
    assert location["character_offset"] == expected
    assert location["line_1_based"] == 1 and location["column_1_based"] == expected + 1
    assert location["token"] is not None and location["token"]["text"] == "LEVEL"
