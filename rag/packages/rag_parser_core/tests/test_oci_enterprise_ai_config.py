"""OCI Enterprise AI(Vision)parser の env 解決(#211)。"""

from __future__ import annotations

from rag_parser_core.oci_enterprise_ai import OciEnterpriseAiConfig


def test_config_from_env_reads_platform_names() -> None:
    """共通設定は backend と同じ PLATFORM_OCI_* を読む。"""
    config = OciEnterpriseAiConfig.from_env(
        {
            "PLATFORM_OCI_ENTERPRISE_AI_ENDPOINT": "https://inference.example/openai/v1",
            "PLATFORM_OCI_ENTERPRISE_AI_API_KEY": "sk-test",
            "PLATFORM_OCI_ENTERPRISE_AI_PROJECT_OCID": "ocid1.generativeaiproject.oc1..x",
            "PLATFORM_OCI_COMPARTMENT_ID": "ocid1.compartment.oc1..x",
            "PLATFORM_OCI_ENTERPRISE_AI_VLM_MODEL": "vendor.vision",
            "PLATFORM_OCI_ENTERPRISE_AI_LLM_MODEL": "vendor.llm",
            "PLATFORM_OCI_ENTERPRISE_AI_TIMEOUT_SECONDS": "30",
        }
    )

    assert config.oci_enterprise_ai_endpoint == "https://inference.example/openai/v1"
    assert config.oci_enterprise_ai_api_key == "sk-test"
    assert config.oci_compartment_id == "ocid1.compartment.oc1..x"
    assert config.vision_model_id == "vendor.vision"
    assert config.default_model_id == "vendor.llm"
    assert config.oci_enterprise_ai_timeout_seconds == 30.0


def test_config_from_env_ignores_legacy_names() -> None:
    """旧名(接頭辞なし)は読まない。"""
    config = OciEnterpriseAiConfig.from_env(
        {
            "OCI_ENTERPRISE_AI_ENDPOINT": "https://legacy.example",
            "OCI_ENTERPRISE_AI_API_KEY": "sk-legacy",
        }
    )

    assert config.oci_enterprise_ai_endpoint == ""
    assert config.oci_enterprise_ai_api_key == ""
