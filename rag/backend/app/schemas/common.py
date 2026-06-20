"""共通スキーマ。

汎用 envelope（ApiResponse / Page / HealthData / JsonValue）は共有 backend インフラ
`production-ready-backend-core`（pr_backend_core）へ移管した。3 サービスで同一契約を共有する。
RAG 固有の DB ステータス型はここに残す。
"""

from typing import Literal

from pr_backend_core.schemas import ApiResponse, HealthData, JsonValue, Page
from pydantic import BaseModel

# 互換のため re-export（既存の `from app.schemas.common import ApiResponse` 等を維持）。
__all__ = [
    "ApiResponse",
    "Page",
    "HealthData",
    "JsonValue",
    "DatabaseAvailability",
    "DatabaseStatusData",
]


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
