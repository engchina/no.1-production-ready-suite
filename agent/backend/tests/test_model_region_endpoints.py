"""Agentの接続確認も認証regionではなく選択regionを使う。"""

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.features.agent import router
from app.settings import Settings


@pytest.mark.parametrize("region", ["ap-tokyo-1", "ap-osaka-1", "us-chicago-1"])
def test_agent_connection_client_uses_selected_region(
    monkeypatch: pytest.MonkeyPatch, region: str
) -> None:
    auth = {"region": "eu-frankfurt-1", "user": "synthetic-user"}
    captured: dict[str, Any] = {}
    monkeypatch.setattr(router, "_load_oci_config_without_prompt", lambda *_a: auth)

    def factory(config: dict[str, Any], **kwargs: Any) -> Any:
        captured.update(config=config, **kwargs)
        return SimpleNamespace(**captured)

    def fake_import(name: str) -> Any:
        if name == "oci.config":
            return SimpleNamespace()
        if name == "oci.generative_ai_inference":
            return SimpleNamespace(GenerativeAiInferenceClient=factory)
        raise AssertionError(name)

    monkeypatch.setattr(router, "import_module", fake_import)
    router._oci_genai_inference_client(SimpleNamespace(), region=region)
    assert captured["config"]["region"] == region
    assert (
        captured["service_endpoint"]
        == f"https://inference.generativeai.{region}.oci.oraclecloud.com"
    )
    assert auth["region"] == "eu-frankfurt-1"


def test_product_model_regions_default_to_osaka() -> None:
    settings = Settings()
    assert settings.oci_genai_embedding_region == "ap-osaka-1"
    assert settings.oci_genai_rerank_region == "ap-osaka-1"


@pytest.mark.parametrize("field", ["oci_genai_embedding_region", "oci_genai_rerank_region"])
def test_product_rejects_unsupported_model_region(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: "eu-frankfurt-1"})
