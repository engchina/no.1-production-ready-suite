"""業務プロファイルの Select AI Agent アセットの最後の再構築の日時と結果（#1691）。

再構築は業務プロファイルの保存の Oracle 反映のジョブ（profile_sync）の工程で行い、結果は
ジョブの記録に残る。`GET /api/nl2sql/profiles/{profile_id}/agent-asset-rebuild` は、
その記録から最後の再構築を業務プロファイルの版と別に返す。
LLM と Oracle は呼ばない（スタブの業務プロファイルの service）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request
from test_nl2sql_profile_sync import _FakeProfileService

from app.features.nl2sql import profile_sync as profile_sync_module
from app.features.nl2sql import router as nl2sql_router
from app.features.nl2sql.models import (
    AssetRefreshData,
    Nl2SqlEngine,
    Nl2SqlProfile,
    ProfileAgentAssetRebuildStatus,
    ProfileSyncJobRequest,
    ProfileSyncJobStatus,
)
from app.features.nl2sql.ontology_store import InMemoryOntologyStore
from app.features.nl2sql.profile_sync import ProfileSyncService


class _StubProfileService(_FakeProfileService):
    """Agent の再構築の成否を切り替えられるスタブ。

    無い業務プロファイルは KeyError（実装と同じ）。
    """

    def __init__(self) -> None:
        super().__init__()
        self.fail_agent = False
        self.team_name = "NL2SQL_TEAM_1"

    def get_profile(self, profile_id: str) -> Nl2SqlProfile:
        if profile_id != self.profile.id:
            raise KeyError(profile_id)
        return self.profile.model_copy(deep=True)

    def refresh_select_ai_agent_assets(
        self,
        profile_id: str,
        *,
        profile_already_synced: bool = False,
    ) -> AssetRefreshData:
        assert profile_id == self.profile.id
        assert profile_already_synced is True
        self.agent_calls += 1
        if self.fail_agent:
            return AssetRefreshData(
                engine=Nl2SqlEngine.SELECT_AI_AGENT,
                refreshed=False,
                status="error",
                warning="ORA-20051: Agent team の作成に失敗 at line 12 private stack",
            )
        return AssetRefreshData(
            engine=Nl2SqlEngine.SELECT_AI_AGENT,
            refreshed=True,
            status="ready",
            team_name=self.team_name,
        )

    def save_profile(self) -> None:
        """編集画面の保存（PATCH）と同じく、業務プロファイルの版を進める。"""
        self.profile = self.profile.model_copy(update={"etag": f"{self.profile.etag}-saved"})


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        nl2sql_ontology_worker_mode="external",
        nl2sql_profile_sync_job_timeout_seconds=300.0,
    )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[datetime]]:
    """ジョブの日時を 1 分ずつ進める（並列・高速な実行でも順序が決まる）。"""

    current = [datetime(2026, 10, 13, 9, 0, tzinfo=UTC)]

    def now() -> str:
        current[0] += timedelta(minutes=1)
        return current[0].isoformat()

    monkeypatch.setattr(profile_sync_module, "_now", now)
    yield current


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> tuple[ProfileSyncService, _StubProfileService]:
    monkeypatch.setattr(profile_sync_module, "get_settings", _settings)
    store = InMemoryOntologyStore()
    service = _StubProfileService()
    sync = ProfileSyncService(service=service, store_provider=lambda: store)  # type: ignore[arg-type]
    return sync, service


def _save(sync: ProfileSyncService, key: str, *, rebuild: bool) -> str:
    started = sync.start(
        "profile-1",
        ProfileSyncJobRequest(confirmation="ADMIN_EXECUTE", rebuild_agent_assets=rebuild),
        idempotency_key=key,
    )
    return sync.run_persisted(started.job_id).job_id


def test_never_rebuilt_profile_returns_never(
    stub: tuple[ProfileSyncService, _StubProfileService],
) -> None:
    sync, _service = stub

    result = sync.last_agent_rebuild("profile-1")

    assert result.status == ProfileAgentAssetRebuildStatus.NEVER
    assert result.rebuilt_at == ""
    assert result.job_id == ""
    assert result.profile_changed_since is False


def test_saves_without_rebuild_or_failed_before_rebuild_are_not_counted(
    stub: tuple[ProfileSyncService, _StubProfileService],
    clock: list[datetime],
) -> None:
    """再構築しない保存と、DBMS_CLOUD_AI Profile の反映で止まった保存は、再構築に数えない。"""
    sync, service = stub
    _save(sync, "without-rebuild", rebuild=False)
    service.fail_oracle = True
    _save(sync, "oracle-failed", rebuild=True)

    result = sync.last_agent_rebuild("profile-1")

    assert service.agent_calls == 0
    assert result.status == ProfileAgentAssetRebuildStatus.NEVER


def test_successful_rebuild_returns_time_job_and_team(
    stub: tuple[ProfileSyncService, _StubProfileService],
    clock: list[datetime],
) -> None:
    sync, service = stub
    job_id = _save(sync, "rebuild", rebuild=True)
    job = sync.get(job_id)
    assert job is not None and job.status == ProfileSyncJobStatus.SUCCEEDED

    result = sync.last_agent_rebuild("profile-1")

    assert result.status == ProfileAgentAssetRebuildStatus.SUCCEEDED
    assert result.job_id == job_id
    # スタブの再構築は日時を返さないので、ジョブが終わった日時。
    assert datetime.fromisoformat(result.rebuilt_at) == datetime.fromisoformat(job.finished_at)
    assert result.team_name == "NL2SQL_TEAM_1"
    assert result.error_code == "" and result.error_message_ja == ""
    assert result.profile_etag == service.profile.etag
    assert result.profile_changed_since is False


def test_refreshed_at_of_agent_result_is_preferred(
    stub: tuple[ProfileSyncService, _StubProfileService],
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync, service = stub
    original = service.refresh_select_ai_agent_assets

    def refresh(profile_id: str, *, profile_already_synced: bool = False) -> AssetRefreshData:
        data = original(profile_id, profile_already_synced=profile_already_synced)
        return data.model_copy(update={"refreshed_at": "2026-10-13T09:01:30+00:00"})

    monkeypatch.setattr(service, "refresh_select_ai_agent_assets", refresh)
    _save(sync, "rebuild", rebuild=True)

    result = sync.last_agent_rebuild("profile-1")

    assert result.rebuilt_at == "2026-10-13T09:01:30+00:00"


def test_failed_rebuild_returns_public_summary_without_oracle_stack(
    stub: tuple[ProfileSyncService, _StubProfileService],
    clock: list[datetime],
) -> None:
    sync, service = stub
    service.fail_agent = True
    job_id = _save(sync, "rebuild-failed", rebuild=True)
    job = sync.get(job_id)
    assert job is not None

    result = sync.last_agent_rebuild("profile-1")

    assert result.status == ProfileAgentAssetRebuildStatus.FAILED
    assert result.job_id == job_id
    assert result.rebuilt_at == job.finished_at
    assert result.error_code == "AGENT_ASSETS_REBUILD_FAILED"
    assert "Select AI Agent アセットを再構築できませんでした" in result.error_message_ja
    assert "ORA-" not in result.error_message_ja
    assert result.team_name == ""


def test_latest_rebuild_wins_and_later_saves_mark_profile_changed(
    stub: tuple[ProfileSyncService, _StubProfileService],
    clock: list[datetime],
) -> None:
    sync, service = stub
    service.fail_agent = True
    _save(sync, "first-failed", rebuild=True)
    service.fail_agent = False
    succeeded_job = _save(sync, "then-succeeded", rebuild=True)

    result = sync.last_agent_rebuild("profile-1")
    assert result.status == ProfileAgentAssetRebuildStatus.SUCCEEDED
    assert result.job_id == succeeded_job

    # 再構築をオフにして保存すると、最後の再構築はそのまま、その後の変更があると分かる。
    service.save_profile()
    _save(sync, "saved-without-rebuild", rebuild=False)
    result = sync.last_agent_rebuild("profile-1")
    assert result.status == ProfileAgentAssetRebuildStatus.SUCCEEDED
    assert result.job_id == succeeded_job
    assert result.profile_changed_since is True

    # 次の再構築の失敗が最後の結果になる。
    service.fail_agent = True
    failed_job = _save(sync, "later-failed", rebuild=True)
    result = sync.last_agent_rebuild("profile-1")
    assert result.status == ProfileAgentAssetRebuildStatus.FAILED
    assert result.job_id == failed_job
    assert result.profile_changed_since is False


def test_unknown_profile_raises_key_error(
    stub: tuple[ProfileSyncService, _StubProfileService],
) -> None:
    sync, _service = stub
    with pytest.raises(KeyError):
        sync.last_agent_rebuild("missing")


def _anon_request() -> Request:
    return cast(Request, SimpleNamespace(state=SimpleNamespace(principal=None)))


def test_router_returns_last_rebuild_and_404_for_unknown_profile(
    stub: tuple[ProfileSyncService, _StubProfileService],
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync, _service = stub
    monkeypatch.setattr(profile_sync_module, "profile_sync_service", sync)
    _save(sync, "rebuild", rebuild=True)

    response = nl2sql_router.get_profile_agent_asset_rebuild("profile-1", _anon_request())

    assert response.data is not None
    assert response.data.status == ProfileAgentAssetRebuildStatus.SUCCEEDED
    with pytest.raises(HTTPException) as raised:
        nl2sql_router.get_profile_agent_asset_rebuild("missing", _anon_request())
    assert raised.value.status_code == 404
