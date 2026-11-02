"""active の切替の基線の照合・job の実行の確認・履歴のテスト（#4167）。

実 Oracle での確認は ``test_recipe_activation_baseline_oracle.py``。ここは SQL の条件を fake の
接続で、worker の結果の扱いを fake の client で確かめる。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from app.api.routes import documents as documents_route
from app.clients.oracle import (
    IngestionJobFence,
    OracleClient,
    RecipeActivationConflictError,
    RecipeActivationFencedError,
)
from app.clients.recipe_version_log import (
    INSERT_RECIPE_VERSION_LOG_SQL,
    recipe_version_log_schema_sql,
)
from app.config import Settings
from app.rag import oracle_schema
from app.schemas.document import (
    FileStatus,
    IngestionJob,
    IngestionJobLease,
    IngestionJobPhase,
    IngestionJobStatus,
)
from tests.language_release_support import install_language_release_stub
from tests.test_document_recipes import (
    _CompletingRecipeOracle,
    _extract_job,
    _RecordingRecipePipeline,
)

_DOCUMENT_ID = "doc-1"


@dataclass
class _Rows:
    """SELECT の結果の元。実際の表の代わりに、切替が読む値だけを持つ。"""

    active: str | None = None
    job: Mapping[str, object] | None = None


class _Cursor:
    description = None

    def __init__(self, rows: _Rows, calls: list[tuple[str, dict[str, object]]]) -> None:
        self._source = rows
        self._calls = calls
        self._rows: list[dict[str, object]] = []

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> None:
        text = " ".join(statement.split())
        self._calls.append((text, dict(parameters or {})))
        self._rows = []
        if text.startswith("SELECT recipe_id, document_id FROM rag_document_recipes"):
            self._rows = [{"recipe_id": "recipe-1", "document_id": _DOCUMENT_ID}]
        elif text.startswith("SELECT status, attempt_count, lease_owner"):
            self._rows = [dict(self._source.job)] if self._source.job else []
        elif "is_active = 1" in text and text.startswith("SELECT chunk_set_id"):
            self._rows = [{"chunk_set_id": self._source.active}] if self._source.active else []
        elif text.startswith("SELECT chunk_set_id FROM rag_chunk_sets WHERE chunk_set_id"):
            self._rows = [{"chunk_set_id": (parameters or {})["chunk_set_id"]}]

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows

    def close(self) -> None:
        return None


class _Connection:
    def __init__(self, rows: _Rows) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object]]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self.rows, self.calls)

    def writes(self) -> list[str]:
        return [
            statement
            for statement, _ in self.calls
            if statement.startswith(("UPDATE", "DELETE", "INSERT"))
        ]

    def log_binds(self) -> list[dict[str, object]]:
        return [binds for statement, binds in self.calls if "rag_recipe_version_log" in statement]


def _client(monkeypatch: pytest.MonkeyPatch, connection: _Connection) -> OracleClient:
    client = OracleClient(settings=Settings.model_construct())

    async def run_transaction(operation: Callable[[object], object]) -> object:
        return operation(connection)

    monkeypatch.setattr(client, "_run_transaction", run_transaction)
    return client


_RUNNING_JOB = {"status": "RUNNING", "attempt_count": 2, "lease_owner": "worker-a"}
_FENCE = IngestionJobFence(job_id="job-1", attempt_count=2, lease_owner="worker-a")


async def test_activation_rejects_when_base_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection(_Rows(active="cs-newer"))
    client = _client(monkeypatch, connection)

    with pytest.raises(RecipeActivationConflictError) as caught:
        await client.activate_recipe_chunk_set(
            recipe_id="recipe-1", chunk_set_id="cs-candidate", expected_base_chunk_set_id="cs-base"
        )

    assert (caught.value.expected, caught.value.current) == ("cs-base", "cs-newer")
    assert connection.writes() == []


async def test_activation_rejects_when_first_version_appeared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """開始の時に有効な版が無かった job は、その間にできた版を上書きしない。"""
    connection = _Connection(_Rows(active="cs-newer"))
    client = _client(monkeypatch, connection)

    with pytest.raises(RecipeActivationConflictError):
        await client.activate_recipe_chunk_set(
            recipe_id="recipe-1", chunk_set_id="cs-candidate", expected_base_chunk_set_id=None
        )

    assert connection.writes() == []


@pytest.mark.parametrize(
    "job_row",
    [
        None,
        {**_RUNNING_JOB, "status": "CANCELLED"},
        {**_RUNNING_JOB, "status": "QUEUED", "lease_owner": None},
        {**_RUNNING_JOB, "lease_owner": "worker-b"},
        # 同じ worker が claim し直した。lease の持ち主だけでは区別できない。
        {**_RUNNING_JOB, "attempt_count": 3},
    ],
    ids=["job_missing", "cancelled", "recovered", "reclaimed_by_other", "reclaimed_by_same_worker"],
)
async def test_activation_rejects_when_run_no_longer_holds_the_job(
    monkeypatch: pytest.MonkeyPatch, job_row: Mapping[str, object] | None
) -> None:
    connection = _Connection(_Rows(active="cs-base", job=job_row))
    client = _client(monkeypatch, connection)

    with pytest.raises(RecipeActivationFencedError):
        await client.activate_recipe_chunk_set(
            recipe_id="recipe-1",
            chunk_set_id="cs-candidate",
            expected_base_chunk_set_id="cs-base",
            job_fence=_FENCE,
        )

    assert connection.writes() == []


@pytest.mark.parametrize(
    ("base", "operation"),
    [(None, "INITIAL"), ("cs-base", "REPROCESS")],
    ids=["first_version", "reprocess"],
)
async def test_activation_logs_one_row_with_parent(
    monkeypatch: pytest.MonkeyPatch, base: str | None, operation: str
) -> None:
    connection = _Connection(_Rows(active=base, job=_RUNNING_JOB))
    client = _client(monkeypatch, connection)

    await client.activate_recipe_chunk_set(
        recipe_id="recipe-1",
        chunk_set_id="cs-candidate",
        expected_base_chunk_set_id=base,
        extraction_recipe_id="er-1",
        job_fence=_FENCE,
        chunk_set_input_id="cs-input",
    )

    [binds] = connection.log_binds()
    assert binds["parent_chunk_set_id"] == base
    assert binds["operation"] == operation
    assert binds["chunk_set_id"] == "cs-candidate"
    assert binds["job_id"] == "job-1"
    assert binds["chunk_set_input_id"] == "cs-input"
    assert binds["extraction_recipe_id"] == "er-1"


async def test_activation_of_the_active_version_adds_no_log_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(_Rows(active="cs-base"))
    client = _client(monkeypatch, connection)

    await client.activate_recipe_chunk_set(
        recipe_id="recipe-1", chunk_set_id="cs-base", expected_base_chunk_set_id="cs-base"
    )

    assert connection.log_binds() == []


def test_log_has_no_fk_to_chunk_sets_and_no_text_column() -> None:
    """旧版を消しても履歴が残り、本文・質問を持つ列が無い。"""
    ddl = recipe_version_log_schema_sql()
    assert "REFERENCES rag_chunk_sets" not in ddl
    assert "CLOB" not in ddl
    assert "JSON" not in ddl
    assert "ON DELETE CASCADE" in ddl
    assert "rag_chunks" not in INSERT_RECIPE_VERSION_LOG_SQL


def test_log_migration_only_adds_objects() -> None:
    sections = {
        section.name: section for section in oracle_schema.oracle_schema_migration_sections()
    }
    section = sections["20261102_006_recipe_version_log"]
    assert not section.destructive_note
    assert "CREATE TABLE rag_recipe_version_log" in section.sql
    assert not any(word in section.sql.upper() for word in ("DROP ", "DELETE FROM", "TRUNCATE "))


class _LeaseOracle(_CompletingRecipeOracle):
    """lease を持ち、切替の照合を実物と同じ条件で行う fake。"""

    def __init__(self, *, active: str | None = None) -> None:
        super().__init__()
        self.active = active
        self.lease_owner: str | None = None
        self.discarded: list[str] = []
        self.recipe_statuses: list[FileStatus] = []
        self.activation_kwargs: list[dict[str, Any]] = []

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, object]:
        row = await super().get_document_recipe(document_id, recipe_id)
        return {**row, "active_chunk_set_id": self.active}

    async def claim_ingestion_job(
        self, job_id: str, *, started_at: datetime, lease_owner: str | None = None
    ) -> IngestionJob | None:
        self.lease_owner = lease_owner
        return await super().claim_ingestion_job(
            job_id, started_at=started_at, lease_owner=lease_owner
        )

    async def get_ingestion_job_lease(self, job_id: str) -> IngestionJobLease:
        return IngestionJobLease(status=self.jobs[job_id].status, lease_owner=self.lease_owner)

    async def update_ingestion_job_progress(self, *args: object, **kwargs: object) -> None:
        _ = args, kwargs

    async def update_document_recipe_status(
        self, *, recipe_id: str, status: FileStatus, **kwargs: object
    ) -> None:
        self.recipe_statuses.append(status)
        await super().update_document_recipe_status(recipe_id=recipe_id, status=status, **kwargs)

    async def transition_ingestion_job(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
        skip_reason: str | None = None,
        **kwargs: Any,
    ) -> IngestionJob | None:
        if lease_owner is not None and lease_owner != self.lease_owner:
            return None
        updated = await super().transition_ingestion_job(job_id, **kwargs)
        if updated is not None:
            updated = updated.model_copy(update={"skip_reason": skip_reason})
            self.jobs[job_id] = updated
        return updated

    async def activate_recipe_chunk_set(self, *, chunk_set_id: str, **kwargs: Any) -> None:
        self.activation_kwargs.append({"chunk_set_id": chunk_set_id, **kwargs})
        fence: IngestionJobFence = kwargs["job_fence"]
        job = self.jobs[fence.job_id]
        if (
            job.status != IngestionJobStatus.RUNNING
            or job.attempt_count != fence.attempt_count
            or self.lease_owner != fence.lease_owner
        ):
            raise RecipeActivationFencedError()
        expected = kwargs["expected_base_chunk_set_id"]
        if self.active != expected:
            raise RecipeActivationConflictError(expected=expected, current=self.active)
        self.active = chunk_set_id
        await super().activate_recipe_chunk_set(chunk_set_id=chunk_set_id, **kwargs)

    async def discard_recipe_candidate_chunk_set(
        self, *, recipe_id: str, chunk_set_id: str
    ) -> bool:
        _ = recipe_id
        self.discarded.append(chunk_set_id)
        return True


@pytest.fixture
def lease_oracle(monkeypatch: pytest.MonkeyPatch) -> _LeaseOracle:
    install_language_release_stub(monkeypatch)
    fake = _LeaseOracle(active="cs-base")
    _RecordingRecipePipeline.calls = []
    _RecordingRecipePipeline.events = []
    monkeypatch.setattr(documents_route, "IngestionPipeline", _RecordingRecipePipeline)
    monkeypatch.setattr(documents_route, "get_settings", lambda: Settings())
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(documents_route, "_dispatch_ingestion_job", lambda *a, **k: None)
    fake.jobs["job-1"] = _extract_job("job-1").model_copy(update={"phase": IngestionJobPhase.INDEX})
    return fake


def _after_last_cancel_check(monkeypatch: pytest.MonkeyPatch, action: Callable[[], None]) -> None:
    """切替の直前の取り消しの確認の後に ``action`` を起こす。"""
    record = documents_route._record_recipe_artifact_layers

    async def record_then_act(*args: Any, **kwargs: Any) -> None:
        await record(*args, **kwargs)
        action()

    monkeypatch.setattr(documents_route, "_record_recipe_artifact_layers", record_then_act)


async def test_run_passes_baseline_read_before_building_and_its_own_identity(
    lease_oracle: _LeaseOracle,
) -> None:
    await documents_route._run_ingestion_job("job-1", lease_owner="worker-a")

    [kwargs] = lease_oracle.activation_kwargs
    assert kwargs["expected_base_chunk_set_id"] == "cs-base"
    assert kwargs["job_fence"] == IngestionJobFence(
        job_id="job-1", attempt_count=1, lease_owner="worker-a"
    )
    assert kwargs["chunk_set_input_id"]
    assert lease_oracle.jobs["job-1"].status == IngestionJobStatus.SUCCEEDED
    assert lease_oracle.active == "chunk-set-pending"


async def test_run_that_lost_the_lease_after_the_last_check_does_not_switch(
    lease_oracle: _LeaseOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """切替の直前の確認を通った後に lease を失っても、切替の中で止まる（#4167 の不具合）。"""

    def lose_lease() -> None:
        lease_oracle.lease_owner = "worker-b"

    _after_last_cancel_check(monkeypatch, lose_lease)

    await documents_route._run_ingestion_job("job-1", lease_owner="worker-a")

    assert lease_oracle.active == "cs-base"
    assert lease_oracle.activated == []
    # 新しい実行の状態を変えない。候補も消さない（新しい実行が同じ候補を使うことがある）。
    assert lease_oracle.jobs["job-1"].status == IngestionJobStatus.RUNNING
    assert lease_oracle.discarded == []
    assert "success" not in _RecordingRecipePipeline.events


