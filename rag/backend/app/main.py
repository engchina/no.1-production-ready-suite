"""FastAPI アプリケーションのエントリポイント。"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

# 共有 backend インフラ（CORS / request-id / エラー envelope）。
from pr_backend_core.api.errors import api_error_response, http_exception_messages
from pr_backend_core.api.validation import validation_error_response
from pr_backend_core.observability.metrics import MetricsMiddleware
from pr_backend_core.observability.request_context import generate_request_id
from pr_backend_core.security.cors import configure_cors
from pr_system_settings.auth.errors import SecurityApiError, SecurityMigrationRequired
from prometheus_client import make_asgi_app
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response

from app.api.router import api_router
from app.clients.oracle import close_oracle_pool
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.rag.chat_answer_runs import get_chat_answer_run_service
from app.rag.evaluation_jobs import get_evaluation_job_service
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
from app.readiness import pending_legacy_local_storage_dir

logger = logging.getLogger(__name__)
UNHANDLED_ERROR_MESSAGE = "サーバー内部でエラーが発生しました。時間をおいて再度お試しください。"


def _warn_pending_legacy_local_storage_dir(settings: Settings) -> None:
    """旧既定ディレクトリに原本が残っている場合、移行手順を警告ログで案内する。"""
    legacy_dir = pending_legacy_local_storage_dir(settings)
    if legacy_dir is None:
        return
    logger.warning(
        "legacy_local_storage_dir_detected",
        extra={
            "legacy_local_storage_dir": legacy_dir,
            "local_storage_dir": settings.local_storage_dir,
            "advice": (
                "files are not moved automatically; copy legacy files into "
                "PLATFORM_LOCAL_STORAGE_DIR "
                "(see docs/deployment.md) or set PLATFORM_LOCAL_STORAGE_DIR to the legacy path"
            ),
        },
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """起動・終了時の初期化/後始末。"""
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_trace_exporter(settings)
    _warn_pending_legacy_local_storage_dir(settings)
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
        # API プロセスの停止(uvicorn --reload・gunicorn の再起動)は待たせず、実行中の job の子を
        # 止めて自分の lease の job を QUEUED に戻す(grace 0。#357)。専用 worker は grace まで待つ。
        worker = IngestionQueueWorker(settings=settings, shutdown_grace_seconds=0.0)
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
        # このプロセスで実行中の品質評価の job を打ち切り、失敗にする（#390。別のプロセスは
        # 引き継がない）。
        await get_evaluation_job_service().shutdown()
        # このプロセスで作成中のチャットの回答を打ち切り、中断として保存する（#1175）。
        await get_chat_answer_run_service().shutdown()
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
    # API ドキュメント（/docs・/redoc・/openapi.json）は公開しない（#748）。
    app = FastAPI(
        title="Production Ready RAG API",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    configure_cors(app, origins=settings.cors_origins)

    @app.middleware("http")
    async def metrics_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """HTTP レベルのメトリクスを記録し、監査コンテキストの初期値を付与する。

        認証と認可は `/api` の router の dependency（`app.security.dependencies`）が行い、
        認可を通った利用者から監査 / 対象範囲の context を作り直す（#214）。ここでは
        dependency を通らない応答（公開 path・404 など）のための初期値だけを作る。
        production では client の `X-User-ID` と対象範囲の header を使わない。
        """
        # 外側の共通 ASGI middleware が body 完了まで HTTP 相関とメトリクスを持つ。
        request_id = _response_request_id(request)
        local_mode = settings.local_debug_enabled
        context = audit_request_context_from_headers(
            request.headers,
            request_id=request_id,
            settings=settings,
            allow_user_header=local_mode,
            trust_scope_headers=local_mode,
        )
        context_token = set_audit_request_context(context)
        try:
            return await call_next(request)
        finally:
            reset_audit_request_context(context_token)

    app.add_middleware(MetricsMiddleware, record_request=record_http_request)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """HTTPException を ApiResponse 形式へ統一する（共有 envelope）。"""
        return api_error_response(
            exc.status_code,
            http_exception_messages(exc.detail, exc.status_code),
            headers=exc.headers,
            request_id=_response_request_id(request),
        )

    @app.exception_handler(SecurityApiError)
    async def security_api_error_handler(request: Request, exc: SecurityApiError) -> JSONResponse:
        """認証・認可・ユーザー / ロール操作のエラー（共通認証。#214）。

        ApiResponse の envelope に、共通画面が使う `error_code` と `problem.field_errors` を足す
        （NL2SQL と同じ形）。
        """
        return security_error_response(
            request,
            status_code=exc.status_code,
            detail=exc.public_message,
            code=exc.code,
            title=exc.title,
            retryable=exc.retryable,
            field_errors=exc.field_errors,
            headers=exc.headers,
        )

    @app.exception_handler(SecurityMigrationRequired)
    async def security_migration_required_handler(
        request: Request, exc: SecurityMigrationRequired
    ) -> JSONResponse:
        """共通認証・RAG のロール権限の表が未作成（システムテーブルの初期化が必要）。"""
        logger.error(
            "security_schema_migration_required",
            extra={"request_id": _response_request_id(request), "database_object": exc.object_name},
        )
        return security_error_response(
            request,
            status_code=409,
            detail=(
                "認証・権限のテーブルが未作成です。システム設定 > データベース の"
                "「システムテーブル」で作成・更新してから再試行してください。"
            ),
            code="SECURITY_SCHEMA_MIGRATION_REQUIRED",
            title="セキュリティ初期化が必要です",
            retryable=False,
            field_errors=(),
        )

    def security_error_response(
        request: Request,
        *,
        status_code: int,
        detail: str,
        code: str,
        title: str | None,
        retryable: bool,
        field_errors: Sequence[Mapping[str, str]],
        headers: Mapping[str, str] | None = None,
    ) -> JSONResponse:
        request_id = _response_request_id(request)
        return JSONResponse(
            status_code=status_code,
            # 429 の `Retry-After` など、エラーが持つ header を足す（#1087）。
            headers={**(headers or {}), "X-Request-ID": request_id},
            content={
                "data": None,
                "error_messages": [detail],
                "warning_messages": [],
                "error_code": code,
                "problem": {
                    "title": title,
                    "status": status_code,
                    "detail": detail,
                    "code": code,
                    "request_id": request_id,
                    "retryable": retryable,
                    "field_errors": [dict(item) for item in field_errors],
                },
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """リクエスト検証エラーを、3 製品共通の日本語の文と problem 契約で返す（#1065）。"""
        return validation_error_response(exc.errors(), request_id=_response_request_id(request))

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
