"""LLM / VLM の呼び出しの seed（#1434）。

全モデルで共通の 1 つで、既定は 42。範囲（0〜2147483647）の外は保存（422）でも読み込みでも
検証エラー。値の解釈は `seed_to_send` / `enterprise_ai_seed` / `seed_payload` の 1 か所。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_model import PAYLOAD, FakeSettings, make_client, make_store

from pr_system_settings.model import (
    DEFAULT_ENTERPRISE_AI_SEED,
    ENTERPRISE_AI_SEED_MAX,
    ENTERPRISE_AI_SEED_MIN,
    EnterpriseAiModelSettings,
    ModelSettingsTestRequest,
    enterprise_ai_seed,
    model_payload,
    seed_payload,
    seed_to_send,
)


def _payload_with_seed(seed: object) -> dict[str, Any]:
    payload = copy.deepcopy(PAYLOAD)
    payload["enterprise_ai"]["seed"] = seed
    return payload


def test_default_is_42() -> None:
    assert DEFAULT_ENTERPRISE_AI_SEED == 42
    assert (ENTERPRISE_AI_SEED_MIN, ENTERPRISE_AI_SEED_MAX) == (0, 2_147_483_647)
    assert EnterpriseAiModelSettings().seed == 42
    # 値なし（None）も既定。
    assert EnterpriseAiModelSettings.model_validate({"seed": None}).seed == 42
    settings = FakeSettings()
    assert enterprise_ai_seed(settings) == 42
    assert seed_payload(settings) == {"seed": 42}
    assert model_payload(settings).enterprise_ai.seed == 42


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (42, 42),
        (0, 0),
        (ENTERPRISE_AI_SEED_MAX, ENTERPRISE_AI_SEED_MAX),
        (" 7 ", 7),
        (None, 42),
        ("", 42),
        (-1, 42),
        (ENTERPRISE_AI_SEED_MAX + 1, 42),
        ("abc", 42),
        (True, 42),
        (1.5, 42),
    ],
    ids=["42", "min", "max", "text", "none", "empty", "negative", "over", "abc", "bool", "float"],
)
def test_seed_to_send(value: object, expected: int) -> None:
    """解釈はここだけ: 範囲の中の整数はその値、それ以外（env の直書きなど）は既定の 42。"""
    assert seed_to_send(value) == expected


def test_seed_is_saved_and_reloaded(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)

    response = make_client(settings, store).patch("/api/settings/model", json=_payload_with_seed(7))

    assert response.status_code == 200
    assert response.json()["data"]["settings"]["enterprise_ai"]["seed"] == 7
    document = json.loads((tmp_path / "model-settings.json").read_text(encoding="utf-8"))
    assert document["enterprise_ai"]["seed"] == 7
    assert enterprise_ai_seed(settings) == 7
    # 別の worker も同じ値を読む。
    other = FakeSettings()
    make_store(tmp_path).load(other)
    assert enterprise_ai_seed(other) == 7
    assert seed_payload(other) == {"seed": 7}


def test_document_without_seed_is_read_as_default_42(tmp_path: Path) -> None:
    """#1434 より前の JSON（項目なし）は既定の 42 を送る。"""
    (tmp_path / "model-settings.json").write_text(
        json.dumps(
            {
                "version": 4,
                "enterprise_ai": {
                    "models": [{"model_id": "llm-a", "display_name": "A"}],
                    "default_text_model_id": "llm-a",
                },
            }
        ),
        encoding="utf-8",
    )
    settings = FakeSettings(oci_enterprise_ai_seed=7)
    make_store(tmp_path).load(settings)

    assert enterprise_ai_seed(settings) == 42


@pytest.mark.parametrize(
    "seed",
    [-1, ENTERPRISE_AI_SEED_MAX + 1, "abc", True],
    ids=["negative", "over", "abc", "bool"],
)
def test_document_with_invalid_seed_is_rejected(tmp_path: Path, seed: object) -> None:
    """範囲外・整数でない値は、読み込みでも検証エラーにする（既定に寄せない）。"""
    (tmp_path / "model-settings.json").write_text(
        json.dumps({"version": 4, "enterprise_ai": {"seed": seed}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="モデル設定ファイルを読み込めません"):
        make_store(tmp_path).load(FakeSettings())


@pytest.mark.parametrize(
    "seed",
    [-1, ENTERPRISE_AI_SEED_MAX + 1, "abc", True, 1.5],
    ids=["negative", "over", "abc", "bool", "float"],
)
def test_api_rejects_invalid_seed(tmp_path: Path, seed: object) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)

    response = make_client(settings, store).patch(
        "/api/settings/model", json=_payload_with_seed(seed)
    )

    assert response.status_code == 422
    assert not (tmp_path / "model-settings.json").exists()


def test_model_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        EnterpriseAiModelSettings.model_validate({"seed": ENTERPRISE_AI_SEED_MAX + 1})


def test_model_test_uses_unsaved_seed(tmp_path: Path) -> None:
    """「テスト」は保存前の seed で呼ぶ。"""
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    seen: dict[str, Any] = {}

    async def run_test(candidate: Any, request: ModelSettingsTestRequest) -> dict[str, Any]:
        seen["seed"] = seed_payload(candidate)
        return {}

    request = {
        "settings": _payload_with_seed(9),
        "target_type": "enterprise_text",
        "model_id": "llm-a",
    }
    data = (
        make_client(settings, store, run_model_test=run_test)
        .post("/api/settings/model/test", json=request)
        .json()["data"]
    )

    assert data["status"] == "success"
    assert seen["seed"] == {"seed": 9}
    # 保存済みの設定は変えない。
    assert enterprise_ai_seed(settings) == 42
