"""OCI Generative AI embedding client boundary for NL2SQL feedback learning.

LLM/VLM は Enterprise AI、embedding は OCI Generative AI という AGENTS.md の
役割分担を保つため、feedback vector index 用の embedding 呼び出しはここに閉じ込める。
CI/local は optional import のまま deterministic に動作する。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Protocol

from app.settings import Settings


class EmbeddingClientError(RuntimeError):
    """Embedding client の実行時エラー。"""


class FeedbackEmbeddingClient(Protocol):
    """Feedback learning 用 embedding client contract."""

    def is_configured(self) -> bool:
        """Return whether live embedding execution is configured."""

    def module_available(self) -> bool:
        """Return whether the provider SDK can be imported."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed texts into 1536-dimensional vectors."""


class OciGenAiEmbeddingClient:
    """OCI Generative AI Cohere Embed v4 client.

    The exact OCI SDK objects are imported lazily to keep tests and local development
    independent from live OCI credentials.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._oci: Any | None = None
        self._client: Any | None = None

    def is_configured(self) -> bool:
        return bool(
            self.settings.nl2sql_feedback_embedding_enabled
            and self.settings.oci_region
            and self.settings.oci_compartment_id
            and self.settings.oci_genai_embed_model_id
        )

    def module_available(self) -> bool:
        try:
            self._load_oci()
        except EmbeddingClientError:
            return False
        return True

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.is_configured():
            raise EmbeddingClientError("OCI GenAI embedding 設定が不足しています。")
        client = self._get_client()
        models = self._load_models()
        details = models.EmbedTextDetails()
        details.inputs = texts
        details.compartment_id = self.settings.oci_compartment_id
        details.serving_mode = models.OnDemandServingMode(
            model_id=self.settings.oci_genai_embed_model_id
        )
        if hasattr(details, "truncate"):
            details.truncate = "END"
        if hasattr(details, "input_type"):
            details.input_type = "SEARCH_DOCUMENT"
        try:
            response = client.embed_text(details)
        except Exception as exc:  # pragma: no cover - live OCI defensive boundary
            raise EmbeddingClientError(f"OCI GenAI embedding に失敗しました: {exc}") from exc
        embeddings = getattr(response.data, "embeddings", None)
        if embeddings is None:
            raise EmbeddingClientError("OCI GenAI embedding 応答に embeddings がありません。")
        vectors = [[float(value) for value in vector] for vector in embeddings]
        for vector in vectors:
            if len(vector) != 1536:
                raise EmbeddingClientError(f"embedding 次元が 1536 ではありません: {len(vector)}")
        return vectors

    def _load_oci(self) -> Any:
        if self._oci is not None:
            return self._oci
        try:
            self._oci = importlib.import_module("oci")
        except ModuleNotFoundError as exc:
            raise EmbeddingClientError("OCI SDK がインストールされていません。") from exc
        return self._oci

    def _load_models(self) -> Any:
        try:
            return importlib.import_module("oci.generative_ai_inference.models")
        except ModuleNotFoundError as exc:
            raise EmbeddingClientError(
                "OCI Generative AI Inference models が見つかりません。"
            ) from exc

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        oci = self._load_oci()
        inference = importlib.import_module("oci.generative_ai_inference")
        endpoint = self.settings.oci_genai_endpoint or (
            f"https://inference.generativeai.{self.settings.oci_region}.oci.oraclecloud.com"
        )
        auth_mode = self.settings.oci_auth_mode.strip().lower()
        signer = None
        config: dict[str, Any] = {}
        if auth_mode == "instance_principal":
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        elif auth_mode == "resource_principal":
            signer = oci.auth.signers.get_resource_principals_signer()
        else:
            config_file = self.settings.oci_config_file or str(Path.home() / ".oci" / "config")
            config = oci.config.from_file(
                config_file,
                self.settings.resolved_oci_config_profile,
            )
        client_kwargs: dict[str, Any] = {"config": config, "service_endpoint": endpoint}
        if signer is not None:
            client_kwargs["signer"] = signer
        self._client = inference.GenerativeAiInferenceClient(**client_kwargs)
        return self._client
