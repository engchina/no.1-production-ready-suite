from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.features.nl2sql.models import (
    AssetRefreshData,
    Nl2SqlEngine,
    Nl2SqlProfile,
    ProfileSyncJobRequest,
    ProfileSyncJobStatus,
    SelectAiDbProfileMutationData,
)
from app.features.nl2sql.ontology_store import InMemoryOntologyStore
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.features.nl2sql.profile_sync import ProfileSyncService
from app.settings import Settings


class _FakeProfileService:
    def __init__(self) -> None:
        self.profile = Nl2SqlProfile(id="profile-1", name="請求分析", etag="etag-1")
        self.oracle_calls = 0
        self.agent_calls = 0
        self.fail_oracle = False

    def get_profile(self, profile_id: str) -> Nl2SqlProfile:
        if profile_id != self.profile.id:
            raise ValueError("指定された profile が見つかりません。")
        return self.profile.model_copy(deep=True)

    def upsert_profile_select_ai_profile(
        self,
        profile_id: str,
        _request: object,
    ) -> SelectAiDbProfileMutationData:
        assert profile_id == self.profile.id
        self.oracle_calls += 1
        if self.fail_oracle:
            raise TimeoutError("Oracle round-trip timeout")
        return SelectAiDbProfileMutationData(
            executed=True,
            status="updated",
            profile_name="INVOICE_PROFILE",
        )

    def refresh_select_ai_agent_assets(
        self,
        profile_id: str,
        *,
        profile_already_synced: bool = False,
    ) -> AssetRefreshData:
        assert profile_id == self.profile.id
        assert profile_already_synced is True
        self.agent_calls += 1
        return AssetRefreshData(
            engine=Nl2SqlEngine.SELECT_AI_AGENT,
            refreshed=True,
            status="ready",
        )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        nl2sql_ontology_worker_mode="external",
        nl2sql_profile_sync_job_timeout_seconds=300.0,
    )


def test_profile_sync_is_idempotent_and_agent_reuses_oracle_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.features.nl2sql.profile_sync.get_settings", _settings)
    store = InMemoryOntologyStore()
    service = _FakeProfileService()
    sync = ProfileSyncService(service=service, store_provider=lambda: store)  # type: ignore[arg-type]
    request = ProfileSyncJobRequest(
        confirmation="ADMIN_EXECUTE",
        rebuild_agent_assets=True,
    )

    first = sync.start("profile-1", request, idempotency_key="same-request")
    duplicate = sync.start("profile-1", request, idempotency_key="same-request")
    assert duplicate.job_id == first.job_id

    completed = sync.run_persisted(first.job_id)
    assert completed.status == ProfileSyncJobStatus.SUCCEEDED
    assert service.oracle_calls == 1
    assert service.agent_calls == 1


def test_concurrent_profile_sync_submissions_share_one_persisted_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.features.nl2sql.profile_sync.get_settings", _settings)
    store = InMemoryOntologyStore()
    service = _FakeProfileService()
    sync = ProfileSyncService(service=service, store_provider=lambda: store)  # type: ignore[arg-type]
    request = ProfileSyncJobRequest(confirmation="ADMIN_EXECUTE")

    with ThreadPoolExecutor(max_workers=8) as executor:
        jobs = list(
            executor.map(
                lambda _index: sync.start(
                    "profile-1",
                    request,
                    idempotency_key="concurrent-request",
                ),
                range(16),
            )
        )

    assert len({job.job_id for job in jobs}) == 1
    assert len(store.list_documents("jobs", {"profile_id": "profile-1"})) == 1


def test_profile_sync_failure_is_persisted_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.features.nl2sql.profile_sync.get_settings", _settings)
    store = InMemoryOntologyStore()
    service = _FakeProfileService()
    service.fail_oracle = True
    sync = ProfileSyncService(service=service, store_provider=lambda: store)  # type: ignore[arg-type]
    request = ProfileSyncJobRequest(confirmation="ADMIN_EXECUTE")

    started = sync.start("profile-1", request, idempotency_key="will-fail")
    failed = sync.run_persisted(started.job_id)
    assert failed.status == ProfileSyncJobStatus.FAILED
    assert "Oracle round-trip timeout" in failed.error_message_ja

    service.fail_oracle = False
    retried = sync.retry(failed.job_id)
    completed = sync.run_persisted(retried.job_id)
    assert completed.status == ProfileSyncJobStatus.SUCCEEDED
    assert completed.retry_of_job_id == failed.job_id
    assert service.oracle_calls == 2


def test_profile_sync_can_be_cancelled_before_worker_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.features.nl2sql.profile_sync.get_settings", _settings)
    store = InMemoryOntologyStore()
    service = _FakeProfileService()
    sync = ProfileSyncService(service=service, store_provider=lambda: store)  # type: ignore[arg-type]
    started = sync.start(
        "profile-1",
        ProfileSyncJobRequest(confirmation="ADMIN_EXECUTE"),
        idempotency_key="cancel-me",
    )

    assert sync.cancel_for_profile("profile-1") == 1
    cancelled = sync.run_persisted(started.job_id)
    assert cancelled.status == ProfileSyncJobStatus.CANCELLED
    assert service.oracle_calls == 0


def test_oracle_connection_applies_round_trip_timeout_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Connection:
        call_timeout = 0
        closed = False
        rollbacks = 0

        def close(self) -> None:
            self.closed = True

        def rollback(self) -> None:
            self.rollbacks += 1

    connection = Connection()

    class Driver:
        @staticmethod
        def connect(**_kwargs: object) -> Connection:
            return connection

    adapter = OracleNl2SqlAdapter(
        Settings(
            oracle_user="APP",
            oracle_password="password",
            oracle_dsn="localhost/FREEPDB1",
            nl2sql_oracle_call_timeout_seconds=12.5,
        )
    )
    monkeypatch.setattr(adapter, "_load_oracledb", lambda: Driver())
    monkeypatch.setattr(adapter, "_init_client", lambda _driver: None)

    with pytest.raises(TimeoutError, match="round-trip"), adapter.connection() as opened:
        assert opened is connection
        assert connection.call_timeout == 12_500
        raise TimeoutError("round-trip")

    assert connection.closed is True
    assert connection.rollbacks == 1


def test_drop_profile_compatibility_signatures_stop_after_first_success() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, _sql: str, _params: object) -> None:
            self.calls += 1

    cursor = Cursor()
    adapter = OracleNl2SqlAdapter(Settings())

    adapter._drop_cloud_ai_profile_best_effort(cursor, "INVOICE_PROFILE")  # noqa: SLF001

    assert cursor.calls == 1
