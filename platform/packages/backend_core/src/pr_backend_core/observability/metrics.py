"""Prometheus メトリクス + HTTP ミドルウェア（サービス横断で共通）。"""

from collections.abc import Awaitable, Callable
from time import perf_counter

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, make_asgi_app
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from .request_context import generate_request_id, request_id_var

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP リクエスト総数",
    labelnames=("method", "path", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP リクエスト処理時間（秒）",
    labelnames=("method", "path"),
)


def record_http_request(*, method: str, path: str, status: int, seconds: float) -> None:
    """HTTP リクエストのメトリクスを記録する。"""
    HTTP_REQUESTS.labels(method=method, path=path, status=str(status)).inc()
    HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(seconds)


def metrics_asgi_app() -> ASGIApp:
    """Prometheus エクスポジション用 ASGI アプリ（`/metrics` に mount）。"""
    return make_asgi_app()


def _route_path(request: Request) -> str:
    """label cardinality を抑えるため route template を返す。"""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path


class MetricsMiddleware(BaseHTTPMiddleware):
    """request id 付与 + HTTP メトリクス記録を行う共通ミドルウェア。

    - 受信 x-request-id を検証/発行し、`request.state.request_id` と contextvar に設定、
      応答ヘッダ X-Request-ID にも反映する。
    - route template 単位で件数と処理時間を記録する。

    認証や監査コンテキスト等のサービス固有処理は各サービスのミドルウェアで足す。
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started_at = perf_counter()
        request_id = generate_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
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
            request_id_var.reset(token)


__all__ = [
    "CONTENT_TYPE_LATEST",
    "MetricsMiddleware",
    "metrics_asgi_app",
    "record_http_request",
]
