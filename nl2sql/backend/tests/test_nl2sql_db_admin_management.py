"""テーブル/ビュー/データ管理(SQL Assist 移植)API のテスト。"""

from __future__ import annotations

import importlib
import io
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.features.nl2sql.enterprise_ai_client import EnterpriseAiDirectError
from app.features.nl2sql.models import (
    AssetRefreshData,
    DbAdminAiAnalysisRequest,
    DbAdminCsvUploadRequest,
    DbAdminDataPreviewRequest,
    DbAdminDropViewRequest,
    DbAdminImportTabularRequest,
    DbAdminJoinWhereRequest,
    DbAdminStatementsRequest,
    MetadataSqlGenerateRequest,
    MetadataSqlSampleRequest,
    Nl2SqlEngine,
    Nl2SqlProfile,
    SampleDataMutationRequest,
    SampleDataStep,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.oracle_adapter import (
    OracleAdapterError,
    _flexible_date_value,
    _normalize_select_ai_object_list,
)
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore


class FakeEnterpriseAiClient:
    def __init__(self, *responses: str | Exception, configured: bool = True) -> None:
        self.responses = list(responses)
        self.configured = configured
        self.calls: list[dict[str, str]] = []

    def is_configured(self) -> bool:
        return self.configured

    def model_id(self) -> str:
        return "fake-enterprise-ai"

    def generate(self, *, prompt: str, context: str, system_prompt: str) -> str:
        self.calls.append({"prompt": prompt, "context": context, "system_prompt": system_prompt})
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


class _FakeMetadataSamplesAdapter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[list[dict[str, Any]], int]] = []

    def fetch_metadata_sample_values(
        self, targets: list[dict[str, Any]], sample_limit: int
    ) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
        self.calls.append((targets, sample_limit))
        if self.fail:
            raise OracleAdapterError("接続できません")
        return {"EMPLOYEE": {"EMPLOYEE_NAME": ["山田", "佐藤"]}}, []


class _OracleRuntimeService(Nl2SqlService):
    def __init__(self, adapter: Any) -> None:
        super().__init__(store=MemoryNl2SqlStore())
        self._oracle_adapter = adapter

    def _use_oracle_runtime(self) -> bool:
        return True


class _FakeSelectAiProfileAdapter:
    """DBMS_CLOUD_AI profile list/detail を固定で返す fake adapter。"""

    def list_select_ai_profiles(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "FINANCE_SELECT_AI",
                "status": "available",
                "description": "財務 profile",
                "created_at": "2026-07-10T00:00:00+00:00",
            }
        ]

    def get_select_ai_profile_detail(self, *, profile_name: str) -> dict[str, Any]:
        assert profile_name == "FINANCE_SELECT_AI"
        return {
            "name": profile_name,
            "attributes": {
                "provider": "oci",
                "region": "ap-osaka-1",
                "model": "cohere.command-r-plus",
                "embedding_model": "cohere.embed-v4.0",
                "object_list": [
                    {"owner": "APP", "name": "INVOICES"},
                    {"owner": "APP", "name": "V_INVOICE_SUMMARY"},
                ],
            },
        }


class _FakeMixedSelectAiProfileAdapter:
    """業務 profile 由来/無関係の DBMS_CLOUD_AI profile を混在させる fake adapter。"""

    def __init__(self) -> None:
        self.detail_calls: list[str] = []

    def list_select_ai_profiles(self) -> list[dict[str, Any]]:
        return [
            {"name": "FINANCE_SELECT_AI", "status": "available"},
            {"name": "ARCHIVED_SELECT_AI", "status": "available"},
            {"name": "NL2SQL_DERIVED_FILTER_PROFILE", "status": "available"},
            {"name": "MANUAL_SELECT_AI", "status": "available"},
        ]

    def get_select_ai_profile_detail(self, *, profile_name: str) -> dict[str, Any]:
        self.detail_calls.append(profile_name)
        return {
            "name": profile_name,
            "attributes": {
                "provider": "oci",
                "object_list": [{"owner": "APP", "name": "INVOICES"}],
            },
        }


