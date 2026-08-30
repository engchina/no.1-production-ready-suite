"""FastAPI エントリポイント。共通 app factory で薄く構成する。"""

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pr_backend_core import configure_logging, create_app
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.api.problems import api_problem_response, request_id_for, validation_field_problems
from app.api.router import api_router
from app.clients.oracle_runtime import close_oracle_pools
from app.features.nl2sql.ontology_router import OntologyApiRuntime, ontology_runtime
from app.features.nl2sql.service import (
    SCHEMA_CATALOG_EMPTY_ERROR_CODE,
    DbAdminOperationFailed,
    Nl2SqlPersistenceUnavailable,
    Nl2SqlRepositoryOperationFailed,
    Nl2SqlService,
    SchemaCatalogEmptyError,
    nl2sql_service,
)
from app.readiness import readiness_checks
from app.security.permissions import UNCLASSIFIED_PERMISSION, permission_for_route
from app.security.service import SecurityApiError
from app.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

_ORACLE_RAW_DETAIL_RE = re.compile(r"\s*:?[ ]*ORA-\d{5}.*$", re.IGNORECASE)


def _runtime_readiness_checks() -> dict[str, str]:
    """汎用 /ready は event loop を塞がない軽量設定チェックだけを返す。"""

    return readiness_checks(get_settings())


@dataclass(frozen=True, slots=True)
class ServiceContainer:
    """DB I/O を行わずに構成できる request service container。"""

    nl2sql: Nl2SqlService
    ontology: OntologyApiRuntime


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    # Startup is intentionally DB-free. A fresh Compute can boot before the
    # database, wallet, or any application table is ready; those failures belong
    # to readiness probes or the first operation that actually needs them.
    application.state.services = ServiceContainer(
        nl2sql=nl2sql_service,
        ontology=ontology_runtime,
    )
    runtime_settings = get_settings()
    if runtime_settings.local_debug_enabled:
        logger.warning("local_debug_auth_bypass_enabled")
    try:
        yield
    finally:
        close_oracle_pools()


def _assert_route_manifest(application: FastAPI) -> None:
    missing: list[str] = []
    for path, operations in application.openapi().get("paths", {}).items():
        if not path.startswith("/api"):
            continue
        route_path = path.removeprefix("/api")
        for method in operations:
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            permissions = permission_for_route(method, route_path)
            if permissions and UNCLASSIFIED_PERMISSION in permissions:
                missing.append(f"{method.upper()} {path}")
    if missing:
        raise RuntimeError("未登録の API 権限があります: " + ", ".join(sorted(missing)))


app = create_app(
    service_name=settings.service_name,
    version=settings.app_version,
    cors_origins=settings.cors_origins,
    api_router=api_router,
    readiness_checks_getter=_runtime_readiness_checks,
    lifespan=lifespan,
    enable_metrics=settings.enable_metrics,
)
_assert_route_manifest(app)


@app.exception_handler(SchemaCatalogEmptyError)
async def schema_catalog_empty_handler(
    request: Request,
    exc: SchemaCatalogEmptyError,
) -> JSONResponse:
    """schema 未整備を表示文言そのままで機械判定可能な error_code 付き 400 にする。"""
    return api_problem_response(
        request,
        status_code=400,
        detail=str(exc),
        code=SCHEMA_CATALOG_EMPTY_ERROR_CODE,
    )


@app.exception_handler(Nl2SqlPersistenceUnavailable)
async def nl2sql_persistence_unavailable_handler(
    request: Request,
    exc: Nl2SqlPersistenceUnavailable,
) -> JSONResponse:
    """永続化障害を統一 ApiResponse と retry hint へ正規化する。"""
    return api_problem_response(
        request,
        status_code=503,
        detail=exc.public_message,
        code=exc.reason_code,
        retryable=True,
        headers={"Retry-After": "5"},
    )


@app.exception_handler(Nl2SqlRepositoryOperationFailed)
async def nl2sql_repository_operation_failed_handler(
    request: Request,
    exc: Nl2SqlRepositoryOperationFailed,
) -> JSONResponse:
    """SQL 実装/互換性エラーを DB 全体の停止と誤認させず局所化する。"""
    return api_problem_response(
        request,
        status_code=500,
        detail=exc.public_message,
        code=exc.reason_code,
    )


@app.exception_handler(DbAdminOperationFailed)
async def db_admin_operation_failed_handler(
    request: Request,
    exc: DbAdminOperationFailed,
) -> JSONResponse:
    """DB 管理操作の失敗を、画面で復旧できる構造化情報として返す。"""
    request_id = request_id_for(request)
    logger.error(
        "db_admin_operation_failed",
        extra={
            "request_id": request_id,
            "error_code": exc.error_code,
            "target_name": exc.target_name,
            "target_type": exc.target_type,
            "operation": exc.operation,
            "raw_error": exc.raw_message,
        },
    )
    return api_problem_response(
        request,
        status_code=500,
        detail=exc.summary,
        code=exc.error_code,
        extra={
            "error_details": {
                "summary": exc.summary,
                "cause": exc.cause,
                "actions": exc.actions,
                "target_name": exc.target_name,
                "target_type": exc.target_type,
                "operation": exc.operation,
            }
        },
    )


@app.exception_handler(SecurityApiError)
async def security_api_error_handler(
    request: Request,
    exc: SecurityApiError,
) -> JSONResponse:
    return api_problem_response(
        request,
        status_code=exc.status_code,
        detail=exc.public_message,
        code=exc.code,
        title=exc.title,
        retryable=exc.retryable,
        field_errors=exc.field_errors,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """FastAPI/Pydantic の入力エラーを field pointer 付きで返す。"""

    return api_problem_response(
        request,
        status_code=422,
        detail="入力内容に誤りがあります。該当項目を確認してください。",
        code="REQUEST_VALIDATION_FAILED",
        field_errors=validation_field_problems(exc.errors()),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_problem_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """汎用 HTTP error も同じ problem 契約へ正規化する。"""

    code: str | None = None
    if exc.status_code == 500:
        detail = "サーバー内部でエラーが発生しました。時間をおいて再試行してください。"
    elif isinstance(exc.detail, str) and exc.detail.strip():
        detail = _ORACLE_RAW_DETAIL_RE.sub("", exc.detail.strip()).rstrip(" :。")
        if detail != exc.detail.strip():
            detail += "。データベースの状態と実行権限を確認して再試行してください。"
    elif isinstance(exc.detail, dict):
        raw_code = exc.detail.get("code")
        code = raw_code if isinstance(raw_code, str) and raw_code else None
        detail = next(
            (
                value
                for key in ("message_ja", "message", "error")
                if isinstance((value := exc.detail.get(key)), str) and value.strip()
            ),
            "リクエストの処理に失敗しました。入力内容を確認してください。",
        )
        if code and code not in detail:
            detail = f"{code}: {detail}"
    else:
        detail = "リクエストの処理に失敗しました。入力内容を確認してください。"
    return api_problem_response(
        request,
        status_code=exc.status_code,
        detail=detail,
        code=code,
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unhandled_problem_handler(request: Request, exc: Exception) -> JSONResponse:
    """未処理例外は詳細を返さず request id でログと相関する。"""

    logger.exception(
        "unhandled_api_error",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "method": request.method,
            "path": request.url.path,
            "exception_type": type(exc).__name__,
        },
    )
    return api_problem_response(
        request,
        status_code=500,
        detail="サーバー内部でエラーが発生しました。時間をおいて再試行してください。",
        code="INTERNAL_SERVER_ERROR",
    )
