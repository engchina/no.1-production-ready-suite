"""テーブル/ビュー/データ管理(SQL Assist 移植)API のテスト。"""

from __future__ import annotations

import base64
import csv
import importlib
import io
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, cast

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.features.nl2sql.enterprise_ai_client import EnterpriseAiDirectError
from app.features.nl2sql.models import (
    AllowedObjects,
    AssetRefreshData,
    CsvImportColumn,
    DbAdminAiAnalysisRequest,
    DbAdminCsvUploadRequest,
    DbAdminDataPreviewRequest,
    DbAdminDropTableRequest,
    DbAdminDropViewRequest,
    DbAdminExecuteRequest,
    DbAdminImportTabularRequest,
    DbAdminJoinWhereRequest,
    DbAdminStatementsRequest,
    DbAdminTruncateTableRequest,
    MetadataSqlGenerateRequest,
    MetadataSqlSampleRequest,
    Nl2SqlEngine,
    Nl2SqlProfile,
    QueryResults,
    SampleDataMutationRequest,
    SampleDataStep,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
    SelectAiDbProfileRefreshMode,
    SelectAiDbProfileRefreshTarget,
    SelectAiDbProfileUpsertRequest,
)
from app.features.nl2sql.oracle_adapter import (
    OracleAdapterError,
    OracleNl2SqlAdapter,
    TabularImportValidationError,
    _flexible_date_value,
    _normalize_select_ai_object_list,
)
from app.features.nl2sql.router import db_admin_import_tabular, db_admin_upload_csv
from app.features.nl2sql.service import (
    DbAdminOperationFailed,
    Nl2SqlService,
    SchemaRefreshMutationSync,
    _db_admin_error,
)
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.security.request_actor import actor_scope
from app.settings import get_settings


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

    def fetch_catalog(self, *, include_samples: bool = True) -> SchemaCatalog:
        del include_samples
        return SchemaCatalog(refreshed_at="2026-07-10T00:00:00+00:00", tables=[])


class _FakeAdminSqlAdapter(_FakeStatementsAdapter):
    """Admin SQL の SELECT/data plane と statement/control plane を分けて記録する。"""

    def __init__(
        self,
        *,
        select_result: QueryResults | None = None,
        select_error: OracleAdapterError | None = None,
        statement_results: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(statement_results or [])
        self.select_result = select_result or QueryResults(
            columns=["ID"],
            rows=[{"ID": 1}],
            total=1,
            execution_context="oracle_data_plane",
            vpd_context_enforced=False,
        )
        self.select_error = select_error
        self.select_calls: list[tuple[str, int | None]] = []

    def execute_select(self, sql: str, max_rows: int | None) -> QueryResults:
        self.select_calls.append((sql, max_rows))
        if self.select_error is not None:
            raise self.select_error
        return self.select_result


class _FakeObjectTypeAdapter(_FakeStatementsAdapter):
    def __init__(self, object_type: str | None) -> None:
        super().__init__([])
        self.object_type = object_type
        self.object_type_calls: list[str] = []

    def find_db_admin_object_type(self, object_name: str, owner: str = "") -> str | None:
        self.object_type_calls.append(f"{owner}.{object_name}" if owner else object_name)
        return self.object_type


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


class _FakeCsvUploadAdapter:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def upload_csv_to_existing_table(
        self,
        *,
        table_name: str,
        owner: str = "",
        columns: list[CsvImportColumn],
        rows: list[dict[str, str | None]],
        truncate: bool,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "table_name": table_name,
                "owner": owner,
                "columns": columns,
                "rows": rows,
                "truncate": truncate,
            }
        )
        return self.result


