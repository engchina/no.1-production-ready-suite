"""業務別サンプルの API、対象分離、SQL の関係整合性を検証する。"""

import sqlite3
from unittest.mock import MagicMock

import httpx
import pytest
import sqlglot

from app.features.nl2sql import router as nl2sql_router
from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import (
    Nl2SqlProfile,
    SampleDataMutationRequest,
    SampleDataset,
    SampleDataStep,
    SchemaCatalog,
    SchemaTable,
)
from app.features.nl2sql.sample_datasets import SAMPLE_DATASETS
from app.features.nl2sql.service import Nl2SqlService, SchemaRefreshMutationSync
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.main import app
from app.security.service import SecurityApiError
from app.settings import get_settings


def make_service() -> Nl2SqlService:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = SchemaCatalog(
        current_owner="APP", tables=[], refreshed_at="2026-09-13T00:00:00Z"
    )
    return service


@pytest.mark.parametrize("dataset", list(SampleDataset))
async def test_api_dataset_preview_import_and_delete(
    dataset: SampleDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service()
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)
    definition = SAMPLE_DATASETS[dataset]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # HR は既存 caller と同様に dataset を省略しても同じ SQL と確認語になる。
        params = {} if dataset == SampleDataset.HR else {"dataset": dataset.value}
        preview = await client.get("/api/nl2sql/sample-data", params=params)
        assert preview.status_code == 200
        info = preview.json()["data"]
        assert info["dataset"] == dataset.value
        assert info["objects"] == list(definition.objects)
        assert info["confirmation"] == definition.confirmation
        assert info["imported_objects"] == []
        payload = {**params, "confirmation": definition.confirmation}
        imported = await client.post("/api/nl2sql/sample-data/import", json=payload)
        assert imported.status_code == 200
        assert imported.json()["data"]["executed"] is True
        assert imported.json()["data"]["dataset"] == dataset.value
        info = (await client.get("/api/nl2sql/sample-data", params=params)).json()["data"]
        assert info["imported_objects"] == list(definition.objects)
        deleted = await client.post("/api/nl2sql/sample-data/delete", json=payload)
        assert deleted.status_code == 200
        assert deleted.json()["data"]["executed"] is True
        assert deleted.json()["data"]["dataset"] == dataset.value
        assert service.sample_data_info(dataset).imported_objects == []


