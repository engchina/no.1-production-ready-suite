"""LLM の呼び出しの seed（#1434。既定 42）が組み込み Runtime と接続テストに渡ること。

OCI へは接続しない（モデルは差し替え、HTTP は httpx.MockTransport）。
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import httpx
import pytest
from agents.testing import ScriptedModel
from pr_system_settings.model import (
    EnterpriseAiConfiguredModel,
    ModelSettingsTestRequest,
    model_test_candidate,
)

import app.features.agent.router as agent_router
from app.features.agent import builtin_runtime
from app.features.agent.builtin_runtime import ModelTarget
from app.settings import Settings

ENDPOINT = "https://primary.example/openai/v1"


def _target(*, seed: int = 42, effort: str = "medium") -> ModelTarget:
    return ModelTarget(
        model_id="m",
        endpoint=ENDPOINT,
        project_ocid="",
        api_key="k",
        reasoning_effort=effort,
        seed=seed,
    )


@pytest.mark.parametrize("effort", ["medium", "none"], ids=["with-reasoning", "no-reasoning"])
def test_model_settings_send_seed_in_extra_body(effort: str) -> None:
    """seed は推論の量の有無にかかわらず、SDK の extra_body（top-level の `seed`）で送る。"""
    assert builtin_runtime.model_settings_for(_target(effort=effort)).extra_body == {"seed": 42}
    assert builtin_runtime.model_settings_for(_target(seed=7, effort=effort)).extra_body == {
        "seed": 7
    }


def test_resolve_model_target_reads_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    def settings(**overrides: Any) -> Settings:
        return Settings(
            _env_file=None,
            oci_enterprise_ai_endpoint=ENDPOINT,
            oci_enterprise_ai_api_key="sk-test",
            oci_enterprise_ai_models=[EnterpriseAiConfiguredModel(model_id="text-a")],
            oci_enterprise_ai_default_text_model="text-a",
            **overrides,
        )

    default = settings()
    monkeypatch.setattr(builtin_runtime, "get_settings", lambda: default)
    assert builtin_runtime.resolve_model_target("").seed == 42

    custom = settings(oci_enterprise_ai_seed=7)
    monkeypatch.setattr(builtin_runtime, "get_settings", lambda: custom)
    assert builtin_runtime.resolve_model_target("").seed == 7


def test_sdk_agent_sends_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        builtin_runtime, "resolve_model_target", lambda _model_id="": _target(seed=9)
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: ScriptedModel([]))

    agent = builtin_runtime.build_sdk_agent(
        "run-seed", name="a", instructions="指示", skill_ids=[], model_id="m"
    )

    assert agent.model_settings.extra_body == {"seed": 9}


def _payload(seed: int | None) -> dict[str, Any]:
    enterprise: dict[str, Any] = {
        "connections": [{"connection_id": "primary", "endpoint": ENDPOINT, "api_key": "sk"}],
        "models": [
            {"model_id": "text-a"},
            {"model_id": "vision-b", "vision_enabled": True},
        ],
        "default_text_model_id": "text-a",
        "default_vision_model_id": "vision-b",
        "text_response_path": "/output_text",
        "vision_response_path": "/output_text",
    }
    if seed is not None:
        enterprise["seed"] = seed
    return {"enterprise_ai": enterprise, "generative_ai": {}}


@pytest.mark.parametrize(
    ("target_type", "model_id", "seed", "expected"),
    [
        ("enterprise_text", "text-a", None, 42),
        ("enterprise_text", "text-a", 7, 7),
        ("enterprise_vision", "vision-b", 7, 7),
    ],
    ids=["text-default", "text-7", "vision-7"],
)
def test_model_test_sends_unsaved_seed(
    monkeypatch: pytest.MonkeyPatch,
    target_type: str,
    model_id: str,
    seed: int | None,
    expected: int,
) -> None:
    """「テスト」は保存前の seed で呼ぶ。"""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"output_text": "ok"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    request = ModelSettingsTestRequest.model_validate(
        {"settings": _payload(seed), "target_type": target_type, "model_id": model_id}
    )
    candidate = model_test_candidate(Settings(_env_file=None), request)

    anyio.run(agent_router._run_model_settings_test, candidate, request)

    assert sent[0]["model"] == model_id
    assert sent[0]["seed"] == expected
