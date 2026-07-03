"""旧 Prompt file の rollback 互換と Oracle Prompt API の単体テスト。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.api.routes import settings as settings_route
from app.clients.oracle import (
    CustomPromptNotConfiguredError,
    StoredGenerationSettings,
    StoredPromptVersion,
)
from app.config import Settings
from app.main import app
from app.rag import prompt_versions
from app.rag.generation_adapter import resolve_generation_adapter
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧 file と Oracle API fake をテストごとに隔離する。"""
    monkeypatch.setenv(
        prompt_versions.PROMPT_VERSIONS_FILE_ENV, str(tmp_path / "prompt-versions.json")
    )
    FakePromptOracle.reset()
    monkeypatch.setattr(settings_route, "OracleClient", FakePromptOracle)


class FakePromptOracle:
    """Prompt route が使う Oracle transaction 境界の最小 fake。"""

    settings: StoredGenerationSettings
    versions: list[StoredPromptVersion]

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    @classmethod
    def reset(cls) -> None:
        cls.settings = StoredGenerationSettings(
            profile="grounded_concise",
            active_prompt_version_id=None,
            revision=1,
            updated_at=datetime(2026, 7, 3, tzinfo=UTC),
        )
        cls.versions = []

    async def list_prompt_versions(
        self,
    ) -> tuple[StoredGenerationSettings, list[StoredPromptVersion]]:
        return self.settings, list(self.versions)

    async def create_prompt_version(
        self,
        *,
        name: str,
        system_prompt: str,
        note: str = "",
        activate: bool = True,
    ) -> tuple[StoredGenerationSettings, list[StoredPromptVersion]]:
        version = StoredPromptVersion(
            version_id=f"prompt-{len(self.versions) + 1}",
            name=name,
            system_prompt=system_prompt,
            note=note,
            created_at=datetime.now(UTC),
        )
        self.versions.insert(0, version)
        self.__class__.settings = replace(
            self.settings,
            active_prompt_version_id=(
                version.version_id if activate else self.settings.active_prompt_version_id
            ),
            revision=self.settings.revision + 1,
            updated_at=version.created_at,
        )
        return self.settings, list(self.versions)

    async def activate_prompt_version(
        self, version_id: str
    ) -> tuple[StoredGenerationSettings, list[StoredPromptVersion]]:
        if all(version.version_id != version_id for version in self.versions):
            raise KeyError(version_id)
        now = datetime.now(UTC)
        self.__class__.settings = replace(
            self.settings,
            active_prompt_version_id=version_id,
            revision=self.settings.revision + 1,
            updated_at=now,
        )
        return self.settings, list(self.versions)


def test_create_first_version_is_auto_activated() -> None:
    version = prompt_versions.create_prompt_version(
        name="v1", system_prompt="あなたは厳密なアシスタントです。", activate=False
    )
    # 初版は activate=False でも有効版になる。
    active = prompt_versions.get_active_prompt_version()
    assert active is not None
    assert active.version_id == version.version_id
    assert prompt_versions.active_custom_system_prompt() == "あなたは厳密なアシスタントです。"


def test_create_without_activate_keeps_previous_active() -> None:
    first = prompt_versions.create_prompt_version(name="v1", system_prompt="prompt one")
    prompt_versions.create_prompt_version(name="v2", system_prompt="prompt two", activate=False)
    active = prompt_versions.get_active_prompt_version()
    assert active is not None
    assert active.version_id == first.version_id


def test_activate_rolls_back_to_older_version() -> None:
    first = prompt_versions.create_prompt_version(name="v1", system_prompt="prompt one")
    second = prompt_versions.create_prompt_version(name="v2", system_prompt="prompt two")
    assert prompt_versions.active_custom_system_prompt() == "prompt two"
    # rollback: 旧版を再有効化。
    prompt_versions.activate_prompt_version(first.version_id)
    assert prompt_versions.active_custom_system_prompt() == "prompt one"
    assert prompt_versions.get_active_prompt_version().version_id == first.version_id  # type: ignore[union-attr]
    _ = second


def test_activate_unknown_version_raises() -> None:
    prompt_versions.create_prompt_version(name="v1", system_prompt="prompt one")
    with pytest.raises(KeyError):
        prompt_versions.activate_prompt_version("does-not-exist")


def test_list_versions_is_newest_first_and_persists() -> None:
    prompt_versions.create_prompt_version(name="v1", system_prompt="one")
    prompt_versions.create_prompt_version(name="v2", system_prompt="two")
    store = prompt_versions.list_prompt_versions()
    assert [v.name for v in store.versions] == ["v2", "v1"]
    # 別呼び出しでも永続している。
    assert len(prompt_versions.load_prompt_version_store().versions) == 2


def test_empty_and_blank_prompt_rejected() -> None:
    with pytest.raises(ValueError, match="空にできません"):
        prompt_versions.create_prompt_version(name="v1", system_prompt="   ")


def test_no_active_when_store_empty() -> None:
    assert prompt_versions.get_active_prompt_version() is None
    assert prompt_versions.active_custom_system_prompt() is None


def test_custom_generation_profile_resolves_active_prompt() -> None:
    params = resolve_generation_adapter(
        Settings(
            rag_generation_profile="custom",
            rag_generation_custom_prompt="カスタム指示です。",
            rag_generation_custom_prompt_version_id="prompt-v1",
            rag_generation_service_enabled=False,
        )
    )
    assert params.profile == "custom"
    assert "必須の根拠・安全制約" in (params.system_prompt or "")
    assert "カスタム指示です。" in (params.system_prompt or "")


def test_custom_profile_rejects_when_no_active_version() -> None:
    with pytest.raises(CustomPromptNotConfiguredError):
        resolve_generation_adapter(
            Settings(
                rag_generation_profile="custom",
                rag_generation_service_enabled=False,
            )
        )


def test_prompts_api_create_list_and_rollback() -> None:
    create_1 = client.post(
        "/api/settings/prompts",
        json={"name": "厳格版", "system_prompt": "厳密に答えてください。"},
    )
    assert create_1.status_code == 200
    create_2 = client.post(
        "/api/settings/prompts",
        json={"name": "詳細版", "system_prompt": "詳細に答えてください。"},
    )
    assert create_2.status_code == 200

    listing = client.get("/api/settings/prompts").json()["data"]
    assert [v["name"] for v in listing["versions"]] == ["詳細版", "厳格版"]
    # 直近作成が有効版。
    active = next(v for v in listing["versions"] if v["active"])
    assert active["name"] == "詳細版"

    # rollback: 旧版を再有効化。
    old_id = next(v["version_id"] for v in listing["versions"] if v["name"] == "厳格版")
    rolled = client.post(f"/api/settings/prompts/{old_id}/activate").json()["data"]
    assert rolled["active_version_id"] == old_id


def test_prompts_api_activate_unknown_returns_404() -> None:
    resp = client.post("/api/settings/prompts/does-not-exist/activate")
    assert resp.status_code == 404


def test_prompts_api_rejects_blank_prompt() -> None:
    resp = client.post("/api/settings/prompts", json={"name": "x", "system_prompt": "   "})
    assert resp.status_code == 422
