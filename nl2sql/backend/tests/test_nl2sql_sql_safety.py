"""SELECT-only 安全判定の回帰テスト。

危険語・`;` の判定は SQL の構造だけを見るべきで、業務データの値やコメントに
含まれる語で正当な SELECT を弾いてはならない(Issue: is_select_only の誤ブロック)。
"""

from __future__ import annotations

import pytest

from app.features.nl2sql.models import AllowedObjects
from app.features.nl2sql.service import (
    Nl2SqlService,
    _mask_sql_literals_and_comments,
    is_select_only,
)
from app.features.nl2sql.store import MemoryNl2SqlStore


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM T WHERE MEMO = 'please delete this row'",
        "SELECT * FROM T WHERE ACTION = 'update'",
        "SELECT * FROM T WHERE NOTE = 'it''s an update'",
        "-- 売上集計\nSELECT * FROM T",
        "/* 集計 */ SELECT * FROM T",
        "-- 1 行目\n/* 2 行目 */\nSELECT * FROM T",
        "SELECT ';' AS X FROM DUAL",
        'SELECT "DELETE FLAG" FROM T',
        "SELECT * FROM T WHERE NOTE = q'[drop; create]'",
        "SELECT ID FROM T -- ; DELETE FROM T",
        "SELECT ID FROM T /* truncate table */ WHERE ID = 1",
        "(SELECT 1 FROM DUAL)",
        "( SELECT 1 FROM DUAL )",
        "WITH x AS (SELECT 1 AS v FROM DUAL) SELECT v FROM x",
        "SELECT CREATE_DATE, UPDATED_BY FROM T",
        "SELECT * FROM T;",
        "SELECT * FROM T;  -- 末尾コメント",
    ],
)
def test_is_select_only_accepts_read_only_sql_with_literals_and_comments(sql: str) -> None:
    assert is_select_only(sql) is True


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "-- only a comment",
        "/* x */ DELETE FROM T",
        "-- 説明\nUPDATE T SET A = 1",
        "SELECT 1 FROM DUAL; DELETE FROM T",
        "SELECT 1 FROM DUAL; SELECT 2 FROM DUAL",
        "BEGIN NULL; END;",
        "DECLARE v NUMBER; BEGIN NULL; END;",
        "CALL P()",
        "SELECT * FROM T WHERE ID = (DELETE FROM T)",
        "(DELETE FROM T)",
        "SELECT 'unterminated FROM T; DELETE FROM T",
        "drop table t",
        "merge into t using d on (t.id = d.id) when matched then update set t.a = d.a",
    ],
)
def test_is_select_only_rejects_mutations_and_multi_statements(sql: str) -> None:
    assert is_select_only(sql) is False


def test_mask_keeps_length_and_blanks_only_literal_and_comment_bodies() -> None:
    sql = "SELECT 'a;b' AS x, \"Q\" FROM T -- c\n/* d */ WHERE y = q'[z]'"
    masked = _mask_sql_literals_and_comments(sql)

    assert len(masked) == len(sql)
    assert "a;b" not in masked
    assert "-- c" not in masked
    assert "/* d */" not in masked
    assert "q'[z]'" not in masked
    assert masked.split() == ["SELECT", "AS", "x,", "FROM", "T", "WHERE", "y", "="]


def test_analyze_sql_keeps_leading_comment_select_executable() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    sql = "-- 注文一覧\nSELECT ID FROM APP.ORDERS WHERE STATUS = 'update'"

    analysis = service.analyze_sql(sql, AllowedObjects(), 100)

    assert analysis.safety.is_select_only is True
    assert analysis.safety.blocked_reason == ""
    assert analysis.safety.is_safe is True
    assert analysis.executable_sql == sql