class _FakeSelectAiProfileAdapter:
    """DBMS_CLOUD_AI profile list/detail を固定で返す fake adapter。"""

    def __init__(self) -> None:
        self.list_calls = 0
        self.name_fetch_calls: list[set[str] | None] = []
        self.detail_calls: list[str] = []

    def list_select_ai_profiles(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        return [
            {
                "name": "FINANCE_SELECT_AI",
                "status": "available",
                "description": "財務 profile",
                "created_at": "2026-07-10T00:00:00+00:00",
            }
        ]

    def fetch_select_ai_profile_names(
        self,
        profile_names: set[str] | None = None,
    ) -> set[str]:
        self.name_fetch_calls.append(set(profile_names) if profile_names is not None else None)
        names = {"FINANCE_SELECT_AI"}
        return names if profile_names is None else names & {name.upper() for name in profile_names}

    def get_select_ai_profile_detail(self, *, profile_name: str) -> dict[str, Any]:
        self.detail_calls.append(profile_name)
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
        self.list_calls = 0
        self.name_fetch_calls: list[set[str] | None] = []

    def list_select_ai_profiles(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        return [
            {"name": "FINANCE_SELECT_AI", "status": "available"},
            {"name": "ARCHIVED_SELECT_AI", "status": "available"},
            {"name": "NL2SQL_DERIVED_FILTER_PROFILE", "status": "available"},
            {"name": "MANUAL_SELECT_AI", "status": "available"},
        ]

    def fetch_select_ai_profile_names(
        self,
        profile_names: set[str] | None = None,
    ) -> set[str]:
        self.name_fetch_calls.append(set(profile_names) if profile_names is not None else None)
        names = {
            "FINANCE_SELECT_AI",
            "ARCHIVED_SELECT_AI",
            "NL2SQL_DERIVED_FILTER_PROFILE",
            "MANUAL_SELECT_AI",
        }
        return names if profile_names is None else names & {name.upper() for name in profile_names}

    def get_select_ai_profile_detail(self, *, profile_name: str) -> dict[str, Any]:
        self.detail_calls.append(profile_name)
        return {
            "name": profile_name,
            "attributes": {
                "provider": "oci",
                "object_list": [{"owner": "APP", "name": "INVOICES"}],
            },
        }


class _MutableSelectAiProfileAdapter:
    """Targeted DB profile list refresh 用の可変 fake adapter。"""

    def __init__(self, profiles: dict[str, dict[str, Any]] | None = None) -> None:
        self.profiles = {
            name.upper(): {
                "name": name.upper(),
                "status": "available",
                "attributes": dict((profile or {}).get("attributes") or {}),
            }
            for name, profile in (profiles or {}).items()
        }
        self.list_calls = 0
        self.name_fetch_calls: list[set[str] | None] = []
        self.detail_calls: list[str] = []
        self.upsert_calls: list[tuple[str, str]] = []
        self.drop_calls: list[str] = []

    def list_select_ai_profiles(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        return [
            {"name": name, "status": profile.get("status", "available")}
            for name, profile in sorted(self.profiles.items())
        ]

    def fetch_select_ai_profile_names(
        self,
        profile_names: set[str] | None = None,
    ) -> set[str]:
        self.name_fetch_calls.append(set(profile_names) if profile_names is not None else None)
        names = set(self.profiles)
        return names if profile_names is None else names & {name.upper() for name in profile_names}

    def get_select_ai_profile_detail(self, *, profile_name: str) -> dict[str, Any]:
        name = profile_name.upper()
        self.detail_calls.append(name)
        if name not in self.profiles:
            raise OracleAdapterError(f"{name} が見つかりません")
        return dict(self.profiles[name])

    def upsert_select_ai_profile_low_level(
        self,
        *,
        profile_name: str,
        attributes: dict[str, Any],
        description: str = "",
        original_name: str = "",
    ) -> dict[str, Any]:
        new_name = profile_name.upper()
        old_name = original_name.upper()
        self.upsert_calls.append((new_name, old_name))
        if old_name and old_name != new_name:
            self.profiles.pop(old_name, None)
        self.profiles[new_name] = {
            "name": new_name,
            "status": "available",
            "description": description,
            "attributes": dict(attributes),
        }
        return {"profile_name": new_name, "status": "saved"}

    def drop_select_ai_profile(self, *, profile_name: str) -> dict[str, Any]:
        name = profile_name.upper()
        self.drop_calls.append(name)
        self.profiles.pop(name, None)
        return {"profile_name": name, "status": "dropped"}


def _run_db_profile_refresh(service: Nl2SqlService, job_id: str = "") -> Any:
    if not job_id:
        job = service.start_select_ai_db_profile_refresh_job(dispatch=False)
        job_id = job.job_id
    assert service._run_select_ai_db_profile_refresh_job(job_id) is True  # noqa: SLF001
    result = service.get_select_ai_db_profile_refresh_job(job_id)
    assert result is not None
    return result


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


def test_admin_mutation_submits_schema_job_only_for_schema_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeStatementsAdapter(
        [
            {
                "index": 1,
                "statement_type": "DML",
                "status": "success",
                "sql": "",
            }
        ]
    )
    service = _OracleRuntimeService(adapter)
    submitted = 0
    submitted_targets: list[list[tuple[str, str, str]]] = []

    def submit(**kwargs: Any) -> SchemaRefreshMutationSync:
        nonlocal submitted
        submitted += 1
        submitted_targets.append(
            [
                (target.owner, target.object_name, target.expected_state)
                for target in kwargs["target_objects"]
            ]
        )
        return SchemaRefreshMutationSync(job_id="refresh-job-1")

    monkeypatch.setattr(service, "_submit_schema_refresh_after_admin_mutation", submit)

    dml = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="UPDATE ORDERS SET STATUS = 'DONE'",
            policy="data_dml",
            confirmation="ADMIN_EXECUTE",
        )
    )
    ddl = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="CREATE TABLE ORDERS_ARCHIVE (ID NUMBER)",
            policy="table_ddl",
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert dml.executed is True
    assert dml.schema_refresh_job_id == ""
    assert ddl.executed is True
    assert ddl.schema_refresh_job_id == "refresh-job-1"
    assert submitted == 1
    assert submitted_targets == [[("ADMIN", "ORDERS_ARCHIVE", "present")]]


def test_admin_plsql_success_requires_manual_schema_refresh() -> None:
    adapter = _FakeStatementsAdapter(
        [
            {
                "index": 1,
                "statement_type": "PLSQL",
                "status": "success",
                "sql": "BEGIN EXECUTE IMMEDIATE 'CREATE TABLE X (ID NUMBER)'; END;",
            }
        ]
    )
    service = _OracleRuntimeService(adapter)

    result = service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql="BEGIN EXECUTE IMMEDIATE 'CREATE TABLE X (ID NUMBER)'; END;",
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert result.executed is True
    assert result.schema_refresh_job_id == ""
    assert result.schema_refresh_required is True
    assert result.schema_refresh_reason_code == "schema_refresh_target_unresolved"
    assert any("DB 構造を再取得" in warning for warning in result.warnings)


def test_select_ai_db_profiles_include_detail_enriches_objects_and_models() -> None:
    adapter = _FakeSelectAiProfileAdapter()
    service = _OracleRuntimeService(adapter)
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
    _run_db_profile_refresh(service)
    adapter.detail_calls.clear()

    data = service.list_select_ai_db_profiles(include_detail=True)

    assert data.runtime == "oracle"
    assert data.warnings == []
    assert adapter.list_calls == 0
    assert adapter.detail_calls == []
    assert len(data.profiles) == 1
    profile = data.profiles[0]
    assert profile.name == "FINANCE_SELECT_AI"
    assert profile.tables == ["APP.INVOICES"]
    assert profile.views == ["APP.V_INVOICE_SUMMARY"]
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
    _run_db_profile_refresh(service)
    adapter.detail_calls.clear()

    data = service.list_select_ai_db_profiles(
        include_detail=True,
        business_profiles_only=True,
        include_archived_business_profiles=True,
    )

    assert [profile.name for profile in data.profiles] == [
        "ARCHIVED_SELECT_AI",
        "FINANCE_SELECT_AI",
        "NL2SQL_DERIVED_FILTER_PROFILE",
    ]
    assert adapter.list_calls == 0
    assert adapter.detail_calls == []

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


def test_select_ai_db_profiles_reports_uninitialized_read_model() -> None:
    service = _OracleRuntimeService(_MutableSelectAiProfileAdapter())

    data = service.list_select_ai_db_profiles()

    assert data.profiles == []
    assert data.profile_list_refresh_required is True
    assert data.profile_list_refresh_reason_code == "profile_list_read_model_uninitialized"
    assert any("read model が未初期化" in warning for warning in data.warnings)


def test_select_ai_db_profiles_initialized_empty_list_does_not_require_refresh() -> None:
    service = _OracleRuntimeService(_MutableSelectAiProfileAdapter())
    _run_db_profile_refresh(service)

    data = service.list_select_ai_db_profiles()

    assert data.profiles == []
    assert data.profile_list_refresh_required is False
    assert data.profile_list_refresh_reason_code == ""
    assert not any("read model が未初期化" in warning for warning in data.warnings)


def test_select_ai_db_profiles_existing_profile_does_not_require_refresh() -> None:
    service = _OracleRuntimeService(
        _MutableSelectAiProfileAdapter({"FINANCE_SELECT_AI": {"attributes": {"object_list": []}}})
    )
    _run_db_profile_refresh(service)

    data = service.list_select_ai_db_profiles()

    assert [profile.name for profile in data.profiles] == ["FINANCE_SELECT_AI"]
    assert data.profile_list_refresh_required is False
    assert data.profile_list_refresh_reason_code == ""


def test_select_ai_profiles_export_can_filter_to_business_profile_names() -> None:
    adapter = _FakeMixedSelectAiProfileAdapter()
    service = _OracleRuntimeService(adapter)
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
    _run_db_profile_refresh(service)
    adapter.detail_calls.clear()

    exported = service.export_select_ai_profiles_json(
        business_profiles_only=True,
        include_archived_business_profiles=True,
    )

    assert [profile.name for profile in exported.profiles] == [
        "ARCHIVED_SELECT_AI",
        "FINANCE_SELECT_AI",
    ]

    active_only = service.export_select_ai_profiles_json(
        business_profiles_only=True,
        include_archived_business_profiles=False,
    )

    assert [profile.name for profile in active_only.profiles] == ["FINANCE_SELECT_AI"]
    assert adapter.list_calls == 0
    assert adapter.detail_calls == []


def test_select_ai_db_profile_upsert_submits_targeted_refresh_without_full_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _MutableSelectAiProfileAdapter()
    service = _OracleRuntimeService(adapter)
    monkeypatch.setattr(
        service,
        "_dispatch_select_ai_db_profile_refresh_job",
        lambda _job_id: False,
    )

    result = service.upsert_select_ai_db_profile(
        SelectAiDbProfileUpsertRequest(
            profile_name="finance_select_ai",
            attributes={"object_list": []},
            confirmation="finance_select_ai",
            reason="pytest",
        )
    )
    adapter.detail_calls.clear()

    assert result.executed is True
    assert result.profile_list_refresh_job_id
    assert result.profile_list_refresh_required is False
    _run_db_profile_refresh(service, result.profile_list_refresh_job_id)

    data = service.list_select_ai_db_profiles()
    assert [profile.name for profile in data.profiles] == ["FINANCE_SELECT_AI"]
    assert adapter.list_calls == 0
    assert adapter.name_fetch_calls[-1] == {"FINANCE_SELECT_AI"}
    assert adapter.detail_calls == ["FINANCE_SELECT_AI"]


def test_select_ai_db_profile_drop_removes_only_target_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _MutableSelectAiProfileAdapter(
        {
            "KEEP_SELECT_AI": {"attributes": {"object_list": []}},
            "DROP_SELECT_AI": {"attributes": {"object_list": []}},
        }
    )
    service = _OracleRuntimeService(adapter)
    _run_db_profile_refresh(service)
    monkeypatch.setattr(
        service,
        "_dispatch_select_ai_db_profile_refresh_job",
        lambda _job_id: False,
    )
    adapter.detail_calls.clear()
    adapter.name_fetch_calls.clear()

    result = service.drop_select_ai_db_profile(
        "DROP_SELECT_AI",
        confirmation="DROP_SELECT_AI",
        reason="pytest",
    )

    assert result.executed is True
    assert result.profile_list_refresh_job_id
    _run_db_profile_refresh(service, result.profile_list_refresh_job_id)

    data = service.list_select_ai_db_profiles()
    assert [profile.name for profile in data.profiles] == ["KEEP_SELECT_AI"]
    assert adapter.list_calls == 0
    assert adapter.name_fetch_calls == [{"DROP_SELECT_AI"}]
    assert adapter.detail_calls == []


def test_select_ai_db_profile_rename_deletes_old_and_upserts_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _MutableSelectAiProfileAdapter({"OLD_SELECT_AI": {"attributes": {"object_list": []}}})
    service = _OracleRuntimeService(adapter)
    _run_db_profile_refresh(service)
    monkeypatch.setattr(
        service,
        "_dispatch_select_ai_db_profile_refresh_job",
        lambda _job_id: False,
    )
    adapter.detail_calls.clear()
    adapter.name_fetch_calls.clear()

    result = service.upsert_select_ai_db_profile(
        SelectAiDbProfileUpsertRequest(
            profile_name="NEW_SELECT_AI",
            original_name="OLD_SELECT_AI",
            attributes={"object_list": []},
            confirmation="NEW_SELECT_AI",
            reason="pytest",
        )
    )
    adapter.detail_calls.clear()

    assert result.executed is True
    _run_db_profile_refresh(service, result.profile_list_refresh_job_id)

    data = service.list_select_ai_db_profiles()
    assert [profile.name for profile in data.profiles] == ["NEW_SELECT_AI"]
    assert adapter.list_calls == 0
    assert adapter.name_fetch_calls[-1] == {"OLD_SELECT_AI", "NEW_SELECT_AI"}
    assert adapter.detail_calls == ["NEW_SELECT_AI"]


def test_select_ai_db_profile_target_mismatch_keeps_old_read_model() -> None:
    adapter = _MutableSelectAiProfileAdapter(
        {"KEEP_SELECT_AI": {"attributes": {"object_list": []}}}
    )
    service = _OracleRuntimeService(adapter)
    _run_db_profile_refresh(service)
    adapter.detail_calls.clear()
    adapter.name_fetch_calls.clear()

    job = service.start_select_ai_db_profile_refresh_job(
        dispatch=False,
        mode=SelectAiDbProfileRefreshMode.TARGETED,
        source="pytest",
        target_profiles=[
            SelectAiDbProfileRefreshTarget(
                profile_name="KEEP_SELECT_AI",
                expected_state="absent",
            )
        ],
    )

    assert service._run_select_ai_db_profile_refresh_job(job.job_id) is False  # noqa: SLF001
    failed = service.get_select_ai_db_profile_refresh_job(job.job_id)
    assert failed is not None
    assert failed.status == "error"
    assert failed.requires_full_refresh is True
    assert failed.error_code == "profile_list_refresh_full_required"
    assert [profile.name for profile in service.list_select_ai_db_profiles().profiles] == [
        "KEEP_SELECT_AI"
    ]
    assert adapter.list_calls == 0
    assert adapter.detail_calls == []


def test_select_ai_db_profile_target_unresolved_requires_manual_refresh() -> None:
    service = _OracleRuntimeService(_MutableSelectAiProfileAdapter())

    sync = service._submit_select_ai_db_profile_list_refresh_after_mutation(  # noqa: SLF001
        target_profiles=[],
        source="pytest",
    )

    assert sync.job_id == ""
    assert sync.required is True
    assert sync.reason_code == "profile_list_refresh_target_unresolved"


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


def test_db_admin_statements_block_nl2sql_system_objects_before_oracle() -> None:
    adapter = _FakeStatementsAdapter(
        [
            {
                "index": 1,
                "statement_type": "DDL",
                "status": "success",
                "sql": "",
            }
        ]
    )
    service = _OracleRuntimeService(adapter)

    create_table = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="CREATE TABLE NL2SQL_SCHEMA_OBJECTS (ID NUMBER)",
            policy="table_ddl",
            confirmation="ADMIN_EXECUTE",
        )
    )
    create_view_ref = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="CREATE OR REPLACE VIEW V_SYSTEM AS SELECT * FROM NL2SQL_SCHEMA_OBJECTS",
            policy="view_ddl",
            confirmation="ADMIN_EXECUTE",
        )
    )
    dml = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="UPDATE NL2SQL_SCHEMA_OBJECTS SET ID = 1",
            policy="data_dml",
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert create_table.statements[0].status == "blocked"
    assert create_view_ref.statements[0].status == "blocked"
    assert dml.statements[0].status == "blocked"
    assert all(
        "システムテーブル管理" in item.error_message
        for item in [
            create_table.statements[0],
            create_view_ref.statements[0],
            dml.statements[0],
        ]
    )
    assert adapter.calls == []