def _import_sample(service: Nl2SqlService) -> None:
    service.import_sample_data(
        SampleDataMutationRequest(
            step=SampleDataStep.ALL,
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
    assert [item.status for item in allowed.statements] == ["confirmation_required"] * 5

    blocked = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="CREATE TABLE T1 (ID NUMBER); GRANT SELECT ON T1 TO PUBLIC",
            policy="table_ddl",
        )
    )
    assert blocked.executed is False
    assert blocked.statements[0].status == "blocked"
    assert blocked.statements[1].status == "blocked"
    assert "禁止された操作" in blocked.statements[1].error_message
    assert any("禁止された操作" in warning for warning in blocked.warnings)


def test_select_ai_db_profiles_include_detail_enriches_objects_and_models() -> None:
    service = _OracleRuntimeService(_FakeSelectAiProfileAdapter())
    cast(Any, service)._catalog = SchemaCatalog(
        refreshed_at="2026-07-10T00:00:00+00:00",
        tables=[
            SchemaTable(table_name="INVOICES", logical_name="請求", table_type="TABLE"),
            SchemaTable(
                table_name="V_INVOICE_SUMMARY",
                logical_name="請求サマリ",
                table_type="VIEW",
            ),
        ],
    )

    data = service.list_select_ai_db_profiles(include_detail=True)

    assert data.runtime == "oracle"
    assert data.warnings == []
    assert len(data.profiles) == 1
    profile = data.profiles[0]
    assert profile.name == "FINANCE_SELECT_AI"
    assert profile.tables == ["INVOICES"]
    assert profile.views == ["V_INVOICE_SUMMARY"]
    assert profile.region == "ap-osaka-1"
    assert profile.model == "cohere.command-r-plus"
    assert profile.embedding_model == "cohere.embed-v4.0"


def test_select_ai_db_profiles_can_filter_to_business_profile_names() -> None:
    adapter = _FakeMixedSelectAiProfileAdapter()
    service = _OracleRuntimeService(adapter)
    service.create_profile(
        Nl2SqlProfile(
            id="finance_filter",
            name="財務プロファイル",
            description="明示 profile 名で照合する。",
            allowed_tables=["INVOICES"],
            glossary={},
            sql_rules=[],
            default_row_limit=100,
            few_shot_examples=[],
            select_ai_config={"profile_name": "FINANCE_SELECT_AI"},
        )
    )
    service.create_profile(
        Nl2SqlProfile(
            id="archived_filter",
            name="アーカイブプロファイル",
            description="archived も既定では照合対象にする。",
            allowed_tables=["INVOICES"],
            glossary={},
            sql_rules=[],
            default_row_limit=100,
            few_shot_examples=[],
            select_ai_config={"profile_name": "ARCHIVED_SELECT_AI"},
            archived=True,
        )
    )
    service.create_profile(
        Nl2SqlProfile(
            id="derived_filter",
            name="導出名プロファイル",
            description="profile_name 空欄時は既存導出名で照合する。",
            allowed_tables=["INVOICES"],
            glossary={},
            sql_rules=[],
            default_row_limit=100,
            few_shot_examples=[],
        )
    )

    data = service.list_select_ai_db_profiles(
        include_detail=True,
        business_profiles_only=True,
        include_archived_business_profiles=True,
    )

    assert [profile.name for profile in data.profiles] == [
        "FINANCE_SELECT_AI",
        "ARCHIVED_SELECT_AI",
        "NL2SQL_DERIVED_FILTER_PROFILE",
    ]
    assert "MANUAL_SELECT_AI" not in adapter.detail_calls

    active_only = service.list_select_ai_db_profiles(
        business_profiles_only=True,
        include_archived_business_profiles=False,
    )

    assert [profile.name for profile in active_only.profiles] == [
        "FINANCE_SELECT_AI",
        "NL2SQL_DERIVED_FILTER_PROFILE",
    ]


