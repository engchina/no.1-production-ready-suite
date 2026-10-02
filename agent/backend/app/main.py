"""FastAPI エントリポイント。共通 app factory で薄く構成する。"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from pr_backend_core import ApiResponse, configure_logging, create_app
from pr_system_settings.auth.errors import SecurityApiError, SecurityMigrationRequired
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response

from app.api.router import api_router
from app.observability import (
    metrics_response,
    start_trace_export_retry_worker,
    stop_trace_export_retry_worker,
)
from app.readiness import readiness_checks
from app.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


def _restore_control_plane() -> None:
    """画面・API で変えた定義（Skill・プラグイン・MCP 接続・ツール権限）を読み込む（#764）。"""
    from app.features.agent.control_plane_store import restore_control_plane

    try:
        restored = restore_control_plane()
    except Exception:  # noqa: BLE001 - DB の障害で起動を止めない（画面の DB の案内に任せる）
        logger.exception("agent_control_plane_restore_failed")
        return
    logger.info("agent_control_plane_restored", extra={"restored": restored})


def _prepare_history() -> None:
    """評価の履歴の整理（保持期間）と、Run の事実の backfill（#794）。

    Run の事実は Oracle の構成だけ保存する。Runtime repository にある Run の事実をすべて
    キューに入れ、バックグラウンドで MERGE する（今見えている Run の集計を消さない）。
    """
    from app.features.agent import run_facts_store
    from app.features.agent.evaluation import evaluation_store
    from app.features.agent.runtime import runtime_repository

    try:
        evaluation_store.prune()
    except Exception:  # noqa: BLE001 - 整理の失敗で起動を止めない
        logger.exception("agent_evaluation_prune_failed")
    try:
        run_facts_store.backfill(runtime_repository)
    except Exception:  # noqa: BLE001 - 集計用の事実の失敗で起動を止めない
        logger.exception("agent_run_facts_backfill_failed")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await asyncio.to_thread(_restore_control_plane)
    await asyncio.to_thread(_prepare_history)
    await start_trace_export_retry_worker()
    # 業務 Agent の自動実行のスケジューラ（#784。gunicorn は 1 worker のため 1 つだけ動く）。
    from app.features.agent.automations import run_scheduler

    scheduler = asyncio.create_task(run_scheduler())
    try:
        yield
    finally:
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler
        await stop_trace_export_retry_worker()
        # キューに残った Run の事実を書いてから終える（間に合わない分は次の起動の backfill）。
        from app.features.agent import run_facts_store

        await asyncio.to_thread(run_facts_store.flush, 5.0)
        # 共通 DB（PLATFORM_ORACLE_*）の接続 pool を閉じる（#793）。
        from app.oracle_connection import close_platform_oracle_pool

        await asyncio.to_thread(close_platform_oracle_pool)


app = create_app(
    service_name=settings.service_name,
    version=settings.app_version,
    cors_origins=settings.cors_origins,
    api_router=api_router,
    readiness_checks_getter=lambda: readiness_checks(get_settings()),
    lifespan=lifespan,
)


@app.exception_handler(StarletteHTTPException)
async def project_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Control Plane error code を envelope のまま機械可読に保つ。"""
    detail = exc.detail
    error_code: str | None = None
    error_details: dict[str, object] = {}
    if isinstance(detail, dict):
        raw_code = detail.get("code")
        raw_message = detail.get("message")
        error_code = raw_code if isinstance(raw_code, str) else None
        if isinstance(raw_message, str) and error_code:
            messages = [f"{error_code}: {raw_message}"]
        else:
            messages = [raw_message] if isinstance(raw_message, str) else [str(detail)]
        error_details = {
            str(key): value for key, value in detail.items() if key not in {"code", "message"}
        }
    elif isinstance(detail, list):
        messages = [str(item) for item in detail]
    else:
        messages = [str(detail)]
    body = ApiResponse[object](data=None, error_messages=messages).model_dump(mode="json")
    if error_code:
        body["error_code"] = error_code
        body["error_details"] = error_details
    headers = dict(exc.headers or {})
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str):
        headers["X-Request-ID"] = request_id
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


def _security_error_response(
    request: Request,
    *,
    status_code: int,
    detail: str,
    code: str,
    title: str | None,
    retryable: bool,
    field_errors: Sequence[Mapping[str, str]],
) -> JSONResponse:
    """認証・認可のエラー。

    共通画面が使う `error_code` と `problem` を足す（NL2SQL / RAG と同じ形）。
    """
    request_id = getattr(request.state, "request_id", None)
    headers = {"X-Request-ID": request_id} if isinstance(request_id, str) else {}
    body = ApiResponse[object](data=None, error_messages=[detail]).model_dump(mode="json")
    body["error_code"] = code
    body["problem"] = {
        "title": title,
        "status": status_code,
        "detail": detail,
        "code": code,
        "request_id": request_id if isinstance(request_id, str) else None,
        "retryable": retryable,
        "field_errors": [dict(item) for item in field_errors],
    }
    return JSONResponse(status_code=status_code, content=body, headers=headers)


@app.exception_handler(SecurityApiError)
async def security_api_error_handler(request: Request, exc: SecurityApiError) -> JSONResponse:
    """認証・認可・ユーザー / ロール操作のエラー（共通認証。#215）。"""
    return _security_error_response(
        request,
        status_code=exc.status_code,
        detail=exc.public_message,
        code=exc.code,
        title=exc.title,
        retryable=exc.retryable,
        field_errors=exc.field_errors,
    )


@app.exception_handler(SecurityMigrationRequired)
async def security_migration_required_handler(
    request: Request, exc: SecurityMigrationRequired
) -> JSONResponse:
    """共通認証・Agent のロール権限の表が未作成（security migration が必要）。"""
    logger.error("security_schema_migration_required", extra={"database_object": exc.object_name})
    return _security_error_response(
        request,
        status_code=409,
        detail=(
            "認証・権限のテーブルが未作成です。"
            "運用設定 > システムテーブル で「作成・更新」を実行するか、"
            "`cd agent/backend && uv run python -m app.cli.agent_system_schema --initialize` を"
            "実行してから再試行してください。"
        ),
        code="SECURITY_SCHEMA_MIGRATION_REQUIRED",
        title="セキュリティ初期化が必要です",
        retryable=False,
        field_errors=(),
    )


async def metrics() -> Response:
    return metrics_response()


app.router.routes.insert(
    0,
    APIRoute("/metrics", metrics, methods=["GET"], include_in_schema=False),
)
