from __future__ import annotations

import pytest
from fastapi import HTTPException, Response

from app.features.nl2sql import ontology_router, profile_sync
from app.features.nl2sql import router as nl2sql_router
from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import ProfileUpsertRequest
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore


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
        ProfileUpsertRequest(name="請求分析", allowed_tables=["APP.INVOICES"]),
        Response(),
    ).data
    assert created is not None
    updated = nl2sql_router.update_profile(
        created.id,
        ProfileUpsertRequest(name="請求分析 v2", allowed_tables=["APP.INVOICES"]),
        Response(),
    ).data
    assert updated is not None
    assert updated.name == "請求分析 v2"
    archived = nl2sql_router.archive_profile(created.id).data
    assert archived is not None
    assert archived.archived is True
    restored = nl2sql_router.restore_profile(created.id).data
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

    deleted = nl2sql_router.delete_profile("default").data

    assert deleted is not None
    assert deleted.id == "default"
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
        nl2sql_router.delete_profile("default")
    assert missing_precondition.value.status_code == 428

    with pytest.raises(HTTPException) as conflict:
        nl2sql_router.delete_profile("default", if_match='"stale"')
    assert conflict.value.status_code == 409
    assert conflict.value.headers is not None
    current_etag = conflict.value.headers["ETag"]
    assert current_etag

    deleted = nl2sql_router.delete_profile(
        "default",
        if_match=current_etag,
    ).data
    assert deleted is not None
    assert deleted.id == "default"

    with pytest.raises(HTTPException) as missing:
        nl2sql_router.delete_profile("default", if_match=current_etag)
    assert missing.value.status_code == 404
