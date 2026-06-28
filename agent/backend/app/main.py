"""FastAPI エントリポイント。共通 app factory で薄く構成する。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from pr_backend_core import ApiResponse, configure_logging, create_app
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


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await start_trace_export_retry_worker()
    try:
        yield
    finally:
        await stop_trace_export_retry_worker()


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


async def metrics() -> Response:
    return metrics_response()


app.router.routes.insert(
    0,
    APIRoute("/metrics", metrics, methods=["GET"], include_in_schema=False),
)