async def test_run_of_the_same_worker_reclaimed_does_not_switch_or_succeed(
    lease_oracle: _LeaseOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じ worker が claim し直すと lease の持ち主では区別できない。古い試行を止める。"""

    def reclaim() -> None:
        job = lease_oracle.jobs["job-1"]
        lease_oracle.jobs["job-1"] = job.model_copy(update={"attempt_count": job.attempt_count + 1})

    _after_last_cancel_check(monkeypatch, reclaim)

    await documents_route._run_ingestion_job("job-1", lease_owner="worker-a")

    assert lease_oracle.active == "cs-base"
    assert lease_oracle.jobs["job-1"].status == IngestionJobStatus.RUNNING
    assert lease_oracle.jobs["job-1"].attempt_count == 2


async def test_conflict_discards_candidate_and_skips_job_without_retry(
    lease_oracle: _LeaseOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    def newer_version_arrives() -> None:
        lease_oracle.active = "cs-newer"

    _after_last_cancel_check(monkeypatch, newer_version_arrives)
    enqueued: list[object] = []

    async def enqueue(*args: object, **kwargs: object) -> None:
        enqueued.append((args, kwargs))

    monkeypatch.setattr(documents_route, "_enqueue_ingestion_job_for_recipe", enqueue)

    # 失敗ではないので、propagate_errors でも例外にしない。
    await documents_route._run_ingestion_job("job-1", propagate_errors=True, lease_owner="worker-a")

    job = lease_oracle.jobs["job-1"]
    assert job.status == IngestionJobStatus.SKIPPED
    assert job.skip_reason == documents_route.INGESTION_SKIP_REASON_SUPERSEDED
    assert job.error_message is None
    assert job.finished_at is not None
    assert lease_oracle.active == "cs-newer"
    assert lease_oracle.discarded == ["chunk-set-pending"]
    assert lease_oracle.recipe_statuses[-1] == FileStatus.INDEXED
    assert enqueued == []
    assert "success" not in _RecordingRecipePipeline.events


async def test_conflict_after_lease_loss_keeps_the_new_run_state(
    lease_oracle: _LeaseOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SKIPPED を書く前に lease を失っていたら、job もレシピの status も変えない。"""

    def newer_version_arrives() -> None:
        lease_oracle.active = "cs-newer"

    _after_last_cancel_check(monkeypatch, newer_version_arrives)
    discard = lease_oracle.discard_recipe_candidate_chunk_set

    async def discard_then_lose_lease(*, recipe_id: str, chunk_set_id: str) -> bool:
        lease_oracle.lease_owner = "worker-b"
        return await discard(recipe_id=recipe_id, chunk_set_id=chunk_set_id)

    monkeypatch.setattr(lease_oracle, "discard_recipe_candidate_chunk_set", discard_then_lose_lease)
    await documents_route._run_ingestion_job("job-1", lease_owner="worker-a")

    assert lease_oracle.jobs["job-1"].status == IngestionJobStatus.RUNNING
    assert lease_oracle.jobs["job-1"].skip_reason is None
    assert lease_oracle.recipe_statuses[-1] != FileStatus.INDEXED


async def test_skip_reason_is_written_with_the_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(_Rows())
    client = _client(monkeypatch, connection)

    await client.transition_ingestion_job(
        "job-1",
        from_statuses=(IngestionJobStatus.RUNNING,),
        to_status=IngestionJobStatus.SKIPPED,
        finished_at=datetime.now(UTC),
        lease_owner="worker-a",
        skip_reason=documents_route.INGESTION_SKIP_REASON_SUPERSEDED,
    )

    statement, binds = connection.calls[0]
    assert "skip_reason = :skip_reason" in statement
    assert "lease_owner = :lease_owner" in statement
    assert binds["skip_reason"] == documents_route.INGESTION_SKIP_REASON_SUPERSEDED
