"""品質評価の過去の評価の見え方・一覧・取り消しのテスト（#4811）。

見え方の規則（`EvaluationJobViewer`）:
- 使った検索・回答プロファイルの分かる job は、そのプロファイルをすべて今使える利用者が見られる。
- プロファイルの分からない job（記録の無い古い行・プロファイルを使わない評価）は、実行した本人と
  system_admin だけ。
- 取り消しは見える job のうち、実行した本人と system_admin だけ。
内容はすべて合成の値。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.main import app
from app.rag import evaluation_jobs
from app.rag.evaluation_jobs import (
    EvaluationJobForbiddenError,
    EvaluationJobNotFoundError,
    EvaluationJobOutcome,
    EvaluationJobRecord,
    EvaluationJobService,
    EvaluationJobViewer,
    MemoryEvaluationJobStore,
    set_evaluation_job_service,
)
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from tests.support import TEST_REQUEST_HEADERS

_TENANT = "t" * 64
_OWNER = "o" * 64
_OTHER = "x" * 64
_LEASE = "pytest-worker"


def _viewer(
    user: str,
    *,
    profiles: set[str] | None = None,
    admin: bool = False,
) -> EvaluationJobViewer:
    return EvaluationJobViewer(
        tenant_id_hash=_TENANT,
        user_id_hash=user,
        all_users_visible=admin,
        allowed_search_answer_profile_ids=None if profiles is None else frozenset(profiles),
    )


_OWNER_WITH_PROFILE = _viewer(_OWNER, profiles={"profile-a"})
_OWNER_WITHOUT_PROFILE = _viewer(_OWNER, profiles={"profile-z"})
_OTHER_WITH_PROFILE = _viewer(_OTHER, profiles={"profile-a", "profile-b"})
_OTHER_WITHOUT_PROFILE = _viewer(_OTHER, profiles={"profile-b"})
_OTHER_UNRESTRICTED = _viewer(_OTHER)
_SYSTEM_ADMIN = _viewer(_OTHER, admin=True)


def _record(
    job_id: str,
    *,
    profile: str | None = "profile-a",
    compare: str | None = None,
    user: str = _OWNER,
    created_at: datetime | None = None,
) -> EvaluationJobRecord:
    return EvaluationJobRecord(
        job_id=job_id,
        kind="run",
        status="RUNNING",
        total_cases=4 if compare else 2,
        time_limit_seconds=3600,
        created_at=created_at or datetime.now(UTC),
        tenant_id_hash=_TENANT,
        user_id_hash=user,
        lease_owner=_LEASE,
        search_answer_profile_id=profile,
        compare_search_answer_profile_id=compare,
        user_uuid="owner-uuid" if profile else None,
    )


@pytest.mark.parametrize(
    ("viewer", "visible", "can_cancel"),
    [
        (_OWNER_WITH_PROFILE, True, True),
        (_OWNER_WITHOUT_PROFILE, False, False),
        (_OTHER_WITH_PROFILE, True, False),
        (_OTHER_WITHOUT_PROFILE, False, False),
        (_OTHER_UNRESTRICTED, True, False),
        (_SYSTEM_ADMIN, True, True),
    ],
    ids=[
        "owner",
        "owner-lost-profile",
        "other-with-profile",
        "other-without-profile",
        "other-unrestricted",
        "system-admin",
    ],
)
def test_job_with_known_profile_is_visible_to_profile_users(
    viewer: EvaluationJobViewer, visible: bool, can_cancel: bool
) -> None:
    record = _record("job-known")
    assert viewer.can_view(record) is visible
    assert viewer.can_cancel(record) is can_cancel


@pytest.mark.parametrize(
    ("viewer", "visible"),
    [
        (_OWNER_WITH_PROFILE, True),
        (_OTHER_WITH_PROFILE, False),
        (_OTHER_UNRESTRICTED, False),
        (_SYSTEM_ADMIN, True),
    ],
    ids=["owner", "other-with-profile", "other-unrestricted", "system-admin"],
)
def test_job_without_known_profile_is_visible_to_owner_and_admin(
    viewer: EvaluationJobViewer, visible: bool
) -> None:
    """プロファイルの分からない古い行は、今の規則（本人と system_admin）だけ。"""
    record = _record("job-legacy", profile=None)
    assert viewer.can_view(record) is visible
    assert viewer.can_cancel(record) is visible


def test_compared_profile_must_also_be_usable() -> None:
    record = _record("job-compare", compare="profile-b")
    assert _OTHER_WITH_PROFILE.can_view(record)
    assert not _viewer(_OTHER, profiles={"profile-a"}).can_view(record)


def test_other_tenant_never_sees_the_job() -> None:
    record = replace(_record("job-tenant"), tenant_id_hash="y" * 64)
    assert not _SYSTEM_ADMIN.can_view(record)


# ---- service（memory の保存先） ----


@pytest.fixture
def store() -> MemoryEvaluationJobStore:
    return MemoryEvaluationJobStore()


@pytest.fixture
def service(store: MemoryEvaluationJobStore) -> Iterator[EvaluationJobService]:
    installed = set_evaluation_job_service(
        EvaluationJobService(store, heartbeat_seconds=0.01, worker_id=_LEASE)
    )
    yield installed
    set_evaluation_job_service(None)


def _as_viewer(monkeypatch: pytest.MonkeyPatch, viewer: EvaluationJobViewer) -> None:
    monkeypatch.setattr(evaluation_jobs, "current_evaluation_job_viewer", lambda: viewer)


async def _seed(
    store: MemoryEvaluationJobStore, record: EvaluationJobRecord, *, finished: bool = True
) -> None:
    await store.create(record)
    # memory の保存先は作成の時刻を今にする。並びを確かめるため、渡した時刻に戻す。
    store._jobs[record.job_id].created_at = record.created_at  # noqa: SLF001
    if finished:
        await store.finish(
            record.job_id,
            lease_owner=_LEASE,
            status="SUCCEEDED",
            result={"passed": True, "case_count": 2},
            error_message=None,
            evaluation_run_id=None,
        )


async def test_list_shows_only_visible_jobs_newest_first(
    service: EvaluationJobService,
    store: MemoryEvaluationJobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    await _seed(store, _record("job-old", created_at=now - timedelta(hours=2)))
    await _seed(store, _record("job-new", created_at=now - timedelta(hours=1)))
    await _seed(store, _record("job-legacy", profile=None, created_at=now))
    await _seed(store, _record("job-hidden", profile="profile-z", created_at=now))

    _as_viewer(monkeypatch, _OTHER_WITH_PROFILE)
    items, total = await service.list_jobs(offset=0, limit=25)
    assert total == 2
    assert [item.job_id for item in items] == ["job-new", "job-old"]
    assert items[0].passed is True
    assert items[0].case_count == 2
    assert (items[0].owned, items[0].can_cancel) == (False, False)

    items, total = await service.list_jobs(offset=1, limit=1)
    assert (total, [item.job_id for item in items]) == (2, ["job-old"])

    _as_viewer(monkeypatch, _OWNER_WITH_PROFILE)
    items, _ = await service.list_jobs(offset=0, limit=25)
    assert [item.job_id for item in items] == ["job-legacy", "job-new", "job-old"]
    assert all(item.owned and item.can_cancel for item in items)

    _as_viewer(monkeypatch, _SYSTEM_ADMIN)
    _, total = await service.list_jobs(offset=0, limit=25)
    assert total == 4


async def test_get_hides_jobs_the_viewer_cannot_see(
    service: EvaluationJobService,
    store: MemoryEvaluationJobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(store, _record("job-known"))
    await _seed(store, _record("job-legacy", profile=None))

    _as_viewer(monkeypatch, _OTHER_WITH_PROFILE)
    job = await service.get("job-known")
    assert job.search_answer_profile_id == "profile-a"
    assert (job.owned, job.can_cancel) == (False, False)
    for job_id in ("job-legacy", "job-missing"):
        with pytest.raises(EvaluationJobNotFoundError):
            await service.get(job_id)

    _as_viewer(monkeypatch, _OTHER_WITHOUT_PROFILE)
    with pytest.raises(EvaluationJobNotFoundError):
        await service.get("job-known")

    _as_viewer(monkeypatch, _SYSTEM_ADMIN)
    assert (await service.get("job-legacy")).can_cancel


async def test_cancel_is_only_for_the_owner_and_system_admin(
    service: EvaluationJobService,
    store: MemoryEvaluationJobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(store, _record("job-a"), finished=False)
    await _seed(store, _record("job-b"), finished=False)

    _as_viewer(monkeypatch, _OTHER_WITHOUT_PROFILE)
    with pytest.raises(EvaluationJobNotFoundError):
        await service.cancel("job-a")
    _as_viewer(monkeypatch, _OTHER_WITH_PROFILE)
    with pytest.raises(EvaluationJobForbiddenError):
        await service.cancel("job-a")
    assert store._jobs["job-a"].status == "RUNNING"  # noqa: SLF001

    _as_viewer(monkeypatch, _OWNER_WITH_PROFILE)
    assert (await service.cancel("job-a")).status == "CANCELLED"
    _as_viewer(monkeypatch, _SYSTEM_ADMIN)
    assert (await service.cancel("job-b")).status == "CANCELLED"


async def test_submit_records_profiles_and_user(
    service: EvaluationJobService, store: MemoryEvaluationJobStore
) -> None:
    token = set_audit_request_context(
        AuditRequestContext(tenant_id_hash=_TENANT, user_id_hash=_OWNER, user_uuid="owner-uuid")
    )

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        return EvaluationJobOutcome(result={"case_count": 0})

    try:
        job = await service.submit(
            kind="run",
            total_cases=2,
            time_limit_seconds=60,
            executor=executor,
            search_answer_profile_id="profile-a",
            compare_search_answer_profile_id="profile-b",
        )
        await service.wait_for(job.job_id)
    finally:
        reset_audit_request_context(token)
    record = store._jobs[job.job_id]  # noqa: SLF001
    assert record.search_answer_profile_id == "profile-a"
    assert record.compare_search_answer_profile_id == "profile-b"
    assert record.user_uuid == "owner-uuid"


# ---- API ----


@pytest.fixture
async def api_client(service: EvaluationJobService) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=TEST_REQUEST_HEADERS
    ) as client:
        yield client


@pytest.fixture
def names(monkeypatch: pytest.MonkeyPatch) -> None:
    async def profile_names(ids: Any) -> dict[str, str]:
        return {"profile-a": "プロファイル A"}

    async def user_names(ids: Any) -> dict[str, str]:
        return {"owner-uuid": "利用者 A"}

    monkeypatch.setattr("app.api.routes.evaluation.evaluation_profile_names", profile_names)
    monkeypatch.setattr("app.api.routes.evaluation.evaluation_user_names", user_names)


async def test_list_api_returns_page_with_names(
    api_client: httpx.AsyncClient,
    store: MemoryEvaluationJobStore,
    names: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(store, _record("job-known"))
    await _seed(store, _record("job-legacy", profile=None))
    _as_viewer(monkeypatch, _OTHER_WITH_PROFILE)

    response = await api_client.get("/api/evaluation/jobs", params={"limit": 25, "offset": 0})
    assert response.status_code == 200
    page = response.json()["data"]
    assert (page["total"], page["limit"], page["offset"], page["has_next"]) == (1, 25, 0, False)
    [item] = page["items"]
    assert item["job_id"] == "job-known"
    assert item["search_answer_profile_name"] == "プロファイル A"
    assert item["actor_display_name"] == "利用者 A"
    assert item["passed"] is True
    assert item["can_cancel"] is False
    # 利用者の UUID は表示名を引くためだけに使い、応答に出さない。
    assert "actor_user_uuid" not in item
    assert "result" not in str(item.keys())

    assert (await api_client.get("/api/evaluation/jobs", params={"limit": 101})).status_code == 422


async def test_detail_export_and_cancel_api_follow_visibility(
    api_client: httpx.AsyncClient,
    store: MemoryEvaluationJobStore,
    names: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(store, _record("job-known"), finished=False)
    _as_viewer(monkeypatch, _OTHER_WITHOUT_PROFILE)
    for response in (
        await api_client.get("/api/evaluation/jobs/job-known"),
        await api_client.post("/api/evaluation/jobs/job-known/results.xlsx", json={}),
        await api_client.post("/api/evaluation/jobs/job-known/cancel"),
    ):
        assert response.status_code == 404
        assert "profile-a" not in response.text

    _as_viewer(monkeypatch, _OTHER_WITH_PROFILE)
    detail = await api_client.get("/api/evaluation/jobs/job-known")
    assert detail.status_code == 200
    assert detail.json()["data"]["search_answer_profile_name"] == "プロファイル A"
    assert detail.json()["data"]["owned"] is False
    assert (await api_client.post("/api/evaluation/jobs/job-known/cancel")).status_code == 403

    _as_viewer(monkeypatch, _OWNER_WITH_PROFILE)
    cancelled = await api_client.post("/api/evaluation/jobs/job-known/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "CANCELLED"


def test_list_permission_is_registered() -> None:
    from app.security.permissions import MENU_EVALUATION, permission_for_route

    assert permission_for_route("GET", "/evaluation/jobs") == frozenset({MENU_EVALUATION})
