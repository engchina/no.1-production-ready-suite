"""SDKを呼ばずにembedding/rerankの接続先リージョンを確認する。"""

import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.clients import oci_genai
from app.config import Settings


def test_embedding_and_rerank_use_separate_regions_and_refresh_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, Any]] = []
    auth = {"region": "eu-frankfurt-1", "user": "synthetic-user"}
    settings = Settings(
        oci_genai_embedding_region="ap-tokyo-1", oci_genai_rerank_region="us-chicago-1"
    )
    monkeypatch.setattr(oci_genai, "load_oci_config_without_prompt", lambda *_a: auth)
    original_import = importlib.import_module

    def factory(config: dict[str, Any], **kwargs: Any) -> Any:
        record = {"config": config, **kwargs}
        created.append(record)
        return SimpleNamespace(**record)

    def fake_import(name: str) -> Any:
        if name == "oci.config":
            return SimpleNamespace()
        if name == "oci.generative_ai_inference":
            return SimpleNamespace(GenerativeAiInferenceClient=factory)
        if name == "oci.retry":
            return SimpleNamespace(NoneRetryStrategy=object)
        if name == "oci.circuit_breaker":
            return SimpleNamespace(NoCircuitBreakerStrategy=object)
        return original_import(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    client = oci_genai.OciGenAiClient(settings=settings)
    embedding = client._client("embedding")
    rerank = client._client("rerank")
    assert embedding is not rerank
    assert client._client("embedding") is embedding
    assert client._client("rerank") is rerank
    assert [item["config"]["region"] for item in created] == ["ap-tokyo-1", "us-chicago-1"]
    assert [item["service_endpoint"] for item in created] == [
        "https://inference.generativeai.ap-tokyo-1.oci.oraclecloud.com",
        "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
    ]
    assert auth["region"] == "eu-frankfurt-1"
    settings.oci_genai_embedding_region = "ap-osaka-1"
    assert client._client("embedding") is not embedding
    assert client._client("rerank") is rerank
    assert created[-1]["config"]["region"] == "ap-osaka-1"
    assert len(created) == 3


async def test_changing_embedding_region_does_not_reuse_another_regions_result() -> None:
    from tests.test_oci_genai import CountingEmbeddingClient

    settings = Settings.model_construct(
        oci_genai_embedding_dim=3, oci_genai_embedding_region="ap-tokyo-1"
    )
    client = CountingEmbeddingClient(settings=settings)
    first = await client.embed(["合成の本文"])
    assert await client.embed(["合成の本文"]) == first
    assert client.calls == 1
    settings.oci_genai_embedding_region = "ap-osaka-1"
    await client.embed(["合成の本文"])
    assert client.calls == 2
    settings.oci_genai_embedding_region = "ap-tokyo-1"
    assert await client.embed(["合成の本文"]) == first
    assert client.calls == 2


async def test_changing_rerank_region_does_not_reuse_another_regions_result() -> None:
    from tests.test_oci_genai import CountingRerankClient

    client = CountingRerankClient()
    client._settings.oci_genai_rerank_region = "ap-tokyo-1"
    first = await client.rerank("合成の質問", ["合成の本文"], top_n=1)
    assert await client.rerank("合成の質問", ["合成の本文"], top_n=1) == first
    assert client.calls == 1
    client._settings.oci_genai_rerank_region = "us-chicago-1"
    await client.rerank("合成の質問", ["合成の本文"], top_n=1)
    assert client.calls == 2
    client._settings.oci_genai_rerank_region = "ap-tokyo-1"
    assert await client.rerank("合成の質問", ["合成の本文"], top_n=1) == first
    assert client.calls == 2


def test_product_model_regions_default_to_osaka() -> None:
    settings = Settings()
    assert settings.oci_genai_embedding_region == "ap-osaka-1"
    assert settings.oci_genai_rerank_region == "ap-osaka-1"


@pytest.mark.parametrize("field", ["oci_genai_embedding_region", "oci_genai_rerank_region"])
def test_product_rejects_unsupported_model_region(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: "eu-frankfurt-1"})
