"""依存設定の readiness チェック（/ready 用）。"""

from pathlib import Path

from app.settings import Settings

READINESS_OK = "ok"
READINESS_MISSING = "missing"
READINESS_MISSING_CREDENTIALS = "missing_credentials"
READINESS_WALLET_NOT_FOUND = "wallet_not_found"
READINESS_INVALID_CONFIGURATION = "invalid_configuration"
ORACLE_WALLET_THIN_REQUIRED_FILES = frozenset({"tnsnames.ora", "ewallet.pem"})
ORACLE_WALLET_THICK_REQUIRED_FILES = frozenset({"tnsnames.ora", "sqlnet.ora", "cwallet.sso"})


def readiness_checks(settings: Settings) -> dict[str, str]:
    """共通 readiness では Oracle 依存時だけ設定状態を返す。"""
    runtime = settings.nl2sql_runtime_mode.strip().lower()
    persistence = settings.nl2sql_persistence_mode.strip().lower()
    if runtime == "deterministic" and persistence == "memory":
        return {}
    return {"oracle": oracle_readiness_check(settings)}


def oracle_readiness_check(settings: Settings) -> str:
    """Oracle 接続に必要な非 secret 設定の状態を返す。"""
    if settings.oracle_deepsec_enabled and settings.oracle_driver_mode.strip().lower() != "thin":
        return READINESS_INVALID_CONFIGURATION
    if not settings.oracle_user.strip() or not settings.oracle_dsn.strip():
        return READINESS_MISSING
    connection_security = _oracle_connection_security(settings)
    if connection_security == "walletless_tls":
        return READINESS_OK if settings.oracle_password.strip() else READINESS_MISSING_CREDENTIALS

    if not _wallet_mtls_files_exist(settings):
        return READINESS_WALLET_NOT_FOUND
    return READINESS_OK


def _oracle_connection_security(settings: Settings) -> str:
    value = getattr(settings, "oracle_connection_security", "wallet_mtls").strip().lower()
    return value if value in {"wallet_mtls", "walletless_tls"} else "wallet_mtls"


def _wallet_mtls_required_files(settings: Settings) -> frozenset[str]:
    if settings.oracle_driver_mode.strip().lower() == "thin":
        return ORACLE_WALLET_THIN_REQUIRED_FILES
    return ORACLE_WALLET_THICK_REQUIRED_FILES


def _wallet_mtls_files_exist(settings: Settings) -> bool:
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    if not wallet_dir:
        return False
    wallet_path = Path(wallet_dir).expanduser()
    return wallet_path.is_dir() and all(
        (wallet_path / file_name).is_file() for file_name in _wallet_mtls_required_files(settings)
    )
