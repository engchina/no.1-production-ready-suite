"""OCI Enterprise AI の接続を 2 件まで持ち、モデルごとに選ぶ（#533）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dotenv import dotenv_values
from test_model import FakeSettings, make_client, make_store

from pr_system_settings import model as shared_model
from pr_system_settings.model import (
    ENTERPRISE_AI_API_KEY_ENV,
    ENTERPRISE_AI_SECONDARY_API_KEY_ENV,
    MAX_ENTERPRISE_AI_CONNECTIONS,
    EnterpriseAiConfiguredModel,
    EnterpriseAiModelSettings,
    ModelSettingsTestRequest,
    enterprise_ai_connection_for_model,
    enterprise_ai_connections,
    validate_enterprise_ai_connections,
)

PRIMARY_ENDPOINT = "https://primary.invalid/openai/v1"
SECONDARY_ENDPOINT = "https://secondary.invalid/openai/v1"


@pytest.fixture(autouse=True)
def _no_process_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENTERPRISE_AI_API_KEY_ENV, raising=False)
    monkeypatch.delenv(ENTERPRISE_AI_SECONDARY_API_KEY_ENV, raising=False)


def two_connection_payload(**secondary: Any) -> dict[str, Any]:
    return {
        "enterprise_ai": {
            "connections": [
                {
                    "connection_id": "primary",
                    "display_name": "大阪",
                    "endpoint": PRIMARY_ENDPOINT,
                    "project_ocid": "ocid1.project.primary",
                    "api_key": "sk-primary",
                },
                {
                    "connection_id": "secondary",
                    "display_name": "シカゴ",
                    "endpoint": SECONDARY_ENDPOINT,
                    "project_ocid": "ocid1.project.secondary",
                    "api_key": "sk-secondary",
                    **secondary,
                },
            ],
            "models": [
                {"model_id": "llm-a", "display_name": "A", "vision_enabled": False},
                {
                    "model_id": "vlm-b",
                    "display_name": "B",
                    "vision_enabled": True,
                    "connection_id": "secondary",
                },
            ],
            "default_text_model_id": "llm-a",
            "default_vision_model_id": "vlm-b",
        },
        "generative_ai": {"embedding_model": "cohere.embed-v4.0", "rerank_model": "rr"},
    }


def test_max_connections_is_two() -> None:
    assert MAX_ENTERPRISE_AI_CONNECTIONS == 2
    with pytest.raises(ValueError):
        EnterpriseAiModelSettings.model_validate(
            {"connections": [{"connection_id": "primary"}] * 3}
        )


def test_connections_must_start_with_primary_without_duplicates() -> None:
    for connections in (
        [{"connection_id": "secondary"}],
        [{"connection_id": "primary"}, {"connection_id": "primary"}],
    ):
        with pytest.raises(ValueError):
            EnterpriseAiModelSettings.model_validate({"connections": connections})


def test_legacy_payload_is_read_as_primary_connection() -> None:
    settings = EnterpriseAiModelSettings.model_validate(
        {"endpoint": PRIMARY_ENDPOINT, "project_ocid": "ocid1", "api_key": "sk", "models": []}
    )
    assert [c.connection_id for c in settings.connections] == ["primary"]
    assert settings.primary_connection.endpoint == PRIMARY_ENDPOINT
    assert settings.primary_connection.api_key == "sk"


def test_model_without_connection_uses_primary() -> None:
    assert EnterpriseAiConfiguredModel(model_id="m").connection_id == "primary"
    assert EnterpriseAiConfiguredModel(model_id="m", connection_id=" ").connection_id == "primary"


def test_save_two_connections_keeps_keys_only_in_env(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)

    response = make_client(settings, store).patch(
        "/api/settings/model", json=two_connection_payload()
    )

    assert response.status_code == 200, response.text
    enterprise = response.json()["data"]["settings"]["enterprise_ai"]
    assert [c["connection_id"] for c in enterprise["connections"]] == ["primary", "secondary"]
    assert [c["display_name"] for c in enterprise["connections"]] == ["大阪", "シカゴ"]
    assert all(c["api_key"] == "" and c["has_api_key"] for c in enterprise["connections"])
    assert [m["connection_id"] for m in enterprise["models"]] == ["primary", "secondary"]
    assert "sk-primary" not in response.text and "sk-secondary" not in response.text

    raw = (tmp_path / "model-settings.json").read_text(encoding="utf-8")
    assert "sk-primary" not in raw and "sk-secondary" not in raw
    document = json.loads(raw)
    assert document["enterprise_ai"]["connections"] == [
        {
            "connection_id": "primary",
            "display_name": "大阪",
            "endpoint": PRIMARY_ENDPOINT,
            "project_ocid": "ocid1.project.primary",
        },
        {
            "connection_id": "secondary",
            "display_name": "シカゴ",
            "endpoint": SECONDARY_ENDPOINT,
            "project_ocid": "ocid1.project.secondary",
        },
    ]
    assert "endpoint" not in document["enterprise_ai"]
    assert document["enterprise_ai"]["models"][1]["connection_id"] == "secondary"
    env = dotenv_values(tmp_path / ".env")
    assert env[ENTERPRISE_AI_API_KEY_ENV] == "sk-primary"
    assert env[ENTERPRISE_AI_SECONDARY_API_KEY_ENV] == "sk-secondary"
    assert settings.oci_enterprise_ai_secondary_endpoint == SECONDARY_ENDPOINT
    assert settings.oci_enterprise_ai_secondary_api_key == "sk-secondary"


def test_resolver_returns_connection_of_each_model(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    make_client(settings, store).patch("/api/settings/model", json=two_connection_payload())

    text = enterprise_ai_connection_for_model(settings, "llm-a")
    vision = enterprise_ai_connection_for_model(settings, "vlm-b")
    assert (text.connection_id, text.endpoint, text.api_key, text.project_ocid) == (
        "primary",
        PRIMARY_ENDPOINT,
        "sk-primary",
        "ocid1.project.primary",
    )
    assert (vision.connection_id, vision.endpoint, vision.api_key, vision.project_ocid) == (
        "secondary",
        SECONDARY_ENDPOINT,
        "sk-secondary",
        "ocid1.project.secondary",
    )
    # 登録モデルにない ID は接続 1
    assert enterprise_ai_connection_for_model(settings, "unknown").connection_id == "primary"
    # API key は repr に出さない
    assert "sk-secondary" not in repr(vision)


def test_resolver_falls_back_to_primary_when_connection_is_gone() -> None:
    settings = FakeSettings(
        oci_enterprise_ai_endpoint=PRIMARY_ENDPOINT,
        oci_enterprise_ai_api_key="sk-primary",
        oci_enterprise_ai_models=[
            EnterpriseAiConfiguredModel(model_id="m", connection_id="secondary")
        ],
    )
    assert [c.connection_id for c in enterprise_ai_connections(settings)] == ["primary"]
    assert enterprise_ai_connection_for_model(settings, "m").endpoint == PRIMARY_ENDPOINT


def test_secondary_connection_from_environment_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env だけで接続 2 を設定した環境（JSON なし）も、接続 2 を使える。"""
    monkeypatch.setenv(ENTERPRISE_AI_SECONDARY_API_KEY_ENV, "sk-env-secondary")
    settings = FakeSettings(
        oci_enterprise_ai_secondary_endpoint=SECONDARY_ENDPOINT,
        oci_enterprise_ai_secondary_api_key="sk-env-secondary",
    )
    make_store(tmp_path).load(settings)
    connections = enterprise_ai_connections(settings)
    assert [c.connection_id for c in connections] == ["primary", "secondary"]
    assert connections[1].api_key == "sk-env-secondary"


