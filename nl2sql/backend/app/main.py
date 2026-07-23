"""FastAPI エントリポイント。共通 app factory で薄く構成する。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from pr_backend_core import configure_logging, create_app
from starlette.responses import JSONResponse

from app.api.concurrency import run_sync_io as run_in_threadpool
from app.api.router import api_router
from app.clients.oracle_runtime import close_oracle_pools
from app.features.nl2sql.ontology_router import OntologyApiRuntime, ontology_runtime
from app.features.nl2sql.service import (
    Nl2SqlPersistenceUnavailable,
    Nl2SqlRepositoryOperationFailed,
    Nl2SqlService,
    nl2sql_service,
)
from app.readiness import readiness_checks
from app.security.permissions import UNCLASSIFIED_PERMISSION, permission_for_route
from app.security.service import SecurityApiError, get_security_service
from app.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


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
    # Constructor は client/repository の wiring のみ。auth 有効時だけ、排他制御された
    # 初回 SYSTEM_ADMIN bootstrap を startup gate として実行する。
    application.state.services = ServiceContainer(
        nl2sql=nl2sql_service,
        ontology=ontology_runtime,
    )
    runtime_settings = get_settings()
    if runtime_settings.local_debug_enabled:
        logger.warning("local_debug_auth_bypass_enabled")
    elif runtime_settings.app_auth_enabled:
        await run_in_threadpool(get_security_service().ensure_bootstrapped)
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
            if permission_for_route(method, route_path) == UNCLASSIFIED_PERMISSION:
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


@app.exception_handler(Nl2SqlPersistenceUnavailable)
async def nl2sql_persistence_unavailable_handler(
    _request: Request,
    exc: Nl2SqlPersistenceUnavailable,
) -> JSONResponse:
    """永続化障害を統一 ApiResponse と retry hint へ正規化する。"""
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "5"},
        content={
            "data": None,
            "error_messages": [exc.public_message],
            "warning_messages": [],
            "error_code": exc.reason_code,
        },
    )


@app.exception_handler(Nl2SqlRepositoryOperationFailed)
async def nl2sql_repository_operation_failed_handler(
    _request: Request,
    exc: Nl2SqlRepositoryOperationFailed,
) -> JSONResponse:
    """SQL 実装/互換性エラーを DB 全体の停止と誤認させず局所化する。"""
    return JSONResponse(
        status_code=500,
        content={
            "data": None,
            "error_messages": [exc.public_message],
            "warning_messages": [],
            "error_code": exc.reason_code,
        },
    )


@app.exception_handler(SecurityApiError)
async def security_api_error_handler(
    _request: Request,
    exc: SecurityApiError,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "data": None,
            "error_messages": [exc.public_message],
            "warning_messages": [],
        },
    )
