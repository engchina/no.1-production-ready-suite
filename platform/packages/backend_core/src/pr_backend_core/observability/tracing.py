"""独立した OTel provider、機密を含めない span、W3C context と表示用の記録。

グローバル provider を置き換えず、export は SDK の bounded batch queue に任せる。
本文・SQL・引数・結果・例外の文を収集しない。表示用の記録は sampling と独立させる。
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import time_ns
from typing import Literal

from opentelemetry import context, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import AwareDatetime, BaseModel, Field, field_validator

from ..config.tracing import TracingSettings
from .request_context import validated_traceparent

logger = logging.getLogger(__name__)
_PROVIDER: TracerProvider | None = None
_PROVIDER_PID: int | None = None
_SETTINGS = TracingSettings()
_LOCK = threading.RLock()
_PROPAGATOR = TraceContextTextMapPropagator()
_SAFE_ATTRIBUTES = frozenset(
    {
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "db.system.name",
        "db.operation.name",
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.tool.name",
        "gen_ai.tool.call.id",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.reasoning.output_tokens",
        "pr.operation.id",
        "pr.operation.status",
        "pr.attempt",
        "pr.cache.hit",
        "pr.result.count",
        "pr.stage",
        "error.type",
    }
)
type SafeValue = str | int | float | bool


_EXPORT_LOG_SCOPE: ContextVar[bool] = ContextVar("safe_trace_export_logs", default=False)


class _ExporterLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _EXPORT_LOG_SCOPE.get():
            record.msg = "trace_export_transport_event"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


_EXPORT_LOG_FILTER = _ExporterLogFilter()


class _SafeExporter(SpanExporter):
    def __init__(self, inner: SpanExporter) -> None:
        self.inner = inner
        for name in (
            "opentelemetry.exporter.otlp.proto.http.trace_exporter",
            "opentelemetry.exporter.otlp.common._internal",
        ):
            logging.getLogger(name).addFilter(_EXPORT_LOG_FILTER)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        token = _EXPORT_LOG_SCOPE.set(True)
        try:
            return self.inner.export(spans)
        except Exception as exc:
            logger.warning("trace_export_failed", extra={"error_type": type(exc).__name__})
            return SpanExportResult.FAILURE
        finally:
            _EXPORT_LOG_SCOPE.reset(token)

    def shutdown(self) -> None:
        token = _EXPORT_LOG_SCOPE.set(True)
        try:
            self.inner.shutdown()
        except Exception as exc:
            logger.warning("trace_export_shutdown_failed", extra={"error_type": type(exc).__name__})
        finally:
            _EXPORT_LOG_SCOPE.reset(token)


class SpanSummary(BaseModel):
    span_id: str = Field(pattern="^[0-9a-f]{16}$")
    parent_span_id: str | None = Field(default=None, pattern="^[0-9a-f]{16}$")
    trace_id: str = Field(pattern="^[0-9a-f]{32}$")
    operation_name: str = Field(max_length=128)
    kind: str
    status: Literal["done", "failed", "cancelled", "skipped"]
    started_at: AwareDatetime
    finished_at: AwareDatetime
    duration_ms: float = Field(ge=0)
    timing_source: Literal["measured"] = "measured"
    attributes: dict[str, SafeValue] = Field(default_factory=dict)

    @field_validator("attributes", mode="before")
    @classmethod
    def validate_attributes(cls, value: object) -> dict[str, SafeValue]:
        return _safe_attributes(value) if isinstance(value, Mapping) else {}


class TraceSummary(BaseModel):
    schema_version: Literal[1] = 1
    operation_id: str = ""
    root_span_id: str | None = None
    telemetry_trace_id: str | None = None
    completeness: Literal["complete", "partial", "unavailable"] = "unavailable"
    spans: list[SpanSummary] = Field(default_factory=list)
    truncated: bool = False
    sampled: bool = False


@dataclass
class _Capture:
    limit: int
    summary: TraceSummary = field(default_factory=TraceSummary)
    lock: threading.Lock = field(default_factory=threading.Lock)
    root: ObservedSpan | None = None
    active_count: int = 0

    def append(self, span: SpanSummary) -> None:
        with self.lock:
            self.active_count = max(0, self.active_count - 1)
            if len(self.summary.spans) >= self.limit:
                self.summary.truncated = True
                self.summary.completeness = "partial"
                if span.span_id == self.summary.root_span_id:
                    self.summary.spans[-1] = span
            else:
                self.summary.spans.append(span)


_CAPTURE: ContextVar[_Capture | None] = ContextVar("trace_capture", default=None)


def configure_tracing(
    settings: TracingSettings, *, service_name: str, exporter: SpanExporter | None = None
) -> None:
    """lifespan / worker の起動後に呼ぶ。import 時や fork 前には thread を起動しない。"""
    global _PROVIDER, _PROVIDER_PID, _SETTINGS
    with _LOCK:
        if _PROVIDER is not None and os.getpid() == _PROVIDER_PID:
            raise RuntimeError(
                "trace provider は既に初期化されています。終了してから初期化してください。"
            )
        _SETTINGS = settings
        _PROVIDER_PID = os.getpid()
        _PROVIDER = None
        if not settings.trace_enabled:
            return
        provider = TracerProvider(
            resource=Resource({"service.name": service_name}),
            sampler=ParentBased(TraceIdRatioBased(settings.trace_sample_rate)),
            shutdown_on_exit=False,
        )
        if exporter is None and settings.trace_otlp_endpoint:
            headers = {}
            if settings.trace_otlp_api_key:
                headers["Authorization"] = (
                    f"Bearer {settings.trace_otlp_api_key.get_secret_value()}"
                )
            exporter = OTLPSpanExporter(
                endpoint=settings.trace_otlp_endpoint,
                headers=headers,
                timeout=settings.trace_export_timeout_seconds,
            )
        if exporter is not None:
            provider.add_span_processor(
                BatchSpanProcessor(
                    _SafeExporter(exporter),
                    max_queue_size=settings.trace_queue_size,
                    max_export_batch_size=settings.trace_batch_size,
                    export_timeout_millis=int(settings.trace_export_timeout_seconds * 1000),
                )
            )
        _PROVIDER = provider


@contextmanager
def tracing_lifecycle(settings: TracingSettings, *, service_name: str) -> Iterator[None]:
    configure_tracing(settings, service_name=service_name)
    try:
        yield
    finally:
        shutdown_tracing()


def shutdown_tracing() -> bool:
    """呼び出し側の停止を設定の期限内に返す。export の停止は daemon で完了させる。"""
    global _PROVIDER, _PROVIDER_PID
    with _LOCK:
        provider, _PROVIDER = _PROVIDER, None
        _PROVIDER_PID = None
        timeout = _SETTINGS.trace_shutdown_timeout_seconds
    if provider is None:
        return True
    stopped = threading.Event()

    def stop() -> None:
        try:
            provider.shutdown()
        finally:
            stopped.set()

    threading.Thread(target=stop, name="trace-shutdown", daemon=True).start()
    finished = stopped.wait(timeout)
    if not finished:
        logger.warning("trace_shutdown_deadline_exceeded")
    return finished


def _safe_attributes(values: Mapping[str, object]) -> dict[str, SafeValue]:
    return {
        key: value[:128] if isinstance(value, str) else value
        for key, value in values.items()
        if key in _SAFE_ATTRIBUTES
        and isinstance(value, (str, int, float, bool))
        and (not isinstance(value, float) or math.isfinite(value))
    }


def capture_trace_context() -> dict[str, str]:
    """キュー / MCP へ渡す W3C traceparent だけ。baggage・認証・本文は保存しない。"""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    parent = validated_traceparent(carrier.get("traceparent"))
    return {"traceparent": parent} if parent else {}


@contextmanager
def attach_trace_context(carrier: Mapping[str, str]) -> Iterator[None]:
    parent = validated_traceparent(carrier.get("traceparent"))
    token = context.attach(
        _PROPAGATOR.extract({"traceparent": parent}) if parent else context.Context()
    )
    try:
        yield
    finally:
        context.detach(token)


def current_span_fields() -> dict[str, str]:
    span = trace.get_current_span().get_span_context()
    if not span.is_valid:
        return {}
    identity = "parent_span_id" if span.is_remote else "span_id"
    return {
        "trace_id": f"{span.trace_id:032x}",
        identity: f"{span.span_id:016x}",
        "trace_flags": f"{int(span.trace_flags):02x}",
    }


@dataclass
class ObservedSpan:
    span: trace.Span
    attributes: dict[str, SafeValue] = field(default_factory=dict)
    status: Literal["done", "failed", "cancelled", "skipped"] = "done"
    _finish: Callable[[], None] | None = field(default=None, repr=False)
    _ended: bool = field(default=False, repr=False)

    def finish(self) -> None:
        if self._finish is not None:
            self._finish()

    def set_attributes(self, values: Mapping[str, object]) -> None:
        if self._ended:
            return
        safe = _safe_attributes(values)
        self.attributes.update(safe)
        self.span.set_attributes(safe)

    def set_status(self, status: str) -> None:
        if self._ended:
            return
        if status == "done":
            self.status = "done"
        elif status == "failed":
            self.status = "failed"
        elif status == "cancelled":
            self.status = "cancelled"
        elif status == "skipped":
            self.status = "skipped"
        self.set_attributes({"pr.operation.status": status})
        if status == "failed":
            self.span.set_status(trace.Status(trace.StatusCode.ERROR))


@contextmanager
def observed_span(
    name: str,
    *,
    kind: str = "internal",
    attributes: Mapping[str, object] | None = None,
    finish_on_exit: bool = True,
    cancellation_check: Callable[[], bool] | None = None,
) -> Iterator[ObservedSpan]:
    """実際の呼出しの範囲に置く。例外の本文や stack trace は span に記録しない。"""
    provider = _PROVIDER if os.getpid() == _PROVIDER_PID else None
    tracer = provider.get_tracer("production-ready-suite") if provider else trace.NoOpTracer()
    start = time_ns()
    span_kind = trace.SpanKind[kind.upper()]
    sdk_span = tracer.start_span(name, kind=span_kind, start_time=start)
    observed = ObservedSpan(sdk_span)
    observed.set_attributes(attributes or {})
    parent = trace.get_current_span().get_span_context()
    finished = False
    capture = _CAPTURE.get()
    if capture is not None and provider is not None:
        with capture.lock:
            capture.active_count += 1

    def finish() -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        observed._ended = True
        end = max(start, time_ns())
        sdk_span.end(end_time=end)
        identity = sdk_span.get_span_context()
        if capture is not None and provider is not None and identity.is_valid:
            capture.append(
                SpanSummary(
                    span_id=f"{identity.span_id:016x}",
                    parent_span_id=f"{parent.span_id:016x}" if parent.is_valid else None,
                    trace_id=f"{identity.trace_id:032x}",
                    operation_name=name,
                    kind=kind,
                    status=observed.status,
                    started_at=datetime.fromtimestamp(start / 1e9, UTC),
                    finished_at=datetime.fromtimestamp(end / 1e9, UTC),
                    duration_ms=(end - start) / 1e6,
                    attributes=dict(observed.attributes),
                )
            )

    observed._finish = finish
    try:
        with trace.use_span(
            sdk_span, end_on_exit=False, record_exception=False, set_status_on_exception=False
        ):
            yield observed
    except BaseException as exc:
        observed.set_attributes({"error.type": type(exc).__name__})
        cancelled = isinstance(exc, asyncio.CancelledError) or bool(
            cancellation_check and cancellation_check()
        )
        observed.set_status("cancelled" if cancelled else "failed")
        observed.finish()
        raise
    finally:
        if finish_on_exit:
            observed.finish()


@contextmanager
def operation_trace(
    name: str, *, operation_id: str, attributes: Mapping[str, object] | None = None
) -> Iterator[TraceSummary]:
    """1 回の実行の表示用 summary。子 task と thread は同じ記録へ追加できる。"""
    capture = _Capture(
        limit=_SETTINGS.trace_ui_max_spans, summary=TraceSummary(operation_id=operation_id)
    )
    token = _CAPTURE.set(capture)
    try:
        with observed_span(
            name, attributes={**(attributes or {}), "pr.operation.id": operation_id}
        ) as root:
            capture.root = root
            identity = root.span.get_span_context()
            if _PROVIDER is not None and identity.is_valid:
                capture.summary.root_span_id = f"{identity.span_id:016x}"
                capture.summary.telemetry_trace_id = f"{identity.trace_id:032x}"
                capture.summary.sampled = bool(identity.trace_flags.sampled)
                capture.summary.completeness = "complete"
            yield capture.summary
    finally:
        with capture.lock:
            if capture.active_count:
                capture.summary.completeness = "partial"
            capture.summary.spans.sort(key=lambda span: (span.started_at, span.span_id))
        _CAPTURE.reset(token)


def finish_operation_trace(operation_id: str, *, status: str = "done") -> TraceSummary | None:
    """実行を確定する既存の排他・fencing の内側から呼ぶ。別の実行の記録には触れない。"""
    capture = _CAPTURE.get()
    if capture is None or capture.summary.operation_id != operation_id or capture.root is None:
        return None
    capture.root.set_status(status)
    capture.root.finish()
    with capture.lock:
        if capture.active_count:
            capture.summary.completeness = "partial"
        capture.summary.spans.sort(key=lambda span: (span.started_at, span.span_id))
        return capture.summary.model_copy(deep=True)


def validated_trace_context(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    incoming = value.get("traceparent")
    parent = validated_traceparent(incoming) if isinstance(incoming, str) else None
    return {"traceparent": parent} if parent else {}


def snapshot_operation_trace() -> TraceSummary | None:
    """処理途中の保存用。終了していない root は再建せず、部分記録とする。"""
    capture = _CAPTURE.get()
    if capture is None:
        return None
    with capture.lock:
        summary = capture.summary.model_copy(deep=True)
    if capture.root is not None and not capture.root._ended:
        summary.completeness = "partial"
    summary.spans.sort(key=lambda span: (span.started_at, span.span_id))
    return summary


def append_trace_segment(
    segments: Sequence[TraceSummary], summary: TraceSummary | None, *, limit: int
) -> list[TraceSummary]:
    """同じ実行区間を重ねず、再開・再試行の表示用の記録を上限まで保持する。"""
    result = list(segments)
    if summary is None:
        return result
    previous = next((item for item in result if item.root_span_id == summary.root_span_id), None)
    if previous is not None:
        if previous.completeness == "partial":
            summary.completeness = "partial"
        return result
    result.append(summary)
    if len(result) > max(1, limit):
        summary.completeness = "partial"
        result = result[-max(1, limit) :]
    return result
