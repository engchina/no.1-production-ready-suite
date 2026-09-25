"""Agent Runtime の観測性ヘルパー。

Prometheus は依存に含まれているため即時に公開し、Langfuse / OpenTelemetry は
Secret 設定の有無を状態として露出する。SDK exporter は本番接続情報が揃った段階で差し込む。
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import Response

JsonObject = dict[str, Any]
TRACE_EVENTS: deque[TraceEvent] = deque()
TRACE_EVENTS_LOCK = Lock()
TRACE_EXPORT_STATUS_LOCK = Lock()
TRACE_POLICY_LOCK = Lock()
TRACE_EXPORT_RETRY_QUEUE: deque[TraceExportRetryItem] = deque()
TRACE_EXPORT_RETRY_QUEUE_LOCK = Lock()
TRACE_EXPORT_RETRY_WORKER_TASK: asyncio.Task[None] | None = None
TRACE_EXPORT_RETRY_WORKER_EXECUTOR: ThreadPoolExecutor | None = None

HTTP_REQUESTS = Counter(
    "agent_http_requests_total",
    "HTTP request count.",
    ("method", "path", "status"),
)
HTTP_LATENCY = Histogram(
    "agent_http_request_duration_seconds",
    "HTTP request latency.",
    ("method", "path"),
)
RUNTIME_EVENTS = Counter(
    "agent_runtime_events_total",
    "Agent runtime event count.",
    ("event_type",),
)
RUNS_TOTAL = Counter(
    "agent_runs_total",
    "Agent run terminal status count.",
    ("status",),
)
TOOL_CALLS_TOTAL = Counter(
    "agent_tool_calls_total",
    "Agent tool call result count.",
    ("tool", "status"),
)
APPROVALS_TOTAL = Counter(
    "agent_approvals_total",
    "Agent approval decision count.",
    ("tool", "decision"),
)
GUARDRAIL_WARNINGS_TOTAL = Counter(
    "agent_guardrail_warnings_total",
    "Agent guardrail warning count.",
    ("code",),
)
ARTIFACTS_TOTAL = Counter(
    "agent_artifacts_total",
    "Agent artifact count.",
    ("kind",),
)
MEMORY_WRITES_TOTAL = Counter(
    "agent_memory_writes_total",
    "Agent memory write count.",
    ("kind",),
)
MEMORY_ENTRIES_CURRENT = Gauge(
    "agent_memory_entries_current",
    "Current in-process Agent memory entries.",
)


class TraceExporterStatus(BaseModel):
    configured: bool = False
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    retry_queue_size: int = 0
    retry_queue_max_size: int = 0
    retry_max_attempts: int = 0
    retry_worker_enabled: bool = False
    retry_worker_running: bool = False
    retry_worker_interval_seconds: float = 0.0


class ObservabilityStatus(BaseModel):
    metrics_enabled: bool
    prometheus_metrics_path: str
    trace_events_enabled: bool
    trace_events_buffer_size: int
    trace_events_retention_seconds: int
    trace_sample_rate: float
    trace_exporter_configured: bool
    trace_exporter_last_success_at: datetime | None = None
    trace_exporter_last_error: str | None = None
    retry_queue_size: int = 0
    retry_queue_max_size: int = 0
    retry_max_attempts: int = 0
    retry_worker_enabled: bool = False
    retry_worker_running: bool = False
    retry_worker_interval_seconds: float = 0.0
    langfuse_configured: bool
    opentelemetry_configured: bool


class TraceEvent(BaseModel):
    id: str
    event_type: str
    run_id: str | None = None
    step_id: str | None = None
    tool_name: str | None = None
    trace_id: str | None = None
    attributes: JsonObject
    created_at: datetime
    sampled: bool = True


class TraceEventsData(BaseModel):
    total: int
    events: list[TraceEvent]


class TraceExportRetryItem(BaseModel):
    event: TraceEvent
    attempts: int = 1
    last_error: str
    queued_at: datetime
    next_retry_at: datetime


class TraceExportRetryData(BaseModel):
    attempted: int = 0
    succeeded: int = 0
    requeued: int = 0
    dropped: int = 0
    skipped: int = 0
    queue_size: int = 0


class TracePolicySettings(BaseModel):
    trace_events_enabled: bool = True
    trace_events_buffer_size: int = 500
    trace_events_retention_seconds: int = 86_400
    trace_sample_rate: float = 1.0


class TracePolicyPatch(BaseModel):
    trace_events_enabled: bool | None = None
    trace_events_buffer_size: int | None = None
    trace_events_retention_seconds: int | None = None
    trace_sample_rate: float | None = None


TRACE_EXPORT_STATUS = TraceExporterStatus()
TRACE_POLICY_OVERRIDE = TracePolicyPatch()


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def request_path_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return request.url.path


def observe_http_request(method: str, path: str, status_code: int, duration_seconds: float) -> None:
    status = str(status_code)
    HTTP_REQUESTS.labels(method=method, path=path, status=status).inc()
    HTTP_LATENCY.labels(method=method, path=path).observe(duration_seconds)


def record_runtime_event(event_type: str, payload: JsonObject) -> None:
    RUNTIME_EVENTS.labels(event_type=event_type).inc()
    if event_type == "run.completed":
        RUNS_TOTAL.labels(status="completed").inc()
    elif event_type == "run.cancelled":
        RUNS_TOTAL.labels(status="cancelled").inc()
    elif event_type == "tool.completed":
        TOOL_CALLS_TOTAL.labels(tool=_payload_text(payload, "tool_name"), status="success").inc()
    elif event_type == "tool.failed":
        TOOL_CALLS_TOTAL.labels(tool=_payload_text(payload, "tool_name"), status="failed").inc()
        RUNS_TOTAL.labels(status="failed").inc()
    elif event_type == "approval.decided":
        decision = "approved" if payload.get("approved") is True else "rejected"
        APPROVALS_TOTAL.labels(tool=_payload_text(payload, "tool_name"), decision=decision).inc()
    elif event_type == "tool.guardrail_warning":
        for warning in _payload_list(payload, "warnings"):
            GUARDRAIL_WARNINGS_TOTAL.labels(code=warning).inc()
    elif event_type == "artifact.created":
        ARTIFACTS_TOTAL.labels(kind=_payload_text(payload, "kind")).inc()
    elif event_type == "memory.written":
        MEMORY_WRITES_TOTAL.labels(kind=_payload_text(payload, "kind")).inc()
    _append_trace_event(event_type, payload)


def list_trace_events(
    *,
    event_type: str | None = None,
    run_id: str | None = None,
    tool_name: str | None = None,
    limit: int = 100,
) -> TraceEventsData:
    policy = get_trace_policy()
    with TRACE_EVENTS_LOCK:
        _prune_trace_events_locked(datetime.now(UTC), policy)
        events = list(TRACE_EVENTS)
    filtered = [
        event
        for event in reversed(events)
        if (event_type is None or event.event_type == event_type)
        and (run_id is None or event.run_id == run_id)
        and (tool_name is None or event.tool_name == tool_name)
    ]
    return TraceEventsData(total=len(filtered), events=filtered[:limit])


def get_trace_policy() -> TracePolicySettings:
    from app.settings import get_settings

    settings = get_settings()
    policy = TracePolicySettings(
        trace_events_enabled=settings.agent_trace_events_enabled,
        trace_events_buffer_size=settings.agent_trace_events_buffer_size,
        trace_events_retention_seconds=settings.agent_trace_events_retention_seconds,
        trace_sample_rate=settings.agent_trace_sample_rate,
    )
    with TRACE_POLICY_LOCK:
        override = TRACE_POLICY_OVERRIDE.model_dump()
    data = policy.model_dump()
    for key, value in override.items():
        if value is not None:
            data[key] = value
    return TracePolicySettings.model_validate(data)


def patch_trace_policy(patch: TracePolicyPatch) -> TracePolicySettings:
    if patch.trace_events_buffer_size is not None and patch.trace_events_buffer_size < 1:
        raise ValueError("trace_events_buffer_size must be greater than or equal to 1")
    if (
        patch.trace_events_retention_seconds is not None
        and patch.trace_events_retention_seconds < 0
    ):
        raise ValueError("trace_events_retention_seconds must be greater than or equal to 0")
    if patch.trace_sample_rate is not None and not 0.0 <= patch.trace_sample_rate <= 1.0:
        raise ValueError("trace_sample_rate must be between 0.0 and 1.0")
    with TRACE_POLICY_LOCK:
        for key, value in patch.model_dump().items():
            if value is not None:
                setattr(TRACE_POLICY_OVERRIDE, key, value)
    policy = get_trace_policy()
    with TRACE_EVENTS_LOCK:
        _prune_trace_events_locked(datetime.now(UTC), policy)
    return policy


def reset_trace_policy_overrides() -> None:
    with TRACE_POLICY_LOCK:
        for key in TracePolicyPatch.model_fields:
            setattr(TRACE_POLICY_OVERRIDE, key, None)


async def start_trace_export_retry_worker() -> None:
    """到期済み exporter retry event をバックグラウンドで再送する。"""

    global TRACE_EXPORT_RETRY_WORKER_EXECUTOR, TRACE_EXPORT_RETRY_WORKER_TASK

    from app.settings import get_settings

    if not get_settings().agent_trace_exporter_retry_worker_enabled:
        return
    if TRACE_EXPORT_RETRY_WORKER_TASK is not None and not TRACE_EXPORT_RETRY_WORKER_TASK.done():
        return
    if TRACE_EXPORT_RETRY_WORKER_EXECUTOR is None:
        TRACE_EXPORT_RETRY_WORKER_EXECUTOR = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="agent-trace-export-retry",
        )
    TRACE_EXPORT_RETRY_WORKER_TASK = asyncio.create_task(
        _trace_export_retry_worker_loop(),
        name="agent-trace-export-retry-worker",
    )


async def stop_trace_export_retry_worker() -> None:
    """起動済み exporter retry worker を停止する。"""

    global TRACE_EXPORT_RETRY_WORKER_EXECUTOR, TRACE_EXPORT_RETRY_WORKER_TASK

    task = TRACE_EXPORT_RETRY_WORKER_TASK
    executor = TRACE_EXPORT_RETRY_WORKER_EXECUTOR
    TRACE_EXPORT_RETRY_WORKER_TASK = None
    TRACE_EXPORT_RETRY_WORKER_EXECUTOR = None
    if task is None:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)


def trace_export_retry_worker_running() -> bool:
    task = TRACE_EXPORT_RETRY_WORKER_TASK
    return task is not None and not task.done()


def trace_exporter_status() -> TraceExporterStatus:
    from app.settings import get_settings

    settings = get_settings()
    with TRACE_EXPORT_STATUS_LOCK:
        status = TRACE_EXPORT_STATUS.model_copy()
    with TRACE_EXPORT_RETRY_QUEUE_LOCK:
        retry_queue_size = len(TRACE_EXPORT_RETRY_QUEUE)
    status.configured = bool(
        settings.agent_trace_exporter_url
        or settings.agent_opentelemetry_endpoint
        or _langfuse_configured(settings)
    )
    status.retry_queue_size = retry_queue_size
    status.retry_queue_max_size = max(0, settings.agent_trace_exporter_retry_queue_size)
    status.retry_max_attempts = max(1, settings.agent_trace_exporter_retry_max_attempts)
    status.retry_worker_enabled = settings.agent_trace_exporter_retry_worker_enabled
    status.retry_worker_running = trace_export_retry_worker_running()
    status.retry_worker_interval_seconds = _trace_retry_worker_interval_seconds()
    return status


def observe_memory_entries(count: int) -> None:
    MEMORY_ENTRIES_CURRENT.set(count)


def observe_request_start() -> float:
    return perf_counter()


def elapsed_since(started_at: float) -> float:
    return perf_counter() - started_at


def _append_trace_event(event_type: str, payload: JsonObject) -> None:
    policy = get_trace_policy()
    if not policy.trace_events_enabled:
        return
    event = TraceEvent(
        id=f"trace_event_{uuid4().hex}",
        event_type=event_type,
        run_id=_payload_text_or_none(payload, "run_id"),
        step_id=_payload_text_or_none(payload, "step_id"),
        tool_name=_payload_text_or_none(payload, "tool_name"),
        trace_id=_trace_id_from_payload(payload),
        attributes=_safe_trace_attributes(event_type, payload),
        created_at=datetime.now(UTC),
    )
    if not _should_sample_trace_event(event, policy.trace_sample_rate):
        return
    with TRACE_EVENTS_LOCK:
        TRACE_EVENTS.append(event)
        _prune_trace_events_locked(event.created_at, policy)
    _export_trace_event(event)


def _prune_trace_events_locked(now: datetime, policy: TracePolicySettings) -> None:
    max_events = max(1, policy.trace_events_buffer_size)
    while len(TRACE_EVENTS) > max_events:
        TRACE_EVENTS.popleft()
    if policy.trace_events_retention_seconds <= 0:
        return
    cutoff = now - timedelta(seconds=policy.trace_events_retention_seconds)
    while TRACE_EVENTS and TRACE_EVENTS[0].created_at < cutoff:
        TRACE_EVENTS.popleft()


def _should_sample_trace_event(event: TraceEvent, sample_rate: float) -> bool:
    if _is_trace_priority_event(event):
        return True
    bounded_rate = min(1.0, max(0.0, sample_rate))
    if bounded_rate >= 1.0:
        return True
    if bounded_rate <= 0.0:
        return False
    basis = event.trace_id or event.run_id or event.id
    digest = sha256(f"{basis}:{event.event_type}".encode()).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return bucket < bounded_rate


def _is_trace_priority_event(event: TraceEvent) -> bool:
    if event.event_type.endswith(".failed"):
        return True
    if event.event_type in {"tool.guardrail_warning", "approval.decided"}:
        return True
    return bool(event.attributes.get("error_code"))


def _export_trace_event(event: TraceEvent) -> None:
    errors, exported = _export_trace_event_once(event)
    if errors:
        message = ";".join(errors)
        _mark_trace_export_error(message)
        _enqueue_trace_export_retry(event, message)
    elif exported:
        _mark_trace_export_success()


def flush_trace_export_retry_queue(
    limit: int = 100, *, force: bool = False
) -> TraceExportRetryData:
    from app.settings import get_settings

    settings = get_settings()
    max_attempts = max(1, settings.agent_trace_exporter_retry_max_attempts)
    bounded_limit = max(1, limit)
    with TRACE_EXPORT_RETRY_QUEUE_LOCK:
        items = [
            TRACE_EXPORT_RETRY_QUEUE.popleft()
            for _ in range(min(bounded_limit, len(TRACE_EXPORT_RETRY_QUEUE)))
        ]
    data = TraceExportRetryData()
    now = datetime.now(UTC)
    for item in items:
        if not force and item.next_retry_at > now:
            _requeue_trace_export_retry(item)
            data.skipped += 1
            continue
        data.attempted += 1
        errors, exported = _export_trace_event_once(item.event)
        if errors:
            item.attempts += 1
            item.last_error = ";".join(errors)
            if item.attempts >= max_attempts:
                data.dropped += 1
                _mark_trace_export_error(item.last_error)
            else:
                item.next_retry_at = _next_trace_retry_at(item.attempts)
                _requeue_trace_export_retry(item)
                data.requeued += 1
        elif exported:
            data.succeeded += 1
            _mark_trace_export_success()
    with TRACE_EXPORT_RETRY_QUEUE_LOCK:
        data.queue_size = len(TRACE_EXPORT_RETRY_QUEUE)
    return data


async def _trace_export_retry_worker_loop() -> None:
    while True:
        try:
            with TRACE_EXPORT_RETRY_QUEUE_LOCK:
                has_retry_items = bool(TRACE_EXPORT_RETRY_QUEUE)
            if has_retry_items:
                flush_due_items = partial(
                    flush_trace_export_retry_queue,
                    limit=_trace_retry_worker_batch_size(),
                    force=False,
                )
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    TRACE_EXPORT_RETRY_WORKER_EXECUTOR,
                    flush_due_items,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - worker 失敗は runtime を止めない
            _mark_trace_export_error(f"retry_worker:{_safe_trace_export_error(exc)}")
        await asyncio.sleep(_trace_retry_worker_interval_seconds())


def clear_trace_export_retry_queue() -> None:
    with TRACE_EXPORT_RETRY_QUEUE_LOCK:
        TRACE_EXPORT_RETRY_QUEUE.clear()


def _export_trace_event_once(event: TraceEvent) -> tuple[list[str], bool]:
    from app.settings import get_settings

    settings = get_settings()
    if (
        not settings.agent_trace_exporter_url
        and not settings.agent_opentelemetry_endpoint
        and not _langfuse_configured(settings)
    ):
        return [], False
    errors: list[str] = []
    exported = False
    if settings.agent_trace_exporter_url:
        try:
            _export_trace_event_webhook(event)
            exported = True
        except Exception as exc:  # noqa: BLE001 - exporter は runtime を止めない
            errors.append(f"webhook:{_safe_trace_export_error(exc)}")
    if settings.agent_opentelemetry_endpoint:
        try:
            _export_trace_event_otlp(event)
            exported = True
        except Exception as exc:  # noqa: BLE001 - exporter は runtime を止めない
            errors.append(f"otlp:{_safe_trace_export_error(exc)}")
    if _langfuse_configured(settings):
        try:
            _export_trace_event_langfuse(event)
            exported = True
        except Exception as exc:  # noqa: BLE001 - exporter は runtime を止めない
            errors.append(f"langfuse:{_safe_trace_export_error(exc)}")
    return errors, exported


def _enqueue_trace_export_retry(event: TraceEvent, last_error: str) -> None:
    from app.settings import get_settings

    settings = get_settings()
    max_size = max(0, settings.agent_trace_exporter_retry_queue_size)
    if max_size <= 0:
        return
    item = TraceExportRetryItem(
        event=event,
        attempts=1,
        last_error=last_error,
        queued_at=datetime.now(UTC),
        next_retry_at=_next_trace_retry_at(1),
    )
    with TRACE_EXPORT_RETRY_QUEUE_LOCK:
        TRACE_EXPORT_RETRY_QUEUE.append(item)
        while len(TRACE_EXPORT_RETRY_QUEUE) > max_size:
            TRACE_EXPORT_RETRY_QUEUE.popleft()


def _requeue_trace_export_retry(item: TraceExportRetryItem) -> None:
    from app.settings import get_settings

    settings = get_settings()
    max_size = max(0, settings.agent_trace_exporter_retry_queue_size)
    if max_size <= 0:
        return
    with TRACE_EXPORT_RETRY_QUEUE_LOCK:
        TRACE_EXPORT_RETRY_QUEUE.append(item)
        while len(TRACE_EXPORT_RETRY_QUEUE) > max_size:
            TRACE_EXPORT_RETRY_QUEUE.popleft()


def _next_trace_retry_at(attempts: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=_trace_retry_delay_seconds(attempts))


def _trace_retry_delay_seconds(attempts: int) -> float:
    from app.settings import get_settings

    settings = get_settings()
    base_delay = float(max(0.0, settings.agent_trace_exporter_retry_base_delay_seconds))
    max_delay = float(max(base_delay, settings.agent_trace_exporter_retry_max_delay_seconds))
    return float(min(max_delay, base_delay * (2 ** max(0, attempts - 1))))


def _trace_retry_worker_interval_seconds() -> float:
    from app.settings import get_settings

    return float(max(0.01, get_settings().agent_trace_exporter_retry_worker_interval_seconds))


def _trace_retry_worker_batch_size() -> int:
    from app.settings import get_settings

    return max(1, int(get_settings().agent_trace_exporter_retry_worker_batch_size))


def _export_trace_event_webhook(event: TraceEvent) -> None:
    from app.settings import get_settings

    settings = get_settings()
    if settings.agent_trace_exporter_url is None:
        return
    payload: JsonObject = {
        "source": settings.service_name,
        "event": event.model_dump(mode="json"),
    }
    with httpx.Client(timeout=settings.agent_trace_exporter_timeout_seconds) as client:
        response = client.post(
            settings.agent_trace_exporter_url,
            json=payload,
            headers=_trace_export_headers(settings.agent_trace_exporter_api_key),
        )
        response.raise_for_status()


def _export_trace_event_otlp(event: TraceEvent) -> None:
    from app.settings import get_settings

    settings = get_settings()
    if settings.agent_opentelemetry_endpoint is None:
        return
    with httpx.Client(timeout=settings.agent_trace_exporter_timeout_seconds) as client:
        response = client.post(
            _otlp_traces_endpoint(settings.agent_opentelemetry_endpoint),
            json=_otlp_trace_payload(event, service_name=settings.service_name),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()


def _export_trace_event_langfuse(event: TraceEvent) -> None:
    from app.settings import get_settings

    settings = get_settings()
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("sdk_missing") from exc
    client = Langfuse(
        public_key=settings.agent_langfuse_public_key,
        secret_key=settings.agent_langfuse_secret_key,
        base_url=settings.agent_langfuse_host,
    )
    metadata = {
        "event_type": event.event_type,
        "run_id": event.run_id,
        "step_id": event.step_id,
        "tool_name": event.tool_name,
        "trace_id": event.trace_id,
        **event.attributes,
    }
    with client.start_as_current_observation(
        as_type="span",
        name=f"agent.{event.event_type}",
    ) as span:
        span.update(metadata={key: value for key, value in metadata.items() if value is not None})
    flush = getattr(client, "flush", None)
    if callable(flush):
        flush()


def _langfuse_configured(settings: Any) -> bool:
    return bool(
        settings.agent_langfuse_host
        and settings.agent_langfuse_public_key
        and settings.agent_langfuse_secret_key
    )


def _otlp_traces_endpoint(endpoint: str) -> str:
    normalized = endpoint.rstrip("/")
    if normalized.endswith("/v1/traces"):
        return normalized
    return f"{normalized}/v1/traces"


def _otlp_trace_payload(event: TraceEvent, *, service_name: str) -> JsonObject:
    start_unix_nano = _datetime_to_unix_nano(event.created_at)
    end_unix_nano = start_unix_nano + _duration_attribute_to_nanos(
        event.attributes.get("duration_ms")
    )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _otlp_attribute("service.name", service_name),
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "production-ready-agent.agent-runtime"},
                        "spans": [
                            {
                                "traceId": _otlp_trace_id(event),
                                "spanId": _otlp_span_id(event),
                                "name": f"agent.{event.event_type}",
                                "kind": "SPAN_KIND_INTERNAL",
                                "startTimeUnixNano": str(start_unix_nano),
                                "endTimeUnixNano": str(end_unix_nano),
                                "attributes": _otlp_trace_attributes(event),
                                "status": _otlp_status(event),
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _otlp_trace_attributes(event: TraceEvent) -> list[JsonObject]:
    attributes: JsonObject = {
        "agent.event_type": event.event_type,
    }
    if event.run_id:
        attributes["agent.run_id"] = event.run_id
    if event.step_id:
        attributes["agent.step_id"] = event.step_id
    if event.tool_name:
        attributes["agent.tool_name"] = event.tool_name
    if event.trace_id:
        attributes["agent.trace_id"] = event.trace_id
    for key, value in event.attributes.items():
        attributes[f"agent.{key}"] = value
    return [_otlp_attribute(key, value) for key, value in attributes.items()]


def _otlp_attribute(key: str, value: Any) -> JsonObject:
    return {"key": key, "value": _otlp_any_value(value)}


def _otlp_any_value(value: Any) -> JsonObject:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if value is None:
        return {"stringValue": ""}
    if isinstance(value, str):
        return {"stringValue": value}
    return {"stringValue": json.dumps(value, ensure_ascii=False, sort_keys=True)}


def _otlp_trace_id(event: TraceEvent) -> str:
    source = event.trace_id or event.run_id or event.id
    normalized = source.lower()
    if len(normalized) == 32 and all(char in "0123456789abcdef" for char in normalized):
        return normalized
    return sha256(source.encode("utf-8")).hexdigest()[:32]


def _otlp_span_id(event: TraceEvent) -> str:
    return sha256(event.id.encode("utf-8")).hexdigest()[:16]


def _datetime_to_unix_nano(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _duration_attribute_to_nanos(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        return max(0, int(value * 1_000_000))
    return 0


def _otlp_status(event: TraceEvent) -> JsonObject:
    if event.event_type.endswith(".failed") or event.attributes.get("error_code"):
        return {"code": "STATUS_CODE_ERROR"}
    return {"code": "STATUS_CODE_OK"}


def _trace_export_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _mark_trace_export_success() -> None:
    global TRACE_EXPORT_STATUS
    with TRACE_EXPORT_STATUS_LOCK:
        TRACE_EXPORT_STATUS = TRACE_EXPORT_STATUS.model_copy(
            update={
                "last_success_at": datetime.now(UTC),
                "last_error": None,
                "last_error_at": None,
            }
        )


def _mark_trace_export_error(message: str) -> None:
    global TRACE_EXPORT_STATUS
    with TRACE_EXPORT_STATUS_LOCK:
        TRACE_EXPORT_STATUS = TRACE_EXPORT_STATUS.model_copy(
            update={
                "last_error": message,
                "last_error_at": datetime.now(UTC),
            }
        )


def _safe_trace_export_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_status:{exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return exc.__class__.__name__
    return exc.__class__.__name__


def _safe_trace_attributes(event_type: str, payload: JsonObject) -> JsonObject:
    keys = {
        "approval_id",
        "approved",
        "artifact_id",
        "duration_ms",
        "error_code",
        "kind",
        "policy_decision",
        "warnings",
    }
    attributes: JsonObject = {
        key: value for key, value in payload.items() if key in keys and _is_safe_trace_value(value)
    }
    attributes["event_type"] = event_type
    return attributes


def _is_safe_trace_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(isinstance(item, str | int | float | bool) for item in value)
    return False


def _payload_text_or_none(payload: JsonObject, key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _trace_id_from_payload(payload: JsonObject) -> str | None:
    trace_id = _payload_text_or_none(payload, "trace_id")
    if trace_id is not None:
        return trace_id
    audit_metadata = payload.get("audit_metadata")
    if isinstance(audit_metadata, dict):
        value = audit_metadata.get("trace_id")
        if isinstance(value, str) and value:
            return value
    return None


def _payload_text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    return "unknown"


def _payload_list(payload: JsonObject, key: str) -> list[str]:
    value = payload.get(key)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []
