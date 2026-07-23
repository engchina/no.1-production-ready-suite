from __future__ import annotations

import pytest
from fastapi import HTTPException, Response

from app.features.nl2sql import ontology_router
from app.features.nl2sql import router as nl2sql_router
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


def test_default_profile_delete_returns_stable_conflict_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)

    with pytest.raises(HTTPException) as caught:
        nl2sql_router.delete_profile("default")

    assert caught.value.status_code == 409
    assert isinstance(caught.value.detail, dict)
    assert caught.value.detail["code"] == "DEFAULT_PROFILE_DELETE_FORBIDDEN"
