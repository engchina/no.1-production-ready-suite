from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException, Request, Response
from pydantic import ValidationError

from app.features.nl2sql import ontology_router, profile_sync
from app.features.nl2sql import router as nl2sql_router
from app.features.nl2sql.incremental_store import (
    IncrementalVersionConflict,
    MemoryIncrementalNl2SqlRepository,
)
from app.features.nl2sql.models import (
    Nl2SqlEngine,
    Nl2SqlProfile,
    ProfilePatchRequest,
    ProfileUpsertRequest,
)
from app.features.nl2sql.oracle_adapter import OracleAdapterError
from app.features.nl2sql.service import (
    Nl2SqlService,
    ProfileNameConflict,
    ProfileOracleCleanupFailed,
)
from app.features.nl2sql.store import MemoryNl2SqlStore


def _anon_request() -> Request:
    """認証無効(principal なし)相当の request。router の RBAC 引数へ渡す。"""
    return cast(Request, SimpleNamespace(state=SimpleNamespace(principal=None)))


class _FakeOracleCleanupAdapter:
    def __init__(self, *, fail_agent: bool = False) -> None:
        self.fail_agent = fail_agent
        self.calls: list[tuple[str, dict[str, str]]] = []

    def drop_select_ai_agent_assets(self, **kwargs: str) -> dict[str, str]:
        self.calls.append(("agent", kwargs))
        if self.fail_agent:
            raise OracleAdapterError("agent cleanup failed")
        return {"runtime": "oracle", "package": "DBMS_CLOUD_AI_AGENT"}

    def drop_select_ai_profile(self, **kwargs: str) -> dict[str, str]:
        self.calls.append(("profile", kwargs))
        return {"runtime": "oracle", "package": "DBMS_CLOUD_AI"}


def test_profile_upsert_request_uses_name_for_select_ai_profile_name() -> None:
    request = ProfileUpsertRequest(
        name="sales_profile",
        select_ai_config={"profile_name": "LEGACY_PROFILE"},
    )

    assert request.name == "SALES_PROFILE"
    assert request.select_ai_config.profile_name == "SALES_PROFILE"


def test_profile_upsert_request_rejects_invalid_name() -> None:
    with pytest.raises(ValidationError):
        ProfileUpsertRequest(name="1PROFILE")

    with pytest.raises(ValidationError):
        ProfileUpsertRequest(name="新プロファイル")


def test_profile_name_uniqueness_rejects_case_variants_and_archived_profiles() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    created = service.create_profile(Nl2SqlProfile(id="profile-a", name="sales_profile"))

    with pytest.raises(ProfileNameConflict):
        service.create_profile(Nl2SqlProfile(id="profile-b", name="SALES_PROFILE"))

    service.archive_profile(created.id)
    with pytest.raises(ProfileNameConflict):
        service.create_profile(Nl2SqlProfile(id="profile-c", name="sales_profile"))

    other = service.create_profile(Nl2SqlProfile(id="profile-d", name="orders_profile"))
    with pytest.raises(ProfileNameConflict):
        service.update_profile(
            other.id,
            lambda profile: profile.model_copy(update={"name": "Sales_Profile"}),
        )


def test_profile_name_conflict_returns_422_field_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)

    created = nl2sql_router.create_profile(
        ProfileUpsertRequest(name="sales_profile"),
        Response(),
    ).data
    assert created is not None
    archived = nl2sql_router.archive_profile(created.id, _anon_request()).data
    assert archived is not None
    assert archived.archived is True

    with pytest.raises(HTTPException) as exc_info:
        nl2sql_router.create_profile(ProfileUpsertRequest(name="SALES_PROFILE"), Response())

    assert exc_info.value.status_code == 422
    detail = cast(dict[str, Any], exc_info.value.detail)
    assert detail == {
        "code": "NL2SQL_PROFILE_NAME_CONFLICT",
        "message_ja": "業務 profile 名「SALES_PROFILE」は既に使用されています。",
        "field_errors": [
            {
                "pointer": "/name",
                "code": "profile_name_conflict",
                "message": "業務 profile 名「SALES_PROFILE」は既に使用されています。",
            }
        ],
    }