def test_old_json_is_read_as_single_connection(tmp_path: Path) -> None:
    """#533 より前の JSON（接続 1 組）は接続 1 として読み、モデルは接続 1 を使う。"""
    old = {
        "version": 3,
        "enterprise_ai": {
            "endpoint": PRIMARY_ENDPOINT,
            "project_ocid": "ocid1.project.primary",
            "models": [{"model_id": "vlm", "display_name": "V", "vision_enabled": True}],
            "default_text_model_id": "",
            "default_vision_model_id": "vlm",
        },
    }
    (tmp_path / "model-settings.json").write_text(json.dumps(old), encoding="utf-8")
    (tmp_path / ".env").write_text(f"{ENTERPRISE_AI_API_KEY_ENV}=sk-old\n", encoding="utf-8")
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)

    enterprise = (
        make_client(settings, store)
        .get("/api/settings/model")
        .json()["data"]["settings"]["enterprise_ai"]
    )
    assert enterprise["connections"] == [
        {
            "connection_id": "primary",
            "display_name": "",
            "endpoint": PRIMARY_ENDPOINT,
            "project_ocid": "ocid1.project.primary",
            "api_key": "",
            "has_api_key": True,
            "clear_api_key": False,
        }
    ]
    assert enterprise["models"][0]["connection_id"] == "primary"
    connection = enterprise_ai_connection_for_model(settings, "vlm")
    assert (connection.endpoint, connection.api_key) == (PRIMARY_ENDPOINT, "sk-old")


def test_saved_json_with_unknown_connections_is_read_leniently(tmp_path: Path) -> None:
    document = {
        "version": 3,
        "enterprise_ai": {
            "connections": [
                {"connection_id": "tertiary", "endpoint": "https://x.invalid"},
                {"connection_id": "secondary", "endpoint": SECONDARY_ENDPOINT},
            ],
            "models": [],
        },
    }
    (tmp_path / "model-settings.json").write_text(json.dumps(document), encoding="utf-8")
    settings = FakeSettings(oci_enterprise_ai_endpoint="https://env.invalid")
    make_store(tmp_path).load(settings)
    assert [c.connection_id for c in enterprise_ai_connections(settings)] == [
        "primary",
        "secondary",
    ]
    assert settings.oci_enterprise_ai_endpoint == ""


