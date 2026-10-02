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
    ENTERPRISE_AI_SECONDARY_API_KEY_ENV,
    ENTERPRISE_AI_TERTIARY_API_KEY_ENV,
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
    oci_enterprise_ai_secondary_endpoint: str = ""
    oci_enterprise_ai_secondary_project_ocid: str = ""
    oci_enterprise_ai_secondary_api_key: str = ""
    oci_enterprise_ai_tertiary_endpoint: str = ""
    oci_enterprise_ai_tertiary_project_ocid: str = ""
    oci_enterprise_ai_tertiary_api_key: str = ""
    oci_enterprise_ai_models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list)
    oci_enterprise_ai_default_text_model: str = ""
    oci_enterprise_ai_default_vision_model: str = ""
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
        "default_text_model_id": "llm-a",
        "default_vision_model_id": "vlm-b",
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
    monkeypatch.delenv(ENTERPRISE_AI_SECONDARY_API_KEY_ENV, raising=False)
    monkeypatch.delenv(ENTERPRISE_AI_TERTIARY_API_KEY_ENV, raising=False)
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
    assert data["settings"]["enterprise_ai"]["connections"][0]["api_key"] == ""
    assert data["settings"]["enterprise_ai"]["connections"][0]["has_api_key"] is True
    assert data["secret_source"] == "environment"
    document = json.loads((tmp_path / "model-settings.json").read_text(encoding="utf-8"))
    assert document["version"] == 3
    assert "api_key" not in document["enterprise_ai"]
    assert "sk-new" not in json.dumps(document)
    assert document["parser_adapters"] == {"backend": "docling"}
    assert stat.S_IMODE((tmp_path / "model-settings.json").stat().st_mode) == 0o600
    assert dotenv_values(tmp_path / ".env")[ENTERPRISE_AI_API_KEY_ENV] == "sk-new"
    assert settings.oci_enterprise_ai_api_key == "sk-new"
    assert settings.oci_enterprise_ai_default_vision_model == "vlm-b"
    assert document["enterprise_ai"]["default_text_model_id"] == "llm-a"
    assert document["enterprise_ai"]["default_vision_model_id"] == "vlm-b"
    assert "default_model_id" not in document["enterprise_ai"]


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
    assert data["settings"]["enterprise_ai"]["connections"][0]["has_api_key"] is False
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
    assert data["settings"]["enterprise_ai"]["default_text_model_id"] == "llm-a"
    assert data["settings"]["enterprise_ai"]["default_vision_model_id"] == "vlm-b"


