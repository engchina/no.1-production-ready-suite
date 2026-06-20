"""OCI Enterprise AI クライアント(共有 core の Settings adapter / re-export shim)。

正本は共有 package `rag_parser_core.oci_enterprise_ai`。backend は Settings から
`OciEnterpriseAiConfig` を組み立てて駆動する薄い adapter として本モジュールを使い、
従来の import パス(`app.clients.oci_enterprise_ai`)と公開シンボルを維持する。
parser マイクロサービス(oci_genai_vision)は共有 core を env 由来 config で使う。
"""

from __future__ import annotations

from rag_parser_core.oci_enterprise_ai import (
    DEFAULT_MIME_TYPE,
    EnterpriseAiHttpTransport,
    EnterpriseAiIncompleteResponseError,
    EnterpriseAiRequestPreview,
    EnterpriseAiTimeoutError,
    EnterpriseAiUnsupportedInputError,
    EnterpriseAiValidationError,
    OciEnterpriseAiConfig,
    _parse_planned_queries,
    _raise_for_status_with_body,
)
from rag_parser_core.oci_enterprise_ai import (
    OciEnterpriseAiClient as _SharedOciEnterpriseAiClient,
)

from app.config import (
    Settings,
    enterprise_ai_default_model_id,
    enterprise_ai_vision_model_id,
    get_settings,
)

__all__ = [
    "DEFAULT_MIME_TYPE",
    "EnterpriseAiHttpTransport",
    "EnterpriseAiIncompleteResponseError",
    "EnterpriseAiRequestPreview",
    "EnterpriseAiTimeoutError",
    "EnterpriseAiUnsupportedInputError",
    "EnterpriseAiValidationError",
    "OciEnterpriseAiClient",
    "OciEnterpriseAiConfig",
    "config_from_settings",
    "_parse_planned_queries",
    "_raise_for_status_with_body",
]


def config_from_settings(settings: Settings) -> OciEnterpriseAiConfig:
    """backend Settings から共有 core 用の config を組み立てる。

    vision/default model ID は model catalog 解決(config.py)で従来同等に決める。
    """
    return OciEnterpriseAiConfig(
        oci_enterprise_ai_endpoint=settings.oci_enterprise_ai_endpoint,
        oci_enterprise_ai_api_key=settings.oci_enterprise_ai_api_key,
        oci_enterprise_ai_project_ocid=settings.oci_enterprise_ai_project_ocid,
        oci_compartment_id=settings.oci_compartment_id,
        vision_model_id=enterprise_ai_vision_model_id(settings),
        default_model_id=enterprise_ai_default_model_id(settings),
        oci_enterprise_ai_llm_path=settings.oci_enterprise_ai_llm_path,
        oci_enterprise_ai_vlm_path=settings.oci_enterprise_ai_vlm_path,
        oci_enterprise_ai_llm_response_path=settings.oci_enterprise_ai_llm_response_path,
        oci_enterprise_ai_vlm_response_path=settings.oci_enterprise_ai_vlm_response_path,
        oci_enterprise_ai_llm_payload_template=settings.oci_enterprise_ai_llm_payload_template,
        oci_enterprise_ai_vlm_payload_template=settings.oci_enterprise_ai_vlm_payload_template,
        oci_enterprise_ai_vlm_input_mode=getattr(
            settings, "oci_enterprise_ai_vlm_input_mode", "auto"
        ),
        oci_enterprise_ai_timeout_seconds=float(settings.oci_enterprise_ai_timeout_seconds),
        oci_enterprise_ai_max_retries=int(settings.oci_enterprise_ai_max_retries),
        oci_enterprise_ai_llm_max_output_tokens=int(
            getattr(settings, "oci_enterprise_ai_llm_max_output_tokens", 1200)
        ),
        oci_enterprise_ai_vlm_max_output_tokens=int(
            getattr(settings, "oci_enterprise_ai_vlm_max_output_tokens", 65536)
        ),
    )


class OciEnterpriseAiClient(_SharedOciEnterpriseAiClient):
    """Settings から config を組み立てて共有 client を駆動する backend adapter。"""

    def __init__(
        self,
        settings: Settings | None = None,
        http_transport: EnterpriseAiHttpTransport | None = None,
    ) -> None:
        super().__init__(
            config_from_settings(settings or get_settings()),
            http_transport=http_transport,
        )
