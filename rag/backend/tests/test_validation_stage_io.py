"""最終照合と選択済み FAQ の実入出力を、保存した工程ごとに確かめる。"""

from typing import Any, cast

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.tracing import (
    TraceSummary,
    configure_tracing,
    shutdown_tracing,
)

from app.config import Settings
from app.rag.answer_engine import AnswerEngine
from tests.test_answer_engine import FakeGenAi, FakeOracle


@pytest.fixture(autouse=True)
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        yield
    finally:
        shutdown_tracing()


def call_of(summary: TraceSummary, name: str) -> Any:
    saved = TraceSummary.model_validate_json(summary.model_dump_json())
    span = next(item for item in saved.spans if item.attributes.get("pr.stage") == name)
    assert span.display and span.display.details and span.display.details.call
    return span.display.details.call


def engine(**kwargs: Any) -> AnswerEngine:
    return AnswerEngine(
        Settings(),
        oracle=cast(Any, FakeOracle()),
        genai=cast(Any, FakeGenAi()),
        **kwargs,
    )
