"""FastAPI アプリケーションのエントリポイント。"""

import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response

from app.api.router import api_router
from app.clients.oracle import close_oracle_pool
from app.config import get_settings
from app.logging_config import configure_logging
from app.rag.observability import record_http_request
from app.rag.request_context import (
    audit_request_context_from_headers,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)
UNHANDLED_ERROR_MESSAGE = "サーバー内部でエラーが発生しました。時間をおいて再度お試しください。"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """起動・終了時の初期化/後始末。"""
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        yield
    finally:
        close_oracle_pool()


def _route_path(request: Request) -> str:
    """メトリクスの label cardinality を抑えるため route template を返す。"""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path


def _request_id(request: Request) -> str:
    """リクエスト ID を取得または発行する。"""
    incoming = request.headers.get("x-request-id", "").strip()
    if REQUEST_ID_PATTERN.fullmatch(incoming):
        return incoming
    return uuid4().hex


def _api_error_response(
    status_code: int,
    messages: list[str],
    headers: Mapping[str, str] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    """ApiResponse 形式のエラーレスポンスを返す。"""
    body = ApiResponse[object](data=None, error_messages=messages)
    response_headers = dict(headers or {})
    if request_id:
        response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=response_headers,
    )


def _http_exception_messages(detail: Any, status_code: int) -> list[str]:
    """HTTPException.detail を API エラー配列へ正規化する。"""
    if isinstance(detail, str):
        if status_code == 404 and detail == "Not Found":
            return ["リソースが見つかりません。"]
        if status_code == 405 and detail == "Method Not Allowed":
            return ["許可されていない HTTP メソッドです。"]
        return [detail]
    if isinstance(detail, list):
        return [str(item) for item in detail]
    if isinstance(detail, dict):
        return [str(detail)]
    return ["リクエストの処理に失敗しました。"]


def _response_request_id(request: Request) -> str:
    """例外ハンドラからも同じ request id を返せるよう取得する。"""
    state_request_id = getattr(request.state, "request_id", None)
    return state_request_id if isinstance(state_request_id, str) else _request_id(request)


def create_app() -> FastAPI:
    """FastAPI アプリを生成する。"""
    settings = get_settings()
    app = FastAPI(
        title="お任せ！RAG - production-ready RAG API",
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def metrics_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """HTTP レベルのメトリクスを記録する。"""
        started_at = perf_counter()
        request_id = _request_id(request)
        request.state.request_id = request_id
        context = audit_request_context_from_headers(
            request.headers,
            request_id=request_id,
            settings=settings,
        )
        context_token = set_audit_request_context(context)
        try:
            response = await call_next(request)
        except Exception:
            record_http_request(
                method=request.method,
                path=_route_path(request),
                status=500,
                seconds=perf_counter() - started_at,
            )
            raise
        else:
            response.headers["X-Request-ID"] = request_id
            record_http_request(
                method=request.method,
                path=_route_path(request),
                status=response.status_code,
                seconds=perf_counter() - started_at,
            )
            return response
        finally:
            reset_audit_request_context(context_token)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """HTTPException を ApiResponse 形式へ統一する。"""
        return _api_error_response(
            exc.status_code,
            _http_exception_messages(exc.detail, exc.status_code),
            headers=exc.headers,
            request_id=_response_request_id(request),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """リクエスト検証エラーを ApiResponse 形式へ統一する。"""
        messages = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        ]
        return _api_error_response(
            422,
            messages or ["リクエストの形式が不正です。"],
            request_id=_response_request_id(request),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """未処理例外を秘匿した ApiResponse 形式へ統一する。"""
        request_id = _response_request_id(request)
        logger.exception(
            "unhandled_api_error",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "exception_type": type(exc).__name__,
            },
        )
        return _api_error_response(
            500,
            [UNHANDLED_ERROR_MESSAGE],
            request_id=request_id,
        )

    app.include_router(api_router, prefix="/api")
    app.mount("/metrics", make_asgi_app())
    return app


app = create_app()
