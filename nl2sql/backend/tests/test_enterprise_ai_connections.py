"""OCI Enterprise AI の接続をモデルごとに選ぶ（#533）。呼び出しが対象モデルの接続を使うこと。

HTTP は httpx.MockTransport（決定論）で受け、URL・API key・Project のヘッダーを確かめる。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.features.nl2sql.enterprise_ai_client import OciEnterpriseAiDirectClient
from app.main import app
from app.settings import EnterpriseAiConfiguredModel, Settings

PRIMARY = "https://primary.example/openai/v1"
SECONDARY = "https://secondary.example/openai/v1"
PRIMARY_CONNECTION = (PRIMARY, "Bearer sk-primary", "ocid1.project.primary")
SECONDARY_CONNECTION = (SECONDARY, "Bearer sk-secondary", "ocid1.project.secondary")


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "oci_enterprise_ai_endpoint": PRIMARY,
        "oci_enterprise_ai_project_ocid": "ocid1.project.primary",
        "oci_enterprise_ai_api_key": "sk-primary",
        "oci_enterprise_ai_secondary_endpoint": SECONDARY,
        "oci_enterprise_ai_secondary_project_ocid": "ocid1.project.secondary",
        "oci_enterprise_ai_secondary_api_key": "sk-secondary",
        "oci_enterprise_ai_models": [
            EnterpriseAiConfiguredModel(model_id="text-a"),
            EnterpriseAiConfiguredModel(
                model_id="vision-b", vision_enabled=True, connection_id="secondary"
            ),
        ],
        "oci_enterprise_ai_default_text_model": "text-a",
        "oci_enterprise_ai_default_vision_model": "vision-b",
        "oci_enterprise_ai_max_retries": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """同期の httpx.Client を MockTransport に差し替え、送った接続を記録する。"""
    calls: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        base = PRIMARY if url.startswith(PRIMARY) else SECONDARY
        calls.append(
            (base, request.headers["authorization"], request.headers.get("OpenAI-Project", ""))
        )
        return httpx.Response(200, json={"output_text": json.dumps({"text": "ok"})})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return calls


def test_text_and_vision_calls_use_each_model_connection(
    recorded: list[tuple[str, str, str]],
) -> None:
    client = OciEnterpriseAiDirectClient(_settings())

    client.generate(prompt="質問", context="", system_prompt="日本語で答える")
    client.generate_from_image(b"jpeg", "説明してください")

    assert recorded == [PRIMARY_CONNECTION, SECONDARY_CONNECTION]


def test_text_model_on_secondary_uses_secondary(recorded: list[tuple[str, str, str]]) -> None:
    settings = _settings(oci_enterprise_ai_default_text_model="vision-b")
    client = OciEnterpriseAiDirectClient(settings)

    client.generate(prompt="質問", context="", system_prompt="日本語で答える")

    assert recorded == [SECONDARY_CONNECTION]


def test_is_configured_checks_text_model_connection() -> None:
    assert OciEnterpriseAiDirectClient(_settings()).is_configured() is True
    assert (
        OciEnterpriseAiDirectClient(
            _settings(
                oci_enterprise_ai_default_text_model="vision-b",
                oci_enterprise_ai_secondary_api_key="",
            )
        ).is_configured()
        is False
    )


def _post(path: str, body: dict[str, Any]) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as async_client:
            return await async_client.post(path, json=body)

    return asyncio.run(send())


@pytest.mark.parametrize(
    ("target_type", "model_id", "expected"),
    [
        ("enterprise_text", "text-a", PRIMARY_CONNECTION),
        ("enterprise_vision", "vision-b", SECONDARY_CONNECTION),
    ],
    ids=["text-primary", "vision-secondary"],
)
def test_model_settings_test_calls_connection_of_tested_model(
    recorded: list[tuple[str, str, str]],
    target_type: str,
    model_id: str,
    expected: tuple[str, str, str],
) -> None:
    """接続テストは、保存前の入力値のうち、テストするモデルの接続で呼ぶ。"""
    response = _post(
        "/api/settings/model/test",
        {
            "target_type": target_type,
            "model_id": model_id,
            "settings": {
                "enterprise_ai": {
                    "connections": [
                        {
                            "connection_id": "primary",
                            "endpoint": PRIMARY,
                            "project_ocid": "ocid1.project.primary",
                            "api_key": "sk-primary",
                        },
                        {
                            "connection_id": "secondary",
                            "endpoint": SECONDARY,
                            "project_ocid": "ocid1.project.secondary",
                            "api_key": "sk-secondary",
                        },
                    ],
                    "models": [
                        {"model_id": "text-a"},
                        {
                            "model_id": "vision-b",
                            "vision_enabled": True,
                            "connection_id": "secondary",
                        },
                    ],
                    "default_text_model_id": "text-a",
                    "default_vision_model_id": "vision-b",
                    "max_retries": 0,
                },
                "generative_ai": {
                    "embedding_model": "cohere.embed-v4.0",
                    "embedding_dim": 1536,
                    "rerank_model": "cohere.rerank-v4.0-fast",
                },
            },
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "success", response.text
    assert recorded == [expected]
    assert "sk-primary" not in response.text and "sk-secondary" not in response.text
