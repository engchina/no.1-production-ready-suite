"""dev uv runner が OCI microservice へ渡す env(service_env)の検証。

backend Settings → oci_service_env → microservice の from_env → is_configured まで
往復し、UI/.env で設定した OCI 値が microservice へ届く(縮退→稼働中になる)ことを担保する。
"""

from __future__ import annotations

from rag_parser_core.oci_document_understanding import OciDocumentUnderstandingConfig
from rag_parser_core.oci_enterprise_ai import OciEnterpriseAiConfig

from app.config import Settings, get_settings
from app.services.service_env import oci_service_env


def _configured() -> Settings:
    return get_settings().model_copy(
        update={
            "oci_compartment_id": "ocid1.compartment.oc1..test",
            "object_storage_namespace": "ns",
            "object_storage_bucket": "bkt",
            # DU 専用 override は空 → fallback(compartment/object_storage)で充足する
            "oci_document_understanding_compartment_id": "",
            "oci_document_understanding_namespace": "",
            "oci_document_understanding_input_bucket": "",
            # Enterprise AI (Vision)
            "oci_enterprise_ai_endpoint": "https://inference.generativeai.example.com",
            "oci_enterprise_ai_api_key": "key-123",
            "oci_enterprise_ai_llm_model": "",
            "oci_enterprise_ai_vlm_model": "meta.llama-3.2-90b-vision-instruct",
            "oci_enterprise_ai_models": [],
        }
    )


def _unconfigured() -> Settings:
    return get_settings().model_copy(
        update={
            "oci_compartment_id": "",
            "object_storage_namespace": "",
            "object_storage_bucket": "",
            "oci_document_understanding_compartment_id": "",
            "oci_document_understanding_namespace": "",
            "oci_document_understanding_input_bucket": "",
            "oci_enterprise_ai_endpoint": "",
            "oci_enterprise_ai_api_key": "",
            "oci_enterprise_ai_llm_model": "",
            "oci_enterprise_ai_vlm_model": "",
            "oci_enterprise_ai_models": [],
        }
    )


def test_oci_service_env_roundtrips_to_configured_microservice() -> None:
    """設定済み Settings → env → microservice config が configured になる。"""
    env = oci_service_env(_configured())

    du = OciDocumentUnderstandingConfig.from_env(env)
    assert du.is_configured() is True
    assert du.resolve_compartment_id() == "ocid1.compartment.oc1..test"
    assert du.resolve_namespace() == "ns"
    assert du.resolve_input_bucket() == "bkt"

    vlm = OciEnterpriseAiConfig.from_env(env)
    assert vlm.oci_enterprise_ai_endpoint == "https://inference.generativeai.example.com"
    assert vlm.oci_enterprise_ai_api_key == "key-123"
    assert vlm.vision_model_id == "meta.llama-3.2-90b-vision-instruct"


def test_oci_service_env_unconfigured_stays_degraded() -> None:
    """未設定 Settings → env → microservice config は未充足のまま(縮退)。"""
    env = oci_service_env(_unconfigured())
    assert OciDocumentUnderstandingConfig.from_env(env).is_configured() is False
    vlm = OciEnterpriseAiConfig.from_env(env)
    assert vlm.oci_enterprise_ai_endpoint == ""
    assert vlm.oci_enterprise_ai_api_key == ""
    assert vlm.vision_model_id == ""


def test_oci_service_env_contains_expected_keys() -> None:
    """OCI 両サービスが from_env で読む主要キーを含む。"""
    env = oci_service_env(_configured())
    for key in (
        "OCI_ENTERPRISE_AI_ENDPOINT",
        "OCI_ENTERPRISE_AI_API_KEY",
        "OCI_ENTERPRISE_AI_VLM_MODEL",
        "OCI_COMPARTMENT_ID",
        "OBJECT_STORAGE_NAMESPACE",
        "OBJECT_STORAGE_BUCKET",
        "OCI_DOCUMENT_UNDERSTANDING_FEATURES",
        "OCI_CONFIG_FILE",
    ):
        assert key in env