def test_db_admin_execute_blocks_nl2sql_select_dml_and_plsql_before_oracle() -> None:
    adapter = _FakeAdminSqlAdapter()
    service = _OracleRuntimeService(adapter)

    select_result = service.execute_db_admin_sql(
        DbAdminExecuteRequest(sql="SELECT * FROM APP.NL2SQL_SCHEMA_OBJECTS", row_limit=10)
    )
    dml_result = service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql="DELETE FROM NL2SQL_SCHEMA_OBJECTS",
            confirmation="ADMIN_EXECUTE",
        )
    )
    plsql_result = service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql="BEGIN EXECUTE IMMEDIATE 'DROP TABLE NL2SQL_SCHEMA_OBJECTS'; END;",
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert [item.status for item in select_result.statements] == ["blocked"]
    assert [item.status for item in dml_result.statements] == ["blocked"]
    assert [item.status for item in plsql_result.statements] == ["blocked"]
    assert "システムテーブル管理" in select_result.statements[0].error_message
    assert adapter.select_calls == []
    assert adapter.calls == []


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
    assert missing_confirmation.execution_context == "admin_control_plane"

    requires_oracle = service.execute_db_admin_statements(
        DbAdminStatementsRequest(
            sql="CREATE TABLE T1 (ID NUMBER)",
            policy="table_ddl",
            confirmation="ADMIN_EXECUTE",
        )
    )
    assert requires_oracle.statements[0].status == "requires_oracle"
    assert requires_oracle.execution_context == "admin_control_plane"
    assert any("NL2SQL_RUNTIME_MODE=oracle" in warning for warning in requires_oracle.warnings)


def test_admin_sql_single_select_uses_data_plane_without_statement_executor() -> None:
    adapter = _FakeAdminSqlAdapter()
    service = _OracleRuntimeService(adapter)

    result = service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql="SELECT ID FROM T1",
            row_limit=50,
        )
    )

    assert len(adapter.select_calls) == 1
    assert adapter.select_calls[0][1] == 50
    assert adapter.calls == []
    assert result.executed is True
    assert result.committed is False
    assert result.runtime == "oracle"
    assert result.execution_context == "oracle_data_plane"
    assert result.vpd_context_enforced is False
    assert result.statements[0].status == "executed"


def test_admin_sql_system_admin_select_uses_oracle_plane_when_deepsec_enabled() -> None:
    adapter = _FakeAdminSqlAdapter()
    service = _OracleRuntimeService(adapter)
    service._deepsec_enabled = True

    with actor_scope("system-admin-id", is_system_admin=True):
        result = service.execute_db_admin_sql(DbAdminExecuteRequest(sql="SELECT ID FROM T1"))

    assert len(adapter.select_calls) == 1
    assert result.executed is True
    assert result.execution_context == "oracle_data_plane"
    assert result.vpd_context_enforced is False


def test_admin_sql_non_admin_select_surfaces_deepsec_data_plane() -> None:
    adapter = _FakeAdminSqlAdapter(
        select_result=QueryResults(
            columns=["ID"],
            rows=[{"ID": 1}],
            total=1,
            execution_context="deepsec_data_plane",
            vpd_context_enforced=True,
        )
    )
    service = _OracleRuntimeService(adapter)
    service._deepsec_enabled = True

    with actor_scope("business-user-id", is_system_admin=False):
        result = service.execute_db_admin_sql(DbAdminExecuteRequest(sql="SELECT ID FROM T1"))

    assert len(adapter.select_calls) == 1
    assert result.executed is True
    assert result.execution_context == "deepsec_data_plane"
    assert result.vpd_context_enforced is True


def test_admin_sql_deepsec_select_error_fails_closed_without_control_fallback() -> None:
    adapter = _FakeAdminSqlAdapter(
        select_error=OracleAdapterError(
            "DeepSec データ接続には認証済み application user が必要です。"
        )
    )
    service = _OracleRuntimeService(adapter)
    service._deepsec_enabled = True

    result = service.execute_db_admin_sql(DbAdminExecuteRequest(sql="SELECT ID FROM T1"))

    assert len(adapter.select_calls) == 1
    assert adapter.calls == []
    assert result.executed is False
    assert result.committed is False
    assert result.rolled_back is False
    assert result.runtime == "oracle"
    assert result.execution_context == "deepsec_data_plane"
    assert result.vpd_context_enforced is True
    assert result.statements[0].status == "error"
    assert "認証済み application user" in result.statements[0].error_message


def test_admin_sql_mixed_select_and_dml_stays_blocked_control_plane() -> None:
    adapter = _FakeAdminSqlAdapter()
    service = _OracleRuntimeService(adapter)

    result = service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql="SELECT ID FROM T1; UPDATE T1 SET ID = 2",
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert adapter.select_calls == []
    assert adapter.calls == []
    assert result.executed is False
    assert result.execution_context == "admin_control_plane"
    assert result.vpd_context_enforced is False
    assert {item.status for item in result.statements} == {"blocked"}


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
    assert result.execution_context == "admin_control_plane"
    assert any("部分的に成功" in warning for warning in result.warnings)
    assert any(
        item["operation"] == "db_admin_statements_table_ddl" for item in service._admin_audit
    )


def test_admin_sql_delegates_data_dml_batch_to_partial_commit_policy() -> None:
    adapter = _FakeStatementsAdapter(
        [
            {
                "index": 1,
                "statement_type": "INSERT",
                "status": "success",
                "sql": "INSERT INTO T1 VALUES (1)",
                "message": "RowsAffected=1",
            },
            {
                "index": 2,
                "statement_type": "UPDATE",
                "status": "error",
                "sql": "UPDATE MISSING_TABLE SET ID = 2",
                "error_message": "ORA-00942: table or view does not exist",
            },
        ]
    )
    service = _OracleRuntimeService(adapter)

    result = service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql="INSERT INTO T1 VALUES (1); UPDATE MISSING_TABLE SET ID = 2",
            confirmation="ADMIN_EXECUTE",
            reason="admin-sql-admin",
        )
    )

    assert adapter.calls == [
        (
            ["INSERT INTO T1 VALUES (1)", "UPDATE MISSING_TABLE SET ID = 2"],
            False,
        )
    ]
    assert result.executed is True
    assert result.committed is True
    assert result.rolled_back is False
    assert result.execution_context == "admin_control_plane"
    assert result.vpd_context_enforced is False
    assert any("部分的に成功しました(1/2 件)" in warning for warning in result.warnings)
    assert any(item["operation"] == "db_admin_statements_data_dml" for item in service._admin_audit)


def test_admin_sql_commits_when_all_data_dml_statements_succeed() -> None:
    adapter = _FakeStatementsAdapter(
        [
            {
                "index": 1,
                "statement_type": "INSERT",
                "status": "success",
                "sql": "INSERT INTO T1 VALUES (1)",
                "message": "RowsAffected=1",
            },
            {
                "index": 2,
                "statement_type": "DELETE",
                "status": "success",
                "sql": "DELETE FROM T1 WHERE ID = 2",
                "message": "RowsAffected=1",
            },
        ]
    )
    service = _OracleRuntimeService(adapter)

    result = service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql="INSERT INTO T1 VALUES (1); DELETE FROM T1 WHERE ID = 2",
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert adapter.calls[0][1] is False
    assert result.executed is True
    assert result.committed is True
    assert result.rolled_back is False
    assert result.execution_context == "admin_control_plane"
    assert result.warnings == []


def test_admin_sql_rolls_back_when_all_data_dml_statements_fail() -> None:
    adapter = _FakeStatementsAdapter(
        [
            {
                "index": 1,
                "statement_type": "DELETE",
                "status": "error",
                "sql": "DELETE FROM MISSING_TABLE",
                "error_message": "ORA-00942: table or view does not exist",
            },
            {
                "index": 2,
                "statement_type": "UPDATE",
                "status": "error",
                "sql": "UPDATE MISSING_TABLE SET ID = 2",
                "error_message": "ORA-00942: table or view does not exist",
            },
        ]
    )
    service = _OracleRuntimeService(adapter)

    result = service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql="DELETE FROM MISSING_TABLE; UPDATE MISSING_TABLE SET ID = 2",
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert adapter.calls[0][1] is False
    assert result.executed is False
    assert result.committed is False
    assert result.rolled_back is True
    assert result.execution_context == "admin_control_plane"


def test_admin_sql_keeps_mixed_admin_statements_atomic() -> None:
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
                "statement_type": "UPDATE",
                "status": "error",
                "sql": "UPDATE MISSING_TABLE SET ID = 2",
                "error_message": "ORA-00942: table or view does not exist",
            },
        ]
    )
    service = _OracleRuntimeService(adapter)

    result = service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql="CREATE TABLE T1 (ID NUMBER); UPDATE MISSING_TABLE SET ID = 2",
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert adapter.calls[0][1] is True
    assert result.executed is False
    assert result.committed is False
    assert result.rolled_back is True
    assert result.execution_context == "admin_control_plane"
    assert not any("部分的に成功" in warning for warning in result.warnings)


