from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import Field

from pr_system_settings import model as shared_model
from pr_system_settings.model import (
    ENTERPRISE_AI_API_KEY_ENV,
    EnterpriseAiConfiguredModel,
    ModelSecretStateMixin,
    ModelSettingsSection,
    ModelSettingsStore,
    ModelSettingsTestRequest,
    SectionSecret,
    build_model_router,
)


class FakeSettings(ModelSecretStateMixin):
    """製品の Settings のうちモデル設定 API が読む属性だけを持つ。"""

    model_settings_file: str = "model-settings.json"
    oci_enterprise_ai_endpoint: str = ""
    oci_enterprise_ai_project_ocid: str = ""
    oci_enterprise_ai_api_key: str = ""
    oci_enterprise_ai_models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list)
    oci_enterprise_ai_default_model: str = ""
    oci_enterprise_ai_llm_model: str = ""
    oci_enterprise_ai_vlm_model: str = ""
    oci_enterprise_ai_llm_path: str = "/responses"
    oci_enterprise_ai_vlm_path: str = "/responses"
    oci_enterprise_ai_vlm_input_mode: str = "auto"
    oci_enterprise_ai_llm_payload_template: str = ""
    oci_enterprise_ai_vlm_payload_template: str = ""
    oci_enterprise_ai_llm_response_path: str = ""
    oci_enterprise_ai_vlm_response_path: str = ""
    oci_enterprise_ai_timeout_seconds: float = 600.0
    oci_enterprise_ai_max_retries: int = 3
    oci_enterprise_ai_llm_max_output_tokens: int = 1200
    oci_enterprise_ai_vlm_max_output_tokens: int = 65536
    oci_genai_embedding_model: str = "cohere.embed-v4.0"
    oci_genai_embedding_dim: int = 1536
    oci_genai_rerank_model: str = "cohere.rerank-v4.0-fast"
    parser_backend: str = "unstructured"
    parser_api_key: str = ""


PAYLOAD: dict[str, Any] = {
    "enterprise_ai": {
        "endpoint": "https://example.invalid/openai/v1",
        "project_ocid": "ocid1.generativeaiproject.oc1..aaaa",
        "api_key": "sk-new",
        "models": [
            {"model_id": "llm-a", "display_name": "A", "vision_enabled": False},
            {"model_id": "vlm-b", "display_name": "B", "vision_enabled": True},
        ],
        "default_model_id": "llm-a",
    },
    "generative_ai": {"embedding_model": "cohere.embed-v4.0", "rerank_model": "rr"},
}


PARSER_KEY_ENV = "TEST_PARSER_API_KEY"


def _load_parser(settings: Any, raw: Mapping[str, Any] | None, version: int) -> None:
    if raw is not None:
        settings.parser_backend = raw["backend"]
        settings.parser_api_key = raw.get("api_key", settings.parser_api_key)


PARSER_SECTION = ModelSettingsSection(
    name="parser_adapters",
    load=_load_parser,
    dump=lambda settings: {"backend": settings.parser_backend, "api_key": settings.parser_api_key},
    secrets=(SectionSecret(key="api_key", attr="parser_api_key", env=PARSER_KEY_ENV),),
)


@pytest.fixture(autouse=True)
def _no_process_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENTERPRISE_AI_API_KEY_ENV, raising=False)
    monkeypatch.delenv(PARSER_KEY_ENV, raising=False)


def make_store(tmp_path: Path) -> ModelSettingsStore:
    return ModelSettingsStore(
        resolve_path=lambda settings: tmp_path / settings.model_settings_file,
        env_file=lambda settings: tmp_path / ".env",
        sections=(PARSER_SECTION,),
    )


def make_client(settings: FakeSettings, store: ModelSettingsStore, **kwargs: Any) -> TestClient:
    async def run_test(candidate: Any, request: ModelSettingsTestRequest) -> dict[str, Any]:
        return {"key": candidate.oci_enterprise_ai_api_key, "model": request.model_id}

    def get_settings() -> FakeSettings:
        store.reload_if_changed(settings)
        return settings

    app = FastAPI()
    app.include_router(
        build_model_router(
            get_settings=get_settings,
            store=store,
            run_model_test=kwargs.get("run_model_test", run_test),
        ),
        prefix="/api/settings",
    )
    return TestClient(app)


