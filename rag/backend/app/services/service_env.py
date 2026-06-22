"""dev の uv runner が OCI parser microservice へ渡す環境変数を組み立てる。

OCI parser microservice(oci_genai_vision / oci_document_understanding)は ``os.environ``
(``*Config.from_env``)から OCI 設定を読む。しかし backend の有効設定は ``Settings``
(``.env`` + ``model-settings.json`` の UI 上書き)であり、``os.environ`` には載らない
(pydantic-settings は env_file を Settings へ読むだけ)。

そのため dev の ``UvProcessDriver`` は profile=oci のサービスを起動するとき、本モジュールで
backend Settings から作った env を子プロセスへ渡し、UI / ``.env`` で設定した OCI 値を
microservice へ確実に届ける。prod(docker)は compose の ``env_file: backend/.env`` が等価。
"""

from __future__ import annotations

import json

from app.config import (
    Settings,
    enterprise_ai_default_model_id,
    enterprise_ai_vision_model_id,
)


def oci_service_env(settings: Settings) -> dict[str, str]:
    """OCI parser microservice が ``from_env`` で読む OCI 設定を backend Settings から作る。

    モデル ID は model catalog 解決済みの値を渡す(microservice 側は単一 ID を読む)。
    値は env 文字列にし、list(features)は JSON 文字列、数値は str 化する。
    """
    features = [str(item) for item in getattr(settings, "oci_document_understanding_features", [])]
    env: dict[str, str] = {
        # --- OCI 共通 / 認証 ---
        "OCI_CONFIG_FILE": str(settings.oci_config_file),
        "OCI_CONFIG_PROFILE": str(settings.oci_config_profile),
        "OCI_REGION": str(settings.oci_region),
        "OCI_COMPARTMENT_ID": str(settings.oci_compartment_id),
        "OBJECT_STORAGE_REGION": str(settings.object_storage_region),
        "OBJECT_STORAGE_NAMESPACE": str(settings.object_storage_namespace),
        "OBJECT_STORAGE_BUCKET": str(settings.object_storage_bucket),
        # --- OCI Generative AI (Vision) ---
        "OCI_ENTERPRISE_AI_ENDPOINT": str(settings.oci_enterprise_ai_endpoint),
        "OCI_ENTERPRISE_AI_API_KEY": str(settings.oci_enterprise_ai_api_key),
        "OCI_ENTERPRISE_AI_PROJECT_OCID": str(settings.oci_enterprise_ai_project_ocid),
        "OCI_ENTERPRISE_AI_VLM_MODEL": enterprise_ai_vision_model_id(settings),
        "OCI_ENTERPRISE_AI_DEFAULT_MODEL": enterprise_ai_default_model_id(settings),
        "OCI_ENTERPRISE_AI_VLM_PATH": str(settings.oci_enterprise_ai_vlm_path),
        "OCI_ENTERPRISE_AI_VLM_RESPONSE_PATH": str(settings.oci_enterprise_ai_vlm_response_path),
        "OCI_ENTERPRISE_AI_VLM_PAYLOAD_TEMPLATE": str(
            settings.oci_enterprise_ai_vlm_payload_template
        ),
        "OCI_ENTERPRISE_AI_VLM_INPUT_MODE": str(
            getattr(settings, "oci_enterprise_ai_vlm_input_mode", "files_api")
        ),
        "OCI_ENTERPRISE_AI_VLM_MAX_OUTPUT_TOKENS": str(
            getattr(settings, "oci_enterprise_ai_vlm_max_output_tokens", 65536)
        ),
        "OCI_ENTERPRISE_AI_TIMEOUT_SECONDS": str(settings.oci_enterprise_ai_timeout_seconds),
        "OCI_ENTERPRISE_AI_MAX_RETRIES": str(settings.oci_enterprise_ai_max_retries),
        # --- OCI Document Understanding ---
        "OCI_DOCUMENT_UNDERSTANDING_COMPARTMENT_ID": str(
            settings.oci_document_understanding_compartment_id
        ),
        "OCI_DOCUMENT_UNDERSTANDING_NAMESPACE": str(settings.oci_document_understanding_namespace),
        "OCI_DOCUMENT_UNDERSTANDING_INPUT_BUCKET": str(
            settings.oci_document_understanding_input_bucket
        ),
        "OCI_DOCUMENT_UNDERSTANDING_OUTPUT_BUCKET": str(
            settings.oci_document_understanding_output_bucket
        ),
        "OCI_DOCUMENT_UNDERSTANDING_INPUT_PREFIX": str(
            settings.oci_document_understanding_input_prefix
        ),
        "OCI_DOCUMENT_UNDERSTANDING_OUTPUT_PREFIX": str(
            settings.oci_document_understanding_output_prefix
        ),
        "OCI_DOCUMENT_UNDERSTANDING_LANGUAGE": str(settings.oci_document_understanding_language),
        "OCI_DOCUMENT_UNDERSTANDING_FEATURES": json.dumps(features),
        "OCI_DOCUMENT_UNDERSTANDING_POLL_INTERVAL_SECONDS": str(
            settings.oci_document_understanding_poll_interval_seconds
        ),
        "OCI_DOCUMENT_UNDERSTANDING_TIMEOUT_SECONDS": str(
            settings.oci_document_understanding_timeout_seconds
        ),
    }
    return env