def test_admin_sql_keeps_plsql_and_with_dml_atomic() -> None:
    plsql_adapter = _FakeStatementsAdapter(
        [
            {
                "index": 1,
                "statement_type": "PLSQL",
                "status": "success",
                "sql": "BEGIN NULL; END",
                "message": "実行しました。",
            }
        ]
    )
    plsql_service = _OracleRuntimeService(plsql_adapter)

    plsql_result = plsql_service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql="BEGIN NULL; END;",
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert plsql_adapter.calls[0][1] is True
    assert plsql_result.executed is True
    assert plsql_result.committed is True
    assert plsql_result.execution_context == "admin_control_plane"

    with_dml_adapter = _FakeStatementsAdapter(
        [
            {
                "index": 1,
                "statement_type": "SELECT",
                "status": "success",
                "sql": (
                    "WITH TARGET AS (SELECT ID FROM T1) "
                    "UPDATE T1 SET ID = 2 WHERE ID IN (SELECT ID FROM TARGET)"
                ),
                "message": "RowsAffected=1",
            }
        ]
    )
    with_dml_service = _OracleRuntimeService(with_dml_adapter)
    with_dml_sql = (
        "WITH TARGET AS (SELECT ID FROM T1) "
        "UPDATE T1 SET ID = 2 WHERE ID IN (SELECT ID FROM TARGET)"
    )

    with_dml_result = with_dml_service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql=with_dml_sql,
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert with_dml_adapter.calls == [([with_dml_sql], True)]
    assert with_dml_result.executed is True
    assert with_dml_result.committed is True
    assert with_dml_result.execution_context == "admin_control_plane"


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
            structure_text=("OBJECT: DEPARTMENT\nTYPE: table\nCOMMENT: 部署情報を管理するテーブル"),
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

    assert result.sql.index('ALTER TABLE "ADMIN"."A_TABLE"') < result.sql.index(
        'ALTER TABLE "ADMIN"."B_TABLE"'
    )
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
    assert 'DROP VIEW "ADMIN"."V_EMP_DEPT"' in missing.statements[0].sql

    requires_oracle = service.drop_db_admin_view(
        DbAdminDropViewRequest(view_name="V_EMP_DEPT", confirmation="V_EMP_DEPT")
    )
    assert requires_oracle.statements[0].status == "requires_oracle"


def test_truncate_table_requires_target_confirmation_and_oracle() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    missing = service.truncate_db_admin_table(DbAdminTruncateTableRequest(table_name="INVOICES"))
    assert missing.executed is False
    assert missing.statements[0].status == "confirmation_required"
    assert missing.statements[0].statement_type == "TRUNCATE"
    assert 'TRUNCATE TABLE "ADMIN"."INVOICES"' in missing.statements[0].sql

    admin_execute = service.truncate_db_admin_table(
        DbAdminTruncateTableRequest(table_name="INVOICES", confirmation="ADMIN_EXECUTE")
    )
    assert admin_execute.executed is False
    assert admin_execute.statements[0].status == "confirmation_required"
    assert any("ADMIN_EXECUTE では代替できません" in warning for warning in admin_execute.warnings)

    requires_oracle = service.truncate_db_admin_table(
        DbAdminTruncateTableRequest(table_name="INVOICES", confirmation="INVOICES")
    )
    assert requires_oracle.executed is False
    assert requires_oracle.statements[0].status == "requires_oracle"
    assert requires_oracle.statements[0].sql == 'TRUNCATE TABLE "ADMIN"."INVOICES"'


def test_db_admin_direct_object_operations_block_nl2sql_namespace() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    csv_text = "ID\n1\n"
    encoded = base64.b64encode(csv_text.encode()).decode()

    with pytest.raises(ValueError, match="システムテーブル管理"):
        service.get_db_admin_object("NL2SQL_SCHEMA_OBJECTS", "table")
    with pytest.raises(ValueError, match="システムテーブル管理"):
        service.preview_db_admin_data(
            DbAdminDataPreviewRequest(object_name="NL2SQL_SCHEMA_OBJECTS")
        )
    with pytest.raises(ValueError, match="システムテーブル管理"):
        service.drop_db_admin_table(
            DbAdminDropTableRequest(
                table_name="NL2SQL_SCHEMA_OBJECTS",
                confirmation="NL2SQL_SCHEMA_OBJECTS",
            )
        )
    with pytest.raises(ValueError, match="システムテーブル管理"):
        service.truncate_db_admin_table(
            DbAdminTruncateTableRequest(
                table_name="NL2SQL_SCHEMA_OBJECTS",
                confirmation="NL2SQL_SCHEMA_OBJECTS",
            )
        )
    with pytest.raises(ValueError, match="システムテーブル管理"):
        service.upload_db_admin_csv(
            DbAdminCsvUploadRequest(
                table_name="NL2SQL_SCHEMA_OBJECTS",
                content_base64=encoded,
                filename="upload.csv",
                confirmation="NL2SQL_SCHEMA_OBJECTS",
            )
        )
    with pytest.raises(ValueError, match="システムテーブル管理"):
        service.import_db_admin_tabular(
            DbAdminImportTabularRequest(
                table_name="NL2SQL_IMPORTED",
                content_base64=encoded,
                filename="import.csv",
                confirmation="ADMIN_EXECUTE",
            )
        )


def test_nl2sql_execute_safety_blocks_system_tables() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = SchemaCatalog(  # noqa: SLF001
        refreshed_at="2026-07-11T00:00:00+00:00",
        tables=[
            SchemaTable(
                owner="APP",
                table_name="NL2SQL_SCHEMA_OBJECTS",
                logical_name="system",
                columns=[
                    SchemaColumn(
                        column_name="ID",
                        logical_name="ID",
                        data_type="NUMBER",
                    )
                ],
            )
        ],
    )

    safety, _sql, results = service.execute_sql(
        "SELECT * FROM NL2SQL_SCHEMA_OBJECTS",
        AllowedObjects(enforce_table_scope=False),
        10,
    )

    assert safety.is_safe is False
    assert "システムテーブル管理" in safety.blocked_reason
    assert results.total == 0


def test_truncate_table_executes_quoted_statement_in_oracle_runtime() -> None:
    adapter = _FakeStatementsAdapter(
        [
            {
                "index": 1,
                "statement_type": "TRUNCATE",
                "status": "success",
                "sql": 'TRUNCATE TABLE "INVOICES"',
                "row_count": 0,
                "message": "OK",
                "elapsed_ms": 1,
            }
        ]
    )
    service = _OracleRuntimeService(adapter)

    result = service.truncate_db_admin_table(
        DbAdminTruncateTableRequest(
            table_name="INVOICES",
            confirmation="INVOICES",
            reason="ui-data-management-truncate",
        )
    )

    assert result.executed is True
    assert result.committed is True
    assert adapter.calls == [(['TRUNCATE TABLE "ADMIN"."INVOICES"'], False)]


def test_truncate_table_blocks_catalog_views_and_invalid_names() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = SchemaCatalog(
        refreshed_at="2026-07-11T00:00:00+00:00",
        tables=[
            SchemaTable(
                table_name="V_EMP_DEPT",
                logical_name="社員部署ビュー",
                table_type="view",
            )
        ],
    )

    view = service.truncate_db_admin_table(
        DbAdminTruncateTableRequest(table_name="V_EMP_DEPT", confirmation="V_EMP_DEPT")
    )
    assert view.executed is False
    assert view.statements[0].status == "blocked"
    assert any("ビューは TRUNCATE できません" in warning for warning in view.warnings)

    adapter = _FakeObjectTypeAdapter("VIEW")
    oracle_service = _OracleRuntimeService(adapter)
    oracle_service._catalog = SchemaCatalog(
        refreshed_at="2026-07-11T00:00:00+00:00",
        tables=[],
    )
    oracle_view = oracle_service.truncate_db_admin_table(
        DbAdminTruncateTableRequest(table_name="V_EMP_DEPT", confirmation="V_EMP_DEPT")
    )
    assert oracle_view.executed is False
    assert oracle_view.statements[0].status == "blocked"
    assert adapter.object_type_calls == ["ADMIN.V_EMP_DEPT"]
    assert adapter.calls == []

    with pytest.raises(ValueError):
        service.truncate_db_admin_table(
            DbAdminTruncateTableRequest(table_name='INVOICES"; DROP TABLE USERS --')
        )


def test_preview_data_builds_guarded_select() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    plain = service.preview_db_admin_data(DbAdminDataPreviewRequest(object_name="INVOICES"))
    assert plain.runtime == "deterministic"
    assert plain.sql == 'SELECT * FROM "ADMIN"."INVOICES" FETCH FIRST 100 ROWS ONLY'
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

    # システム object は import 用 sanitizer で別名化せず、preview 前に止める。
    with pytest.raises(ValueError, match="システムテーブル管理"):
        service.preview_db_admin_data(
            DbAdminDataPreviewRequest(object_name="DBTOOLS$EXECUTION_HISTORY")
        )

    with pytest.raises(ValueError, match="Oracle 識別子"):
        service.preview_db_admin_data(DbAdminDataPreviewRequest(object_name='ADMIN"."SECRET'))


def test_oracle_adapter_execute_select_normalizes_driver_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingCursor:
        def __enter__(self) -> _FailingCursor:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def execute(self, _sql: str) -> None:
            raise RuntimeError("ORA-00942: table or view does not exist")

    class _FailingConnection:
        def cursor(self) -> _FailingCursor:
            return _FailingCursor()

    @contextmanager
    def failing_connection() -> Iterator[_FailingConnection]:
        yield _FailingConnection()

    adapter = OracleNl2SqlAdapter(get_settings())
    monkeypatch.setattr(adapter, "user_data_connection", failing_connection)

    with pytest.raises(OracleAdapterError, match="SELECT の実行に失敗しました.*ORA-00942"):
        adapter.execute_select('SELECT * FROM "MISSING_TABLE"', 100)


def test_oracle_adapter_system_admin_select_uses_normal_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Cursor:
        description = [("ID",)]

        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def execute(self, _sql: str) -> None:
            return None

        def fetchmany(self, _max_rows: int) -> list[tuple[int]]:
            return [(1,)]

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor()

    @contextmanager
    def normal_connection() -> Iterator[_Connection]:
        yield _Connection()

    settings = get_settings().model_copy(update={"oracle_deepsec_enabled": True})
    adapter = OracleNl2SqlAdapter(settings)
    monkeypatch.setattr(adapter, "connection", normal_connection)

    with actor_scope("system-admin", is_system_admin=True):
        result = adapter.execute_select("SELECT ID FROM T1", 100)

    assert result.rows == [{"ID": 1}]
    assert result.execution_context == "oracle_data_plane"
    assert result.vpd_context_enforced is False


