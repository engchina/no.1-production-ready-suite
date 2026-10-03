"""共通ログの wire 契約・脱機密化・並行相関・SSE の回帰。"""

import asyncio
import io
import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from pr_backend_core import configure_logging
from pr_backend_core.logging import StructuredFormatter, safe_exception_fields
from pr_backend_core.observability.metrics import MetricsMiddleware
from pr_backend_core.observability.request_context import (
    bind_log_context,
    outbound_correlation_headers,
    request_id_var,
    traceparent_var,
)


@pytest.fixture(autouse=True)
def restore_logging() -> Any:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    server = {
        name: (
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in (
            "uvicorn",
            "uvicorn.error",
            "uvicorn.access",
            "gunicorn.error",
            "gunicorn.access",
        )
    }
    yield
    for handler in root.handlers[:]:
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, (original, original_level, propagate) in server.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = original
        logger.setLevel(original_level)
        logger.propagate = propagate


def formatter(limit: int = 32768) -> StructuredFormatter:
    return StructuredFormatter(
        service_name="test-service",
        service_version="1.0",
        environment="production",
        component="worker",
        max_record_bytes=limit,
    )


def test_jst_same_instant_and_readable_japanese() -> None:
    record = logging.LogRecord(
        "app.test", logging.INFO, __file__, 1, "診断の結果\n次の行", (), None
    )
    record.created = datetime(2026, 10, 3, 0, 1, 2, 345000, UTC).timestamp()
    wire = formatter().format(record)
    fields = json.loads(wire)
    assert fields["timestamp"] == "2026-10-03T09:01:02.345+09:00"
    assert datetime.fromisoformat(fields["timestamp"]).timestamp() == record.created
    assert "診断の結果" in wire and "\\u" not in wire and "\n" not in wire
    assert fields["schema_version"] == 1
    assert fields["service_name"] == "test-service"


def test_nested_credentials_exception_chain_and_objects_do_not_escape() -> None:
    class Unsafe:
        def __str__(self) -> str:
            raise AssertionError("object should never be stringified")

    try:
        try:
            raise ValueError("EXCEPTION_SENTINEL_PRIVATE_TEXT")
        except ValueError as error:
            raise RuntimeError("CHAIN_SENTINEL") from error
    except RuntimeError as error:
        record = logging.LogRecord(
            "app.test",
            logging.ERROR,
            __file__,
            1,
            "操作の失敗: %s",
            (error,),
            (type(error), error, error.__traceback__),
        )
    record.api_key = "API_SENTINEL"  # type: ignore[attr-defined]
    record.attributes = {
        "nested": {"Authorization": "AUTH_SENTINEL", "prompt": "PROMPT_SENTINEL"},
        "url": "https://USER_SENTINEL:PASS_SENTINEL@example.com/path?token=QUERY_SENTINEL#FRAGMENT_SENTINEL",
        "number": float("nan"),
        "unknown": Unsafe(),
    }  # type: ignore[attr-defined]
    wire = formatter().format(record)
    assert "SENTINEL" not in wire
    fields = json.loads(wire)
    assert fields["exception_type"] == "RuntimeError"
    assert fields["exception_causes"] == ["ValueError"]
    assert (
        fields["exception_frames"][0]["function"]
        == "test_nested_credentials_exception_chain_and_objects_do_not_escape"
    )
    assert fields["attributes"]["url"] == "https://example.com/path"
    assert fields["attributes"]["number"] is None


def test_bounded_record_cycles_and_malformed_format_keep_logging_alive() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    record = logging.LogRecord("app.test", logging.WARNING, __file__, 1, "%d", ("bad",), None)
    record.attributes = {str(index): "日本語" * 1000 for index in range(40)}  # type: ignore[attr-defined]
    record.cycle = cyclic  # type: ignore[attr-defined]
    wire = formatter(4096).format(record)
    assert len(wire.encode()) <= 4096
    fields = json.loads(wire)
    assert fields["truncated"] is True
    assert fields["level"] == "WARNING"
    assert safe_exception_fields(ValueError("PRIVATE")) == {"exception_type": "ValueError"}


def test_idempotent_configuration_preserves_collectors_and_server_level(capsys: Any) -> None:
    from uvicorn import Config

    Config("unused:app")
    collector = logging.StreamHandler(io.StringIO())
    root = logging.getLogger()
    root.addHandler(collector)
    configure_logging("ERROR", service_name="test-service")
    count = len(root.handlers)
    configure_logging("ERROR", service_name="test-service")
    assert collector in root.handlers and len(root.handlers) == count
    logging.getLogger("uvicorn.access").info("suppressed_info")
    logging.getLogger("uvicorn.error").error("server_failure")
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert len(records) == 1 and records[0]["message"] == "server_failure"
    assert records[0]["service_name"] == "test-service"
    configure_logging("INFO")
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "CLIENT_PRIVATE", "GET", "/safe?secret=PRIVATE", "1.1", 503
    )
    wire = capsys.readouterr().err
    assert "PRIVATE" not in wire
    assert json.loads(wire)["http_status"] == 503