@pytest.mark.parametrize("dataset", ["unknown", "../sql_assist_sample", "", "SALES"])
async def test_invalid_dataset_is_rejected_without_mutation(
    dataset: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service()
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/nl2sql/sample-data", params={"dataset": dataset})
        assert response.status_code == 422
        for operation in ("import", "delete"):
            response = await client.post(
                f"/api/nl2sql/sample-data/{operation}",
                json={"dataset": dataset, "confirmation": "SQL_ASSIST_SAMPLE"},
            )
            assert response.status_code == 422
    assert service.get_catalog().tables == []


@pytest.mark.parametrize("incremental", [False, True])
@pytest.mark.parametrize("dataset", list(SampleDataset))
def test_dataset_and_owner_isolation(dataset: SampleDataset, incremental: bool) -> None:
    service = make_service()
    if incremental:
        service._incremental_repository = MemoryIncrementalNl2SqlRepository()
    for item, definition in SAMPLE_DATASETS.items():
        service.import_sample_data(
            SampleDataMutationRequest(dataset=item, confirmation=definition.confirmation)
        )
    service._catalog.tables.extend(
        table.model_copy(update={"owner": "OTHER"}) for table in list(service._catalog.tables)
    )
    service._catalog.tables.append(
        SchemaTable(owner="APP", table_name="USER_TABLE", logical_name="利用者の表")
    )
    service._persist_local_catalog()
    before = service.get_catalog().model_copy(deep=True)
    definition = SAMPLE_DATASETS[dataset]
    wrong_phrase = "WRONG" if dataset == SampleDataset.HR else "SQL_ASSIST_SAMPLE"
    for operation in (service.import_sample_data, service.delete_sample_data):
        rejected = operation(SampleDataMutationRequest(dataset=dataset, confirmation=wrong_phrase))
        assert rejected.executed is False
        assert {item.status for item in rejected.statements} == {"confirmation_required"}
        assert service.get_catalog() == before

    result = service.delete_sample_data(
        SampleDataMutationRequest(dataset=dataset, confirmation=definition.confirmation)
    )
    assert result.executed
    after = {(t.owner, t.table_name): t for t in service.get_catalog().tables}
    expected = {
        (t.owner, t.table_name): t
        for t in before.tables
        if not (t.owner == "APP" and t.table_name in definition.objects)
    }
    assert after == expected
    assert service.sample_data_info(dataset).imported_objects == []


@pytest.mark.parametrize("dataset", [SampleDataset.SALES, SampleDataset.INQUIRIES])
def test_new_dataset_keeps_legacy_profile_and_has_independent_steps(dataset: SampleDataset) -> None:
    service = make_service()
    service._profiles["sql_assist_sample"] = Nl2SqlProfile(id="sql_assist_sample", name="人事")
    definition = SAMPLE_DATASETS[dataset]
    for step in (SampleDataStep.DATA, SampleDataStep.VIEWS):
        blocked = service.import_sample_data(
            SampleDataMutationRequest(
                dataset=dataset, step=step, confirmation=definition.confirmation
            )
        )
        assert not blocked.executed
        assert {item.status for item in blocked.statements} == {"missing_sample_tables"}
    for step in (SampleDataStep.TABLES, SampleDataStep.VIEWS, SampleDataStep.DATA):
        result = service.import_sample_data(
            SampleDataMutationRequest(
                dataset=dataset, step=step, confirmation=definition.confirmation
            )
        )
        assert result.executed
        if step == SampleDataStep.TABLES:
            assert all(t.row_count == 0 for t in service.get_catalog().tables)
    assert service.sample_data_info(dataset).imported_objects == list(definition.objects)
    counts = {t.table_name: t.row_count for t in service.get_catalog().tables}
    assert [counts[name] for name in definition.tables] == [3, 3, 6]
    service.delete_sample_data(
        SampleDataMutationRequest(dataset=dataset, confirmation=definition.confirmation)
    )
    assert not service._profiles["sql_assist_sample"].archived


@pytest.mark.parametrize("dataset", [SampleDataset.SALES, SampleDataset.INQUIRIES])
@pytest.mark.parametrize("step", list(SampleDataStep))
def test_oracle_uses_selected_sql_status_scope_and_refresh_targets(
    dataset: SampleDataset, step: SampleDataStep, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service()
    adapter = MagicMock()
    definition = SAMPLE_DATASETS[dataset]
    adapter.fetch_catalog.return_value = SchemaCatalog(
        current_owner="APP",
        refreshed_at="2026-09-13T00:00:00Z",
        tables=[
            SchemaTable(owner="APP", table_name=name, logical_name=name)
            for name in definition.objects
        ],
    )
    adapter.execute_admin_statements.side_effect = lambda statements, **kwargs: [
        {"index": i, "statement_type": sql.split()[0], "status": "success", "sql": sql}
        for i, sql in enumerate(statements, 1)
    ]
    monkeypatch.setattr(service, "_oracle_adapter", adapter)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    sync = MagicMock(return_value=SchemaRefreshMutationSync(job_id="sample-refresh"))
    monkeypatch.setattr(service, "_submit_schema_refresh_after_admin_mutation", sync)
    audit = MagicMock()
    monkeypatch.setattr(service, "_record_admin_audit", audit)
    info = service.sample_data_info(dataset)
    assert info.imported_objects == list(definition.objects)
    adapter.fetch_catalog.assert_called_once_with(
        include_samples=False, object_keys={("APP", name) for name in definition.objects}
    )
    request = SampleDataMutationRequest(
        dataset=dataset, step=step, confirmation=definition.confirmation
    )
    result = service.import_sample_data(request)
    assert result.executed and result.schema_refresh_job_id == "sample-refresh"
    sections = ["tables", "views", "data"] if step == SampleDataStep.ALL else [step.value]
    adapter.execute_admin_statements.assert_called_once_with(
        [sql for section in sections for sql in info.sql[section]],
        atomic=False,
        ignored_error_codes=frozenset({"ORA-00955", "ORA-00001"}),
    )
    targets = sync.call_args.kwargs["target_objects"]
    expected = definition.views if step == SampleDataStep.VIEWS else definition.tables
    if step == SampleDataStep.ALL:
        expected = definition.objects
    assert {t.object_name for t in targets} == set(expected)
    assert all(t.owner == "APP" and t.expected_state == "present" for t in targets)
    assert audit.call_args.kwargs["detail"]["dataset"] == dataset.value
    adapter.execute_admin_statements.reset_mock()
    result = service.delete_sample_data(request)
    assert result.executed
    adapter.execute_admin_statements.assert_called_once_with(
        info.sql["delete"], atomic=False, ignored_error_codes=frozenset({"ORA-00942"})
    )
    targets = sync.call_args.kwargs["target_objects"]
    assert {t.object_name for t in targets} == set(definition.objects)
    assert all(t.expected_state == "absent" for t in targets)


@pytest.mark.parametrize("operation", ["import_sample_data", "delete_sample_data"])
def test_domain_checks_current_permission_before_mutation(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service()
    monkeypatch.setattr(get_settings(), "app_auth_enabled", True)
    security = MagicMock()
    security.principal_for_worker.return_value.has_permission.return_value = False
    monkeypatch.setattr("app.security.service.get_security_service", lambda: security)
    with pytest.raises(SecurityApiError) as caught:
        getattr(service, operation)(
            SampleDataMutationRequest(
                dataset=SampleDataset.SALES, confirmation="NL2SQL_SALES_SAMPLE"
            )
        )
    assert caught.value.status_code == 403
    assert service.get_catalog().tables == []


@pytest.mark.parametrize("dataset", [SampleDataset.SALES, SampleDataset.INQUIRIES])
def test_sample_sql_supports_relations_and_example_queries(dataset: SampleDataset) -> None:
    # Oracle 方言として解析後、テスト専用 SQLite に変換して FK・データ・JOIN を検証する。
    # 実 Oracle での DDL 実行検証の代替ではなく、固定 fixture の整合性検証。
    sections = make_service().sample_data_info(dataset).sql
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for section in ("tables", "views", "data"):
            for statement in sections[section]:
                parsed = sqlglot.parse_one(statement, read="oracle")
                if statement.startswith("COMMENT"):
                    continue
                statement = parsed.sql(dialect="sqlite").replace(
                    "CREATE OR REPLACE VIEW", "CREATE VIEW"
                )
                connection.execute(statement)
        for table in SAMPLE_DATASETS[dataset].tables:
            assert connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] > 0
        view = SAMPLE_DATASETS[dataset].views[0]
        assert connection.execute(f'SELECT COUNT(*) FROM "{view}"').fetchone()[0] == 6
        if dataset == SampleDataset.SALES:
            assert (
                connection.execute(
                    "SELECT SUM(SALES_AMOUNT) FROM SAMPLE_NL2SQL_V_SALES_DETAIL "
                    "WHERE STATUS = '出荷済'"
                ).fetchone()[0]
                == 550000
            )
        else:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM SAMPLE_NL2SQL_V_INQUIRY_DETAIL "
                    "WHERE RESOLVED_DATE IS NULL"
                ).fetchone()[0]
                == 3
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
