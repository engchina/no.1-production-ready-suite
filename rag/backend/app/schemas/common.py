"""共通スキーマ。参照実装の ApiResponse 設計を踏襲。"""

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
