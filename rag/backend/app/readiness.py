"""起動前・staging gate 用の依存設定チェック。"""

from collections.abc import Mapping
from pathlib import Path

from app.config import Settings

READINESS_OK = "ok"
READINESS_MISSING = "missing"
READINESS_INVALID = "invalid"
READINESS_MISSING_CREDENTIALS = "missing_credentials"
READINESS_WALLET_NOT_FOUND = "wallet_not_found"


def readiness_checks_are_ok(checks: Mapping[str, str]) -> bool:
    """readiness checks がすべて成功しているか判定する。"""
    return all(value == READINESS_OK for value in checks.values())


def readiness_checks(settings: Settings) -> dict[str, str]:
    """adapter mode ごとの readiness check を実行する。"""
    if settings.ai_service_adapter == "local":
        checks = _upload_storage_checks(settings)
        checks.update(_production_safety_checks(settings))
        return checks
    checks = {
        "oci_common": _required_values_check(
            settings.oci_region,
            settings.oci_compartment_id,
        ),
        "enterprise_ai": _enterprise_ai_check(settings),
        "genai": _genai_check(settings),
        "oracle": _oracle_check(settings),
    }
    checks.update(_upload_storage_checks(settings))
    checks.update(_production_safety_checks(settings))
    return checks


def oracle_readiness_check(settings: Settings) -> str:
    """Oracle 26ai 接続設定の readiness status を返す。"""
    return _oracle_check(settings)


def upload_storage_readiness_checks(settings: Settings) -> dict[str, str]:
    """アップロード原本保存先の readiness checks を返す。"""
    return _upload_storage_checks(settings)


def _production_safety_checks(settings: Settings) -> dict[str, str]:
    """production 環境で必須にする安全設定を確認する。"""
    if not _is_production(settings):
        return {}
    return {
        "deployment_adapter": (
            READINESS_OK if settings.ai_service_adapter == "oci" else READINESS_INVALID
        ),
        "audit_context_salt": (
            READINESS_OK if _is_present(settings.audit_context_hash_salt) else READINESS_MISSING
        ),
    }


def _local_storage_check(settings: Settings) -> str:
    """local adapter の保存先が作成・書き込み可能か確認する。"""
    try:
        root = Path(settings.local_storage_dir).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".readiness"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return "error"
    return READINESS_OK


def _upload_storage_checks(settings: Settings) -> dict[str, str]:
    """アップロード原本の保存先設定を確認する。"""
    if settings.upload_storage_backend == "oci":
        return {
            "object_storage": _required_values_check(
                settings.object_storage_region,
                settings.object_storage_namespace,
                settings.object_storage_bucket,
            )
        }
    return {"local_storage": _local_storage_check(settings)}


def _genai_check(settings: Settings) -> str:
    """OCI Generative AI の embedding/rerank 設定を確認する。"""
    required_status = _required_values_check(
        settings.oci_genai_embedding_model,
        settings.oci_genai_rerank_model,
    )
    if required_status != READINESS_OK:
        return required_status
    if settings.oci_genai_embedding_dim != 1536:
        return READINESS_INVALID
    return READINESS_OK


def _enterprise_ai_check(settings: Settings) -> str:
    """OCI Enterprise AI の endpoint / model または payload template を確認する。"""
    required_status = _required_values_check(
        settings.oci_enterprise_ai_endpoint,
        settings.oci_enterprise_ai_project_ocid,
        settings.oci_enterprise_ai_llm_path,
        settings.oci_enterprise_ai_vlm_path,
    )
    if required_status != READINESS_OK:
        return required_status
    if not _is_present(settings.oci_enterprise_ai_api_key):
        return READINESS_MISSING_CREDENTIALS
    if not _model_setting_is_satisfied(
        settings.oci_enterprise_ai_llm_model,
        settings.oci_enterprise_ai_llm_payload_template,
    ):
        return READINESS_MISSING
    if not _model_setting_is_satisfied(
        settings.oci_enterprise_ai_vlm_model,
        settings.oci_enterprise_ai_vlm_payload_template,
    ):
        return READINESS_MISSING
    return READINESS_OK


def _oracle_check(settings: Settings) -> str:
    """Oracle 26ai の接続設定を確認する。"""
    required_status = _required_values_check(settings.oracle_user, settings.oracle_dsn)
    if required_status != READINESS_OK:
        return required_status

    if _is_present(settings.oracle_password):
        return READINESS_OK

    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    if not _is_present(wallet_dir):
        return READINESS_MISSING_CREDENTIALS
    if not Path(wallet_dir).expanduser().is_dir():
        return READINESS_WALLET_NOT_FOUND
    return READINESS_OK


def _required_values_check(*values: str) -> str:
    """必須文字列がすべて設定済みか確認する。"""
    if all(_is_present(value) for value in values):
        return READINESS_OK
    return READINESS_MISSING


def _is_present(value: str) -> bool:
    """空白のみの値を未設定として扱う。"""
    return bool(value.strip())


def _model_setting_is_satisfied(model: str, payload_template: str) -> bool:
    """template が model を要求しない場合は model id なしを許可する。"""
    if _is_present(model):
        return True
    return _is_present(payload_template) and "${model}" not in payload_template


def _is_production(settings: Settings) -> bool:
    """ENVIRONMENT=production を production 判定に使う。"""
    return settings.environment.strip().lower() == "production"
