"""テーブルブラウザ API。Select AI による自然言語テーブル参照。"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from fastapi import APIRouter, HTTPException, Request

from app.clients.oracle import OracleClient
from app.rag.guardrails import GuardrailPolicy, GuardrailResult
from app.rag.observability import record_guardrail_findings
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse
from app.schemas.table_browser import JsonScalar, TableQueryRequest, TableQueryResponse

router = APIRouter()
READ_ONLY_BLOCKED_CODES = {"sql_mutation_intent"}


@router.post("/query", response_model=ApiResponse[TableQueryResponse])
async def query_table(
    http_request: Request,
    request: TableQueryRequest,
) -> ApiResponse[TableQueryResponse]:
    """Select AI で自然言語からテーブル参照を行う。"""
    enforce_rate_limit("table_query", http_request)
    guardrail = _validate_table_query(request.query)
    raw_rows = await OracleClient().select_ai(guardrail.sanitized_text, limit=request.limit)
    rows = [_json_ready_row(row) for row in raw_rows]
    return ApiResponse(
        data=TableQueryResponse(
            columns=_columns(rows),
            rows=rows,
            row_count=len(rows),
        ),
        warning_messages=guardrail.warnings,
    )


def _validate_table_query(query: str) -> GuardrailResult:
    """Select AI に渡す前に参照専用の guardrail を適用する。"""
    result = GuardrailPolicy().validate_query(query)
    if not result.allowed:
        record_guardrail_findings("table_query", result.findings, "blocked")
        raise HTTPException(
            status_code=422,
            detail=result.warnings or ["テーブル参照クエリを処理できません。"],
        )
    if any(finding.code in READ_ONLY_BLOCKED_CODES for finding in result.findings):
        record_guardrail_findings("table_query", result.findings, "blocked")
        raise HTTPException(
            status_code=422,
            detail="データ変更を伴うテーブル操作は実行できません。参照のみ指定してください。",
        )
    record_guardrail_findings("table_query", result.findings, "warning")
    return result


def _columns(rows: list[dict[str, JsonScalar]]) -> list[str]:
    """行データから初出順で列名を抽出する。"""
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for column in row:
            if column in seen:
                continue
            seen.add(column)
            columns.append(column)
    return columns


def _json_ready_row(row: dict[str, object]) -> dict[str, JsonScalar]:
    """DB adapter の値を API で安全に返せる scalar に変換する。"""
    return {key: _json_ready_value(value) for key, value in row.items()}


def _json_ready_value(value: object) -> JsonScalar:
    """datetime/Decimal/Enum などを JSON scalar に寄せる。"""
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        enum_value = value.value
        return enum_value if isinstance(enum_value, str | bool | int | float) else str(enum_value)
    return str(value)
