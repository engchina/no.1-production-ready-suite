"""サービス設定。共通基底 BaseServiceSettings を継承し、ドメイン設定を足す。"""

from functools import lru_cache

from pr_backend_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """サービス固有設定。

    OCI/Oracle 等の接続設定はここに追加する（例: oracle_dsn, oci_region ...）。
    """

    service_name: str = "production-ready-service"


@lru_cache
def get_settings() -> Settings:
    """設定シングルトン。"""
    return Settings()