def test_oracle_adapter_non_admin_select_uses_deepsec_data_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Cursor:
        description = [("ID",)]

        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def execute(self, _sql: str) -> None:
            return None

        def fetchmany(self, _max_rows: int) -> list[tuple[int]]:
            return [(2,)]

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor()

    class _PoolManager:
        def __init__(self) -> None:
            self.actor_ids: list[str] = []

        @contextmanager
        def data_connection(self, actor_user_uuid: str) -> Iterator[_Connection]:
            self.actor_ids.append(actor_user_uuid)
            yield _Connection()

    manager = _PoolManager()
    settings = get_settings().model_copy(update={"oracle_deepsec_enabled": True})
    adapter = OracleNl2SqlAdapter(settings)
    monkeypatch.setattr(
        "app.clients.oracle_runtime.get_oracle_pool_manager",
        lambda: manager,
    )

    with actor_scope("business-user", is_system_admin=False):
        result = adapter.execute_select("SELECT ID FROM T1", 100)

    assert manager.actor_ids == ["business-user"]
    assert result.rows == [{"ID": 2}]
    assert result.execution_context == "deepsec_data_plane"
    assert result.vpd_context_enforced is True


def test_oracle_adapter_deepsec_select_without_actor_fails_closed() -> None:
    settings = get_settings().model_copy(update={"oracle_deepsec_enabled": True})
    adapter = OracleNl2SqlAdapter(settings)

    with pytest.raises(OracleAdapterError, match="認証済み application user"):
        adapter.execute_select("SELECT ID FROM T1", 100)


class _SelectAiCursor:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.row: tuple[str] = ("",)

    def __enter__(self) -> _SelectAiCursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, *_args: object) -> None:
        self.calls.append(sql)
        if "CREATE_CONVERSATION" in sql:
            self.row = ("conversation-1",)
            return
        self.row = ("SELECT ID FROM T1",)

    def fetchone(self) -> tuple[str]:
        return self.row


class _SelectAiConnection:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def cursor(self) -> _SelectAiCursor:
        return _SelectAiCursor(self.calls)


def test_oracle_adapter_non_admin_select_ai_generation_uses_deepsec_data_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PoolManager:
        def __init__(self) -> None:
            self.actor_ids: list[str] = []
            self.calls: list[str] = []

        @contextmanager
        def data_connection(self, actor_user_uuid: str) -> Iterator[_SelectAiConnection]:
            self.actor_ids.append(actor_user_uuid)
            yield _SelectAiConnection(self.calls)

    manager = _PoolManager()
    settings = get_settings().model_copy(update={"oracle_deepsec_enabled": True})
    adapter = OracleNl2SqlAdapter(settings)

    def forbidden_connection() -> Iterator[_SelectAiConnection]:
        raise AssertionError("non-admin Select AI generation must use DeepSec data connection")

    monkeypatch.setattr(adapter, "connection", forbidden_connection)
    monkeypatch.setattr("app.clients.oracle_runtime.get_oracle_pool_manager", lambda: manager)

    with actor_scope("business-user", is_system_admin=False):
        sql = adapter.generate_select_ai_sql(profile_name="NL2SQL_PROFILE", question="一覧")

    assert sql == "SELECT ID FROM T1"
    assert manager.actor_ids == ["business-user"]
    assert any("DBMS_CLOUD_AI.GENERATE" in call for call in manager.calls)


def test_oracle_adapter_system_admin_select_ai_generation_uses_normal_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    @contextmanager
    def normal_connection() -> Iterator[_SelectAiConnection]:
        yield _SelectAiConnection(calls)

    class _PoolManager:
        def data_connection(self, _actor_user_uuid: str) -> Iterator[_SelectAiConnection]:
            raise AssertionError("system_admin Select AI generation must not use data connection")

    settings = get_settings().model_copy(update={"oracle_deepsec_enabled": True})
    adapter = OracleNl2SqlAdapter(settings)
    monkeypatch.setattr(adapter, "connection", normal_connection)
    monkeypatch.setattr(
        "app.clients.oracle_runtime.get_oracle_pool_manager",
        lambda: _PoolManager(),
    )

    with actor_scope("system-admin", is_system_admin=True):
        sql = adapter.generate_select_ai_sql(profile_name="NL2SQL_PROFILE", question="一覧")

    assert sql == "SELECT ID FROM T1"
    assert any("DBMS_CLOUD_AI.GENERATE" in call for call in calls)


def test_oracle_adapter_non_admin_select_ai_agent_team_uses_deepsec_data_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PoolManager:
        def __init__(self) -> None:
            self.actor_ids: list[str] = []
            self.calls: list[str] = []

        @contextmanager
        def data_connection(self, actor_user_uuid: str) -> Iterator[_SelectAiConnection]:
            self.actor_ids.append(actor_user_uuid)
            yield _SelectAiConnection(self.calls)

    manager = _PoolManager()
    settings = get_settings().model_copy(update={"oracle_deepsec_enabled": True})
    adapter = OracleNl2SqlAdapter(settings)

    def forbidden_connection() -> Iterator[_SelectAiConnection]:
        raise AssertionError("non-admin Select AI Agent must use DeepSec data connection")

    monkeypatch.setattr(adapter, "connection", forbidden_connection)
    monkeypatch.setattr("app.clients.oracle_runtime.get_oracle_pool_manager", lambda: manager)

    with actor_scope("business-user", is_system_admin=False):
        sql, conversation_id = adapter.run_select_ai_agent_team(
            team_name="NL2SQL_TEAM",
            question="一覧",
            tool_name="NL2SQL_TOOL",
        )

    assert sql == "SELECT ID FROM T1"
    assert conversation_id == "conversation-1"
    assert manager.actor_ids == ["business-user", "business-user"]
    assert any("CREATE_CONVERSATION" in call for call in manager.calls)
    assert any("RUN_TEAM" in call for call in manager.calls)


def test_oracle_adapter_non_admin_select_ai_agent_tool_uses_deepsec_data_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PoolManager:
        def __init__(self) -> None:
            self.actor_ids: list[str] = []
            self.calls: list[str] = []

        @contextmanager
        def data_connection(self, actor_user_uuid: str) -> Iterator[_SelectAiConnection]:
            self.actor_ids.append(actor_user_uuid)
            yield _SelectAiConnection(self.calls)

    manager = _PoolManager()
    settings = get_settings().model_copy(update={"oracle_deepsec_enabled": True})
    adapter = OracleNl2SqlAdapter(settings)

    def forbidden_connection() -> Iterator[_SelectAiConnection]:
        raise AssertionError("non-admin Select AI Agent tool must use DeepSec data connection")

    monkeypatch.setattr(adapter, "connection", forbidden_connection)
    monkeypatch.setattr("app.clients.oracle_runtime.get_oracle_pool_manager", lambda: manager)

    with actor_scope("business-user", is_system_admin=False):
        sql, conversation_id = adapter.run_select_ai_agent_tool(
            tool_name="NL2SQL_TOOL",
            question="一覧",
        )

    assert sql == "SELECT ID FROM T1"
    assert conversation_id == "run_tool:NL2SQL_TOOL"
    assert manager.actor_ids == ["business-user"]
    assert any("RUN_TOOL" in call for call in manager.calls)


def test_oracle_adapter_explain_uses_normal_connection_when_deepsec_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _Cursor:
        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def execute(self, sql: str, *_args: object) -> None:
            calls.append(sql)

        def __iter__(self) -> Iterator[tuple[object, ...]]:
            return iter(())

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor()

        def commit(self) -> None:
            calls.append("COMMIT")

    @contextmanager
    def normal_connection() -> Iterator[_Connection]:
        yield _Connection()

    def forbidden_data_connection() -> Iterator[_Connection]:
        raise AssertionError("EXPLAIN must not use DeepSec data connection")

    settings = get_settings().model_copy(update={"oracle_deepsec_enabled": True})
    adapter = OracleNl2SqlAdapter(settings)
    monkeypatch.setattr(adapter, "connection", normal_connection)
    monkeypatch.setattr(adapter, "user_data_connection", forbidden_data_connection)

    with actor_scope("business-user", is_system_admin=False):
        plan = adapter.explain_select("SELECT ID FROM T1")

    assert plan.available is False
    assert any("EXPLAIN PLAN" in sql for sql in calls)
    assert calls[-1] == "COMMIT"


def test_oracle_adapter_generation_samples_use_normal_connection_when_deepsec_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _Cursor:
        def __init__(self) -> None:
            self.rows: list[tuple[str]] = []

        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def execute(self, sql: str, *_args: object) -> None:
            calls.append(sql)
            if "SELECT column_name FROM all_tab_columns" in sql:
                self.rows = [("ID",)]
            else:
                self.rows = [("sample-value",)]

        def __iter__(self) -> Iterator[tuple[str]]:
            return iter(self.rows)

    class _Connection:
        def __init__(self) -> None:
            self.cursor_instance = _Cursor()

        def cursor(self) -> _Cursor:
            return self.cursor_instance

    @contextmanager
    def normal_connection() -> Iterator[_Connection]:
        yield _Connection()

    def forbidden_data_connection() -> Iterator[_Connection]:
        raise AssertionError("generation samples must not use DeepSec data connection")

    settings = get_settings().model_copy(update={"oracle_deepsec_enabled": True})
    adapter = OracleNl2SqlAdapter(settings)
    monkeypatch.setattr(adapter, "connection", normal_connection)
    monkeypatch.setattr(adapter, "user_data_connection", forbidden_data_connection)

    with actor_scope("business-user", is_system_admin=False):
        samples, warnings = adapter.fetch_metadata_sample_values(
            [{"owner": "APP", "object_name": "T1", "columns": ["ID"]}],
            1,
        )

    assert samples == {"APP.T1": {"ID": ["sample-value"]}}
    assert warnings == []
    assert any("all_tab_columns" in sql for sql in calls)


