"""テーブルブラウザ / Select AI 関連スキーマ。"""

from pydantic import BaseModel, Field, field_validator

from app.schemas.search import normalize_query_text

JsonScalar = str | int | float | bool | None


class TableQueryRequest(BaseModel):
    """自然言語によるテーブル参照リクエスト。"""

    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("query")
    @classmethod
    def validate_query(cls, query: str) -> str:
        """空白だけの query を拒否し、前後空白を落とす。"""
        return normalize_query_text(query)


class TableQueryResponse(BaseModel):
    """テーブル参照レスポンス。"""

    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, JsonScalar]] = Field(default_factory=list)
    row_count: int
