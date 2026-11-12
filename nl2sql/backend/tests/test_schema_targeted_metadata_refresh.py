"""指定対象の再取得はDDL時刻に依存せず、全体の差分走査は維持する。"""

from typing import Any

import pytest
from test_nl2sql_incremental_state import _incremental_service, _RefreshAdapter, _table

from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import (
    SchemaCatalog,
    SchemaRefreshJobStatus,
    SchemaRefreshMode,
    SchemaRefreshTargetObject,
)


def initial_repository() -> MemoryIncrementalNl2SqlRepository:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    repository.apply_schema_refresh(
        catalog=SchemaCatalog(refreshed_at="before", tables=[_table("A"), _table("B")]),
        manifest={("APP", "A"): "v1", ("APP", "B"): "v1"},
        changed_keys={("APP", "A"), ("APP", "B")},
        deleted_keys=set(),
    )
    return repository


@pytest.mark.parametrize("mode", [SchemaRefreshMode.TARGETED, SchemaRefreshMode.FULL])
def test_same_ddl_timestamp_refetches_only_explicit_target(mode: SchemaRefreshMode) -> None:
    repository = initial_repository()
    changed = _table("A", comment="新しい注釈")
    changed.columns[0].comment = "新しい列注釈"
    adapter = _RefreshAdapter(
        {("APP", "A"): "v1", ("APP", "B"): "v1"},
        SchemaCatalog(refreshed_at="after", tables=[changed, _table("B", comment="対象外")]),
    )
    service = _incremental_service(repository)
    service._oracle_adapter = adapter  # type: ignore[assignment]
    service._use_oracle_runtime = lambda: True  # type: ignore[method-assign]
    job = service.start_schema_refresh_job(
        dispatch=False,
        mode=mode,
        target_objects=[
            SchemaRefreshTargetObject(owner="APP", object_name="A", object_type="table")
        ]
        if mode == SchemaRefreshMode.TARGETED
        else None,
    )
    assert service._run_schema_refresh_job(job.job_id)
    saved = service.get_schema_refresh_job(job.job_id)
    assert saved is not None and saved.status == SchemaRefreshJobStatus.DONE
    detail = repository.get_schema_object("APP", "A")
    other = repository.get_schema_object("APP", "B")
    assert detail is not None and other is not None
    assert other.table.comment == ""
    if mode == SchemaRefreshMode.TARGETED:
        assert adapter.requested_keys == {("APP", "A")}
        assert detail.table.comment == "新しい注釈"
        assert detail.table.columns[0].comment == "新しい列注釈"
        assert saved.changed_objects == 1
    else:
        assert adapter.requested_keys == set()
        assert detail.table.comment == ""
        assert saved.changed_objects == 0
    assert repository.schema_manifest() == {("APP", "A"): "v1", ("APP", "B"): "v1"}


@pytest.mark.parametrize("missing_result", [False, True])
def test_targeted_detail_failure_does_not_publish_or_report_done(missing_result: bool) -> None:
    repository = initial_repository()
    before = repository.load_catalog(), repository.get_catalog_head()
    adapter = _RefreshAdapter({("APP", "A"): "v1"}, SchemaCatalog(refreshed_at="after", tables=[]))

    def fail(keys: Any) -> SchemaCatalog:
        raise OSError("synthetic detail read failure")

    if not missing_result:
        adapter.fetch_catalog_objects = fail  # type: ignore[method-assign]
    service = _incremental_service(repository)
    service._oracle_adapter = adapter  # type: ignore[assignment]
    service._use_oracle_runtime = lambda: True  # type: ignore[method-assign]
    job = service.start_schema_refresh_job(
        dispatch=False,
        mode=SchemaRefreshMode.TARGETED,
        target_objects=[
            SchemaRefreshTargetObject(owner="APP", object_name="A", object_type="table")
        ],
    )
    assert not service._run_schema_refresh_job(job.job_id)
    saved = service.get_schema_refresh_job(job.job_id)
    assert saved is not None and saved.status == SchemaRefreshJobStatus.ERROR
    assert (repository.load_catalog(), repository.get_catalog_head()) == before
