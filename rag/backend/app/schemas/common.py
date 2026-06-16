"""共通スキーマ。参照実装の ApiResponse 設計を踏襲。"""

from typing import Literal

from pydantic import BaseModel, Field


class ApiResponse[T](BaseModel):
    """API 共通レスポンス形。"""

    data: T | None = None
    error_messages: list[str] = Field(default_factory=list)
    warning_messages: list[str] = Field(default_factory=list)


class Page[T](BaseModel):
    """ページング済みレスポンス。"""

    items: list[T]
    total: int
    limit: int
    offset: int
    has_next: bool


class HealthData(BaseModel):
    """ヘルスチェック結果。"""

    status: str
    version: str
    message: str | None = None
    checks: dict[str, str] = Field(default_factory=dict)


# Oracle 26ai の利用可否。
# - ok: 設定済みかつ実接続成功(検索・取込など DB 機能を利用できる)
# - not_configured: 接続情報が未設定/不足(まず設定が必要)
# - unreachable: 設定済みだが起動していない/到達できない(まず DB 起動が必要)
DatabaseAvailability = Literal["ok", "not_configured", "unreachable"]


class DatabaseStatusData(BaseModel):
    """データベース(Oracle 26ai)の利用可否ステータス。

    フロントの DB ゲートが「設定ページ以外を開く前」に参照する。
    """

    status: DatabaseAvailability
    # readiness check の生ステータス(ok / missing / missing_credentials / wallet_not_found 等)。
    check: str
    detail: str | None = None
