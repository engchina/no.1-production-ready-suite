"""テーブル/ビュー/データ管理(SQL Assist 移植)API のテスト。"""

from __future__ import annotations

import importlib
import io
from datetime import datetime
from typing import Any, cast

import pytest

from app.features.nl2sql.enterprise_ai_client import EnterpriseAiDirectError
from app.features.nl2sql.models import (
    DbAdminAiAnalysisRequest,
    DbAdminCsvUploadRequest,
    DbAdminDataPreviewRequest,
    DbAdminDropViewRequest,
    DbAdminImportTabularRequest,
    DbAdminJoinWhereRequest,
    DbAdminStatementsRequest,
    MetadataSqlGenerateRequest,
    SampleDataMutationRequest,
    SampleDataStep,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.oracle_adapter import _flexible_date_value
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore


class FakeEnterpriseAiClient:
    def __init__(self, *responses: str | Exception, configured: bool = True) -> None:
        self.responses = list(responses)
        self.configured = configured

    def is_configured(self) -> bool:
        return self.configured

    def model_id(self) -> str:
        return "fake-enterprise-ai"

    def generate(self, *, prompt: str, context: str, system_prompt: str) -> str:
        if not self.responses:
            return ""
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeStatementsAdapter:
    """execute_admin_statements の結果を固定で返す fake adapter。"""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results
        self.calls: list[tuple[list[str], bool]] = []

    def execute_admin_statements(
        self, statements: list[str], *, atomic: bool = True
    ) -> list[dict[str, Any]]:
        self.calls.append((statements, atomic))
        return self.results

    def fetch_catalog(self) -> SchemaCatalog:
        return SchemaCatalog(refreshed_at="2026-07-10T00:00:00+00:00", tables=[])


class _OracleRuntimeService(Nl2SqlService):
    def __init__(self, adapter: Any) -> None:
        super().__init__(store=MemoryNl2SqlStore())
        self._oracle_adapter = adapter

    def _use_oracle_runtime(self) -> bool:
        return True


def _import_sample(service: Nl2SqlService) -> None:
    service.import_sample_data(
        SampleDataMutationRequest(
            step=SampleDataStep.ALL,
            execute=True,
            confirmation="SQL_ASSIST_SAMPLE",
        )
    )


def test_statement_policy_table_ddl_accepts_and_blocks() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    allowed = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql=(
                "CREATE TABLE T1 (ID NUMBER);\n"
                "CREATE GLOBAL TEMPORARY TABLE T2 (ID NUMBER);\n"
                "COMMENT ON TABLE T1 IS 'テスト';\n"
                "COMMENT ON COLUMN T1.ID IS 'ID';\n"
                "DROP TABLE T1"
            ),
            policy="table_ddl",
        )
    )
    assert allowed.executed is False
    assert [item.status for item in allowed.statements] == ["dry_run"] * 5

    blocked = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="CREATE TABLE T1 (ID NUMBER); GRANT SELECT ON T1 TO PUBLIC",
            policy="table_ddl",
        )
    )
    assert blocked.executed is False
    assert blocked.statements[0].status == "dry_run"
    assert blocked.statements[1].status == "blocked"
    assert "禁止された操作" in blocked.statements[1].error_message
    assert any("禁止された操作" in warning for warning in blocked.warnings)


def test_statement_policy_view_ddl_and_data_dml() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    view_ok = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql=(
                "CREATE OR REPLACE FORCE EDITIONABLE VIEW V1 AS SELECT 1 AS C FROM DUAL;\n"
                "COMMENT ON TABLE V1 IS 'ビュー';\n"
                "DROP VIEW V1"
            ),
            policy="view_ddl",
        )
    )
    assert [item.status for item in view_ok.statements] == ["dry_run"] * 3

    view_ng = service.execute_db_admin_statements(
        DbAdminStatementsRequest(sql="CREATE TABLE T1 (ID NUMBER)", policy="view_ddl")
    )
    assert view_ng.statements[0].status == "blocked"

    dml_ok = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql=(
                "INSERT INTO T1 VALUES (1);\nUPDATE T1 SET ID = 2;\n"
                "DELETE FROM T1;\nMERGE INTO T1 USING DUAL ON (1=1) "
                "WHEN MATCHED THEN UPDATE SET ID = 3;\nTRUNCATE TABLE T1"
            ),
            policy="data_dml",
        )
    )
    assert [item.status for item in dml_ok.statements] == ["dry_run"] * 5

    select_ng = service.execute_db_admin_statements(
        DbAdminStatementsRequest(sql="SELECT * FROM T1", policy="data_dml")
    )
    assert select_ng.statements[0].status == "blocked"