def test_model_pointing_to_missing_connection_is_a_field_error(tmp_path: Path) -> None:
    payload = two_connection_payload()
    payload["enterprise_ai"]["connections"].pop()
    enterprise = EnterpriseAiModelSettings.model_validate(payload["enterprise_ai"])
    errors = validate_enterprise_ai_connections(enterprise)
    assert [error.field for error in errors] == ["models.1.connection_id"]

    settings = FakeSettings()
    store = make_store(tmp_path)
    response = make_client(settings, store).patch("/api/settings/model", json=payload)
    assert response.status_code == 422
    assert "「B」の接続がありません" in response.json()["detail"]
    assert not (tmp_path / "model-settings.json").exists()


def test_secondary_connection_requires_endpoint() -> None:
    payload = two_connection_payload(endpoint="", display_name="")
    enterprise = EnterpriseAiModelSettings.model_validate(payload["enterprise_ai"])
    errors = validate_enterprise_ai_connections(enterprise)
    assert [(e.field, e.message.split(" の")[0]) for e in errors] == [
        ("connections.1.endpoint", "接続 2")
    ]


def test_removing_secondary_clears_its_settings_and_key(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    client = make_client(settings, store)
    client.patch("/api/settings/model", json=two_connection_payload())

    payload = two_connection_payload()
    payload["enterprise_ai"]["connections"].pop()
    payload["enterprise_ai"]["models"][1]["connection_id"] = "primary"
    payload["enterprise_ai"]["connections"][0]["api_key"] = ""
    response = client.patch("/api/settings/model", json=payload)

    assert response.status_code == 200, response.text
    env = dotenv_values(tmp_path / ".env")
    assert env[ENTERPRISE_AI_API_KEY_ENV] == "sk-primary"
    assert ENTERPRISE_AI_SECONDARY_API_KEY_ENV not in env
    assert settings.oci_enterprise_ai_secondary_endpoint == ""
    assert settings.oci_enterprise_ai_secondary_api_key == ""
    assert [c.connection_id for c in enterprise_ai_connections(settings)] == ["primary"]


def test_blank_secondary_key_keeps_current_and_clear_removes_it(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    client = make_client(settings, store)
    client.patch("/api/settings/model", json=two_connection_payload())

    client.patch("/api/settings/model", json=two_connection_payload(api_key=""))
    assert settings.oci_enterprise_ai_secondary_api_key == "sk-secondary"

    data = client.patch(
        "/api/settings/model", json=two_connection_payload(api_key="", clear_api_key=True)
    ).json()["data"]
    assert data["settings"]["enterprise_ai"]["connections"][1]["has_api_key"] is False
    assert ENTERPRISE_AI_SECONDARY_API_KEY_ENV not in dotenv_values(tmp_path / ".env")
    # 接続 1 の key は変わらない
    assert settings.oci_enterprise_ai_api_key == "sk-primary"


def test_other_worker_picks_up_secondary_key(tmp_path: Path) -> None:
    store_a, store_b = make_store(tmp_path), make_store(tmp_path)
    worker_a, worker_b = FakeSettings(), FakeSettings()
    store_a.load(worker_a)
    store_b.load(worker_b)
    make_client(worker_a, store_a).patch("/api/settings/model", json=two_connection_payload())
    make_client(worker_b, store_b).get("/api/settings/model")
    assert worker_b.oci_enterprise_ai_secondary_api_key == "sk-secondary"
    assert enterprise_ai_connection_for_model(worker_b, "vlm-b").endpoint == SECONDARY_ENDPOINT


def test_model_test_uses_connection_of_tested_model(tmp_path: Path) -> None:
    """接続テストは、テストするモデルの接続（保存前の入力値）で呼ぶ。"""
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    seen: list[tuple[str, str, str]] = []

    async def run_test(candidate: Any, request: ModelSettingsTestRequest) -> dict[str, Any]:
        connection = enterprise_ai_connection_for_model(candidate, request.model_id)
        seen.append((request.model_id, connection.endpoint, connection.api_key))
        raise RuntimeError(f"401 for {connection.api_key}")

    client = make_client(settings, store, run_model_test=run_test)
    for model_id, target in (("llm-a", "enterprise_text"), ("vlm-b", "enterprise_vision")):
        data = client.post(
            "/api/settings/model/test",
            json={
                "settings": two_connection_payload(),
                "target_type": target,
                "model_id": model_id,
            },
        ).json()["data"]
        assert "sk-" not in data["raw_error"]
    assert seen == [
        ("llm-a", PRIMARY_ENDPOINT, "sk-primary"),
        ("vlm-b", SECONDARY_ENDPOINT, "sk-secondary"),
    ]
    # テストは保存しない
    assert settings.oci_enterprise_ai_secondary_endpoint == ""


def test_model_payload_hides_keys_of_both_connections() -> None:
    settings = FakeSettings(
        oci_enterprise_ai_api_key="sk-primary",
        oci_enterprise_ai_secondary_endpoint=SECONDARY_ENDPOINT,
        oci_enterprise_ai_secondary_api_key="sk-secondary",
    )
    dumped = shared_model.model_payload(settings).model_dump_json()
    assert "sk-primary" not in dumped and "sk-secondary" not in dumped