def test_select_ai_db_profiles_filter_also_applies_to_asset_metadata_fallback() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service.create_profile(
        Nl2SqlProfile(
            id="finance_filter",
            name="財務プロファイル",
            description="deterministic fallback の照合対象。",
            allowed_tables=["INVOICES"],
            glossary={},
            sql_rules=[],
            default_row_limit=100,
            few_shot_examples=[],
            select_ai_config={"profile_name": "FINANCE_SELECT_AI"},
        )
    )
    cast(Any, service)._asset_meta = {
        Nl2SqlEngine.SELECT_AI: AssetRefreshData(
            engine=Nl2SqlEngine.SELECT_AI,
            refreshed=True,
            status="ready",
            profile_name="FINANCE_SELECT_AI",
        ),
        Nl2SqlEngine.SELECT_AI_AGENT: AssetRefreshData(
            engine=Nl2SqlEngine.SELECT_AI_AGENT,
            refreshed=True,
            status="ready",
            profile_name="MANUAL_SELECT_AI",
        ),
    }

    data = service.list_select_ai_db_profiles(business_profiles_only=True)

    assert data.runtime == "deterministic"
    assert [profile.name for profile in data.profiles] == ["FINANCE_SELECT_AI"]


def test_select_ai_profiles_export_can_filter_to_business_profile_names() -> None:
    service = _OracleRuntimeService(_FakeMixedSelectAiProfileAdapter())
    service.create_profile(
        Nl2SqlProfile(
            id="finance_filter",
            name="財務プロファイル",
            description="export JSON の照合対象。",
            allowed_tables=["INVOICES"],
            glossary={},
            sql_rules=[],
            default_row_limit=100,
            few_shot_examples=[],
            select_ai_config={"profile_name": "FINANCE_SELECT_AI"},
        )
    )
    service.create_profile(
        Nl2SqlProfile(
            id="archived_filter",
            name="アーカイブプロファイル",
            description="archived の export filter を確認する。",
            allowed_tables=["INVOICES"],
            glossary={},
            sql_rules=[],
            default_row_limit=100,
            few_shot_examples=[],
            select_ai_config={"profile_name": "ARCHIVED_SELECT_AI"},
            archived=True,
        )
    )

    exported = service.export_select_ai_profiles_json(
        business_profiles_only=True,
        include_archived_business_profiles=True,
    )

    assert [profile.name for profile in exported.profiles] == [
        "FINANCE_SELECT_AI",
        "ARCHIVED_SELECT_AI",
    ]

    active_only = service.export_select_ai_profiles_json(
        business_profiles_only=True,
        include_archived_business_profiles=False,
    )

    assert [profile.name for profile in active_only.profiles] == ["FINANCE_SELECT_AI"]


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
    assert [item.status for item in view_ok.statements] == ["confirmation_required"] * 3

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
    assert [item.status for item in dml_ok.statements] == ["confirmation_required"] * 5

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
    assert [item.status for item in comment_ok.statements] == ["confirmation_required"] * 4

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
                "ALTER VIEW V1 ANNOTATIONS (UI_Display 'V1');\n"
                "ALTER TABLE T1 ANNOTATIONS (Business_Label '業務名');\n"
                "ALTER TABLE T1 ANNOTATIONS (ADD IF NOT EXISTS \"COMMENT\" '説明')"
            ),
            policy="annotation_sql",
        )
    )
    assert [item.status for item in annotation_ok.statements] == ["confirmation_required"] * 6

    annotation_ng = service.execute_db_admin_statements(
        DbAdminStatementsRequest(sql="ALTER TABLE T1 ADD C1 NUMBER", policy="annotation_sql")
    )
    assert annotation_ng.statements[0].status == "blocked"


