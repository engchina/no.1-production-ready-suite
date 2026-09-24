"""API エラーを漸進互換の problem 契約へ正規化する。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from fastapi import Request
from pr_backend_core.observability import generate_request_id
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse


class ApiFieldProblem(BaseModel):
    """入力 payload 上の問題。pointer は RFC 6901 JSON Pointer。"""

    pointer: str
    code: str
    message: str


class ApiProblem(BaseModel):
    """RFC 9457 の語彙を既存 envelope 内で段階導入する。"""

    type: str
    title: str
    status: int
    detail: str
    code: str
    request_id: str
    retryable: bool = False
    field_errors: list[ApiFieldProblem] = Field(default_factory=list)


_STATUS_TITLES: dict[int, str] = {
    400: "リクエストを処理できません",
    401: "認証が必要です",
    403: "この操作を実行する権限がありません",
    404: "対象が見つかりません",
    405: "この操作方法は利用できません",
    409: "現在の状態では操作を完了できません",
    422: "入力内容を確認してください",
    429: "リクエストが集中しています",
    500: "サーバー内部でエラーが発生しました",
    502: "外部サービスから応答を取得できません",
    503: "サービスを一時的に利用できません",
    504: "処理がタイムアウトしました",
}

_STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "REQUEST_VALIDATION_FAILED",
    429: "RATE_LIMITED",
    500: "INTERNAL_SERVER_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}

_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


def request_id_for(request: Request) -> str:
    """middleware が採番した request id を安全に取得する。"""

    value = getattr(request.state, "request_id", "")
    if isinstance(value, str) and value:
        return value
    generated = generate_request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = generated
    return generated


def default_problem_title(status_code: int) -> str:
    return _STATUS_TITLES.get(status_code, "リクエストの処理に失敗しました")


def default_problem_code(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, f"HTTP_{status_code}")


def problem_type_for(code: str) -> str:
    slug = code.strip().lower().replace("_", "-") or "unknown"
    return f"urn:nl2sql:problem:{slug}"


def api_problem_response(
    request: Request,
    *,
    status_code: int,
    detail: str,
    code: str | None = None,
    title: str | None = None,
    retryable: bool | None = None,
    field_errors: Iterable[ApiFieldProblem | Mapping[str, str]] = (),
    headers: Mapping[str, str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> JSONResponse:
    """既存 envelope を保ったまま構造化 problem を追加する。"""

    resolved_code = code or default_problem_code(status_code)
    request_id = request_id_for(request)
    normalized_field_errors = [
        item if isinstance(item, ApiFieldProblem) else ApiFieldProblem.model_validate(item)
        for item in field_errors
    ]
    problem = ApiProblem(
        type=problem_type_for(resolved_code),
        title=title or default_problem_title(status_code),
        status=status_code,
        detail=detail,
        code=resolved_code,
        request_id=request_id,
        retryable=(status_code in _RETRYABLE_STATUSES if retryable is None else retryable),
        field_errors=normalized_field_errors,
    )
    response_headers = dict(headers or {})
    if request_id:
        response_headers["X-Request-ID"] = request_id
    content: dict[str, Any] = {
        "data": None,
        "error_messages": [detail],
        "warning_messages": [],
        "error_code": resolved_code,
        "problem": problem.model_dump(mode="json"),
    }
    if extra:
        content.update(extra)
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=response_headers,
    )


def validation_field_problems(errors: Sequence[Mapping[str, Any]]) -> list[ApiFieldProblem]:
    """Pydantic/FastAPI validation errors を安全な日本語 field error へ変換する。"""

    return [
        ApiFieldProblem(
            pointer=_json_pointer(error.get("loc", ())),
            code=str(error.get("type") or "invalid"),
            message=_validation_message(error),
        )
        for error in errors
    ]


def _json_pointer(location: object) -> str:
    parts = list(location) if isinstance(location, (list, tuple)) else []
    if parts and parts[0] in {"body", "query", "path", "header", "cookie"}:
        parts = parts[1:]
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped) if escaped else "/"


def _validation_message(error: Mapping[str, Any]) -> str:
    error_type = str(error.get("type") or "")
    raw_message = str(error.get("msg") or "").strip()
    if raw_message.startswith("Value error, "):
        value_message = raw_message.removeprefix("Value error, ").strip()
        if value_message:
            return value_message
    messages = {
        "missing": "必須項目を入力してください。",
        "string_too_short": "入力文字数が不足しています。",
        "string_too_long": "入力文字数が上限を超えています。",
        "string_pattern_mismatch": "入力形式を確認してください。",
        "greater_than_equal": "指定できる最小値を確認してください。",
        "less_than_equal": "指定できる最大値を確認してください。",
        "list_too_short": "必要な項目を選択してください。",
    }
    return messages.get(error_type, "入力内容を確認してください。")
