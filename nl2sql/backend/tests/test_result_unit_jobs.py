"""保存済みジョブ・チャットの受取結果・MCPの単位metadataが同じ版を保つ。"""

from __future__ import annotations

from typing import Any

import pytest
from test_nl2sql_job_runtime import _repository, _request, _worker

from app.features.mcp.tools import _job_result
from app.features.nl2sql import result_units
from app.features.nl2sql.models import JobStatus, QueryResults
from app.features.nl2sql.ontology_definitions import PropertyDefinition
from app.settings import get_settings


@pytest.mark.parametrize("chat", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_job_result_unit_metadata_survives_storage_and_mcp(
    monkeypatch: pytest.MonkeyPatch, chat: bool, enabled: bool
) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_job_worker_mode", "external")
    repo = _repository()
    service = _worker(repo)
    # 決定論の既定モックは全列を返すため、実SQLの射影に一致するDBスタブを使う。
    monkeypatch.setattr(
        service,
        "_mock_execute",
        lambda *_args: QueryResults(
            columns=["ID"],
            rows=[{"ID": 1}],
            total=1,
        ),
    )
    definition = PropertyDefinition(
        api_name="source_id",
        name_ja="合成項目",
        unit="declared-unit",
        review_status="reviewed",
        mappings=[{"owner": "APP", "object_name": "ORDERS", "column_name": "ID"}],
    )
    monkeypatch.setattr(service, "_resolve_job_business_release", lambda *args: "pinned_release")
    monkeypatch.setattr(service, "_job_published_ontology_markdown", lambda **kwargs: None)
    calls: list[str] = []

    def load(runtime: Any, profile: str, release: str) -> list[PropertyDefinition]:
        calls.append(release)
        return [definition]

    monkeypatch.setattr(result_units, "published_unit_definitions", load)
    request = _request().model_copy(
        update={"chat": chat, "use_ontology_context": enabled, "include_ontology_grounding": False}
    )
    created = service.start_job(request, actor_user_uuid="user-1")
    assert service.run_next_nl2sql_job(worker_id="unit-job", job_id=created.job_id)
    observer = _worker(repo)
    job = observer.get_job(created.job_id, actor_user_uuid="user-1")
    assert job is not None
    assert job.result is not None
    assert job.status == JobStatus.DONE, job.error_message
    if chat:
        results = observer.take_chat_execution_result(job.job_id, actor_user_uuid="user-1").results
    else:
        results = job.result.results
        assert _job_result(job).column_metadata == results.column_metadata
    assert results.column_metadata[0].name == "ID"
    assert calls == (["pinned_release"] if enabled else [])
    assert results.column_metadata[0].unit == ("declared-unit" if enabled else None), (
        job.result.executable_sql,
        results.column_metadata,
    )
    assert calls == (["pinned_release"] if enabled else [])