def test_annotation_comment_name_is_blocked_before_oracle_execution() -> None:
    adapter = _FakeStatementsAdapter([])
    service = _OracleRuntimeService(adapter)
    invalid_sql = (
        "ALTER TABLE DEPARTMENT ANNOTATIONS "
        "(ADD IF NOT EXISTS COMMENT '部署情報を管理するテーブル');\n"
        "ALTER TABLE DEPARTMENT MODIFY (DEPARTMENT_ID ANNOTATIONS "
        "(ADD IF NOT EXISTS COMMENT '部署ID。主キー。'));\n"
        "ALTER TABLE DEPARTMENT MODIFY (DEPARTMENT_NAME ANNOTATIONS "
        "(ADD IF NOT EXISTS COMMENT '部署名。'));\n"
        "ALTER TABLE DEPARTMENT MODIFY (LOCATION ANNOTATIONS "
        "(ADD IF NOT EXISTS COMMENT '所在地。'));\n"
        "ALTER TABLE DEPARTMENT MODIFY (CREATED_AT ANNOTATIONS "
        "(ADD IF NOT EXISTS COMMENT 'レコード作成日時。'));"
    )

    result = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql=invalid_sql,
            policy="annotation_sql",
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert result.executed is False
    assert adapter.calls == []
    assert {item.status for item in result.statements} == {"blocked"}
    assert all("ORA-11548" in item.error_message for item in result.statements)
    assert all("UI_Display" in item.error_message for item in result.statements)


def test_statements_execute_requires_confirmation_and_oracle_runtime() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    missing_confirmation = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="CREATE TABLE T1 (ID NUMBER)",
            policy="table_ddl",
        )
    )
    assert missing_confirmation.statements[0].status == "confirmation_required"

    requires_oracle = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="CREATE TABLE T1 (ID NUMBER)",
            policy="table_ddl",
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
        "(EMPLOYEE_NAME ANNOTATIONS (ADD IF NOT EXISTS UI_Display '社員名'));"
    )


def test_annotation_generation_ports_reference_prompt_and_filters_sample_annotations() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    enterprise_ai = FakeEnterpriseAiClient(
        "```sql\n"
        "ALTER TABLE T1 ANNOTATIONS "
        "(UI_Display 'T, One', sample_header 'ID,NAME', sample_data '1,A');\n"
        "```"
    )
    service._enterprise_ai_client = enterprise_ai

    result = service.generate_annotation_sql(
        MetadataSqlGenerateRequest(
            targets=[{"object_name": "T1", "object_type": "table"}],
            structure_text="OBJECT: T1\nTYPE: table\nCOMMENT: T One",
            sample_text="",
        )
    )

    assert result.source == "oci_enterprise_ai"
    assert result.sql == "ALTER TABLE T1 ANNOTATIONS (ADD IF NOT EXISTS UI_Display 'T, One');"
    assert "COMMENT: は入力メタデータ" in enterprise_ai.calls[0]["prompt"]
    assert "sample_header / sample_data を生成しない" in enterprise_ai.calls[0]["prompt"]
    assert "未引用の COMMENT は禁止" in enterprise_ai.calls[0]["system_prompt"]

    with_samples_ai = FakeEnterpriseAiClient(
        "ALTER TABLE T1 ANNOTATIONS "
        "(UI_Display 'T One', sample_header 'ID,NAME', sample_data '1,A');"
    )
    service._enterprise_ai_client = with_samples_ai
    with_samples = service.generate_annotation_sql(
        MetadataSqlGenerateRequest(
            targets=[{"object_name": "T1", "object_type": "table"}],
            structure_text="OBJECT: T1\nTYPE: table\nCOMMENT: T One",
            sample_text="OBJECT: T1\nID: 1\nNAME: A",
        )
    )

    assert "sample_header 'ID,NAME'" in with_samples.sql
    assert "sample_data '1,A'" in with_samples.sql
    assert "sample_header / sample_data を生成可能" in with_samples_ai.calls[0]["prompt"]