def test_patch_saves_key_only_in_env_and_keeps_product_section(tmp_path: Path) -> None:
    settings = FakeSettings(parser_backend="docling")
    store = make_store(tmp_path)
    store.load(settings)

    response = make_client(settings, store).patch("/api/settings/model", json=PAYLOAD)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["settings"]["enterprise_ai"]["api_key"] == ""
    assert data["settings"]["enterprise_ai"]["has_api_key"] is True
    assert data["secret_source"] == "environment"
    document = json.loads((tmp_path / "model-settings.json").read_text(encoding="utf-8"))
    assert document["version"] == 3
    assert "api_key" not in document["enterprise_ai"]
    assert "sk-new" not in json.dumps(document)
    assert document["parser_adapters"] == {"backend": "docling"}
    assert stat.S_IMODE((tmp_path / "model-settings.json").stat().st_mode) == 0o600
    assert dotenv_values(tmp_path / ".env")[ENTERPRISE_AI_API_KEY_ENV] == "sk-new"
    assert settings.oci_enterprise_ai_api_key == "sk-new"
    assert settings.oci_enterprise_ai_vlm_model == "vlm-b"


def test_blank_key_keeps_current_and_clear_removes_it(tmp_path: Path) -> None:
    settings = FakeSettings(oci_enterprise_ai_api_key="sk-old")
    store = make_store(tmp_path)
    store.load(settings)
    client = make_client(settings, store)
    keep = {**PAYLOAD, "enterprise_ai": {**PAYLOAD["enterprise_ai"], "api_key": ""}}

    assert client.patch("/api/settings/model", json=keep).status_code == 200
    assert settings.oci_enterprise_ai_api_key == "sk-old"

    clear = {**PAYLOAD, "enterprise_ai": {**keep["enterprise_ai"], "clear_api_key": True}}
    data = client.patch("/api/settings/model", json=clear).json()["data"]
    assert data["settings"]["enterprise_ai"]["has_api_key"] is False
    assert data["secret_source"] == "missing"
    assert ENTERPRISE_AI_API_KEY_ENV not in dotenv_values(tmp_path / ".env")


def test_legacy_json_key_is_used_until_saved_to_env(tmp_path: Path) -> None:
    legacy = {"version": 2, "enterprise_ai": {"api_key": "sk-legacy", "models": []}}
    (tmp_path / "model-settings.json").write_text(json.dumps(legacy), encoding="utf-8")
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    assert settings.oci_enterprise_ai_api_key == "sk-legacy"
    assert settings.model_secret_source == "legacy_json"
    assert settings.legacy_model_secret_detected is True

    keep = {**PAYLOAD, "enterprise_ai": {**PAYLOAD["enterprise_ai"], "api_key": ""}}
    data = make_client(settings, store).patch("/api/settings/model", json=keep).json()["data"]

    assert data["secret_source"] == "environment"
    assert data["legacy_secret_detected"] is False
    assert dotenv_values(tmp_path / ".env")[ENTERPRISE_AI_API_KEY_ENV] == "sk-legacy"
    assert "sk-legacy" not in (tmp_path / "model-settings.json").read_text(encoding="utf-8")


def test_environment_key_wins_over_legacy_json(tmp_path: Path) -> None:
    legacy = {"version": 1, "enterprise_ai": {"api_key": "sk-legacy"}}
    (tmp_path / "model-settings.json").write_text(json.dumps(legacy), encoding="utf-8")
    settings = FakeSettings(oci_enterprise_ai_api_key="sk-env")
    make_store(tmp_path).load(settings)
    assert settings.oci_enterprise_ai_api_key == "sk-env"
    assert settings.model_secret_source == "environment"
    assert settings.legacy_model_secret_detected is True