def test_profile_requests_reject_blank_glossary_keys_and_control_characters() -> None:
    with pytest.raises(ValidationError, match="用語キー"):
        ProfileUpsertRequest(name="sales_profile", glossary={"  ": "INVOICES.TOTAL_AMOUNT"})

    with pytest.raises(ValidationError, match="制御文字"):
        ProfilePatchRequest(glossary={"売上": "INVOICES.\x01TOTAL_AMOUNT"})

    request = ProfilePatchRequest(glossary={" 売上 ": " INVOICES.TOTAL_AMOUNT "})

    assert request.glossary == {"売上": "INVOICES.TOTAL_AMOUNT"}


def test_learning_material_upload_rejects_large_file_before_read() -> None:
    class TooLargeUpload:
        filename = "terms.xlsx"
        size = nl2sql_router.LEARNING_MATERIAL_UPLOAD_MAX_BYTES + 1

        async def read(self, _size: int = -1) -> bytes:
            raise AssertionError("oversized learning material must be rejected before read")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(nl2sql_router.import_legacy_terms(cast(Any, TooLargeUpload())))

    assert exc_info.value.status_code == 413


def test_profile_patch_ignores_separate_select_ai_profile_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)

    created = nl2sql_router.create_profile(
        ProfileUpsertRequest(name="sales_profile"),
        Response(),
    ).data
    assert created is not None

    updated = nl2sql_router.update_profile(
        created.id,
        ProfilePatchRequest(
            select_ai_config={"profile_name": "OTHER_PROFILE", "region": "ap-osaka-1"}
        ),
        _anon_request(),
        Response(),
    ).data

    assert updated is not None
    assert updated.name == "SALES_PROFILE"
    assert updated.select_ai_config.profile_name == "SALES_PROFILE"
    assert updated.select_ai_config.region == "ap-osaka-1"


def test_profile_create_update_and_restore_never_materialize_ontology_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)
    monkeypatch.setattr(
        ontology_router.ontology_runtime,
        "materialize_profile_view",
        lambda _profile_id: pytest.fail("profile mutation must not materialize view"),
    )

    created = nl2sql_router.create_profile(
        ProfileUpsertRequest(name="invoice_profile", allowed_tables=["APP.INVOICES"]),
        Response(),
    ).data
    assert created is not None
    assert created.name == "INVOICE_PROFILE"
    assert created.select_ai_config.profile_name == "INVOICE_PROFILE"
    updated = nl2sql_router.update_profile(
        created.id,
        ProfileUpsertRequest(name="invoice_profile_v2", allowed_tables=["APP.INVOICES"]),  # type: ignore[arg-type]
        _anon_request(),
        Response(),
    ).data
    assert updated is not None
    assert updated.name == "INVOICE_PROFILE_V2"
    assert updated.select_ai_config.profile_name == "INVOICE_PROFILE_V2"
    assert updated.select_ai_config.previous_profile_name == "INVOICE_PROFILE"
    archived = nl2sql_router.archive_profile(created.id, _anon_request()).data
    assert archived is not None
    assert archived.archived is True
    restored = nl2sql_router.restore_profile(created.id, _anon_request()).data
    assert restored is not None
    assert restored.archived is False


def test_default_profile_delete_uses_shared_physical_delete_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)
    cleanup_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ontology_router.ontology_build_service,
        "cancel_profile_jobs",
        lambda profile_id: cleanup_calls.append(("build", profile_id)),
    )
    monkeypatch.setattr(
        profile_sync.profile_sync_service,
        "cancel_for_profile",
        lambda profile_id: cleanup_calls.append(("sync", profile_id)),
    )
    monkeypatch.setattr(
        ontology_router.ontology_runtime,
        "delete_profile_state",
        lambda profile_id: cleanup_calls.append(("runtime", profile_id)),
    )

    deleted = nl2sql_router.delete_profile("default", _anon_request()).data

    assert deleted is not None
    assert deleted.profile.id == "default"
    assert [item.engine for item in deleted.oracle_cleanup] == [
        Nl2SqlEngine.SELECT_AI_AGENT,
        Nl2SqlEngine.SELECT_AI,
    ]
    assert {item.status for item in deleted.oracle_cleanup} == {"skipped"}
    assert service.list_profiles(include_archived=True) == []
    assert cleanup_calls == [
        ("build", "default"),
        ("sync", "default"),
        ("runtime", "default"),
    ]


