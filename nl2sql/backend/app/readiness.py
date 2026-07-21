"""依存設定の readiness チェック（/ready 用）。"""

from pathlib import Path

from app.settings import Settings

READINESS_OK = "ok"
READINESS_MISSING = "missing"
READINESS_MISSING_CREDENTIALS = "missing_credentials"
READINESS_WALLET_NOT_FOUND = "wallet_not_found"


def readiness_checks(settings: Settings) -> dict[str, str]:
    """共通 readiness では Oracle 依存時だけ設定状態を返す。"""
    runtime = settings.nl2sql_runtime_mode.strip().lower()
    persistence = settings.nl2sql_persistence_mode.strip().lower()
    if runtime == "deterministic" and persistence == "memory":
        return {}
    return {"oracle": oracle_readiness_check(settings)}


def oracle_readiness_check(settings: Settings) -> str:
    """Oracle 接続に必要な非 secret 設定の状態を返す。"""
    if not settings.oracle_user.strip() or not settings.oracle_dsn.strip():
        return READINESS_MISSING
    if settings.oracle_password.strip():
        return READINESS_OK
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    if not wallet_dir:
        return READINESS_MISSING_CREDENTIALS
    if not Path(wallet_dir).expanduser().is_dir():
        return READINESS_WALLET_NOT_FOUND
    return READINESS_OK
