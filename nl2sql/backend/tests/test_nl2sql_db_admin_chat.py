"""DB 管理チャット（#2264）: 分類・方針で止める文・確認語・実行・監査・生成・分析。

LLM（OCI Enterprise AI）と Oracle は決定論のスタブで置き換える。表・列の名前は合成のもの。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.features.nl2sql.db_admin_chat import (
    forbidden_statement_error,
    select_context_tables,
    single_object_confirmation_target,
)
from app.features.nl2sql.models import (
    DbAdminChatAnalyzeRequest,
    DbAdminChatExecuteRequest,
    DbAdminChatGenerateRequest,
    DbAdminChatHistoryTurn,
    QueryResults,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.service import (
    DbAdminChatGenerationError,
    Nl2SqlService,
    SchemaRefreshMutationSync,
    _mask_sql_literals_and_comments,
    _similarity_tokens,
)
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.security.request_actor import actor_scope


class _FakeAdapter:
    """管理の実行（更新系）と SELECT の呼び出しを記録する。"""

    def __init__(self, statement_results: list[dict[str, Any]] | None = None) -> None:
        self.statement_results = statement_results or []
        self.statement_calls: list[tuple[list[str], bool]] = []
        self.select_calls: list[tuple[str, int | None, bool]] = []

    def execute_admin_statements(
        self,
        statements: list[str],
        *,
        atomic: bool = True,
        ignored_error_codes: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        del ignored_error_codes
        self.statement_calls.append((statements, atomic))
        return self.statement_results

    def execute_select(
        self, sql: str, max_rows: int | None, *, admin_control_plane: bool = False
    ) -> QueryResults:
        self.select_calls.append((sql, max_rows, admin_control_plane))
        return QueryResults(
            columns=["N"],
            rows=[{"N": 1}],
            total=1,
            execution_context="admin_control_plane",
        )


class _FakeEnterpriseAi:
    def __init__(self, responses: list[dict[str, Any]], *, configured: bool = True) -> None:
        self.responses = responses
        self.configured = configured
        self.calls: list[dict[str, Any]] = []

    def is_configured(self) -> bool:
        return self.configured

    def model_id(self) -> str:
        return "test-text-model"

    def generate(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


class _Service(Nl2SqlService):
    def __init__(self, adapter: _FakeAdapter, *, oracle: bool = True) -> None:
        super().__init__(store=MemoryNl2SqlStore())
        self._oracle_adapter = adapter  # type: ignore[assignment]
        self._oracle = oracle

    def _use_oracle_runtime(self) -> bool:
        return self._oracle

    def _current_schema_owner(self) -> str:
        return "APP"


def _masked(sql: str) -> str:
    return _mask_sql_literals_and_comments(sql.strip())


# ---- 方針で止める文 ----


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER SYSTEM SET OPEN_CURSORS = 300",
        "alter system kill session '1,2'",
        "ALTER DATABASE OPEN",
        "ALTER PLUGGABLE DATABASE CLOSE",
        "CREATE PLUGGABLE DATABASE P1 ADMIN USER A IDENTIFIED BY X",
        "DROP PLUGGABLE DATABASE P1",
        "SHUTDOWN IMMEDIATE",
        "STARTUP",
        "DROP USER U1 CASCADE",
        "CREATE USER U1 IDENTIFIED BY X",
        "ALTER USER U1 IDENTIFIED BY X",
        "DROP TABLESPACE TS1",
        "NOAUDIT ALL",
        "ALTER SESSION SET CURRENT_SCHEMA = OTHER",
        "COMMIT",
        "ROLLBACK",
        "GRANT DBA TO U1",
        "GRANT SYSDBA TO U1",
        "GRANT ALL PRIVILEGES TO U1",
        "BEGIN EXECUTE IMMEDIATE 'DROP TABLE T1'; END;",
        "DECLARE c NUMBER; BEGIN c := DBMS_SQL.OPEN_CURSOR; END;",
        "SELECT UTL_HTTP.REQUEST('http://example.invalid') FROM DUAL",
        "BEGIN DBMS_SCHEDULER.CREATE_JOB(job_name => 'J1'); END;",
    ],
    ids=lambda sql: sql[:40],
)
def test_forbidden_statements_are_blocked(sql: str) -> None:
    assert forbidden_statement_error(_masked(sql))


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT BANNER_FULL FROM V$VERSION",
        "SELECT 'ALTER SYSTEM' AS LABEL FROM DUAL",
        "SELECT * FROM T1 WHERE NOTE = 'DROP USER'",
        "CREATE INDEX IX_T1 ON T1 (C1)",
        "ALTER TABLE T1 ADD (C2 NUMBER)",
        "ALTER DATABASE LINK L1 CONNECT TO U IDENTIFIED BY X",
        "GRANT SELECT ON T1 TO R1",
        "INSERT INTO T1 (C1) VALUES ('COMMIT')",
    ],
    ids=lambda sql: sql[:40],
)
def test_ordinary_statements_and_literals_are_not_blocked(sql: str) -> None:
    assert forbidden_statement_error(_masked(sql)) == ""


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("DROP TABLE app.orders_tmp PURGE", "APP.ORDERS_TMP"),
        ('DROP VIEW "MixedCase"', "MixedCase"),
        ("TRUNCATE TABLE T1", "T1"),
        ("DROP INDEX IX_T1", "IX_T1"),
        ("DELETE FROM T1", ""),
        ("CREATE TABLE T1 (ID NUMBER)", ""),
    ],
)
def test_single_object_confirmation_target(sql: str, expected: str) -> None:
    assert single_object_confirmation_target(sql, _masked(sql)) == expected


def test_context_tables_rank_names_over_columns() -> None:
    tables = [
        SchemaTable(
            table_name="ITEM_STOCK",
            logical_name="",
            owner="APP",
            columns=[SchemaColumn(column_name="QTY", logical_name="", data_type="NUMBER")],
        ),
        SchemaTable(
            table_name="OTHER",
            logical_name="",
            owner="APP",
            columns=[SchemaColumn(column_name="STOCK_NOTE", logical_name="", data_type="CHAR")],
        ),
        SchemaTable(table_name="UNRELATED", logical_name="", owner="APP"),
    ]
    picked = select_context_tables(tables, _similarity_tokens("STOCK の件数"), _similarity_tokens)
    assert [table.table_name for table in picked] == ["ITEM_STOCK", "OTHER"]
    assert select_context_tables(tables, set(), _similarity_tokens) == []


# ---- 分類 ----


def test_plan_select_is_read_only_and_allows_dynamic_performance_views() -> None:
    service = _Service(_FakeAdapter())
    plan = service._db_admin_chat_plan("SELECT BANNER_FULL FROM V$VERSION")
    assert plan.read_only is True
    assert plan.requires_confirmation is False
    assert plan.blocked is False
    joined = service._db_admin_chat_plan(
        "SELECT s.SID FROM V$SESSION s JOIN V$LOCKED_OBJECT l ON s.SID = l.SESSION_ID"
    )
    assert joined.blocked is False


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM NL2SQL_PROFILES",
        "SELECT * FROM PLATFORM_USERS",
        "SELECT * FROM SYS.OBJ$",
        "DELETE FROM V$SESSION",
        "DROP TABLE AGENT_RUNS",
        "ALTER SYSTEM FLUSH SHARED_POOL",
    ],
)
def test_plan_blocks_product_objects_internal_objects_and_policy(sql: str) -> None:
    plan = _Service(_FakeAdapter())._db_admin_chat_plan(sql)
    assert plan.blocked is True
    assert plan.blocked_reasons


def test_plan_for_mutation_requires_confirmation_and_marks_implicit_commit() -> None:
    service = _Service(_FakeAdapter())
    script = service._db_admin_chat_plan("CREATE TABLE T1 (ID NUMBER);\nINSERT INTO T1 VALUES (1);")
    assert [item.statement_type for item in script.statements] == ["CREATE", "INSERT"]
    assert script.requires_confirmation is True
    assert script.confirmation_target == "ADMIN_EXECUTE"
    assert script.implicit_commit is True
    drop = service._db_admin_chat_plan("DROP TABLE T1 PURGE")
    assert drop.confirmation_target == "T1"
    mixed = service._db_admin_chat_plan("SELECT 1 FROM DUAL; DELETE FROM T1")
    assert mixed.blocked is True


# ---- 実行と監査 ----


def test_select_executes_on_admin_connection_without_confirmation_and_is_audited() -> None:
    adapter = _FakeAdapter()
    service = _Service(adapter)
    with actor_scope("chat-user", is_system_admin=False):
        result = service.execute_db_admin_chat_sql(
            DbAdminChatExecuteRequest(sql="SELECT 1 AS N FROM DUAL", row_limit=50)
        )
    assert result.executed is True
    assert result.execution_context == "admin_control_plane"
    assert adapter.select_calls == [("SELECT 1 AS N FROM DUAL", 50, True)]
    [audit] = service._admin_audit_store.list_recent()
    assert audit.source == "db_admin_chat"
    assert audit.operation == "db_admin_chat_execute"
    assert audit.outcome == "success"
    assert audit.statement_types == ["SELECT"]
    assert audit.confirmation_given is False
    assert audit.committed is False
    assert audit.actor_user_uuid == "chat-user"
    assert audit.sql_text == "SELECT 1 AS N FROM DUAL"
    assert audit.detail["returned_rows"] == 1


def test_mutation_requires_confirmation_phrase_before_executing() -> None:
    adapter = _FakeAdapter(
        [
            {"index": 1, "statement_type": "INSERT", "status": "success", "row_count": 2},
            {"index": 2, "statement_type": "UPDATE", "status": "success", "row_count": 3},
        ]
    )
    service = _Service(adapter)
    sql = "INSERT INTO T1 SELECT * FROM T2; UPDATE T1 SET C1 = 0"
    pending = service.execute_db_admin_chat_sql(DbAdminChatExecuteRequest(sql=sql))
    assert pending.executed is False
    assert {item.status for item in pending.statements} == {"confirmation_required"}
    assert adapter.statement_calls == []
    assert service._admin_audit_store.list_recent() == []

    result = service.execute_db_admin_chat_sql(
        DbAdminChatExecuteRequest(sql=sql, confirmation="ADMIN_EXECUTE")
    )
    assert result.committed is True
    # DML だけの複数の文も、全部成功したときだけ commit する（部分の commit の経路へ渡さない）。
    assert adapter.statement_calls == [
        (["INSERT INTO T1 SELECT * FROM T2", "UPDATE T1 SET C1 = 0"], True)
    ]
    [audit] = service._admin_audit_store.list_recent()
    assert audit.outcome == "success"
    assert audit.confirmation_given is True
    assert audit.affected_rows == 5
    assert audit.statement_types == ["INSERT", "UPDATE"]


def test_single_object_drop_requires_the_object_name(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _FakeAdapter([{"index": 1, "statement_type": "DROP", "status": "success"}])
    service = _Service(adapter)
    monkeypatch.setattr(
        service,
        "_submit_schema_refresh_after_admin_mutation",
        lambda **_kwargs: SchemaRefreshMutationSync(job_id="refresh-1"),
    )
    rejected = service.execute_db_admin_chat_sql(
        DbAdminChatExecuteRequest(sql="DROP TABLE T1 PURGE", confirmation="ADMIN_EXECUTE")
    )
    assert rejected.executed is False
    assert "confirmation=T1" in rejected.warnings[0]
    assert adapter.statement_calls == []
    accepted = service.execute_db_admin_chat_sql(
        DbAdminChatExecuteRequest(sql="DROP TABLE T1 PURGE", confirmation="T1")
    )
    assert accepted.committed is True
    assert accepted.schema_refresh_job_id == "refresh-1"


@pytest.mark.parametrize(
    "sql",
    ["ALTER SYSTEM SET OPEN_CURSORS = 300", "SELECT * FROM NL2SQL_PROFILES", "DROP USER U1"],
)
def test_blocked_statements_are_not_executed_and_are_audited(sql: str) -> None:
    adapter = _FakeAdapter([{"index": 1, "statement_type": "ALTER", "status": "success"}])
    service = _Service(adapter)
    result = service.execute_db_admin_chat_sql(
        DbAdminChatExecuteRequest(sql=sql, confirmation="ADMIN_EXECUTE")
    )
    assert result.executed is False
    assert {item.status for item in result.statements} == {"blocked"}
    assert adapter.statement_calls == []
    assert adapter.select_calls == []
    [audit] = service._admin_audit_store.list_recent()
    assert audit.outcome == "blocked"
    assert audit.executed is False


# ---- 生成と分析 ----


def _catalog_service(adapter: _FakeAdapter, ai: _FakeEnterpriseAi) -> _Service:
    service = _Service(adapter)
    service._enterprise_ai_client = ai
    service._catalog = SchemaCatalog(
        refreshed_at="2026-10-18T00:00:00+00:00",
        current_owner="APP",
        tables=[
            SchemaTable(
                table_name="ITEM_STOCK",
                logical_name="",
                owner="APP",
                columns=[SchemaColumn(column_name="QTY", logical_name="", data_type="NUMBER")],
            ),
            SchemaTable(table_name="OTHER_TABLE", logical_name="", owner="APP"),
        ],
    )
    return service


def test_generate_returns_plan_without_executing() -> None:
    adapter = _FakeAdapter()
    ai = _FakeEnterpriseAi(
        [{"sql": "```sql\nDELETE FROM APP.ITEM_STOCK WHERE QTY = 0\n```", "explanation": "説明"}]
    )
    service = _catalog_service(adapter, ai)
    data = service.generate_db_admin_chat_sql(
        DbAdminChatGenerateRequest(
            question="ITEM_STOCK の QTY が 0 の行を消す",
            history=[DbAdminChatHistoryTurn(question="前の質問", sql="SELECT 1 FROM DUAL")],
        )
    )
    assert data.plan.sql == "DELETE FROM APP.ITEM_STOCK WHERE QTY = 0"
    assert data.plan.requires_confirmation is True
    assert data.plan.confirmation_target == "ADMIN_EXECUTE"
    assert data.context_tables == ["APP.ITEM_STOCK"]
    assert data.model == "test-text-model"
    assert adapter.statement_calls == [] and adapter.select_calls == []
    [call] = ai.calls
    assert "V$VERSION" in call["system_prompt"]
    assert "DBA_TABLESPACE_USAGE_METRICS" in call["system_prompt"]
    assert "table APP.ITEM_STOCK" in call["context"]
    assert "APP.OTHER_TABLE" in call["context"]
    assert "前の質問" in call["context"]


def test_generate_requires_enterprise_ai() -> None:
    service = _catalog_service(_FakeAdapter(), _FakeEnterpriseAi([], configured=False))
    with pytest.raises(DbAdminChatGenerationError) as error:
        service.generate_db_admin_chat_sql(DbAdminChatGenerateRequest(question="版"))
    assert error.value.status_code == 409


def test_generate_with_empty_sql_keeps_the_explanation() -> None:
    ai = _FakeEnterpriseAi([{"sql": "", "explanation": "方針で実行できません"}])
    service = _catalog_service(_FakeAdapter(), ai)
    data = service.generate_db_admin_chat_sql(
        DbAdminChatGenerateRequest(question="インスタンスを停止して")
    )
    assert data.plan.statements == []
    assert data.explanation == "方針で実行できません"


def test_analyze_returns_corrected_plan() -> None:
    ai = _FakeEnterpriseAi(
        [{"analysis": "1) 原因 2) 直し方 3) 結論", "corrected_sql": "DROP TABLE T2 PURGE"}]
    )
    service = _catalog_service(_FakeAdapter(), ai)
    data = service.analyze_db_admin_chat_failure(
        DbAdminChatAnalyzeRequest(sql="DROP TABLE T1", result_text="ORA-00942")
    )
    assert data.source == "oci_enterprise_ai"
    assert data.corrected_plan is not None
    assert data.corrected_plan.confirmation_target == "T2"


def test_analyze_falls_back_without_enterprise_ai() -> None:
    service = _catalog_service(_FakeAdapter(), _FakeEnterpriseAi([], configured=False))
    data = service.analyze_db_admin_chat_failure(
        DbAdminChatAnalyzeRequest(sql="SELECT * FROM T9", result_text="ORA-00942: 表がありません")
    )
    assert "ORA-00942" in data.analysis
    assert data.corrected_plan is None
    assert data.warnings