def test_get_without_models_returns_one_blank_row(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    models = (
        make_client(settings, store)
        .get("/api/settings/model")
        .json()["data"]["settings"]["enterprise_ai"]["models"]
    )
    assert models == [
        {"model_id": "", "display_name": "", "vision_enabled": False, "connection_id": "primary"}
    ]


def test_model_test_uses_unsaved_payload_and_masks_secret(tmp_path: Path) -> None:
    settings = FakeSettings(oci_enterprise_ai_api_key="sk-saved")
    store = make_store(tmp_path)
    store.load(settings)
    request = {"settings": PAYLOAD, "target_type": "enterprise_vision", "model_id": "llm-a"}

    ok = make_client(settings, store).post("/api/settings/model/test", json=request).json()["data"]
    assert ok["status"] == "success"
    assert ok["details"] == {"key": "sk-new", "model": "llm-a"}

    async def fail(candidate: Any, _request: ModelSettingsTestRequest) -> dict[str, Any]:
        assert candidate.oci_enterprise_ai_default_vision_model == "llm-a"
        assert shared_model.enterprise_ai_vision_model_id(candidate) == "llm-a"
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
    assert data["settings"]["enterprise_ai"]["connections"][0]["has_api_key"] is True

    clear = {**PAYLOAD, "enterprise_ai": {**PAYLOAD["enterprise_ai"], "clear_api_key": True}}
    cleared = client.patch("/api/settings/model", json=clear).json()["data"]
    assert cleared["settings"]["enterprise_ai"]["connections"][0]["has_api_key"] is True
    client.get("/api/settings/model")
    assert settings.oci_enterprise_ai_api_key == "sk-process"


def test_broken_json_is_reported(tmp_path: Path) -> None:
    (tmp_path / "model-settings.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="モデル設定ファイルを読み込めません"):
        make_store(tmp_path).load(FakeSettings())


def test_catalog_falls_back_to_default_text_and_vision_ids() -> None:
    settings = FakeSettings(
        oci_enterprise_ai_default_text_model="llm", oci_enterprise_ai_default_vision_model="vlm"
    )
    assert [
        (m.model_id, m.vision_enabled) for m in shared_model.enterprise_ai_model_catalog(settings)
    ] == [("llm", False), ("vlm", True)]
    assert shared_model.enterprise_ai_default_model_id(settings) == "llm"
    assert shared_model.enterprise_ai_vision_model_id(settings) == "vlm"


# --------------------------------------------------------------------------- #499 既定のモデル 2 つ

MODELS = [
    EnterpriseAiConfiguredModel(model_id="llm-a", display_name="A", vision_enabled=False),
    EnterpriseAiConfiguredModel(model_id="vlm-b", display_name="B", vision_enabled=True),
]


def test_resolution_uses_text_model_and_falls_back_to_vision_model() -> None:
    settings = FakeSettings(
        oci_enterprise_ai_models=MODELS,
        oci_enterprise_ai_default_text_model="llm-a",
        oci_enterprise_ai_default_vision_model="vlm-b",
    )
    assert shared_model.enterprise_ai_default_model_id(settings) == "llm-a"
    assert shared_model.enterprise_ai_vision_model_id(settings) == "vlm-b"

    settings.oci_enterprise_ai_default_text_model = ""
    # 画面・API では必須だが（#566）、未設定の既存環境では画像を扱わない呼び出しも
    # 既定の画像対応モデルを使う（安全策）。
    assert shared_model.enterprise_ai_default_model_id(settings) == "vlm-b"
    assert shared_model.enterprise_ai_vision_model_id(settings) == "vlm-b"


def test_vision_model_is_derived_when_not_set_explicitly() -> None:
    """`.env` で登録モデルだけを書いた環境は、従来どおり Vision 対応のモデルを使う。"""
    settings = FakeSettings(oci_enterprise_ai_models=MODELS)
    assert shared_model.enterprise_ai_vision_model_id(settings) == "vlm-b"
    assert shared_model.enterprise_ai_default_model_id(settings) == "vlm-b"


@pytest.mark.parametrize(
    ("update", "field", "message"),
    [
        (
            {"models": [{"model_id": "llm-a", "vision_enabled": False}]},
            "default_vision_model_id",
            "画像入力に対応したモデルがありません",
        ),
        ({"default_vision_model_id": ""}, "default_vision_model_id", "選択してください"),
        ({"default_vision_model_id": "gone"}, "default_vision_model_id", "登録モデルにありません"),
        (
            {"default_vision_model_id": "llm-a"},
            "default_vision_model_id",
            "画像入力に対応していません",
        ),
        ({"default_text_model_id": ""}, "default_text_model_id", "既定のテキストモデルを選択"),
        ({"default_text_model_id": "gone"}, "default_text_model_id", "登録モデルにありません"),
    ],
    ids=[
        "no-vision-model",
        "vision-empty",
        "vision-removed",
        "vision-not-capable",
        "text-empty",
        "text-removed",
    ],
)
def test_patch_rejects_invalid_default_models(
    tmp_path: Path, update: dict[str, Any], field: str, message: str
) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    enterprise = {**PAYLOAD["enterprise_ai"], **update}
    errors = shared_model.validate_default_models(
        shared_model.EnterpriseAiModelSettings.model_validate(enterprise)
    )
    assert [error.field for error in errors] == [field]

    response = make_client(settings, store).patch(
        "/api/settings/model", json={**PAYLOAD, "enterprise_ai": enterprise}
    )

    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert not (tmp_path / "model-settings.json").exists()


def test_default_model_errors_follow_screen_order() -> None:
    """2 つとも空なら、画面の並び順（テキスト → Vision）でエラーを返す（#566）。"""
    enterprise = {
        **PAYLOAD["enterprise_ai"],
        "default_text_model_id": "",
        "default_vision_model_id": "",
    }
    errors = shared_model.validate_default_models(
        shared_model.EnterpriseAiModelSettings.model_validate(enterprise)
    )
    assert [error.field for error in errors] == ["default_text_model_id", "default_vision_model_id"]


def test_patch_accepts_vision_capable_text_model(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    client = make_client(settings, store)

    vision_text = {**PAYLOAD["enterprise_ai"], "default_text_model_id": "vlm-b"}
    response = client.patch("/api/settings/model", json={**PAYLOAD, "enterprise_ai": vision_text})
    assert response.status_code == 200
    assert shared_model.enterprise_ai_default_model_id(settings) == "vlm-b"


def test_patch_without_models_needs_no_vision_model(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    enterprise = {
        **PAYLOAD["enterprise_ai"],
        "models": [],
        "default_text_model_id": "",
        "default_vision_model_id": "",
    }
    response = make_client(settings, store).patch(
        "/api/settings/model", json={**PAYLOAD, "enterprise_ai": enterprise}
    )
    assert response.status_code == 200


def test_saving_other_sections_is_not_blocked_by_saved_invalid_defaults(tmp_path: Path) -> None:
    """保存済みの状態に Vision 対応のモデルがなくても、接続情報だけの保存は止めない。"""
    (tmp_path / "model-settings.json").write_text(
        json.dumps(
            {
                "version": 3,
                "enterprise_ai": {
                    "models": [{"model_id": "llm-a", "vision_enabled": False}],
                    "default_model_id": "llm-a",
                },
            }
        ),
        encoding="utf-8",
    )
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    client = make_client(settings, store)
    current = client.get("/api/settings/model").json()["data"]["settings"]
    assert current["enterprise_ai"]["default_vision_model_id"] == ""

    current["enterprise_ai"]["connections"][0]["endpoint"] = "https://changed.invalid/openai/v1"
    response = client.patch("/api/settings/model", json=current)

    assert response.status_code == 200
    assert settings.oci_enterprise_ai_endpoint == "https://changed.invalid/openai/v1"


@pytest.mark.parametrize(
    ("enterprise", "expected"),
    [
        # 既定モデルが Vision 対応ならそれを Vision にも使う（従来の導出）。
        (
            {
                "models": [
                    {"model_id": "llm-a", "vision_enabled": False},
                    {"model_id": "vlm-b", "vision_enabled": True},
                    {"model_id": "vlm-c", "vision_enabled": True},
                ],
                "default_model_id": "vlm-c",
            },
            ("vlm-c", "vlm-c"),
        ),
        # 既定モデルが Vision 非対応なら、一覧で最初の Vision 対応モデル。
        (
            {
                "models": [
                    {"model_id": "llm-a", "vision_enabled": False},
                    {"model_id": "vlm-b", "vision_enabled": True},
                ],
                "default_model_id": "llm-a",
            },
            ("llm-a", "vlm-b"),
        ),
        # 既定モデルが空なら先頭の登録モデル。Vision 対応がなければ Vision は空（失敗させない）。
        (
            {"models": [{"model_id": "llm-a", "vision_enabled": False}], "default_model_id": ""},
            ("llm-a", ""),
        ),
    ],
    ids=["default-is-vision", "first-vision", "no-vision"],
)
def test_legacy_json_default_model_id_is_backfilled(
    tmp_path: Path, enterprise: dict[str, Any], expected: tuple[str, str]
) -> None:
    (tmp_path / "model-settings.json").write_text(
        json.dumps({"version": 3, "enterprise_ai": enterprise}), encoding="utf-8"
    )
    settings = FakeSettings()
    make_store(tmp_path).load(settings)

    assert (
        settings.oci_enterprise_ai_default_text_model,
        settings.oci_enterprise_ai_default_vision_model,
    ) == expected
    assert shared_model.enterprise_ai_default_model_id(settings) == expected[0]
    assert shared_model.enterprise_ai_vision_model_id(settings) == expected[1]


def test_saving_legacy_json_writes_new_keys(tmp_path: Path) -> None:
    (tmp_path / "model-settings.json").write_text(
        json.dumps(
            {
                "version": 3,
                "enterprise_ai": {
                    "models": [
                        {"model_id": "llm-a", "vision_enabled": False},
                        {"model_id": "vlm-b", "vision_enabled": True},
                    ],
                    "default_model_id": "llm-a",
                },
            }
        ),
        encoding="utf-8",
    )
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    client = make_client(settings, store)
    current = client.get("/api/settings/model").json()["data"]["settings"]

    assert client.patch("/api/settings/model", json=current).status_code == 200

    document = json.loads((tmp_path / "model-settings.json").read_text(encoding="utf-8"))
    assert document["enterprise_ai"]["default_text_model_id"] == "llm-a"
    assert document["enterprise_ai"]["default_vision_model_id"] == "vlm-b"
    assert "default_model_id" not in document["enterprise_ai"]


def test_saved_vision_model_that_is_not_vision_capable_is_replaced_on_load(
    tmp_path: Path,
) -> None:
    """手で編集した JSON の不整合でも読み込みは失敗させず、Vision 対応のモデルへ置き換える。"""
    (tmp_path / "model-settings.json").write_text(
        json.dumps(
            {
                "version": 3,
                "enterprise_ai": {
                    "models": [
                        {"model_id": "llm-a", "vision_enabled": False},
                        {"model_id": "vlm-b", "vision_enabled": True},
                    ],
                    "default_text_model_id": "",
                    "default_vision_model_id": "llm-a",
                },
            }
        ),
        encoding="utf-8",
    )
    settings = FakeSettings()
    make_store(tmp_path).load(settings)

    assert settings.oci_enterprise_ai_default_text_model == ""
    assert settings.oci_enterprise_ai_default_vision_model == "vlm-b"


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


def test_shared_document_keeps_other_product_sections_and_product_secret_file(
    tmp_path: Path,
) -> None:
    """model-settings.json は3製品で共有する（#211）。

    他製品の節を消さず、節の secret は製品の .env へ保存する。
    """
    (tmp_path / "model-settings.json").write_text(
        json.dumps({"version": 3, "other_product": {"keep": True}}), encoding="utf-8"
    )
    store = ModelSettingsStore(
        resolve_path=lambda settings: tmp_path / settings.model_settings_file,
        env_file=lambda settings: tmp_path / "platform.env",
        sections=(PARSER_SECTION,),
        section_env_file=lambda settings: tmp_path / "product.env",
    )
    settings = FakeSettings(parser_backend="docling", parser_api_key="parser-secret")
    store.load(settings)
    settings.parser_api_key = "parser-secret"

    response = make_client(settings, store).patch("/api/settings/model", json=PAYLOAD)

    assert response.status_code == 200
    document = json.loads((tmp_path / "model-settings.json").read_text(encoding="utf-8"))
    assert document["other_product"] == {"keep": True}
    assert document["parser_adapters"] == {"backend": "docling"}
    platform_env = (tmp_path / "platform.env").read_text(encoding="utf-8")
    product_env = (tmp_path / "product.env").read_text(encoding="utf-8")
    assert f"{ENTERPRISE_AI_API_KEY_ENV}=sk-new" in platform_env
    assert PARSER_KEY_ENV not in platform_env
    assert f"{PARSER_KEY_ENV}=parser-secret" in product_env
    assert ENTERPRISE_AI_API_KEY_ENV not in product_env
