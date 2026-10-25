"""製品をまたぐ MCP の呼び出しの trace の受け渡し（#3226 の案 A）。

- 送る側（Agent）は W3C の `traceparent` を、HTTP header（今どおり）と JSON-RPC の `params._meta`
  （OpenTelemetry の MCP の semantic conventions の置き場所）の両方に入れる（`mcp_trace_meta`）。
- 受ける側（RAG / NL2SQL の `POST /api/mcp`）は `mcp_server_trace` の中で要求を処理する。
  **サービストークン（Agent からの呼び出し）の traceparent だけを親にする**（`params._meta`
  を優先し、
  無ければ header）。受けた要求の server の span を共通の span の表に書き、Agent の `mcp.request` の
  下に木をつなげる。
- API キーの外部の MCP クライアントとブラウザの traceparent は親にせず、新しい trace を始める
  （他人の trace に span を差し込めないようにする）。相関のため、外部の span context は
  OpenTelemetry の span link として新しい trace の root に結ぶ（export だけ。表には入れない）。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import context, trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .request_context import validated_traceparent
from .tracing import SpanListener, TraceSummary, capture_trace_context, operation_trace

_PROPAGATOR = TraceContextTextMapPropagator()
MCP_SERVER_SPAN = "mcp.server"


def mcp_trace_meta() -> dict[str, str]:
    """JSON-RPC の `params._meta` に入れる trace の値（今の span の traceparent。無ければ空）。"""
    return capture_trace_context()


def incoming_mcp_traceparent(
    meta: Mapping[str, object] | None, header_traceparent: str | None
) -> str | None:
    """来た traceparent（`params._meta` を優先し、無ければ HTTP header）。形の違う値は捨てる。"""
    candidate = meta.get("traceparent") if isinstance(meta, Mapping) else None
    from_meta = validated_traceparent(candidate) if isinstance(candidate, str) else None
    return from_meta or validated_traceparent(header_traceparent)


def _span_context(traceparent: str) -> trace.SpanContext | None:
    extracted = _PROPAGATOR.extract({"traceparent": traceparent})
    span_context = trace.get_current_span(extracted).get_span_context()
    return span_context if span_context.is_valid else None


@contextmanager
def mcp_server_trace(
    *,
    operation_id: str,
    trusted: bool,
    traceparent: str | None,
    listener: SpanListener | None = None,
) -> Iterator[TraceSummary]:
    """MCP の要求を server の span（`mcp.server`）の中で処理する。

    `trusted`（サービストークンで認証した要求）なら来た traceparent を親にする。そうでなければ新しい
    trace を始め、来た span context は span link にだけ残す。`listener` は共通の span
    の表への書き込み
    （`TraceSpanWriter.listener(...)`）で、server の span は実行の全体の span として表に入る。
    """
    parent = traceparent if trusted else None
    linked = _span_context(traceparent) if traceparent and not trusted else None
    carrier_context = _PROPAGATOR.extract({"traceparent": parent}) if parent else context.Context()
    token = context.attach(carrier_context)
    try:
        with operation_trace(
            MCP_SERVER_SPAN,
            operation_id=operation_id,
            listener=listener,
            kind="server",
            links=[trace.Link(linked)] if linked is not None else (),
        ) as summary:
            yield summary
    finally:
        context.detach(token)


__all__ = [
    "MCP_SERVER_SPAN",
    "incoming_mcp_traceparent",
    "mcp_server_trace",
    "mcp_trace_meta",
]