def test_statement_policy_comment_and_annotation_sql() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    comment_ok = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql=(
                "COMMENT ON TABLE T1 IS 'テーブル';\n"
                "COMMENT ON COLUMN T1.ID IS 'ID';\n"
                "COMMENT ON VIEW V1 IS 'ビュー';\n"
                "COMMENT ON MATERIALIZED VIEW MV1 IS 'MV'"
            ),
            policy="comment_sql",
        )
    )
    assert [item.status for item in comment_ok.statements] == ["dry_run"] * 4

    comment_ng = service.execute_db_admin_statements(
        DbAdminStatementsRequest(sql="CREATE TABLE T1 (ID NUMBER)", policy="comment_sql")
    )
    assert comment_ng.statements[0].status == "blocked"

    annotation_ok = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql=(
                "ALTER TABLE T1 ANNOTATIONS (UI_Display 'T1');\n"
                "ALTER TABLE T1 MODIFY (ID ANNOTATIONS (UI_Display 'ID'));\n"
                "ALTER TABLE T1 MODIFY ID ANNOTATIONS (UI_Display 'ID');\n"
                "ALTER VIEW V1 ANNOTATIONS (UI_Display 'V1')"
            ),
            policy="annotation_sql",
        )
    )
    assert [item.status for item in annotation_ok.statements] == ["dry_run"] * 4

    annotation_ng = service.execute_db_admin_statements(
        DbAdminStatementsRequest(sql="ALTER TABLE T1 ADD C1 NUMBER", policy="annotation_sql")
    )
    assert annotation_ng.statements[0].status == "blocked"


def test_statements_execute_requires_confirmation_and_oracle_runtime() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    missing_confirmation = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="CREATE TABLE T1 (ID NUMBER)",
            policy="table_ddl",
            execute=True,
        )
    )
    assert missing_confirmation.statements[0].status == "confirmation_required"

    requires_oracle = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="CREATE TABLE T1 (ID NUMBER)",
            policy="table_ddl",
            execute=True,
            confirmation="ADMIN_EXECUTE",
        )
    )
    assert requires_oracle.statements[0].status == "requires_oracle"
    assert any("NL2SQL_RUNTIME_MODE=oracle" in warning for warning in requires_oracle.warnings)


def test_statements_partial_success_commits_and_records_audit() -> None:
    adapter = _FakeStatementsAdapter(
        [
            {
                "index": 1,
                "statement_type": "CREATE",
                "status": "success",
                "sql": "CREATE TABLE T1 (ID NUMBER)",
                "message": "実行しました。",
            },
            {
                "index": 2,
                "statement_type": "COMMENT",
                "status": "error",
                "sql": "COMMENT ON TABLE MISSING IS 'x'",
                "error_message": "ORA-00942: table or view does not exist",
            },
        ]
    )
    service = _OracleRuntimeService(adapter)

    result = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="CREATE TABLE T1 (ID NUMBER); COMMENT ON TABLE MISSING IS 'x'",
            policy="table_ddl",
            execute=True,
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert adapter.calls and adapter.calls[0][1] is False  # atomic=False
    assert result.executed is True
    assert result.committed is True
    assert result.rolled_back is False
    assert any("部分的に成功" in warning for warning in result.warnings)
    assert any(
        item["operation"] == "db_admin_statements_table_ddl" for item in service._admin_audit
    )


