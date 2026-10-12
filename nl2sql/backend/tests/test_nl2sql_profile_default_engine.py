"""業務プロファイルの既定の生成方式（`default_engine`。#1524）の回帰テスト。

保存・更新・利用の文脈（usage-context）・この環境で使える方式・ジョブの作成での既定の解決を
確かめる。LLM と Oracle は呼ばない（Enterprise AI は fake、保存は memory）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request, Response
from test_nl2sql_job_runtime import _PROFILE_ID, _repository, _worker

from app.features.nl2sql import router as nl2sql_router
from app.features.nl2sql.models import (
    JobCreateRequest,
    Nl2SqlEngine,
    Nl2SqlProfile,
    ProfilePatchRequest,
    ProfileUpsertRequest,
    SelectAiRequestOverrides,
)
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.security.permissions import (
    PROFILE_MANAGE_PERMISSION,
    QUERY_GENERATE_PERMISSION,
    permission_for_route,
)
from app.settings import get_settings


class _ConfiguredEnterpriseAi:
    def __init__(self, configured: bool) -> None:
        self.configured = configured

    def is_configured(self) -> bool:
        return self.configured


def _anon_request() -> Request:
    return cast(Request, SimpleNamespace(state=SimpleNamespace(principal=None)))


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Nl2SqlService:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._enterprise_ai_client = _ConfiguredEnterpriseAi(True)  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_select_ai_enabled", True)
    monkeypatch.setattr(settings, "nl2sql_select_ai_agent_enabled", True)
    monkeypatch.setattr(settings, "nl2sql_enterprise_ai_direct_enabled", True)
    return service


def test_profile_default_engine_defaults_to_select_ai(service: Nl2SqlService) -> None:
    created = nl2sql_router.create_profile(
        ProfileUpsertRequest(name="sales_profile"), Response()
    ).data
    assert created is not None
    assert created.default_engine == Nl2SqlEngine.SELECT_AI


def test_profile_default_engine_is_saved_updated_and_shown(service: Nl2SqlService) -> None:
    created = nl2sql_router.create_profile(
        ProfileUpsertRequest(name="sales_profile", default_engine=Nl2SqlEngine.SELECT_AI_AGENT),
        Response(),
    ).data
    assert created is not None
    assert created.default_engine == Nl2SqlEngine.SELECT_AI_AGENT
    assert service.get_profile(created.id).default_engine == Nl2SqlEngine.SELECT_AI_AGENT

    updated = nl2sql_router.update_profile(
        created.id,
        ProfilePatchRequest(default_engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT),
        _anon_request(),
        Response(),
    ).data
    assert updated is not None
    assert updated.default_engine == Nl2SqlEngine.ENTERPRISE_AI_DIRECT

    # 省略した PATCH は既定の生成方式を変えない。
    renamed = nl2sql_router.update_profile(
        created.id,
        ProfilePatchRequest(category="営業"),
        _anon_request(),
        Response(),
    ).data
    assert renamed is not None
    assert renamed.default_engine == Nl2SqlEngine.ENTERPRISE_AI_DIRECT

    # AI 活用の画面（SQL 生成・チャット）の初期の選択に使う。
    context = nl2sql_router.get_profile_usage_context(created.id, _anon_request(), Response())
    assert not isinstance(context, Response)
    assert context.data is not None
    assert context.data.default_engine == Nl2SqlEngine.ENTERPRISE_AI_DIRECT


def test_incremental_repository_round_trips_default_engine() -> None:
    repository = _repository()
    stored = repository.get_profile(_PROFILE_ID)
    assert stored is not None
    saved = repository.save_profile(
        stored.model_copy(update={"default_engine": Nl2SqlEngine.ENTERPRISE_AI_DIRECT}),
        expected_etag=stored.etag,
    )
    reloaded = repository.get_profile(_PROFILE_ID)
    assert reloaded is not None
    assert saved.default_engine == reloaded.default_engine == Nl2SqlEngine.ENTERPRISE_AI_DIRECT


def test_engines_follow_environment_settings(
    monkeypatch: pytest.MonkeyPatch, service: Nl2SqlService
) -> None:
    def availability() -> dict[str, bool]:
        data = nl2sql_router.list_engines().data
        assert data is not None
        return {item.engine.value: item.available for item in data.engines}

    assert availability() == {
        "select_ai": True,
        "select_ai_agent": True,
        "enterprise_ai_direct": True,
    }
    monkeypatch.setattr(get_settings(), "nl2sql_select_ai_agent_enabled", False)
    service._enterprise_ai_client = _ConfiguredEnterpriseAi(False)  # type: ignore[assignment]  # noqa: SLF001
    assert availability() == {
        "select_ai": True,
        "select_ai_agent": False,
        "enterprise_ai_direct": False,
    }
    # 業務プロファイルの管理と SQL の生成の画面が読む。
    assert permission_for_route("GET", "/nl2sql/engines") == frozenset(
        {PROFILE_MANAGE_PERMISSION, QUERY_GENERATE_PERMISSION}
    )


def test_unavailable_engine_is_rejected_only_when_it_changes(
    monkeypatch: pytest.MonkeyPatch, service: Nl2SqlService
) -> None:
    created = nl2sql_router.create_profile(
        ProfileUpsertRequest(
            name="sales_profile", default_engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT
        ),
        Response(),
    ).data
    assert created is not None

    # OCI Enterprise AI の設定を外した後も、ほかの項目の保存は止めない。
    service._enterprise_ai_client = _ConfiguredEnterpriseAi(False)  # type: ignore[assignment]  # noqa: SLF001
    kept = nl2sql_router.update_profile(
        created.id,
        ProfilePatchRequest(category="営業", default_engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT),
        _anon_request(),
        Response(),
    ).data
    assert kept is not None
    assert kept.category == "営業"

    monkeypatch.setattr(get_settings(), "nl2sql_select_ai_agent_enabled", False)
    with pytest.raises(HTTPException) as patched:
        nl2sql_router.update_profile(
            created.id,
            ProfilePatchRequest(default_engine=Nl2SqlEngine.SELECT_AI_AGENT),
            _anon_request(),
            Response(),
        )
    assert patched.value.status_code == 422
    assert "選べない生成の方式" in str(patched.value.detail)
    with pytest.raises(HTTPException) as posted:
        nl2sql_router.create_profile(
            ProfileUpsertRequest(
                name="hr_profile", default_engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT
            ),
            Response(),
        )
    assert posted.value.status_code == 422
    # Select AI は常に選べる。
    switched = nl2sql_router.update_profile(
        created.id,
        ProfilePatchRequest(default_engine=Nl2SqlEngine.SELECT_AI),
        _anon_request(),
        Response(),
    ).data
    assert switched is not None
    assert switched.default_engine == Nl2SqlEngine.SELECT_AI


@pytest.fixture
def job_service(monkeypatch: pytest.MonkeyPatch) -> Nl2SqlService:
    # in-process の worker を起こさない（投入された request だけを確かめる）。
    monkeypatch.setattr(get_settings(), "nl2sql_job_worker_mode", "external")
    repository = _repository()
    stored = repository.get_profile(_PROFILE_ID)
    assert stored is not None
    repository.save_profile(
        stored.model_copy(update={"default_engine": Nl2SqlEngine.SELECT_AI_AGENT}),
        expected_etag=stored.etag,
    )
    return _worker(repository)


def _stored_engine(service: Nl2SqlService, job_id: str) -> Nl2SqlEngine:
    job = service._load_job_record(job_id)  # noqa: SLF001
    assert job is not None
    return job.request.engine


def test_job_without_engine_uses_profile_default(job_service: Nl2SqlService) -> None:
    created = job_service.start_job(
        JobCreateRequest(question="注文一覧を確認したい", profile_id=_PROFILE_ID),
        actor_user_uuid="user-1",
    )
    assert _stored_engine(job_service, created.job_id) == Nl2SqlEngine.SELECT_AI_AGENT


def test_job_with_explicit_engine_keeps_it(job_service: Nl2SqlService) -> None:
    for engine in Nl2SqlEngine:
        created = job_service.start_job(
            JobCreateRequest(
                question="注文一覧を確認したい", profile_id=_PROFILE_ID, engine=engine
            ),
            actor_user_uuid="user-1",
        )
        assert _stored_engine(job_service, created.job_id) == engine


def test_job_without_engine_rejects_select_ai_overrides_for_other_default(
    job_service: Nl2SqlService,
) -> None:
    request = JobCreateRequest(
        question="注文一覧を確認したい",
        profile_id=_PROFILE_ID,
        select_ai_overrides=SelectAiRequestOverrides(role="分析者"),
    )
    with pytest.raises(ValueError, match="engine=select_ai"):
        job_service.start_job(request, actor_user_uuid="user-1")


def test_profile_default_engine_resolution_keeps_explicit_engine() -> None:
    omitted = JobCreateRequest(question="売上")
    assert "engine" not in omitted.model_fields_set
    resolved = omitted.with_profile_default_engine(Nl2SqlEngine.ENTERPRISE_AI_DIRECT)
    assert resolved.engine == Nl2SqlEngine.ENTERPRISE_AI_DIRECT
    # 決めた方式は保存・再送の照合で明示の方式として扱う。
    assert resolved.with_profile_default_engine(Nl2SqlEngine.SELECT_AI) is resolved
    explicit = JobCreateRequest(question="売上", engine=Nl2SqlEngine.SELECT_AI)
    assert explicit.with_profile_default_engine(Nl2SqlEngine.SELECT_AI_AGENT) is explicit
    assert isinstance(Nl2SqlProfile(id="p", name="P").default_engine, Nl2SqlEngine)