def test_preview_data_exports_xlsx() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    filename, content = service.export_db_admin_preview_xlsx(
        DbAdminDataPreviewRequest(object_name="INVOICES", limit=10, where_clause="STATUS = 'A'")
    )

    openpyxl = importlib.import_module("openpyxl")
    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    assert filename == "admin_invoices_preview.xlsx"
    assert workbook.sheetnames == ["data", "query"]
    assert workbook["data"].max_row >= 1
    assert (
        workbook["query"]["A2"].value
        == 'SELECT * FROM "ADMIN"."INVOICES" WHERE STATUS = \'A\' FETCH FIRST 10 ROWS ONLY'
    )


def test_cross_schema_management_resolves_duplicate_object_names() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = SchemaCatalog(
        refreshed_at="2026-07-10T00:00:00+00:00",
        current_owner="APP",
        tables=[
            SchemaTable(
                table_name="ORDERS",
                logical_name="自社注文",
                owner="APP",
                row_count=2,
                columns=[
                    SchemaColumn(
                        column_name="ORDER_ID",
                        logical_name="注文ID",
                        data_type="NUMBER",
                    ),
                    SchemaColumn(
                        column_name="ORDER_NAME",
                        logical_name="注文名",
                        data_type="VARCHAR2(100)",
                    ),
                    SchemaColumn(
                        column_name="AMOUNT",
                        logical_name="金額",
                        data_type="NUMBER",
                    ),
                    SchemaColumn(
                        column_name="ORDER_DATE",
                        logical_name="注文日",
                        data_type="DATE",
                    ),
                ],
            ),
            SchemaTable(
                table_name="ORDERS",
                logical_name="共有注文",
                owner="SH",
                row_count=8,
                columns=[
                    SchemaColumn(
                        column_name="ORDER_ID",
                        logical_name="注文ID",
                        data_type="NUMBER",
                    ),
                    SchemaColumn(
                        column_name="ORDER_NAME",
                        logical_name="注文名",
                        data_type="VARCHAR2(100)",
                    ),
                    SchemaColumn(
                        column_name="AMOUNT",
                        logical_name="金額",
                        data_type="NUMBER",
                    ),
                    SchemaColumn(
                        column_name="ORDER_DATE",
                        logical_name="注文日",
                        data_type="DATE",
                    ),
                ],
            ),
        ],
    )

    page = service.list_db_admin_objects_page(
        cursor=None,
        limit=10,
        query="orders",
        object_type="table",
        row_state="all",
    )
    assert {item.qualified_name for item in page.items} == {"APP.ORDERS", "SH.ORDERS"}

    detail = service.get_db_admin_object("ORDERS", "table", owner="SH", include_ddl=False)
    assert detail.owner == "SH"
    assert detail.qualified_name == "SH.ORDERS"
    assert detail.row_count == 8

    preview = service.preview_db_admin_data(
        DbAdminDataPreviewRequest(object_name="ORDERS", owner="SH", limit=5)
    )
    assert preview.sql == 'SELECT * FROM "SH"."ORDERS" FETCH FIRST 5 ROWS ONLY'


def test_cross_schema_mutations_and_metadata_sql_use_qualified_targets() -> None:
    adapter = _FakeStatementsAdapter(
        [
            {
                "index": 1,
                "statement_type": "TRUNCATE",
                "status": "success",
                "sql": 'TRUNCATE TABLE "SH"."ORDERS"',
                "row_count": 0,
                "message": "OK",
                "elapsed_ms": 1,
            }
        ]
    )
    service = _OracleRuntimeService(adapter)
    service._catalog = SchemaCatalog(
        refreshed_at="2026-07-10T00:00:00+00:00",
        current_owner="APP",
        tables=[
            SchemaTable(
                table_name="ORDERS",
                logical_name="共有注文",
                owner="SH",
                row_count=8,
                comment="共有注文",
                columns=[
                    SchemaColumn(
                        column_name="ORDER_ID",
                        logical_name="注文ID",
                        data_type="NUMBER",
                    )
                ],
            )
        ],
    )
    service._enterprise_ai_client = FakeEnterpriseAiClient(configured=False)

    result = service.truncate_db_admin_table(
        DbAdminTruncateTableRequest(
            table_name="ORDERS",
            owner="SH",
            confirmation="SH.ORDERS",
            reason="cross-schema-test",
        )
    )
    assert result.executed is True
    assert adapter.calls == [(['TRUNCATE TABLE "SH"."ORDERS"'], False)]

    comment = service.generate_comment_sql(
        MetadataSqlGenerateRequest(
            targets=[{"owner": "SH", "object_name": "ORDERS", "object_type": "TABLE"}],
            structure_text="OBJECT: SH.ORDERS",
        )
    )
    assert 'COMMENT ON TABLE "SH"."ORDERS"' in comment.sql

    annotation = service.generate_annotation_sql(
        MetadataSqlGenerateRequest(
            targets=[{"owner": "SH", "object_name": "ORDERS", "object_type": "table"}],
            structure_text="OBJECT: SH.ORDERS",
        )
    )
    assert 'ALTER TABLE "SH"."ORDERS"' in annotation.sql


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
    assert filename == "app_invoices_columns.xlsx"
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


def test_view_export_xlsx_contains_column_information_only() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = SchemaCatalog(
        refreshed_at="2026-07-10T00:00:00+00:00",
        tables=[
            SchemaTable(
                table_name="V_EMP_DEPT",
                logical_name="社員部署ビュー",
                owner="APP",
                table_type="view",
                row_count=None,
                comment="社員と部署",
                columns=[
                    SchemaColumn(
                        column_name="EMPLOYEE_NAME",
                        logical_name="社員名",
                        data_type="VARCHAR2(120)",
                        nullable=False,
                        comment="社員名",
                        sample_values=["山田太郎"],
                    ),
                    SchemaColumn(
                        column_name="DEPARTMENT_NAME",
                        logical_name="部署名",
                        data_type="VARCHAR2(80)",
                        nullable=True,
                        comment="部署名",
                        sample_values=["営業部"],
                    ),
                ],
            )
        ],
    )

    filename, content = service.export_db_admin_view_xlsx("V_EMP_DEPT")

    openpyxl = importlib.import_module("openpyxl")
    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    assert filename == "app_v_emp_dept_columns.xlsx"
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
    assert [sheet.cell(row=2, column=index).value for index in range(1, 7)] == [
        "EMPLOYEE_NAME",
        "-",
        "社員名",
        "VARCHAR2(120)",
        "NO",
        "山田太郎",
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

    calls: list[dict[str, str]] = []

    class _StubRuntime:
        def column_business_names(self, **kwargs: str) -> dict[str, str]:
            calls.append(kwargs)
            return {"CUSTOMER_NAME": "得意先名称"}

        def current_ontology(self) -> Any:
            raise AssertionError("table detail must not load the full ontology")

    monkeypatch.setattr(ontology_router, "ontology_runtime", _StubRuntime())

    service = Nl2SqlService(store=MemoryNl2SqlStore())
    # detail 側の logical_name は生コメント由来(サービスがオントロジー業務名で上書き)
    service._catalog = _invoices_catalog(column_logical="取引先名", column_comment="取引先名")

    detail = service.get_db_admin_object("INVOICES", "table")
    column = detail.columns[0]
    assert column.logical_name == "得意先名称"  # オントロジー業務名で上書き
    assert column.comment == "取引先名"  # 生カラムコメントは保持
    assert calls == [{"owner": "APP", "object_name": "INVOICES", "object_type": "table"}]


def test_get_db_admin_object_blanks_logical_when_ontology_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """オントロジー取得に失敗しても例外を出さず、論理名は空にする(コメントを流用しない)。"""
    from app.features.nl2sql import ontology_router

    class _BrokenRuntime:
        def column_business_names(self, **_kwargs: str) -> dict[str, str]:
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
        def column_business_names(self, **_kwargs: str) -> dict[str, str]:
            return {}

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
    assert [c.column_name for c in without_ddl.columns] == [c.column_name for c in with_ddl.columns]
    assert without_ddl.row_count == with_ddl.row_count


def _catalog_with_samples() -> SchemaCatalog:
    return SchemaCatalog(
        refreshed_at="2026-07-11T09:30:00+00:00",
        tables=[
            SchemaTable(
                table_name="ORDERS",
                logical_name="注文",
                owner="APP",
                row_count=4200,
                columns=[
                    SchemaColumn(
                        column_name="STATUS",
                        logical_name="状態",
                        data_type="VARCHAR2(20)",
                        nullable=False,
                        comment="注文状態",
                        sample_values=["NEW", "PAID", "SHIPPED"],
                    ),
                ],
            )
        ],
    )


def test_get_db_admin_object_merges_sample_values_and_num_rows() -> None:
    """詳細は catalog から該当テーブルの sample_values を補完し、行数は num_rows 統計を使う。"""
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = _catalog_with_samples()

    detail = service.get_db_admin_object("ORDERS", "table")
    assert detail.row_count == 4200  # num_rows 統計(COUNT(*) を撃たない)
    assert detail.columns[0].sample_values == ["NEW", "PAID", "SHIPPED"]


def test_list_db_admin_tables_includes_refreshed_at() -> None:
    """一覧応答に catalog の refreshed_at を載せる(ヘッダ用途を catalog 全取得なしで賄う)。"""
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = _catalog_with_samples()

    listed = service.list_db_admin_tables()
    assert listed.refreshed_at == "2026-07-11T09:30:00+00:00"


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


def test_upload_csv_oracle_empty_unmatched_overrides_stale_catalog() -> None:
    """Oracle 実行結果の空配列を正とし、事前 catalog 照合の古い不一致列へ戻さない。"""

    adapter = _FakeCsvUploadAdapter(
        {
            "matched_columns": ["ID", "NAME"],
            "unmatched_csv_columns": [],
            "success_count": 2,
            "error_count": 0,
            "row_errors": [],
            "hint": "",
        }
    )
    service = _OracleRuntimeService(adapter)
    service._catalog = SchemaCatalog(refreshed_at="2026-07-11T09:30:00+00:00", tables=[])
    csv_text = "ID,NAME\n123,456\n666,777\n"

    result = service.upload_db_admin_csv(
        DbAdminCsvUploadRequest(
            table_name="TEST_TABLE",
            content_base64=base64.b64encode(csv_text.encode()).decode(),
            filename="Book2.csv",
            confirmation="TEST_TABLE",
        )
    )

    assert result.executed is True
    assert result.matched_columns == ["ID", "NAME"]
    assert result.unmatched_csv_columns == []
    assert result.success_count == 2
    assert result.error_count == 0
    assert adapter.calls[0]["table_name"] == "TEST_TABLE"
    assert [column.column_name for column in adapter.calls[0]["columns"]] == ["ID", "NAME"]


def test_upload_csv_oracle_keeps_actual_unmatched_file_columns() -> None:
    """Oracle 実行結果が返した追加ファイル列だけを不一致として表示用 response に残す。"""

    adapter = _FakeCsvUploadAdapter(
        {
            "matched_columns": ["ID", "NAME"],
            "unmatched_csv_columns": ["UNKNOWN_COLUMN"],
            "success_count": 1,
            "error_count": 0,
            "row_errors": [],
            "hint": "",
        }
    )
    service = _OracleRuntimeService(adapter)
    service._catalog = SchemaCatalog(refreshed_at="2026-07-11T09:30:00+00:00", tables=[])
    csv_text = "ID,NAME,UNKNOWN_COLUMN\n123,456,extra\n"

    result = service.upload_db_admin_csv(
        DbAdminCsvUploadRequest(
            table_name="TEST_TABLE",
            content_base64=base64.b64encode(csv_text.encode()).decode(),
            filename="Book2.csv",
            confirmation="TEST_TABLE",
        )
    )

    assert result.executed is True
    assert result.matched_columns == ["ID", "NAME"]
    assert result.unmatched_csv_columns == ["UNKNOWN_COLUMN"]
    assert result.sample_rows == [{"ID": "123", "NAME": "456", "UNKNOWN_COLUMN": "extra"}]


def test_upload_csv_accepts_cr_newlines_and_preserves_quoted_cr() -> None:
    """CR 単独改行を行区切りにし、quote 内の CR は cell 値として保持する。"""
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    import base64

    csv_text = 'ORDER_ID,ORDER_NAME,NOTE\r1,青山商事,"第1行\r第2行"\r2,北海物産,通常行\r'
    result = service.upload_db_admin_csv(
        DbAdminCsvUploadRequest(
            table_name="ORDERS",
            content_base64=base64.b64encode(csv_text.encode()).decode(),
            filename="cr-only.csv",
        )
    )

    assert result.row_count == 2
    assert result.sample_rows[0]["ORDER_NAME"] == "青山商事"
    assert result.sample_rows[0]["NOTE"] == "第1行\r第2行"
    assert result.sample_rows[1]["ORDER_NAME"] == "北海物産"


def test_upload_csv_parse_error_returns_http_400() -> None:
    """csv.Error を API 境界で利用者向け 400 応答へ正規化する。"""
    import base64

    oversized_cell = "x" * (csv.field_size_limit() + 1)
    request = DbAdminCsvUploadRequest(
        table_name="ORDERS",
        content_base64=base64.b64encode(f"NOTE\n{oversized_cell}\n".encode()).decode(),
        filename="oversized.csv",
    )

    with pytest.raises(HTTPException) as exc_info:
        db_admin_upload_csv(request)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "CSV の解析に失敗しました。改行形式・引用符・セルの長さを確認してください。"
    )
    assert isinstance(exc_info.value.__cause__, ValueError)
    assert isinstance(exc_info.value.__cause__.__cause__, csv.Error)


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