def test_metadata_sql_generation_fallback_and_fence_cleanup() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient(configured=False)
    fallback = service.generate_comment_sql(
        MetadataSqlGenerateRequest(
            targets=[{"object_name": "EMPLOYEE", "object_type": "table"}],
            structure_text="OBJECT: EMPLOYEE",
        )
    )
    assert "COMMENT ON TABLE" in fallback.sql
    assert fallback.source == "deterministic"
    assert any("OCI Enterprise AI" in warning for warning in fallback.warnings)

    service._enterprise_ai_client = FakeEnterpriseAiClient(
        "```sql\nCOMMENT ON VIEW V_EMP IS '社員ビュー';\n```"
    )
    comment_ai = service.generate_comment_sql(
        MetadataSqlGenerateRequest(
            targets=[{"object_name": "V_EMP", "object_type": "view"}],
            structure_text="OBJECT: V_EMP",
        )
    )
    assert comment_ai.source == "oci_enterprise_ai"
    assert comment_ai.sql == "COMMENT ON VIEW V_EMP IS '社員ビュー';"

    service._enterprise_ai_client = FakeEnterpriseAiClient(
        "```sql\n"
        "ALTER TABLE EMPLOYEE MODIFY (EMPLOYEE_NAME ANNOTATIONS (UI_Display '社員名'));\n"
        "```"
    )
    annotation_ai = service.generate_annotation_sql(
        MetadataSqlGenerateRequest(
            targets=[{"object_name": "EMPLOYEE", "object_type": "table"}],
            structure_text="OBJECT: EMPLOYEE",
        )
    )
    assert annotation_ai.source == "oci_enterprise_ai"
    assert annotation_ai.sql == (
        "ALTER TABLE EMPLOYEE MODIFY "
        "(EMPLOYEE_NAME ANNOTATIONS (UI_Display '社員名'));"
    )


def test_drop_view_confirmation_and_requires_oracle() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    dry_run = service.drop_db_admin_view(DbAdminDropViewRequest(view_name="V_EMP_DEPT"))
    assert dry_run.executed is False
    assert dry_run.statements[0].status == "dry_run"
    assert 'DROP VIEW "V_EMP_DEPT"' in dry_run.statements[0].sql

    missing = service.drop_db_admin_view(
        DbAdminDropViewRequest(view_name="V_EMP_DEPT", execute=True)
    )
    assert missing.statements[0].status == "confirmation_required"

    requires_oracle = service.drop_db_admin_view(
        DbAdminDropViewRequest(view_name="V_EMP_DEPT", execute=True, confirmation="V_EMP_DEPT")
    )
    assert requires_oracle.statements[0].status == "requires_oracle"


def test_preview_data_builds_guarded_select() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    plain = service.preview_db_admin_data(DbAdminDataPreviewRequest(object_name="INVOICES"))
    assert plain.runtime == "deterministic"
    assert plain.sql == 'SELECT * FROM "INVOICES" FETCH FIRST 100 ROWS ONLY'
    assert plain.results.columns

    filtered = service.preview_db_admin_data(
        DbAdminDataPreviewRequest(
            object_name="INVOICES",
            limit=10,
            where_clause="STATUS = 'A' AND TOTAL_AMOUNT > 100",
        )
    )
    assert "WHERE STATUS = 'A' AND TOTAL_AMOUNT > 100" in filtered.sql
    assert filtered.sql.endswith("FETCH FIRST 10 ROWS ONLY")

    with pytest.raises(ValueError, match="複数 statement"):
        service.preview_db_admin_data(
            DbAdminDataPreviewRequest(
                object_name="INVOICES",
                where_clause="1=1; DROP TABLE INVOICES",
            )
        )

    # 先頭の WHERE キーワードは重複しないよう正規化される
    normalized = service.preview_db_admin_data(
        DbAdminDataPreviewRequest(object_name="INVOICES", where_clause="WHERE STATUS = 'X'")
    )
    assert "WHERE STATUS = 'X'" in normalized.sql
    assert "WHERE WHERE" not in normalized.sql