def test_default_profile_delete_honors_incremental_preconditions_and_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MemoryIncrementalNl2SqlRepository()
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._incremental_repository = repository  # noqa: SLF001
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)
    monkeypatch.setattr(
        ontology_router.ontology_build_service,
        "cancel_profile_jobs",
        lambda _profile_id: 0,
    )
    monkeypatch.setattr(
        profile_sync.profile_sync_service,
        "cancel_for_profile",
        lambda _profile_id: 0,
    )
    monkeypatch.setattr(
        ontology_router.ontology_runtime,
        "delete_profile_state",
        lambda _profile_id: 0,
    )
    service.get_profile("default")

    with pytest.raises(HTTPException) as missing_precondition:
        nl2sql_router.delete_profile("default", _anon_request())
    assert missing_precondition.value.status_code == 428

    with pytest.raises(HTTPException) as conflict:
        nl2sql_router.delete_profile("default", _anon_request(), if_match='"stale"')
    assert conflict.value.status_code == 409
    assert conflict.value.headers is not None
    current_etag = conflict.value.headers["ETag"]
    assert current_etag

    deleted = nl2sql_router.delete_profile(
        "default",
        _anon_request(),
        if_match=current_etag,
    ).data
    assert deleted is not None
    assert deleted.profile.id == "default"

    with pytest.raises(HTTPException) as missing:
        nl2sql_router.delete_profile("default", _anon_request(), if_match=current_etag)
    assert missing.value.status_code == 404


def test_profile_delete_executes_oracle_agent_then_profile_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    adapter = _FakeOracleCleanupAdapter()
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service, "_oracle_adapter", adapter)

    deleted = service.delete_profile_with_oracle_cleanup("default")

    assert deleted.profile.id == "default"
    assert [item.engine for item in deleted.oracle_cleanup] == [
        Nl2SqlEngine.SELECT_AI_AGENT,
        Nl2SqlEngine.SELECT_AI,
    ]
    assert [call[0] for call in adapter.calls] == ["agent", "profile"]
    assert adapter.calls[0][1]["profile_name"] == "NL2SQL_DEFAULT_PROFILE"
    assert adapter.calls[0][1]["team_name"] == "NL2SQL_DEFAULT_TEAM"
    assert adapter.calls[1][1]["profile_name"] == "NL2SQL_DEFAULT_PROFILE"
    assert service.list_profiles(include_archived=True) == []


def test_profile_delete_stale_etag_does_not_cleanup_oracle_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MemoryIncrementalNl2SqlRepository()
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._incremental_repository = repository  # noqa: SLF001
    adapter = _FakeOracleCleanupAdapter()
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service, "_oracle_adapter", adapter)

    with pytest.raises(IncrementalVersionConflict):
        service.delete_profile_with_oracle_cleanup("default", expected_etag="stale")

    assert adapter.calls == []
    assert service.get_profile("default").id == "default"


def test_profile_delete_oracle_cleanup_failure_keeps_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    adapter = _FakeOracleCleanupAdapter(fail_agent=True)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service, "_oracle_adapter", adapter)

    with pytest.raises(ProfileOracleCleanupFailed):
        service.delete_profile_with_oracle_cleanup("default")

    assert [call[0] for call in adapter.calls] == ["agent", "profile"]
    assert service.get_profile("default").id == "default"


def test_profile_delete_oracle_cleanup_failure_returns_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    adapter = _FakeOracleCleanupAdapter(fail_agent=True)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service, "_oracle_adapter", adapter)
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)

    with pytest.raises(HTTPException) as exc:
        nl2sql_router.delete_profile("default", _anon_request())

    assert exc.value.status_code == 502
    assert "業務 profile は削除していません" in str(exc.value.detail)
    assert service.get_profile("default").id == "default"
