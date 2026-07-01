"""FastAPI アプリケーションのエントリポイント。"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

# 共有 backend インフラ（CORS / request-id / エラー envelope）。
from pr_backend_core.api.errors import api_error_response, http_exception_messages
from pr_backend_core.observability.request_context import generate_request_id
from pr_backend_core.security.cors import configure_cors
from prometheus_client import make_asgi_app
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response

from app.api.router import api_router
from app.auth import attach_refreshed_auth_cookie, auth_is_enabled, prepare_auth_request
from app.clients.oracle import close_oracle_pool
from app.config import get_settings
from app.logging_config import configure_logging
from app.rag.observability import (
    close_trace_exporter,
    configure_trace_exporter,
    record_http_request,
)
from app.rag.request_context import (
    audit_request_context_from_headers,
    reset_audit_request_context,
    set_audit_request_context,
)

logger = logging.getLogger(__name__)
UNHANDLED_ERROR_MESSAGE = "サーバー内部でエラーが発生しました。時間をおいて再度お試しください。"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """起動・終了時の初期化/後始末。"""
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_trace_exporter(settings)
    worker_task: asyncio.Task[None] | None = None
    worker_stop: asyncio.Event | None = None
    if (
        settings.ingestion_queue_dedicated_worker_enabled
        and settings.ingestion_queue_inprocess_worker_enabled
    ):
        # ローカル開発の既定: API プロセス内では軽量 dispatcher だけを起動し、
        # job 本体は subprocess へ隔離する。別 worker service へ完全に切り出す場合は
        # inprocess を無効化する（この分岐に入らない）。
        from app.rag.ingestion_worker import IngestionQueueWorker

        # in-process ワーカーは Gunicorn worker ごとに 1 つ起動する。複数 worker で
        # 動かすと実効同時取込数が WEB_CONCURRENCY 倍になり OCI/Oracle を過負荷にし得る。
        # 単一プロセス運用にするか、別プロセスワーカー（INPROCESS=false）へ切り出すこと。
        logger.warning(
            "ingestion_inprocess_worker_enabled",
            extra={
                "worker_concurrency": settings.ingestion_queue_worker_concurrency,
                "advice": (
                    "in-process worker runs per process; "
                    "use WEB_CONCURRENCY=1 or a dedicated worker process"
                ),
            },
        )
        worker = IngestionQueueWorker(settings=settings)
        worker_stop = asyncio.Event()
        worker_task = asyncio.create_task(worker.run_forever(stop_event=worker_stop))
    elif (
        not settings.ingestion_queue_dedicated_worker_enabled
        and settings.ingestion_queue_startup_recovery_enabled
    ):
        logger.warning(
            "ingestion_worker_disabled",
            extra={
                "advice": (
                    "ingestion jobs remain queued until an ingestion worker process is running"
                )
            },
        )
    try:
        yield
    finally:
        if worker_task is not None:
            if worker_stop is not None:
                worker_stop.set()
            with suppress(asyncio.CancelledError):
                await worker_task
        close_trace_exporter()
        close_oracle_pool()


def _route_path(request: Request) -> str:
    """メトリクスの label cardinality を抑えるため route template を返す。"""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path


def _response_request_id(request: Request) -> str:
    """例外ハンドラからも同じ request id を返せるよう取得する。"""
    state_request_id = getattr(request.state, "request_id", None)
    if isinstance(state_request_id, str):
        return state_request_id
    return generate_request_id(request.headers.get("x-request-id"))


def create_app() -> FastAPI:
    """FastAPI アプリを生成する。"""
    settings = get_settings()
    app = FastAPI(
        title="Production Ready RAG API",
        version=settings.app_version,
        lifespan=lifespan,
    )

    configure_cors(app, origins=settings.cors_origins)

    @app.middleware("http")
    async def metrics_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """HTTP レベルのメトリクスを記録する（認証/監査コンテキスト付与込み）。"""
        started_at = perf_counter()
        # request id の検証/採番は共有インフラへ委譲（認証/監査は RAG 固有のまま）。
        request_id = generate_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        response = prepare_auth_request(request, settings)
        session = getattr(request.state, "auth_session", None)
        session_user_id = getattr(session, "user_id", None)
        auth_enabled = auth_is_enabled(settings)
        context = audit_request_context_from_headers(
            request.headers,
            request_id=request_id,
            settings=settings,
            authenticated_user_id=session_user_id if auth_enabled else None,
            default_user_id=session_user_id if not auth_enabled else None,
            allow_user_header=not auth_enabled,
        )
        context_token = set_audit_request_context(context)
        try:
            if response is None:
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
            attach_refreshed_auth_cookie(response, request, settings)
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
        """HTTPException を ApiResponse 形式へ統一する（共有 envelope）。"""
        return api_error_response(
            exc.status_code,
            http_exception_messages(exc.detail, exc.status_code),
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
        return api_error_response(
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
        return api_error_response(
            500,
            [UNHANDLED_ERROR_MESSAGE],
            request_id=request_id,
        )

    app.include_router(api_router, prefix="/api")
    app.mount("/metrics", make_asgi_app())
    return app


app = create_app()