def test_job_context_and_outbound_trace_are_validated_and_reset() -> None:
    request = request_id_var.set("request-1")
    trace = traceparent_var.set("00-" + "a" * 32 + "-" + "b" * 16 + "-01")
    try:
        with bind_log_context(job_id="job-1", worker_id="worker-1", password="SECRET"):
            record = logging.LogRecord(
                "app.test", logging.INFO, __file__, 1, "job_started", (), None
            )
            record.trace_id = "business-trace"  # type: ignore[attr-defined]
            data = json.loads(formatter().format(record))
            assert data["job_id"] == "job-1"
            assert data["request_id"] == "request-1"
            assert data["domain_trace_id"] == "business-trace"
            assert data["trace_id"] == "a" * 32 and data["parent_span_id"] == "b" * 16
            assert "span_id" not in data and "password" not in data
            assert outbound_correlation_headers()["traceparent"].endswith("-01")
        assert "job_id" not in json.loads(formatter().format(record))
        traceparent_var.set("00-" + "0" * 32 + "-" + "b" * 16 + "-01")
        assert "traceparent" not in outbound_correlation_headers()
    finally:
        traceparent_var.reset(trace)
        request_id_var.reset(request)


@pytest.mark.asyncio
async def test_concurrent_sse_keeps_context_until_body_finished(capsys: Any) -> None:
    configure_logging("INFO")
    sent: dict[str, list[dict[str, Any]]] = {"request-1": [], "request-2": []}

    async def app(scope: Any, receive: Any, send: Any) -> None:
        scope["route"] = SimpleNamespace(path="/stream/{id}")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        # Uvicorn の headers 時点の access record は二重に出さない。
        logging.getLogger("uvicorn.access").info("native_access_duplicate")
        await asyncio.sleep(0)
        logging.getLogger("app.stream").info("stream_chunk")
        await send({"type": "http.response.body", "body": b"data: safe\n\n", "more_body": True})
        await asyncio.sleep(0)
        logging.getLogger("app.stream").info("stream_finished")
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def run(request: str) -> None:
        async def receive() -> Any:
            return {"type": "http.disconnect"}

        async def send(message: Any) -> None:
            sent[request].append(message)

        await MetricsMiddleware(app, enable_metrics=False)(
            {
                "type": "http",
                "method": "GET",
                "path": "/stream/private",
                "query_string": b"secret=PRIVATE",
                "headers": [(b"x-request-id", request.encode())],
                "http_version": "1.1",
            },
            receive,
            send,
        )
        assert request_id_var.get() is None

    await asyncio.gather(run("request-1"), run("request-2"))
    wire = capsys.readouterr().err
    assert "PRIVATE" not in wire and "native_access_duplicate" not in wire
    records = [json.loads(line) for line in wire.splitlines()]
    for request in sent:
        matching = [record for record in records if record["request_id"] == request]
        assert [record["message"] for record in matching[:2]] == ["stream_chunk", "stream_finished"]
        summary = matching[-1]
        assert summary["event"] == "http_access" and summary["outcome"] == "success"
        assert summary["duration_ms"] >= summary["headers_duration_ms"]
        assert (b"x-request-id", request.encode()) in sent[request][0]["headers"]