def test_invalid_ai_comment_annotation_falls_back_to_idempotent_ui_display_sql() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._enterprise_ai_client = FakeEnterpriseAiClient(
        "ALTER TABLE DEPARTMENT ANNOTATIONS "
        "(ADD IF NOT EXISTS COMMENT '部署情報を管理するテーブル');"
    )

    result = service.generate_annotation_sql(
        MetadataSqlGenerateRequest(
            targets=[{"object_name": "DEPARTMENT", "object_type": "table"}],
            structure_text=(
                "OBJECT: DEPARTMENT\nTYPE: table\n" "COMMENT: 部署情報を管理するテーブル"
            ),
        )
    )

    assert result.source == "deterministic"
    assert "ADD IF NOT EXISTS UI_Display" in result.sql
    assert "ADD IF NOT EXISTS COMMENT" not in result.sql
    assert any("ORA-11548" in warning for warning in result.warnings)


def test_multi_annotation_generation_makes_every_add_idempotent() -> None:
    # ADD IF NOT EXISTS が後続 annotation に伝播せず素の ADD になる ORA-11560 を防ぐ。
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._enterprise_ai_client = FakeEnterpriseAiClient(
        "ALTER TABLE EMPLOYEE MODIFY (EMPLOYEE_ID ANNOTATIONS "
        "(ADD IF NOT EXISTS UI_Display '従業員ID', data_type 'NUMBER', nullable 'N'));"
    )

    result = service.generate_annotation_sql(
        MetadataSqlGenerateRequest(
            targets=[{"object_name": "EMPLOYEE", "object_type": "table"}],
            structure_text=(
                "OBJECT: EMPLOYEE\nTYPE: table\nCOMMENT: 従業員\n"
                "- EMPLOYEE_ID: NUMBER NULLABLE=N COMMENT=従業員ID"
            ),
        )
    )

    assert result.sql.count("ADD IF NOT EXISTS") == 3
    assert "data_type 'NUMBER'" in result.sql
    assert "ANNOTATIONS (ADD IF NOT EXISTS UI_Display" in result.sql
    # 素の ADD(IF NOT EXISTS 無し)が残っていないこと。
    assert re.search(r"(?<!EXISTS )\bdata_type\b", result.sql) is None


def test_deterministic_annotation_sql_sorts_objects_and_escapes_values() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._enterprise_ai_client = FakeEnterpriseAiClient(configured=False)

    result = service.generate_annotation_sql(
        MetadataSqlGenerateRequest(
            targets=[
                {"object_name": "B_TABLE", "object_type": "table"},
                {"object_name": "A_TABLE", "object_type": "table"},
                {"object_name": "V_TABLE", "object_type": "view"},
            ],
            structure_text=(
                "OBJECT: B_TABLE\nTYPE: table\nCOMMENT: B\n"
                "- B_ID: NUMBER NULLABLE=N COMMENT=B's ID\n\n"
                "OBJECT: A_TABLE\nTYPE: table\nCOMMENT: O'Brien\n"
                "- A_ID: NUMBER NULLABLE=N COMMENT=A ID\n\n"
                "OBJECT: V_TABLE\nTYPE: view\nCOMMENT: View\n"
                "- V_ID: NUMBER NULLABLE=Y COMMENT=View ID"
            ),
        )
    )

    assert result.sql.index('ALTER TABLE "A_TABLE"') < result.sql.index('ALTER TABLE "B_TABLE"')
    assert "O''Brien" in result.sql
    assert "ADD IF NOT EXISTS UI_Display" in result.sql
    assert 'MODIFY ("V_ID"' not in result.sql


