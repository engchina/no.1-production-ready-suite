"""seed（#1434）が NL2SQL の Enterprise AI の呼び出しと Select AI の AI profile に入ること。

- Enterprise AI: 共通のモデル設定の seed（既定 42）を top-level の `seed` で送る
- Select AI / Select AI Agent: 業務プロファイルの Select AI 設定の seed（既定 42）を AI profile の
  `seed` 属性に入れる（Select AI Agent は同じ profile を使う）

HTTP は httpx.MockTransport（決定論）で受け、Oracle へは接続しない。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.features.nl2sql.enterprise_ai_client import OciEnterpriseAiDirectClient
from app.features.nl2sql.models import (
    SELECT_AI_SEED_DEFAULT,
    SELECT_AI_SEED_MAX,
    Nl2SqlProfile,
    ProfilePatchRequest,
    ProfileSelectAiConfig,
)
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.settings import EnterpriseAiConfiguredModel, Settings

ENDPOINT = "https://primary.example/openai/v1"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "oci_enterprise_ai_endpoint": ENDPOINT,
        "oci_enterprise_ai_api_key": "sk-primary",
        "oci_enterprise_ai_models": [
            EnterpriseAiConfiguredModel(model_id="text-a"),
            EnterpriseAiConfiguredModel(model_id="vision-b", vision_enabled=True),
        ],
        "oci_enterprise_ai_default_text_model": "text-a",
        "oci_enterprise_ai_default_vision_model": "vision-b",
        "oci_enterprise_ai_max_retries": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def payloads(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """同期の httpx.Client を MockTransport に差し替え、送った payload を記録する。"""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"output_text": json.dumps({"text": "ok"})})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return sent


def test_text_and_image_calls_send_default_seed_42(payloads: list[dict[str, Any]]) -> None:
    client = OciEnterpriseAiDirectClient(_settings())

    client.generate(prompt="質問", context="文脈", system_prompt="指示")
    client.generate_from_image(b"\x89PNG", "読んで")

    assert [payload["seed"] for payload in payloads] == [42, 42]


def test_calls_send_configured_seed(payloads: list[dict[str, Any]]) -> None:
    client = OciEnterpriseAiDirectClient(_settings(oci_enterprise_ai_seed=7))

    client.generate(prompt="質問", context="文脈", system_prompt="指示")
    client.generate_from_image(b"\x89PNG", "読んで")

    assert [payload["seed"] for payload in payloads] == [7, 7]


def test_payload_template_is_left_to_template(payloads: list[dict[str, Any]]) -> None:
    """payload template の構成では、template が送る形をそのまま使う（`seed` を足さない）。"""
    template = json.dumps({"model": "{{model}}", "input": "{{user_message}}"})
    client = OciEnterpriseAiDirectClient(_settings(oci_enterprise_ai_llm_payload_template=template))

    client.generate(prompt="質問", context="文脈", system_prompt="指示")

    assert "seed" not in payloads[0]


def test_settings_rejects_out_of_range_seed() -> None:
    with pytest.raises(ValidationError):
        _settings(oci_enterprise_ai_seed=-1)


def test_select_ai_profile_attributes_carry_seed() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    base = Nl2SqlProfile(id="seed-default", name="seed の既定")

    assert SELECT_AI_SEED_DEFAULT == 42
    assert base.select_ai_config.seed == 42
    assert service.build_select_ai_profile_attributes(base)["seed"] == 42

    custom = base.model_copy(update={"select_ai_config": ProfileSelectAiConfig(seed=7)})
    assert service.build_select_ai_profile_attributes(custom)["seed"] == 7


def test_adapter_profile_attributes_carry_default_seed() -> None:
    """Select AI Agent の assets の profile の属性（adapter が組み立てる）も既定の 42。"""
    adapter = OracleNl2SqlAdapter(_settings())

    attributes = adapter._select_ai_profile_attributes(  # noqa: SLF001
        allowed_tables=["SALES"], row_limit=None, description=""
    )

    assert attributes["seed"] == 42


def test_stored_profile_without_seed_reads_default() -> None:
    """#1434 より前に保存した業務プロファイル（seed なし・None）は既定の 42。"""
    assert ProfileSelectAiConfig.model_validate({}).seed == 42
    assert ProfileSelectAiConfig.model_validate({"seed": None}).seed == 42


@pytest.mark.parametrize(
    "seed",
    [-1, SELECT_AI_SEED_MAX + 1, "42", True, 1.5],
    ids=["negative", "over", "text", "bool", "float"],
)
def test_select_ai_seed_rejects_invalid_values(seed: object) -> None:
    """範囲外・整数でない値は検証エラー（AI profile の CREATE_PROFILE が拒む前に止める）。"""
    with pytest.raises(ValidationError):
        ProfileSelectAiConfig.model_validate({"seed": seed})
    with pytest.raises(ValidationError):
        ProfilePatchRequest.model_validate({"select_ai_config": {"seed": seed}})


def test_patch_without_seed_keeps_it_unset() -> None:
    patch = ProfilePatchRequest.model_validate({"select_ai_config": {"model": "m"}})
    assert patch.select_ai_config is not None
    assert patch.select_ai_config.seed is None
