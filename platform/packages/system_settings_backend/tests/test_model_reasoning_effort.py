"""登録モデルごとの推論（思考）の量 `reasoning_effort`（#1425）。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_model import PAYLOAD, FakeSettings, make_client, make_store

from pr_system_settings.model import (
    ENTERPRISE_AI_REASONING_EFFORTS,
    MODEL_SETTINGS_DOCUMENT_VERSION,
    EnterpriseAiConfiguredModel,
    ModelSettingsTestRequest,
    enterprise_ai_model_catalog,
    enterprise_ai_reasoning,
    enterprise_ai_reasoning_effort,
)


def _payload_with_effort(effort: str) -> dict[str, Any]:
    payload = copy.deepcopy(PAYLOAD)
    payload["enterprise_ai"]["models"][0]["reasoning_effort"] = effort
    return payload


def test_effort_is_saved_per_model_and_reloaded(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)

    response = make_client(settings, store).patch(
        "/api/settings/model", json=_payload_with_effort("low")
    )

    assert response.status_code == 200
    models = response.json()["data"]["settings"]["enterprise_ai"]["models"]
    assert [model["reasoning_effort"] for model in models] == ["low", ""]
    document = json.loads((tmp_path / "model-settings.json").read_text(encoding="utf-8"))
    assert document["version"] == MODEL_SETTINGS_DOCUMENT_VERSION == 4
    assert [model["reasoning_effort"] for model in document["enterprise_ai"]["models"]] == [
        "low",
        "",
    ]
    # 別の worker も同じ値を読む。
    other = FakeSettings()
    make_store(tmp_path).load(other)
    assert enterprise_ai_reasoning_effort(other, "llm-a") == "low"
    assert enterprise_ai_reasoning(other, "llm-a") == {"effort": "low"}
    assert enterprise_ai_reasoning_effort(other, "vlm-b") == ""
    assert enterprise_ai_reasoning(other, "vlm-b") is None


def test_v3_document_without_effort_is_read_as_empty(tmp_path: Path) -> None:
    """#1425 より前の JSON（version 3、項目なし）はそのまま読め、`reasoning` を送らない。"""
    (tmp_path / "model-settings.json").write_text(
        json.dumps(
            {
                "version": 3,
                "enterprise_ai": {
                    "models": [{"model_id": "llm-a", "display_name": "A"}],
                    "default_text_model_id": "llm-a",
                },
            }
        ),
        encoding="utf-8",
    )
    settings = FakeSettings()
    make_store(tmp_path).load(settings)

    assert [model.reasoning_effort for model in enterprise_ai_model_catalog(settings)] == [""]
    assert enterprise_ai_reasoning(settings, "llm-a") is None


def test_unknown_effort_in_document_is_ignored_on_load(tmp_path: Path) -> None:
    """手で書いた知らない値は読み込みを止めず、空（送らない）にする。"""
    (tmp_path / "model-settings.json").write_text(
        json.dumps(
            {
                "version": 4,
                "enterprise_ai": {
                    "models": [
                        {"model_id": "llm-a", "reasoning_effort": "ultra"},
                        {"model_id": "llm-b", "reasoning_effort": " High "},
                    ],
                    "default_text_model_id": "llm-a",
                },
            }
        ),
        encoding="utf-8",
    )
    settings = FakeSettings()
    make_store(tmp_path).load(settings)

    assert enterprise_ai_reasoning_effort(settings, "llm-a") == ""
    assert enterprise_ai_reasoning_effort(settings, "llm-b") == "high"


def test_api_rejects_unknown_effort(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)

    response = make_client(settings, store).patch(
        "/api/settings/model", json=_payload_with_effort("ultra")
    )

    assert response.status_code == 422
    assert not (tmp_path / "model-settings.json").exists()


@pytest.mark.parametrize(
    "effort", ["", *ENTERPRISE_AI_REASONING_EFFORTS], ids=lambda v: v or "empty"
)
def test_model_accepts_every_choice(effort: str) -> None:
    assert EnterpriseAiConfiguredModel(model_id="m", reasoning_effort=effort).reasoning_effort == (
        effort
    )


def test_model_rejects_values_no_model_accepts() -> None:
    with pytest.raises(ValidationError):
        EnterpriseAiConfiguredModel(model_id="m", reasoning_effort="ultra")


def test_unregistered_model_sends_no_reasoning() -> None:
    settings = FakeSettings(
        oci_enterprise_ai_models=[
            EnterpriseAiConfiguredModel(model_id="llm-a", reasoning_effort="none")
        ]
    )
    assert enterprise_ai_reasoning(settings, " llm-a ") == {"effort": "none"}
    assert enterprise_ai_reasoning(settings, "env-only-model") is None


def test_model_test_uses_unsaved_effort(tmp_path: Path) -> None:
    """「テスト」は保存前に選んだ推論の量で呼ぶ（受け付けない値を保存の前に確かめられる）。"""
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    seen: dict[str, Any] = {}

    async def run_test(candidate: Any, request: ModelSettingsTestRequest) -> dict[str, Any]:
        seen["reasoning"] = enterprise_ai_reasoning(candidate, request.model_id)
        return {}

    request = {
        "settings": _payload_with_effort("minimal"),
        "target_type": "enterprise_text",
        "model_id": "llm-a",
    }
    data = (
        make_client(settings, store, run_model_test=run_test)
        .post("/api/settings/model/test", json=request)
        .json()["data"]
    )

    assert data["status"] == "success"
    assert seen["reasoning"] == {"effort": "minimal"}
    # 保存済みの設定は変えない。
    assert enterprise_ai_reasoning(settings, "llm-a") is None
