"""全サービス共通の設定基底クラス。"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """3 サービス共通の最小設定。

    各サービスはこれを継承し、自分のドメイン設定（OCI/Oracle 接続等）を追加する。
    `.env` + 環境変数から読み込む（pydantic-settings）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_version: str = "0.1.0"
    log_level: str = "INFO"
    # local / staging / production
    environment: str = "local"
    # CORS 許可オリジン。CSV か JSON 配列で指定可。
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """カンマ区切り文字列を許容する（"a,b" -> ["a","b"]）。"""
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return value  # JSON 配列は pydantic に委ねる
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        """ENVIRONMENT=production を production 判定に使う。"""
        return self.environment.strip().lower() == "production"