def test_preview_data_exports_xlsx() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    filename, content = service.export_db_admin_preview_xlsx(
        DbAdminDataPreviewRequest(object_name="INVOICES", limit=10, where_clause="STATUS = 'A'")
    )

    openpyxl = importlib.import_module("openpyxl")
    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    assert filename == "invoices_preview.xlsx"
    assert workbook.sheetnames == ["data", "query"]
    assert workbook["data"].max_row >= 1
    assert (
        workbook["query"]["A2"].value
        == 'SELECT * FROM "INVOICES" WHERE STATUS = \'A\' FETCH FIRST 10 ROWS ONLY'
    )


def test_table_export_xlsx_contains_column_information_only() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = SchemaCatalog(
        refreshed_at="2026-07-10T00:00:00+00:00",
        tables=[
            SchemaTable(
                table_name="INVOICES",
                logical_name="請求",
                owner="APP",
                row_count=2,
                comment="請求情報",
                columns=[
                    SchemaColumn(
                        column_name="CUSTOMER_NAME",
                        logical_name="取引先名",
                        data_type="VARCHAR2(120)",
                        nullable=False,
                        comment="取引先名",
                        sample_values=["青山商事"],
                    ),
                    SchemaColumn(
                        column_name="TOTAL_AMOUNT",
                        logical_name="請求金額",
                        data_type="NUMBER",
                        nullable=False,
                        comment="税込請求金額",
                        sample_values=["1200000"],
                    ),
                ],
            )
        ],
    )

    filename, content = service.export_db_admin_table_xlsx("INVOICES")

    openpyxl = importlib.import_module("openpyxl")
    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    assert filename == "invoices_columns.xlsx"
    assert workbook.sheetnames == ["columns"]
    sheet = workbook["columns"]
    assert [sheet.cell(row=1, column=index).value for index in range(1, 6)] == [
        "物理名",
        "論理名",
        "型",
        "NULL 可",
        "サンプル",
    ]
    assert [sheet.cell(row=2, column=index).value for index in range(1, 6)] == [
        "CUSTOMER_NAME",
        "取引先名",
        "VARCHAR2(120)",
        "NO",
        "青山商事",
    ]


def test_upload_csv_dry_run_matches_catalog_columns() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    _import_sample(service)
    table = service._catalog.tables[0]
    known_column = table.columns[0].column_name
    csv_text = f"{known_column},UNKNOWN_COLUMN\n1,foo\n2,bar\n"
    import base64

    result = service.upload_db_admin_csv(
        DbAdminCsvUploadRequest(
            table_name=table.table_name,
            content_base64=base64.b64encode(csv_text.encode()).decode(),
            filename="upload.csv",
        )
    )
    assert result.dry_run is True
    assert result.executed is False
    assert known_column.upper() in result.matched_columns
    assert "UNKNOWN_COLUMN" in result.unmatched_csv_columns
    assert result.row_count == 2

    requires_oracle = service.upload_db_admin_csv(
        DbAdminCsvUploadRequest(
            table_name=table.table_name,
            content_base64=base64.b64encode(csv_text.encode()).decode(),
            execute=True,
            confirmation=result.table_name,
        )
    )
    assert requires_oracle.executed is False
    assert any("NL2SQL_RUNTIME_MODE=oracle" in warning for warning in requires_oracle.warnings)


def test_import_tabular_execute_requires_admin_execute_confirmation() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    import base64

    content_base64 = base64.b64encode("ORDER_ID,ORDER_NAME\n1,青山商事\n".encode()).decode()
    table_confirmation = service.import_db_admin_tabular(
        DbAdminImportTabularRequest(
            table_name="IMPORTED_ORDERS",
            content_base64=content_base64,
            filename="orders.csv",
            execute=True,
            confirmation="IMPORTED_ORDERS",
        )
    )
    assert table_confirmation.executed is False
    assert any("confirmation=ADMIN_EXECUTE" in warning for warning in table_confirmation.warnings)

    admin_confirmation = service.import_db_admin_tabular(
        DbAdminImportTabularRequest(
            table_name="IMPORTED_ORDERS",
            content_base64=content_base64,
            filename="orders.csv",
            execute=True,
            confirmation="ADMIN_EXECUTE",
        )
    )
    assert admin_confirmation.executed is False
    assert any("NL2SQL_RUNTIME_MODE=oracle" in warning for warning in admin_confirmation.warnings)