def test_metadata_samples_use_requested_limit_and_generation_context() -> None:
    adapter = _FakeMetadataSamplesAdapter()
    service = _OracleRuntimeService(adapter)
    request = MetadataSqlSampleRequest(
        targets=[
            {
                "object_name": "EMPLOYEE",
                "object_type": "table",
                "columns": ["EMPLOYEE_NAME"],
            },
            {
                "object_name": "V_EMPLOYEE",
                "object_type": "view",
                "columns": ["EMPLOYEE_NAME"],
            },
        ],
        sample_limit=10,
    )

    samples = service.get_metadata_samples(request)

    assert adapter.calls[0][1] == 10
    assert adapter.calls[0][0][1]["object_type"] == "view"
    assert samples.sample_count == 2
    assert "EMPLOYEE_NAME: 山田, 佐藤" in samples.sample_text

    enterprise_ai = FakeEnterpriseAiClient("COMMENT ON TABLE EMPLOYEE IS '社員';")
    service._enterprise_ai_client = enterprise_ai
    service.generate_comment_sql(
        MetadataSqlGenerateRequest(
            targets=[{"object_name": "EMPLOYEE", "object_type": "table"}],
            structure_text="OBJECT: EMPLOYEE",
            sample_text=samples.sample_text,
        )
    )
    assert "<サンプル>" in enterprise_ai.calls[0]["context"]
    assert "EMPLOYEE_NAME: 山田, 佐藤" in enterprise_ai.calls[0]["context"]

    empty = service.get_metadata_samples(request.model_copy(update={"sample_limit": 0}))
    assert empty.sample_text == ""
    assert empty.sample_count == 0
    assert len(adapter.calls) == 1


def test_metadata_samples_fall_back_to_catalog_when_oracle_fails() -> None:
    service = _OracleRuntimeService(_FakeMetadataSamplesAdapter(fail=True))
    service._catalog = SchemaCatalog(
        refreshed_at="2026-07-11T00:00:00+00:00",
        tables=[
            SchemaTable(
                table_name="EMPLOYEE",
                logical_name="社員",
                columns=[
                    SchemaColumn(
                        column_name="EMPLOYEE_NAME",
                        logical_name="社員名",
                        data_type="VARCHAR2(100)",
                        sample_values=["山田", "佐藤", "鈴木"],
                    )
                ],
            )
        ],
    )

    samples = service.get_metadata_samples(
        MetadataSqlSampleRequest(
            targets=[
                {
                    "object_name": "EMPLOYEE",
                    "object_type": "table",
                    "columns": ["EMPLOYEE_NAME"],
                }
            ],
            sample_limit=2,
        )
    )

    assert samples.sample_text == "OBJECT: EMPLOYEE\nEMPLOYEE_NAME: 山田, 佐藤"
    assert samples.sample_count == 2
    assert any("既存値" in warning for warning in samples.warnings)


