"""API 共通 response model。"""

from typing import Literal

from pydantic import BaseModel


class DatabaseStatusData(BaseModel):
    """アプリケーション DB gate 用の可用性 status。"""

    status: Literal["ok", "not_configured", "setup_required", "unreachable"]
    check: str
    detail: str | None = None
