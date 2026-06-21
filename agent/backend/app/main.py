"""FastAPI エントリポイント。共通 app factory で薄く構成する。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pr_backend_core import configure_logging, create_app
from starlette.responses import Response

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


async def metrics() -> Response:
    return metrics_response()


app.router.routes.insert(
    0,
    APIRoute("/metrics", metrics, methods=["GET"], include_in_schema=False),
)
