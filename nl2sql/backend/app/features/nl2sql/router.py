"""NL2SQL feature（skeleton）。

実 NL2SQL（schema introspection / NL→SQL / 実行）はバックエンド接続時に実装する。
ここでは共通 envelope の利用と **SELECT のみ許可の安全境界** を示す。
"""

from fastapi import APIRouter
from pr_backend_core import ApiResponse
from pydantic import BaseModel, Field

from app.settings import get_settings

router = APIRouter(prefix="/nl2sql", tags=["nl2sql"])

# DDL/DML/PLSQL を弾くための先頭キーワード（安全境界）。
_FORBIDDEN_PREFIXES = (
    "insert",
    "update",
    "delete",
    "merge",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "begin",
    "declare",
    "call",
)


class PreviewRequest(BaseModel):
    question: str = Field(min_length=1)


class PreviewData(BaseModel):
    sql: str
    is_safe: bool
    row_limit: int
    note: str


def is_select_only(sql: str) -> bool:
    """先頭が SELECT/WITH のみ、かつ禁止語で始まらないことを確認する（簡易境界）。"""
    head = sql.strip().lstrip("(").lower()
    if head.startswith(_FORBIDDEN_PREFIXES):
        return False
    return head.startswith("select") or head.startswith("with")


@router.post("/preview", response_model=ApiResponse[PreviewData])
async def preview(req: PreviewRequest) -> ApiResponse[PreviewData]:
    """自然言語から SQL を生成して**実行せずに**プレビューする（skeleton はエコー）。"""
    settings = get_settings()
    # skeleton: 実際は OCI Enterprise AI で NL→SQL する。ここでは固定の安全な SELECT を返す。
    sql = "SELECT 1 AS preview"
    safe = (not settings.nl2sql_allow_select_only) or is_select_only(sql)
    return ApiResponse(
        data=PreviewData(
            sql=sql,
            is_safe=safe,
            row_limit=settings.nl2sql_default_row_limit,
            note=f"質問を受領しました: {req.question[:80]}",
        )
    )