def test_flexible_date_value_parses_common_formats() -> None:
    assert _flexible_date_value("2026-01-31") == datetime(2026, 1, 31)
    assert _flexible_date_value("2026/01/31") == datetime(2026, 1, 31)
    assert _flexible_date_value("20260131") == datetime(2026, 1, 31)
    assert _flexible_date_value("2026-01-31 12:34:56") == datetime(2026, 1, 31, 12, 34, 56)
    assert _flexible_date_value("2026-01-31T12:34:56") == datetime(2026, 1, 31, 12, 34, 56)
    assert _flexible_date_value("2026-01-31 12:34:56.789") == datetime(2026, 1, 31, 12, 34, 56)
    assert _flexible_date_value("2026年01月31日") == datetime(2026, 1, 31)
    # Excel シリアル日付(1899-12-30 起点): 45658 = 2025-01-01
    assert _flexible_date_value("45658") == datetime(2025, 1, 1)
    assert _flexible_date_value("not-a-date") is None
    assert _flexible_date_value("") is None


def test_analyze_error_uses_enterprise_ai_and_falls_back() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient(
        "1) エラー原因: 表が既に存在します。\n2) 解決方法: DROP してください。\n3) 結論: 再実行。"
    )
    analyzed = service.analyze_db_admin_failure(
        DbAdminAiAnalysisRequest(
            sql="CREATE TABLE T1 (ID NUMBER)",
            result_text="ORA-00955: name is already used by an existing object",
        )
    )
    assert analyzed.source == "oci_enterprise_ai"
    assert "エラー原因" in analyzed.analysis

    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient(
        EnterpriseAiDirectError("boom")
    )
    fallback = service.analyze_db_admin_failure(
        DbAdminAiAnalysisRequest(
            sql="CREATE TABLE T1 (ID NUMBER)",
            result_text="ORA-00955: name is already used by an existing object",
        )
    )
    assert fallback.source == "deterministic"
    assert "ORA-00955" in fallback.analysis
    assert fallback.warnings

    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient(configured=False)
    unconfigured = service.analyze_db_admin_failure(
        DbAdminAiAnalysisRequest(sql="INSERT INTO T1 VALUES (1)", result_text="成功", target="data")
    )
    assert unconfigured.source == "deterministic"
    assert unconfigured.warnings


def test_extract_join_where_parses_strict_format_and_falls_back() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient(
        "JOIN:\n[INNER] e(EMPLOYEE).DEPT_ID = d(DEPARTMENT).DEPT_ID\nWHERE:\ne.STATUS = 'A'"
    )
    ddl = (
        'CREATE OR REPLACE VIEW "V_EMP_DEPT" AS\n'
        "SELECT e.EMP_ID, d.DEPT_NAME FROM EMPLOYEE e "
        "JOIN DEPARTMENT d ON e.DEPT_ID = d.DEPT_ID WHERE e.STATUS = 'A'"
    )
    extracted = service.extract_db_admin_join_where(DbAdminJoinWhereRequest(ddl=ddl))
    assert extracted.source == "oci_enterprise_ai"
    assert "EMPLOYEE" in extracted.join_text
    assert extracted.where_text == "e.STATUS = 'A'"

    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient("整形されていない応答")
    fallback = service.extract_db_admin_join_where(DbAdminJoinWhereRequest(ddl=ddl))
    assert fallback.source == "deterministic"
    assert fallback.warnings
    assert "JOIN" in fallback.join_text.upper() or fallback.join_text == "None"
    assert fallback.where_text != ""
