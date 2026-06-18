"""prompt version store(PoweRAG 由来の prompt 版管理)の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.main import app
from app.rag import prompt_versions
from app.rag.generation_adapter import resolve_generation_adapter
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """版管理ファイルをテストごとの一時ファイルへ隔離する。"""
    monkeypatch.setenv(
        prompt_versions.PROMPT_VERSIONS_FILE_ENV, str(tmp_path / "prompt-versions.json")
    )


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
    prompt_versions.create_prompt_version(
        name="v2", system_prompt="prompt two", activate=False
    )
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
    prompt_versions.create_prompt_version(name="v1", system_prompt="カスタム指示です。")
    params = resolve_generation_adapter(
        Settings.model_construct(rag_generation_profile="custom")
    )
    assert params.profile == "custom"
    assert params.system_prompt == "カスタム指示です。"


def test_custom_profile_falls_back_to_default_prompt_when_no_active_version() -> None:
    # 有効版が無ければ system_prompt は None(client 既定 prompt を使う)。
    params = resolve_generation_adapter(
        Settings.model_construct(rag_generation_profile="custom")
    )
    assert params.profile == "custom"
    assert params.system_prompt is None


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
