"""NL2SQL クエリ API のスキーマ(生成/実行の 2 段ゲート)。"""

from typing import Any

from pydantic import BaseModel, Field


class RouterSummaryData(BaseModel):
    """ルーティング判断の非機密サマリ。"""

    profile_selected: str | None = None
    generation_backend: str
    complexity_score: int
    matched_signals: list[str] = Field(default_factory=list)
    reason: str


class GuardrailVerdictData(BaseModel):
    """生成/実行 SQL に対する Guardrail 静的検査結果。"""

    allowed: bool
    policy: str
    statement_type: str
    violations: list[str] = Field(default_factory=list)
    semantic_verify_required: bool = False
    max_rows: int | None = None
    run_role: str | None = None


class Nl2SqlGenerateRequest(BaseModel):
    """生成フェーズ(showsql)のリクエスト。"""

    question: str = Field(..., min_length=1, max_length=4000)
    profile_name: str | None = Field(default=None, max_length=128)
    team_name: str | None = Field(default=None, max_length=128)
    allowed_objects: list[str] = Field(default_factory=list, max_length=500)


class Nl2SqlGenerateResponseData(BaseModel):
    """生成フェーズの応答(**未実行**・人手プレビュー確認待ち)。"""

    question: str
    profile_name: str
    generation_backend: str
    router: RouterSummaryData
    generated_sql: str
    narration: str | None = None
    guardrail: GuardrailVerdictData


class Nl2SqlExecuteRequest(BaseModel):
    """実行フェーズ(runsql)のリクエスト(人手承認済み SQL)。"""

    sql: str = Field(..., min_length=1, max_length=20000)
    allowed_objects: list[str] = Field(default_factory=list, max_length=500)


class SqlResultData(BaseModel):
    """read-only SELECT の結果。"""

    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int
    truncated: bool


class Nl2SqlExecuteResponseData(BaseModel):
    """実行フェーズの応答。"""

    sql: str
    executed: bool
    blocked_reason: str | None = None
    guardrail: GuardrailVerdictData
    result: SqlResultData | None = None