def test_json_failure_restores_env_and_keeps_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(f"{ENTERPRISE_AI_API_KEY_ENV}=sk-old\n", encoding="utf-8")
    settings = FakeSettings(oci_enterprise_ai_api_key="sk-old")
    store = make_store(tmp_path)
    store.load(settings)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "write_document", fail)
    response = make_client(settings, store).patch("/api/settings/model", json=PAYLOAD)

    assert response.status_code == 500
    assert dotenv_values(tmp_path / ".env")[ENTERPRISE_AI_API_KEY_ENV] == "sk-old"
    assert settings.oci_enterprise_ai_api_key == "sk-old"
    assert settings.oci_enterprise_ai_endpoint == ""


def test_other_worker_picks_up_saved_key_and_settings(tmp_path: Path) -> None:
    store_a, store_b = make_store(tmp_path), make_store(tmp_path)
    worker_a, worker_b = FakeSettings(), FakeSettings()
    store_a.load(worker_a)
    store_b.load(worker_b)

    make_client(worker_a, store_a).patch("/api/settings/model", json=PAYLOAD)
    data = make_client(worker_b, store_b).get("/api/settings/model").json()["data"]

    assert worker_b.oci_enterprise_ai_api_key == "sk-new"
    assert data["settings"]["enterprise_ai"]["default_model_id"] == "llm-a"


