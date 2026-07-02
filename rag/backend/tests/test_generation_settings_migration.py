"""旧回答生成設定の Oracle import CLI。"""

from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.rag import generation_settings_migration as migration
from app.rag.prompt_versions import PromptVersion, PromptVersionStore


def _legacy_store() -> PromptVersionStore:
    version = PromptVersion(
        version_id="prompt-v1",
        name="監査版",
        system_prompt="根拠だけを使ってください。",
        created_at=datetime.now(UTC),
    )
    return PromptVersionStore(active_version_id=version.version_id, versions=[version])


def test_dry_run_reports_profile_prompt_count_and_active_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migration,
        "get_settings",
        lambda: Settings(rag_generation_profile="detailed_cited"),
    )
    monkeypatch.setattr(migration, "load_prompt_version_store", _legacy_store)

    plan = migration.legacy_import_plan()

    assert plan == {
        "mode": "dry-run",
        "profile": "detailed_cited",
        "prompt_version_count": 1,
        "active_prompt_version_id": "prompt-v1",
        "legacy_files_preserved": True,
    }


@pytest.mark.anyio
async def test_apply_uses_idempotent_oracle_import_without_writing_legacy_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeOracleClient:
        async def import_legacy_generation_settings(self, **kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {
                "settings_created": True,
                "profile": "grounded_concise",
                "active_prompt_version_id": "prompt-v1",
                "prompt_version_count": 1,
                "legacy_prompt_count": 1,
            }

    monkeypatch.setattr(migration, "get_settings", lambda: Settings())
    monkeypatch.setattr(migration, "load_prompt_version_store", _legacy_store)
    monkeypatch.setattr(migration, "OracleClient", FakeOracleClient)

    first = await migration.apply_legacy_import()
    second = await migration.apply_legacy_import()

    assert first == second
    assert first["mode"] == "apply"
    assert first["legacy_files_preserved"] is True
    assert len(calls) == 2
    assert calls[0]["profile"] == "grounded_concise"
    assert calls[0]["active_version_id"] == "prompt-v1"