def test_import_tabular_create_submits_targeted_schema_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ImportAdapter:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def import_tabular_table(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    adapter = ImportAdapter()
    service = _OracleRuntimeService(adapter)
    submitted_targets: list[list[tuple[str, str, str]]] = []

    def submit(**kwargs: Any) -> SchemaRefreshMutationSync:
        submitted_targets.append(
            [
                (target.owner, target.object_name, target.expected_state)
                for target in kwargs["target_objects"]
            ]
        )
        return SchemaRefreshMutationSync(job_id="targeted-import-refresh")

    monkeypatch.setattr(service, "_submit_schema_refresh_after_admin_mutation", submit)

    content_base64 = base64.b64encode("ORDER_ID,ORDER_NAME\n1,青山商事\n".encode()).decode()
    result = service.import_db_admin_tabular(
        DbAdminImportTabularRequest(
            table_name="IMPORTED_ORDERS",
            content_base64=content_base64,
            filename="orders.csv",
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert result.executed is True
    assert result.schema_refresh_job_id == "targeted-import-refresh"
    assert result.schema_refresh_required is False
    assert submitted_targets == [[("ADMIN", "IMPORTED_ORDERS", "present")]]
    assert adapter.calls[0]["table_name"] == "IMPORTED_ORDERS"


def test_import_tabular_infers_explicit_char_semantics_for_japanese() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    columns, rows, warnings = service._parse_csv_sample(
        table_name="TEST_TABLE",
        csv_text="ID,NAME\n1,株式会社青山\n",
        max_rows=100,
        max_columns=10,
    )

    assert warnings == []
    assert rows == [{"ID": "1", "NAME": "株式会社青山"}]
    assert [column.data_type for column in columns] == ["NUMBER", "VARCHAR2(6 CHAR)"]
    assert service._csv_import_ddl("TEST_TABLE", columns) == (
        'CREATE TABLE "TEST_TABLE" ("ID" NUMBER, "NAME" VARCHAR2(6 CHAR))'
    )


def test_import_tabular_rejects_text_over_varchar2_char_limit() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    with pytest.raises(ValueError, match="4000 文字を超えるセル"):
        service._infer_csv_data_type(["あ" * 4001])


def test_import_tabular_rejects_oversized_existing_byte_column_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Cursor:
        def __init__(self) -> None:
            self.executed: list[tuple[str, dict[str, object]]] = []
            self.executemany_called = False
            self._metadata = [
                ("ID", "NUMBER", 22, 0, None),
                ("NAME", "VARCHAR2", 6, 6, "B"),
            ]
            self._violation: tuple[object, ...] | None = None

        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def execute(self, sql: str, binds: dict[str, object]) -> None:
            self.executed.append((sql, binds))
            if "JSON_TABLE" in sql:
                assert "c0 VARCHAR2(4000 BYTE) TRUNCATE" in sql
                assert "LENGTHB(c0)" in sql
                self._violation = (2, "NAME", 16, 6, "バイト")

        def fetchall(self) -> list[tuple[object, ...]]:
            return self._metadata

        def fetchone(self) -> tuple[object, ...] | None:
            return self._violation

        def setinputsizes(self, **_kwargs: object) -> None:
            return None

        def executemany(
            self,
            _sql: str,
            _rows: list[dict[str, object]],
            *,
            batcherrors: bool,
        ) -> None:
            assert batcherrors is True
            self.executemany_called = True

        def getbatcherrors(self) -> list[Exception]:
            return []

    class _Connection:
        def __init__(self, cursor: _Cursor) -> None:
            self._cursor = cursor
            self.committed = False

        def cursor(self) -> _Cursor:
            return self._cursor

        def commit(self) -> None:
            self.committed = True

    cursor = _Cursor()
    connection = _Connection(cursor)

    @contextmanager
    def fake_connection() -> Iterator[_Connection]:
        yield connection

    adapter = OracleNl2SqlAdapter(get_settings())
    monkeypatch.setattr(adapter, "connection", fake_connection)
    import_columns = [
        CsvImportColumn(
            source_name="ID",
            column_name="ID",
            data_type="NUMBER",
            nullable=False,
        ),
        CsvImportColumn(
            source_name="NAME",
            column_name="NAME",
            data_type="VARCHAR2(6 CHAR)",
            nullable=False,
        ),
    ]

    with pytest.raises(TabularImportValidationError, match="16バイト.*上限6バイト"):
        adapter.import_tabular_table(
            table_name="TEST_TABLE",
            columns=import_columns,
            rows=[{"ID": "1", "NAME": "株式会社青山"}],
            mode="append",
        )

    assert cursor.executemany_called is False
    assert connection.committed is False
    assert not any(
        "DELETE FROM" in sql or "TRUNCATE TABLE" in sql for sql, _binds in cursor.executed
    )


def test_import_tabular_truncate_mode_uses_transactional_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Cursor:
        def __init__(self) -> None:
            self.executed: list[str] = []
            self.batch_rows: list[dict[str, object]] = []
            self._metadata = [
                ("ID", "NUMBER", 22, 0, None),
                ("NAME", "VARCHAR2", 20, 20, "C"),
            ]

        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def execute(self, sql: str, _binds: dict[str, object]) -> None:
            self.executed.append(sql)
            if "JSON_TABLE" in sql:
                assert "c0 CLOB" in sql
                assert "LENGTH(c0)" in sql
                assert "LENGTHB(c0)" not in sql

        def fetchall(self) -> list[tuple[object, ...]]:
            return self._metadata

        def fetchone(self) -> None:
            return None

        def setinputsizes(self, **_kwargs: object) -> None:
            return None

        def executemany(
            self,
            _sql: str,
            rows: list[dict[str, object]],
            *,
            batcherrors: bool,
        ) -> None:
            assert batcherrors is True
            self.batch_rows = rows

        def getbatcherrors(self) -> list[Exception]:
            return []

    class _Connection:
        def __init__(self, cursor: _Cursor) -> None:
            self._cursor = cursor
            self.committed = False

        def cursor(self) -> _Cursor:
            return self._cursor

        def commit(self) -> None:
            self.committed = True

    cursor = _Cursor()
    connection = _Connection(cursor)

    @contextmanager
    def fake_connection() -> Iterator[_Connection]:
        yield connection

    adapter = OracleNl2SqlAdapter(get_settings())
    monkeypatch.setattr(adapter, "connection", fake_connection)
    result = adapter.import_tabular_table(
        table_name="TEST_TABLE",
        columns=[
            CsvImportColumn(
                source_name="ID",
                column_name="ID",
                data_type="NUMBER",
                nullable=False,
            ),
            CsvImportColumn(
                source_name="NAME",
                column_name="NAME",
                data_type="VARCHAR2(6 CHAR)",
                nullable=False,
            ),
        ],
        rows=[{"ID": "1", "NAME": "株式会社青山"}],
        mode="truncate",
    )

    assert result["row_count"] == 1
    assert any('DELETE FROM "TEST_TABLE"' in sql for sql in cursor.executed)
    assert not any("TRUNCATE TABLE" in sql for sql in cursor.executed)
    assert cursor.batch_rows == [{"c0": 1, "c1": "株式会社青山"}]
    assert connection.committed is True


def test_import_tabular_validation_error_returns_http_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql import router as nl2sql_router

    class _FailingService:
        def import_db_admin_tabular(self, _request: DbAdminImportTabularRequest) -> None:
            raise TabularImportValidationError(
                "TEST_TABLE.NAME: ファイル2行目は16バイトで、"
                "取込先列の上限6バイトを超えています。"
                "値を短くするか列定義を拡張して再試行してください。"
            )

    monkeypatch.setattr(nl2sql_router, "nl2sql_service", _FailingService())
    request = DbAdminImportTabularRequest(
        table_name="TEST_TABLE",
        content_base64="YQ==",
        confirmation="ADMIN_EXECUTE",
    )

    with pytest.raises(HTTPException) as exc_info:
        db_admin_import_tabular(request)

    assert exc_info.value.status_code == 422
    assert "16バイト" in str(exc_info.value.detail)
    assert isinstance(exc_info.value.__cause__, TabularImportValidationError)


@pytest.mark.parametrize(
    ("message", "code", "expected"),
    [
        ("ORA-01031: insufficient privileges", "ORA-01031", "権限が不足"),
        ("ORA-01722: invalid number", "ORA-01722", "数値形式"),
        ("ORA-00001: unique constraint violated", "ORA-00001", "一意制約"),
        ("ORA-00054: resource busy", "ORA-00054", "使用中"),
    ],
)
def test_db_admin_error_maps_oracle_codes_to_actionable_japanese(
    message: str, code: str, expected: str
) -> None:
    error = _db_admin_error(OracleAdapterError(message), target_name="TEST_TABLE")
    assert error.error_code == code
    assert expected in error.summary
    assert error.actions


def test_db_admin_duplicate_error_identifies_target_and_safe_recovery() -> None:
    error = _db_admin_error(
        OracleAdapterError("ORA-00955: name is already used by an existing object"),
        target_name="TEST_TABLE",
        target_type="TABLE",
        operation="tabular_import",
    )
    assert isinstance(error, DbAdminOperationFailed)
    assert error.error_code == "ORA-00955"
    assert "TEST_TABLE" in error.summary
    assert "既存のTABLE" in error.cause
    assert all("削除" not in action for action in error.actions)


def test_db_admin_unknown_oracle_error_keeps_code_and_safe_fallback() -> None:
    error = _db_admin_error(OracleAdapterError("ORA-29999: unexpected"))
    assert error.error_code == "ORA-29999"
    assert "ORA-29999" in error.summary
    assert "SQL" in error.actions[0]


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
        '[{"OWNER": "APP", "NAME": "PAYMENTS"}, '
        '{"owner": "APP", "name": "INVOICES"}, '
        '{"owner": "APP", "name": "VECTOR_IDX$VECTAB"}, '
        '{"owner": "APP", "name": "SYS#AUDIT"}]'
    )
    object_list = _normalize_select_ai_object_list(
        {"PROFILE_ATTRIBUTES": {"OBJECT_LIST": oracle_object_list_json}}
    )

    assert [item["name"] for item in object_list] == ["PAYMENTS", "INVOICES"]
    assert object_list[0]["owner"] == "APP"


class _FakeLob:
    """oracledb LOB を模した read() 対応の値。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


class _FakeAttributesCursor:
    """USER_CLOUD_AI_PROFILE_ATTRIBUTES 相当の属性行を返す fake cursor。"""

    def __init__(self, rows: list[tuple[str, object]]) -> None:
        self._rows = rows

    def execute(self, sql: str, binds: dict[str, object]) -> None:
        assert "USER_CLOUD_AI_PROFILE_ATTRIBUTES" in sql
        assert binds["profile_name"] == "HR_PROFILE"

    def fetchall(self) -> list[tuple[str, object]]:
        return self._rows


def test_fetch_cloud_ai_profile_attributes_builds_object_list_from_lob_rows() -> None:
    # 属性ビューは 1 属性 = 1 行、object_list 値は JSON 文字列(LOB)で入る。
    rows: list[tuple[str, object]] = [
        ("provider", "oci"),
        (
            "object_list",
            _FakeLob(
                '[{"owner":"ADMIN","name":"DEPARTMENT"},'
                '{"owner":"ADMIN","name":"EMPLOYEE"},'
                '{"owner":"ADMIN","name":"PROJECT"}]'
            ),
        ),
    ]
    attributes = OracleNl2SqlAdapter._fetch_cloud_ai_profile_attributes(
        _FakeAttributesCursor(rows), "HR_PROFILE"
    )
    assert attributes["provider"] == "oci"
    object_list = _normalize_select_ai_object_list(attributes)
    assert [item["name"] for item in object_list] == ["DEPARTMENT", "EMPLOYEE", "PROJECT"]
    assert object_list[0]["owner"] == "ADMIN"


def test_fetch_cloud_ai_profile_attributes_degrades_on_missing_view() -> None:
    class _RaisingCursor:
        def execute(self, sql: str, binds: dict[str, object]) -> None:
            raise RuntimeError("ORA-00942: table or view does not exist")

    assert (
        OracleNl2SqlAdapter._fetch_cloud_ai_profile_attributes(_RaisingCursor(), "HR_PROFILE") == {}
    )


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


def test_extract_join_where_defaults_to_sql_structure_and_falls_back() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    fake_client = FakeEnterpriseAiClient(
        "## SQL構造分析\n\n"
        "### JOIN句\n"
        "- **JOIN**: EMPLOYEE(e) JOIN DEPARTMENT(d)\n"
        "  - ON: EMPLOYEE(e).DEPT_ID = DEPARTMENT(d).DEPT_ID\n\n"
        "### WHERE句\n"
        "- EMPLOYEE(e).STATUS = 'A'\n"
    )
    cast(Any, service)._enterprise_ai_client = fake_client
    ddl = (
        'CREATE OR REPLACE VIEW "V_EMP_DEPT" AS\n'
        "SELECT e.EMP_ID, d.DEPT_NAME FROM EMPLOYEE e "
        "JOIN DEPARTMENT d ON e.DEPT_ID = d.DEPT_ID WHERE e.STATUS = 'A'"
    )
    extracted = service.extract_db_admin_join_where(DbAdminJoinWhereRequest(ddl=ddl))
    assert extracted.source == "oci_enterprise_ai"
    assert extracted.prompt_profile == "sql_structure"
    assert "EMPLOYEE" in extracted.join_text
    assert extracted.where_text == "EMPLOYEE(e).STATUS = 'A'"
    assert "SQL構造分析" in extracted.structure_markdown
    assert "Analyze the SQL query" in fake_client.calls[0]["prompt"]

    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient("整形されていない応答")
    fallback = service.extract_db_admin_join_where(DbAdminJoinWhereRequest(ddl=ddl))
    assert fallback.source == "deterministic"
    assert fallback.prompt_profile == "sql_structure"
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


def test_extract_join_where_rejects_legacy_strict_prompt_profile() -> None:
    with pytest.raises(ValidationError):
        DbAdminJoinWhereRequest(
            ddl="CREATE OR REPLACE VIEW V1 AS SELECT * FROM EMPLOYEE",
            prompt_profile="join_where_strict",
        )


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


def test_named_target_confirmation_rejects_admin_execute_master_word() -> None:
    """対象名確認を要求する操作は ADMIN_EXECUTE では迂回できない。"""
    from app.features.nl2sql.models import (
        SyntheticDataGenerateRequest,
    )

    service = Nl2SqlService(store=MemoryNl2SqlStore())
    _import_sample(service)
    table = service._catalog.tables[0]

    dropped = service.drop_db_admin_table(
        DbAdminDropTableRequest(table_name=table.table_name, confirmation="ADMIN_EXECUTE")
    )
    assert dropped.executed is False
    assert dropped.statements[0].status == "confirmation_required"
    assert any("ADMIN_EXECUTE では代替できません" in warning for warning in dropped.warnings)

    view_dropped = service.drop_db_admin_view(
        DbAdminDropViewRequest(view_name="V_EMP_DEPT", confirmation="ADMIN_EXECUTE")
    )
    assert view_dropped.executed is False
    assert view_dropped.statements[0].status == "confirmation_required"

    import base64

    csv_text = f"{table.columns[0].column_name}\n1\n"
    uploaded = service.upload_db_admin_csv(
        DbAdminCsvUploadRequest(
            table_name=table.table_name,
            content_base64=base64.b64encode(csv_text.encode()).decode(),
            confirmation="ADMIN_EXECUTE",
        )
    )
    assert uploaded.executed is False
    assert any("ADMIN_EXECUTE では代替できません" in warning for warning in uploaded.warnings)

    synthetic = service.generate_synthetic_data(
        SyntheticDataGenerateRequest(
            table_name=table.table_name,
            row_count=5,
            confirmation="ADMIN_EXECUTE",
        )
    )
    assert synthetic.status == "confirmation_required"

    # 対象名の完全一致は通過し、非 Oracle runtime のため requires_oracle まで進む。
    synthetic_named = service.generate_synthetic_data(
        SyntheticDataGenerateRequest(
            table_name=table.table_name,
            row_count=5,
            confirmation=table.table_name,
        )
    )
    assert synthetic_named.status == "requires_oracle"
