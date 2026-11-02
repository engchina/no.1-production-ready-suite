"""旧版の清掃の流れと設定の契約（#4168。DB は fake。SQL は実 Oracle のテストで確かめる）。"""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from app.clients.chunk_set_retention_store import (
    ExpiredRecipeVersions,
    _unreferenced_paths,
)
from app.clients.document_cleanup_store import DocumentCleanupTargets, DocumentCleanupTask
from app.config import Settings
from app.rag import chunk_set_retention


class _Store:
    def __init__(self, results: dict[str, ExpiredRecipeVersions | Exception]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []
        self.due: dict[str, int] = {}

    async def due_recipe_ids(
        self, *, retention_days: int, retained_versions: int, limit: int
    ) -> list[str]:
        self.due = {
            "retention_days": retention_days,
            "retained_versions": retained_versions,
            "limit": limit,
        }
        return list(self.results)

    async def expire_recipe(self, recipe_id: str, **kwargs: Any) -> ExpiredRecipeVersions:
        self.calls.append({"recipe_id": recipe_id, **kwargs})
        result = self.results[recipe_id]
        if isinstance(result, Exception):
            raise result
        return result


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_retention_defaults_and_bounds() -> None:
    settings = _settings()
    assert settings.rag_chunk_set_retention_days == 7
    assert settings.rag_chunk_set_retained_versions == 1
    with pytest.raises(ValueError):
        _settings(rag_chunk_set_retention_days=-1)
    with pytest.raises(ValueError):
        _settings(rag_chunk_set_retained_versions=-1)


async def test_sweep_passes_settings_and_counts_expired_versions() -> None:
    store = _Store(
        {
            "recipe-a": ExpiredRecipeVersions("recipe-a", chunk_set_ids=("cs-1", "cs-2")),
            "recipe-b": ExpiredRecipeVersions("recipe-b", skipped=True),
        }
    )
    settings = _settings(
        rag_chunk_set_retention_days=3,
        rag_chunk_set_retained_versions=2,
        rag_canonical_artifact_prefix="artifacts/canonical",
    )

    expired = await chunk_set_retention.expire_retired_chunk_sets(
        settings=settings,
        store=store,  # type: ignore[arg-type]
        limit=5,
    )

    assert expired == 2
    assert store.due == {"retention_days": 3, "retained_versions": 2, "limit": 5}
    assert store.calls[0] == {
        "recipe_id": "recipe-a",
        "retention_days": 3,
        "retained_versions": 2,
        "canonical_prefix": "artifacts/canonical",
    }


async def test_sweep_continues_after_one_recipe_fails() -> None:
    store = _Store(
        {
            "recipe-a": RuntimeError("synthetic failure"),
            "recipe-b": ExpiredRecipeVersions("recipe-b", chunk_set_ids=("cs-1",)),
        }
    )

    expired = await chunk_set_retention.expire_retired_chunk_sets(
        settings=_settings(),
        store=store,  # type: ignore[arg-type]
    )

    assert expired == 1
    assert [call["recipe_id"] for call in store.calls] == ["recipe-a", "recipe-b"]


async def test_sweep_runs_recorded_file_cleanup_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = DocumentCleanupTask(
        cleanup_id="cleanup-1",
        document_id="doc-1",
        file_name="synthetic.pdf",
        targets=DocumentCleanupTargets(artifact_uris=("artifacts/canonical/doc-1/t/c.pdf",)),
    )
    store = _Store(
        {"recipe-a": ExpiredRecipeVersions("recipe-a", chunk_set_ids=("cs-1",), cleanup=task)}
    )
    seen: list[DocumentCleanupTask] = []

    async def fake_cleanup(
        _store: object, _storage: object, cleanup: DocumentCleanupTask
    ) -> object:
        seen.append(cleanup)
        return SimpleNamespace()

    monkeypatch.setattr(chunk_set_retention, "run_document_cleanup", fake_cleanup)

    await chunk_set_retention.expire_retired_chunk_sets(
        settings=_settings(),
        store=store,  # type: ignore[arg-type]
        cleanup_store=object(),  # type: ignore[arg-type]
        storage=object(),  # type: ignore[arg-type]
    )

    assert seen == [task]


def test_unreferenced_paths_only_considers_the_document_canonical_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_fetch_one(
        _connection: object, _statement: str, binds: dict[str, object]
    ) -> dict[str, object] | None:
        calls.append(str(binds["path"]))
        return (
            {"referenced": 1} if binds["path"] == "oci://b/n/artifacts/canonical/d/in-use" else None
        )

    monkeypatch.setattr("app.clients.chunk_set_retention_store._fetch_one", fake_fetch_one)
    paths: Sequence[str] = (
        "oci://b/n/artifacts/canonical/d/orphan",
        "oci://b/n/artifacts/canonical/d/in-use",
        "oci://b/n/artifacts/canonical/other-document/x",
        "oci://b/n/uploads/d/original",
    )

    orphans = _unreferenced_paths(
        object(),  # type: ignore[arg-type]
        "d",
        paths,
        prefix="artifacts/canonical/d/",
    )

    assert orphans == ["oci://b/n/artifacts/canonical/d/orphan"]
    assert calls == [
        "oci://b/n/artifacts/canonical/d/orphan",
        "oci://b/n/artifacts/canonical/d/in-use",
    ]