def test_get_without_models_returns_one_blank_row(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    models = (
        make_client(settings, store)
        .get("/api/settings/model")
        .json()["data"]["settings"]["enterprise_ai"]["models"]
    )
    assert models == [{"model_id": "", "display_name": "", "vision_enabled": False}]


def test_model_test_uses_unsaved_payload_and_masks_secret(tmp_path: Path) -> None:
    settings = FakeSettings(oci_enterprise_ai_api_key="sk-saved")
    store = make_store(tmp_path)
    store.load(settings)
    request = {"settings": PAYLOAD, "target_type": "enterprise_vision", "model_id": "llm-a"}

    ok = make_client(settings, store).post("/api/settings/model/test", json=request).json()["data"]
    assert ok["status"] == "success"
    assert ok["details"] == {"key": "sk-new", "model": "llm-a"}

    async def fail(candidate: Any, _request: ModelSettingsTestRequest) -> dict[str, Any]:
        assert candidate.oci_enterprise_ai_vlm_model == "llm-a"
        raise RuntimeError(f"401 Unauthorized for {candidate.oci_enterprise_ai_api_key}")

    failed = (
        make_client(settings, store, run_model_test=fail)
        .post("/api/settings/model/test", json=request)
        .json()["data"]
    )
    assert failed["status"] == "failed"
    assert "sk-new" not in failed["raw_error"]
    assert "<secret>" in failed["raw_error"]
    assert any("認証エラー" in tip for tip in failed["troubleshooting"])
    assert settings.oci_enterprise_ai_endpoint == ""


def test_model_test_requires_model_id(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    request = {"settings": PAYLOAD, "target_type": "rerank", "model_id": " "}
    data = make_client(settings, store).post("/api/settings/model/test", json=request).json()
    assert data["data"]["status"] == "failed"
    assert data["data"]["raw_error"] == "テストするモデル ID を入力してください。"


def test_saved_key_overrides_process_environment_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """画面で保存した key はプロセスの環境変数より優先し、削除すると環境変数へ戻る。"""
    monkeypatch.setenv(ENTERPRISE_AI_API_KEY_ENV, "sk-process")
    settings = FakeSettings(oci_enterprise_ai_api_key="sk-process")
    store = make_store(tmp_path)
    store.load(settings)
    client = make_client(settings, store)

    client.patch("/api/settings/model", json=PAYLOAD)
    data = client.get("/api/settings/model").json()["data"]
    assert settings.oci_enterprise_ai_api_key == "sk-new"
    assert data["settings"]["enterprise_ai"]["has_api_key"] is True

    clear = {**PAYLOAD, "enterprise_ai": {**PAYLOAD["enterprise_ai"], "clear_api_key": True}}
    cleared = client.patch("/api/settings/model", json=clear).json()["data"]
    assert cleared["settings"]["enterprise_ai"]["has_api_key"] is True
    client.get("/api/settings/model")
    assert settings.oci_enterprise_ai_api_key == "sk-process"


def test_broken_json_is_reported(tmp_path: Path) -> None:
    (tmp_path / "model-settings.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="モデル設定ファイルを読み込めません"):
        make_store(tmp_path).load(FakeSettings())


def test_catalog_falls_back_to_legacy_llm_and_vlm_ids() -> None:
    settings = FakeSettings(oci_enterprise_ai_llm_model="llm", oci_enterprise_ai_vlm_model="vlm")
    assert [m.model_id for m in shared_model.enterprise_ai_model_catalog(settings)] == [
        "llm",
        "vlm",
    ]
    assert shared_model.enterprise_ai_default_model_id(settings) == "llm"
    assert shared_model.enterprise_ai_vision_model_id(settings) == "vlm"


def test_key_saved_in_env_file_is_used_on_startup(tmp_path: Path) -> None:
    """コンテナで `.env` を volume に置く場合も、起動時に key を読む。"""
    (tmp_path / ".env").write_text(f"{ENTERPRISE_AI_API_KEY_ENV}=sk-volume\n", encoding="utf-8")
    settings = FakeSettings()
    make_store(tmp_path).load(settings)
    assert settings.oci_enterprise_ai_api_key == "sk-volume"
    assert settings.model_secret_source == "environment"


def test_section_secret_is_saved_only_in_env_file(tmp_path: Path) -> None:
    settings = FakeSettings(parser_api_key="parser-secret")
    store = make_store(tmp_path)
    store.load(settings)
    make_client(settings, store).patch("/api/settings/model", json=PAYLOAD)

    document = (tmp_path / "model-settings.json").read_text(encoding="utf-8")
    assert "parser-secret" not in document
    assert dotenv_values(tmp_path / ".env")[PARSER_KEY_ENV] == "parser-secret"

    other = FakeSettings()
    make_store(tmp_path).load(other)
    assert other.parser_api_key == "parser-secret"


def test_legacy_section_secret_is_used_and_moved_to_env_file(tmp_path: Path) -> None:
    legacy = {
        "version": 2,
        "enterprise_ai": {"models": []},
        "parser_adapters": {"backend": "mineru", "api_key": "legacy-parser"},
    }
    (tmp_path / "model-settings.json").write_text(json.dumps(legacy), encoding="utf-8")
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    assert settings.parser_api_key == "legacy-parser"
    assert settings.legacy_model_secret_detected is True

    make_client(settings, store).patch("/api/settings/model", json=PAYLOAD)

    assert "legacy-parser" not in (tmp_path / "model-settings.json").read_text(encoding="utf-8")
    assert dotenv_values(tmp_path / ".env")[PARSER_KEY_ENV] == "legacy-parser"
    assert settings.parser_api_key == "legacy-parser"
    assert settings.legacy_model_secret_detected is False


def test_section_secret_prefers_env_file_then_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PARSER_KEY_ENV, "from-process")
    (tmp_path / ".env").write_text(f"{PARSER_KEY_ENV}=from-screen\n", encoding="utf-8")
    settings = FakeSettings()
    make_store(tmp_path).load(settings)
    assert settings.parser_api_key == "from-screen"

    (tmp_path / ".env").write_text("", encoding="utf-8")
    other = FakeSettings()
    make_store(tmp_path).load(other)
    assert other.parser_api_key == "from-process"


def test_value_from_process_environment_is_not_written_to_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PARSER_KEY_ENV, "from-process")
    settings = FakeSettings(parser_api_key="from-process")
    store = make_store(tmp_path)
    store.load(settings)
    make_client(settings, store).patch("/api/settings/model", json=PAYLOAD)
    assert PARSER_KEY_ENV not in dotenv_values(tmp_path / ".env")
    assert settings.parser_api_key == "from-process"
