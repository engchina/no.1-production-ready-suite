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
