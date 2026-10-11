"""LLM / VLM の呼び出しの seed（#1434。共通のモデル設定、既定 42）が、RAG の各呼び出しに入ること。

HTTP は決定論の fake transport で受ける（OCI へは接続しない）。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from rag_engine.adapters import oci as engine_oci
from rag_engine.config import get_settings as engine_get_settings
from rag_engine.models.llm import TextOutput
from rag_parser_core.oci_enterprise_ai import OciEnterpriseAiConfig

from app.clients.oci_enterprise_ai import OciEnterpriseAiClient, config_from_settings
from app.config import Settings
from app.rag.answer_engine import build_engine_settings
from app.services.control import SERVICE_RUNTIME_ENV_KEYS, service_runtime_env
from tests.test_oci_enterprise_ai import FakeEnterpriseAiTransport, _oci_settings

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _settings(seed: int | None = None) -> Settings:
    settings = _oci_settings()
    if seed is not None:
        settings.oci_enterprise_ai_seed = seed
    return settings


async def test_text_and_image_calls_send_default_seed_42() -> None:
    transport = FakeEnterpriseAiTransport({"output_text": "回答"})
    client = OciEnterpriseAiClient(settings=_settings(), http_transport=transport)

    await client.generate("質問", "根拠")
    await client.generate_from_image(PNG, "説明して", mime_type="image/png")
    await client.generate_from_images([PNG], "説明して", mime_type="image/png")

    assert [call["payload"]["seed"] for call in transport.calls] == [42, 42, 42]


async def test_calls_send_configured_seed() -> None:
    transport = FakeEnterpriseAiTransport({"output_text": "回答"})
    client = OciEnterpriseAiClient(settings=_settings(7), http_transport=transport)

    await client.generate("質問", "根拠")
    await client.generate_from_image(PNG, "説明して", mime_type="image/png")

    assert [call["payload"]["seed"] for call in transport.calls] == [7, 7]


def test_config_and_parser_env_carry_seed() -> None:
    """共有 client の config と、parser（OCI の VLM。env から読む）の env に同じ値が入る。"""
    assert config_from_settings(_settings()).seed == 42
    config = config_from_settings(_settings(7))
    assert config.seed == 7
    assert config.for_vision().seed == 7

    env = service_runtime_env(_settings(7))
    assert env["PLATFORM_OCI_ENTERPRISE_AI_SEED"] == "7"
    assert set(env) <= set(SERVICE_RUNTIME_ENV_KEYS)
    assert OciEnterpriseAiConfig.from_env(env).seed == 7
    # env に無ければ既定の 42。
    assert OciEnterpriseAiConfig.from_env({}).seed == 42


def test_answer_engine_settings_carry_seed(tmp_path: Path) -> None:
    assert build_engine_settings(_settings(), output_dir=tmp_path).llm_seed == 42
    assert build_engine_settings(_settings(7), output_dir=tmp_path).llm_seed == 7
    assert engine_get_settings(environ={}, dotenv_path=None).llm_seed == 42


class _Responses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(status="completed", output_parsed=TextOutput(text="ok"))


def test_engine_parse_sends_seed_in_extra_body() -> None:
    """rag_engine の Responses API（`responses.parse`）は seed を extra_body（top-level）で送る。"""
    responses = _Responses()
    client = SimpleNamespace(responses=responses)
    provider = SimpleNamespace(model="m", label="m", reasoning_effort="")
    settings = replace(engine_get_settings(environ={}, dotenv_path=None), llm_seed=7)

    with mock.patch.object(engine_oci, "_openai_client_for_provider", return_value=client):
        engine_oci._openai_parsed([], settings, TextOutput, provider)  # type: ignore[arg-type]

    assert responses.calls[0]["extra_body"] == {"seed": 7}
