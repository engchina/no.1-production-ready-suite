"""独立した OTel provider、機密を含めない span、W3C context と表示用の記録。

グローバル provider を置き換えず、export は SDK の bounded batch queue に任せる。
本文・SQL・引数・結果・例外の文を収集しない。表示用の記録は sampling と独立させる。

表示用の記録（`TraceSummary` と span のイベント）は画面の処理の経過の唯一の記録で（#3226）、
export の設定（無効・endpoint の有無・sampling）に左右されない。span の表示用のデータ
（`SpanDisplay`。工程の種類・名前の値・開いて見る詳細）は画面の記録だけに入れ、
OTel へ export しない。
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import time_ns
from typing import Literal

from opentelemetry import context, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import AwareDatetime, BaseModel, Field, field_validator

from ..chat_progress import ChatProgressParamValue, ChatProgressStepDetails
from ..config.tracing import TracingSettings
from .request_context import validated_traceparent

logger = logging.getLogger(__name__)
_PROVIDER: TracerProvider | None = None
_PROVIDER_PID: int | None = None
# export を設定していない process（無効・未設定・CLI・テスト）でも表示用の記録を作る provider。
# span processor を持たないので thread を作らず、何も送らない（#3226）。
_UI_PROVIDER: TracerProvider | None = None
_UI_PROVIDER_PID: int | None = None
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
        "mcp.method.name",
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


# 表示用のデータの上限（名前の値の数・長さ。詳細は `ChatProgressStepDetails` の上限）。
_DISPLAY_MAX_PARAMS = 32
_DISPLAY_TEXT_MAX = 200


class SpanDisplay(BaseModel):
    """span の表示用のデータ（画面の記録だけ。OTel へ export しない。#3226）。

    `kind` は製品の工程の定義の種類（画面の名前の key）、`params` は名前・補足を組み立てる値
    （件数・回数・ツール名など）、`details` は開いて見る詳細（処理の経過の段の詳細と同じ形・上限）。
    """

    kind: str | None = Field(default=None, max_length=_DISPLAY_TEXT_MAX)
    params: dict[str, ChatProgressParamValue] | None = None
    details: ChatProgressStepDetails | None = None

    @field_validator("params", mode="before")
    @classmethod
    def validate_params(cls, value: object) -> dict[str, ChatProgressParamValue] | None:
        if not isinstance(value, Mapping):
            return None
        params: dict[str, ChatProgressParamValue] = {}
        for key, item in list(value.items())[:_DISPLAY_MAX_PARAMS]:
            if not isinstance(key, str) or not key or len(key) > _DISPLAY_TEXT_MAX:
                continue
            if isinstance(item, str):
                params[key] = item[:_DISPLAY_TEXT_MAX]
            elif isinstance(item, (bool, int)) or (isinstance(item, float) and math.isfinite(item)):
                params[key] = item
        return params or None

    def merged(
        self,
        *,
        kind: str | None = None,
        params: Mapping[str, object] | None = None,
        details: ChatProgressStepDetails | None = None,
    ) -> SpanDisplay:
        return SpanDisplay.model_validate(
            {
                "kind": kind if kind is not None else self.kind,
                "params": {**(self.params or {}), **dict(params or {})} or None,
                "details": details if details is not None else self.details,
            }
        )


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
    display: SpanDisplay | None = None

    @field_validator("attributes", mode="before")
    @classmethod
    def validate_attributes(cls, value: object) -> dict[str, SafeValue]:
        return _safe_attributes(value) if isinstance(value, Mapping) else {}


type SpanUpdateStatus = Literal["running", "done", "failed", "cancelled", "skipped"]


class SpanUpdate(BaseModel):
    """span の開始・表示用のデータの変更・終了の知らせ（実行中の処理の経過に流す。#3226）。

    開始は `running` で `finished_at` が無い。終了は最終の状態と時刻を持つ。
    """

    span_id: str = Field(pattern="^[0-9a-f]{16}$")
    parent_span_id: str | None = Field(default=None, pattern="^[0-9a-f]{16}$")
    trace_id: str = Field(pattern="^[0-9a-f]{32}$")
    operation_name: str = Field(max_length=128)
    status: SpanUpdateStatus
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    root: bool = False
    span_kind: Literal["internal", "client", "server", "producer", "consumer"] = "internal"
    attributes: dict[str, SafeValue] = Field(default_factory=dict)
    display: SpanDisplay | None = None


type SpanListener = Callable[[SpanUpdate], None]


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
    listener: SpanListener | None = None
    notified: set[str] = field(default_factory=set)

    def notify(self, update: SpanUpdate) -> None:
        """span の開始・変更・終了を知らせる（上限を超えた span は知らせない）。

        listener が失敗しても処理は止めない。
        """
        if self.listener is None:
            return
        with self.lock:
            if update.span_id not in self.notified:
                if not update.root and len(self.notified) >= self.limit:
                    return
                self.notified.add(update.span_id)
        try:
            self.listener(update)
        except Exception as exc:
            logger.warning("trace_span_listener_failed", extra={"error_type": type(exc).__name__})

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
# `operation_trace` が root の span を開いている間だけ True（root の span のイベントに印を付ける）。
_ROOT_PENDING: ContextVar[bool] = ContextVar("trace_root_pending", default=False)


def _ui_provider() -> TracerProvider:
    """export を設定していない process の表示用の provider（process ごとに 1 つ）。

    span processor を持たないので thread を作らない。
    """
    global _UI_PROVIDER, _UI_PROVIDER_PID
    with _LOCK:
        if _UI_PROVIDER is None or os.getpid() != _UI_PROVIDER_PID:
            _UI_PROVIDER = TracerProvider(
                resource=Resource({"service.name": "production-ready-suite"}),
                sampler=ALWAYS_ON,
                shutdown_on_exit=False,
            )
            _UI_PROVIDER_PID = os.getpid()
        return _UI_PROVIDER


def _active_provider() -> tuple[TracerProvider, bool]:
    """span を作る provider と、export の provider か（False は表示用の記録だけ）。"""
    provider = _PROVIDER if os.getpid() == _PROVIDER_PID else None
    return (provider, True) if provider is not None else (_ui_provider(), False)


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
    display: SpanDisplay | None = None
    _finish: Callable[[], None] | None = field(default=None, repr=False)
    _changed: Callable[[], None] | None = field(default=None, repr=False)
    _ended: bool = field(default=False, repr=False)

    def set_display(
        self,
        *,
        kind: str | None = None,
        params: Mapping[str, object] | None = None,
        details: ChatProgressStepDetails | None = None,
    ) -> None:
        """表示用のデータ（工程の種類・名前の値・開いて見る詳細）を足す。画面の記録だけに入れる。

        `params` は前の値に重ねる。実行中の変更は span のイベントで知らせる。
        """
        if self._ended:
            return
        self.display = (self.display or SpanDisplay()).merged(
            kind=kind, params=params, details=details
        )
        if self._changed is not None:
            self._changed()

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
    display: SpanDisplay | None = None,
    links: Sequence[trace.Link] = (),
) -> Iterator[ObservedSpan]:
    """実際の呼出しの範囲に置く。例外の本文や stack trace は span に記録しない。"""
    provider, _ = _active_provider()
    tracer = provider.get_tracer("production-ready-suite")
    start = time_ns()
    span_kind = trace.SpanKind[kind.upper()]
    sdk_span = tracer.start_span(name, kind=span_kind, start_time=start, links=list(links))
    observed = ObservedSpan(sdk_span)
    observed.set_attributes(attributes or {})
    if display is not None:
        observed.display = display
    parent = trace.get_current_span().get_span_context()
    finished = False
    capture = _CAPTURE.get()
    root = capture is not None and capture.root is None and _ROOT_PENDING.get()
    if capture is not None:
        with capture.lock:
            capture.active_count += 1
    identity_at_start = sdk_span.get_span_context()

    def update(status: SpanUpdateStatus, end: int | None = None) -> SpanUpdate | None:
        if capture is None or not identity_at_start.is_valid:
            return None
        return SpanUpdate(
            span_id=f"{identity_at_start.span_id:016x}",
            parent_span_id=f"{parent.span_id:016x}" if parent.is_valid else None,
            trace_id=f"{identity_at_start.trace_id:032x}",
            operation_name=name,
            status=status,
            started_at=datetime.fromtimestamp(start / 1e9, UTC),
            finished_at=datetime.fromtimestamp(end / 1e9, UTC) if end is not None else None,
            root=bool(root),
            span_kind=kind,
            attributes=dict(observed.attributes),
            display=observed.display,
        )

    def changed() -> None:
        if capture is not None and not observed._ended:
            item = update("running")
            if item is not None:
                capture.notify(item)

    observed._changed = changed

    def finish() -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        observed._ended = True
        end = max(start, time_ns())
        sdk_span.end(end_time=end)
        identity = sdk_span.get_span_context()
        if capture is not None and identity.is_valid:
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
                    display=observed.display,
                )
            )
            item = update(observed.status, end)
            if item is not None:
                capture.notify(item)

    observed._finish = finish
    changed()
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
    name: str,
    *,
    operation_id: str,
    attributes: Mapping[str, object] | None = None,
    listener: SpanListener | None = None,
    display: SpanDisplay | None = None,
    kind: str = "internal",
    links: Sequence[trace.Link] = (),
) -> Iterator[TraceSummary]:
    """1 回の実行の表示用 summary。子 task と thread は同じ記録へ追加できる。

    `listener` は span の開始・表示用のデータの変更・終了を受け取る（実行中の処理の経過。#3226）。
    同じ thread で呼ぶとは限らない（thread の span は thread から呼ぶ）ので、製品は自分の保存の
    lock の中で記録する。
    """
    capture = _Capture(
        limit=_SETTINGS.trace_ui_max_spans,
        summary=TraceSummary(operation_id=operation_id),
        listener=listener,
    )
    token = _CAPTURE.set(capture)
    root_token: Token[bool] | None = _ROOT_PENDING.set(True)
    try:
        with observed_span(
            name,
            kind=kind,
            attributes={**(attributes or {}), "pr.operation.id": operation_id},
            display=display,
            links=links,
        ) as root:
            if root_token is not None:
                _ROOT_PENDING.reset(root_token)
                root_token = None
            capture.root = root
            identity = root.span.get_span_context()
            if identity.is_valid:
                capture.summary.root_span_id = f"{identity.span_id:016x}"
                capture.summary.telemetry_trace_id = f"{identity.trace_id:032x}"
                capture.summary.sampled = (
                    bool(identity.trace_flags.sampled) and _PROVIDER is not None
                )
                capture.summary.completeness = "complete"
            yield capture.summary
    finally:
        if root_token is not None:
            _ROOT_PENDING.reset(root_token)
        with capture.lock:
            if capture.active_count:
                capture.summary.completeness = "partial"
            capture.summary.spans.sort(key=lambda span: (span.started_at, span.span_id))
        _CAPTURE.reset(token)


def record_span(
    name: str,
    *,
    started_at: datetime,
    finished_at: datetime,
    status: Literal["done", "failed", "cancelled", "skipped"] = "done",
    kind: str = "internal",
    attributes: Mapping[str, object] | None = None,
    display: SpanDisplay | None = None,
) -> None:
    """終わった区間を後から span として記録する（#3226）。

    待機・承認の待ちなど、実行の外で過ぎた時間に使う。

    今の span の子にし、表示用の記録と export の両方に入れる。時刻は呼んだ側が測った値。
    """
    start = int(started_at.timestamp() * 1e9)
    end = max(start, int(finished_at.timestamp() * 1e9))
    provider, _ = _active_provider()
    tracer = provider.get_tracer("production-ready-suite")
    sdk_span = tracer.start_span(name, kind=trace.SpanKind[kind.upper()], start_time=start)
    observed = ObservedSpan(sdk_span, display=display)
    observed.set_attributes(attributes or {})
    observed.set_status(status)
    parent = trace.get_current_span().get_span_context()
    observed._ended = True
    sdk_span.end(end_time=end)
    capture = _CAPTURE.get()
    identity = sdk_span.get_span_context()
    if capture is None or not identity.is_valid:
        return
    with capture.lock:
        capture.active_count += 1
    capture.append(
        SpanSummary(
            span_id=f"{identity.span_id:016x}",
            parent_span_id=f"{parent.span_id:016x}" if parent.is_valid else None,
            trace_id=f"{identity.trace_id:032x}",
            operation_name=name,
            kind=kind,
            status=status,
            started_at=datetime.fromtimestamp(start / 1e9, UTC),
            finished_at=datetime.fromtimestamp(end / 1e9, UTC),
            duration_ms=(end - start) / 1e6,
            attributes=dict(observed.attributes),
            display=display,
        )
    )
    capture.notify(
        SpanUpdate(
            span_id=f"{identity.span_id:016x}",
            parent_span_id=f"{parent.span_id:016x}" if parent.is_valid else None,
            trace_id=f"{identity.trace_id:032x}",
            operation_name=name,
            status=status,
            started_at=datetime.fromtimestamp(start / 1e9, UTC),
            finished_at=datetime.fromtimestamp(end / 1e9, UTC),
            span_kind=kind,
            attributes=dict(observed.attributes),
            display=display,
        )
    )


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