def test_drop_view_confirmation_and_requires_oracle() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    missing = service.drop_db_admin_view(DbAdminDropViewRequest(view_name="V_EMP_DEPT"))
    assert missing.executed is False
    assert missing.statements[0].status == "confirmation_required"
    assert 'DROP VIEW "V_EMP_DEPT"' in missing.statements[0].sql

    requires_oracle = service.drop_db_admin_view(
        DbAdminDropViewRequest(view_name="V_EMP_DEPT", confirmation="V_EMP_DEPT")
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
        == "SELECT * FROM \"INVOICES\" WHERE STATUS = 'A' FETCH FIRST 10 ROWS ONLY"
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
    assert [sheet.cell(row=1, column=index).value for index in range(1, 7)] == [
        "物理名",
        "論理名",
        "コメント",
        "型",
        "NULL 可",
        "サンプル",
    ]
    # 論理名はオントロジー業務名のみを正とするため、未構築時は空(表示は "-")
    assert [sheet.cell(row=2, column=index).value for index in range(1, 7)] == [
        "CUSTOMER_NAME",
        "-",
        "取引先名",
        "VARCHAR2(120)",
        "NO",
        "青山商事",
    ]


def _invoices_catalog(*, column_logical: str, column_comment: str) -> SchemaCatalog:
    return SchemaCatalog(
        refreshed_at="2026-07-10T00:00:00+00:00",
        tables=[
            SchemaTable(
                table_name="INVOICES",
                logical_name="請求",
                owner="APP",
                columns=[
                    SchemaColumn(
                        column_name="CUSTOMER_NAME",
                        logical_name=column_logical,
                        data_type="VARCHAR2(120)",
                        nullable=False,
                        comment=column_comment,
                    ),
                ],
            )
        ],
    )


def test_get_db_admin_object_uses_ontology_business_name_for_logical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """論理名はオントロジー業務名、コメントは生カラムコメント(別ソース)になる。"""
    from app.features.nl2sql import ontology_router
    from app.features.nl2sql.ontology_catalog import build_schema_ontology

    # オントロジー側で業務名をキュレーション(コメントとは別文字列)
    ontology = build_schema_ontology(
        _invoices_catalog(column_logical="得意先名称", column_comment="取引先名")
    )

    class _StubRuntime:
        def current_ontology(self) -> Any:
            return ontology

    monkeypatch.setattr(ontology_router, "ontology_runtime", _StubRuntime())

    service = Nl2SqlService(store=MemoryNl2SqlStore())
    # detail 側の logical_name は生コメント由来(サービスがオントロジー業務名で上書き)
    service._catalog = _invoices_catalog(column_logical="取引先名", column_comment="取引先名")

    detail = service.get_db_admin_object("INVOICES", "table")
    column = detail.columns[0]
    assert column.logical_name == "得意先名称"  # オントロジー業務名で上書き
    assert column.comment == "取引先名"  # 生カラムコメントは保持


def test_get_db_admin_object_blanks_logical_when_ontology_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """オントロジー取得に失敗しても例外を出さず、論理名は空にする(コメントを流用しない)。"""
    from app.features.nl2sql import ontology_router

    class _BrokenRuntime:
        def current_ontology(self) -> Any:
            raise RuntimeError("ontology unavailable")

    monkeypatch.setattr(ontology_router, "ontology_runtime", _BrokenRuntime())

    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = _invoices_catalog(column_logical="取引先名", column_comment="取引先名")

    detail = service.get_db_admin_object("INVOICES", "table")
    column = detail.columns[0]
    assert column.logical_name == ""  # 業務名未設定なら論理名は空
    assert column.comment == "取引先名"  # 生カラムコメントは保持


def test_get_db_admin_object_blanks_logical_when_no_ontology_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """オントロジーは在るが該当カラムに業務名が無い場合も論理名は空にする。"""
    from app.features.nl2sql import ontology_router

    class _EmptyRuntime:
        def current_ontology(self) -> Any:
            return SimpleNamespace(nodes=[])

    monkeypatch.setattr(ontology_router, "ontology_runtime", _EmptyRuntime())

    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = _invoices_catalog(column_logical="取引先名", column_comment="取引先名")

    detail = service.get_db_admin_object("INVOICES", "table")
    column = detail.columns[0]
    assert column.logical_name == ""
    assert column.comment == "取引先名"


def test_get_db_admin_object_skips_ddl_when_include_ddl_false() -> None:
    """include_ddl=False で重い DDL 生成を省略し ddl="" を返す(列・行数は保持)。"""
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = _invoices_catalog(column_logical="取引先名", column_comment="取引先名")

    with_ddl = service.get_db_admin_object("INVOICES", "table")
    assert with_ddl.ddl != ""  # 既定は DDL 込み(後方互換)

    without_ddl = service.get_db_admin_object("INVOICES", "table", include_ddl=False)
    assert without_ddl.ddl == ""  # DDL 省略
    # 列・行数など DDL 以外は従来どおり
    assert [c.column_name for c in without_ddl.columns] == [
        c.column_name for c in with_ddl.columns
    ]
    assert without_ddl.row_count == with_ddl.row_count


def test_upload_csv_validates_confirmation_and_matches_catalog_columns() -> None:
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
    assert result.executed is False
    assert any("confirmation=" in warning for warning in result.warnings)
    assert known_column.upper() in result.matched_columns
    assert "UNKNOWN_COLUMN" in result.unmatched_csv_columns
    assert result.row_count == 2

    requires_oracle = service.upload_db_admin_csv(
        DbAdminCsvUploadRequest(
            table_name=table.table_name,
            content_base64=base64.b64encode(csv_text.encode()).decode(),
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


def test_select_ai_object_list_normalizes_nested_oracle_profile_attributes() -> None:
    oracle_object_list_json = (
        '[{"OWNER": "APP", "NAME": "PAYMENTS"}, ' '{"owner": "APP", "name": "INVOICES"}]'
    )
    object_list = _normalize_select_ai_object_list(
        {"PROFILE_ATTRIBUTES": {"OBJECT_LIST": oracle_object_list_json}}
    )

    assert [item["name"] for item in object_list] == ["PAYMENTS", "INVOICES"]
    assert object_list[0]["owner"] == "APP"


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
    fake_client = FakeEnterpriseAiClient(
        "JOIN:\n[INNER] e(EMPLOYEE).DEPT_ID = d(DEPARTMENT).DEPT_ID\nWHERE:\ne.STATUS = 'A'"
    )
    cast(Any, service)._enterprise_ai_client = fake_client
    ddl = (
        'CREATE OR REPLACE VIEW "V_EMP_DEPT" AS\n'
        "SELECT e.EMP_ID, d.DEPT_NAME FROM EMPLOYEE e "
        "JOIN DEPARTMENT d ON e.DEPT_ID = d.DEPT_ID WHERE e.STATUS = 'A'"
    )
    extracted = service.extract_db_admin_join_where(DbAdminJoinWhereRequest(ddl=ddl))
    assert extracted.source == "oci_enterprise_ai"
    assert extracted.prompt_profile == "join_where_strict"
    assert "EMPLOYEE" in extracted.join_text
    assert extracted.where_text == "e.STATUS = 'A'"
    assert "Extract ONLY JOIN and WHERE" in fake_client.calls[0]["prompt"]

    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient("整形されていない応答")
    fallback = service.extract_db_admin_join_where(DbAdminJoinWhereRequest(ddl=ddl))
    assert fallback.source == "deterministic"
    assert fallback.prompt_profile == "join_where_strict"
    assert fallback.warnings
    assert "JOIN" in fallback.join_text.upper() or fallback.join_text == "None"
    assert fallback.where_text != ""


def test_extract_join_where_uses_sql_structure_prompt_profile() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    fake_client = FakeEnterpriseAiClient(
        "## SQL構造分析\n\n"
        "### SELECT句\n"
        "- EMPLOYEE(e).EMP_ID\n\n"
        "### JOIN句\n"
        "- **JOIN**: EMPLOYEE(e) JOIN DEPARTMENT(d)\n"
        "  - ON: EMPLOYEE(e).DEPT_ID = DEPARTMENT(d).DEPT_ID\n\n"
        "### WHERE句\n"
        "- EMPLOYEE(e).STATUS = 'A'\n"
    )
    cast(Any, service)._enterprise_ai_client = fake_client
    ddl = (
        "CREATE OR REPLACE VIEW V_EMP_DEPT AS "
        "SELECT e.EMP_ID, d.DEPT_NAME FROM EMPLOYEE e "
        "JOIN DEPARTMENT d ON e.DEPT_ID = d.DEPT_ID WHERE e.STATUS = 'A'"
    )

    extracted = service.extract_db_admin_join_where(
        DbAdminJoinWhereRequest(ddl=ddl, prompt_profile="sql_structure")
    )

    assert extracted.source == "oci_enterprise_ai"
    assert extracted.prompt_profile == "sql_structure"
    assert "EMPLOYEE(e) JOIN DEPARTMENT(d)" in extracted.join_text
    assert "ON: EMPLOYEE(e).DEPT_ID = DEPARTMENT(d).DEPT_ID" in extracted.join_text
    assert extracted.where_text == "EMPLOYEE(e).STATUS = 'A'"
    assert "SQL構造分析" in extracted.structure_markdown
    assert "Analyze the SQL query" in fake_client.calls[0]["prompt"]


def test_extract_join_where_unconfigured_keeps_selected_prompt_profile() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient(configured=False)
    result = service.extract_db_admin_join_where(
        DbAdminJoinWhereRequest(
            ddl="CREATE OR REPLACE VIEW V1 AS SELECT * FROM EMPLOYEE",
            prompt_profile="sql_structure",
        )
    )

    assert result.source == "deterministic"
    assert result.prompt_profile == "sql_structure"
    assert result.warnings
