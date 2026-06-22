from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from app.features.nl2sql.embedding_client import OciGenAiEmbeddingClient
from app.settings import Settings


class _FakeConfig:
    @staticmethod
    def from_file(config_file: str, profile: str) -> dict[str, str]:
        return {"config_file": config_file, "profile": profile, "region": "us-chicago-1"}


class _FakeInference:
    calls: list[dict[str, Any]] = []

    class GenerativeAiInferenceClient:
        def __init__(self, **kwargs: Any) -> None:
            _FakeInference.calls.append(kwargs)


def _settings(*, auth_mode: str = "config_file") -> Settings:
    return Settings(
        oci_region="us-chicago-1",
        oci_compartment_id="ocid1.compartment.oc1..example",
        oci_config_file="/tmp/oci-config",
        oci_profile="DEFAULT",
        oci_auth_mode=auth_mode,
        oci_genai_endpoint="https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
        oci_genai_embed_model_id="cohere.embed-v4.0",
        nl2sql_feedback_embedding_enabled=True,
    )


def test_oci_genai_embedding_client_config_auth_does_not_pass_none_signer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_oci = SimpleNamespace(config=_FakeConfig())
    _FakeInference.calls = []

    def fake_import_module(name: str) -> object:
        if name == "oci":
            return fake_oci
        if name == "oci.generative_ai_inference":
            return _FakeInference
        raise AssertionError(name)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    OciGenAiEmbeddingClient(_settings())._get_client()

    assert _FakeInference.calls
    assert _FakeInference.calls[0]["config"] == {
        "config_file": "/tmp/oci-config",
        "profile": "DEFAULT",
        "region": "us-chicago-1",
    }
    assert "signer" not in _FakeInference.calls[0]


def test_oci_genai_embedding_client_principal_auth_passes_signer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = object()
    fake_oci = SimpleNamespace(
        config=_FakeConfig(),
        auth=SimpleNamespace(
            signers=SimpleNamespace(
                InstancePrincipalsSecurityTokenSigner=lambda: signer,
            )
        ),
    )
    _FakeInference.calls = []

    def fake_import_module(name: str) -> object:
        if name == "oci":
            return fake_oci
        if name == "oci.generative_ai_inference":
            return _FakeInference
        raise AssertionError(name)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    OciGenAiEmbeddingClient(_settings(auth_mode="instance_principal"))._get_client()

    assert _FakeInference.calls
    assert _FakeInference.calls[0]["config"] == {}
    assert _FakeInference.calls[0]["signer"] is signer
