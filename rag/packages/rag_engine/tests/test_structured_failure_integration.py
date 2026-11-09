"""同じ例外を既存の監査・CRAG・呼び出しログへ運ぶ。外部モデルは呼ばない。"""

import json
import logging

import pytest
from pydantic import BaseModel, ValidationError

from rag_engine.adapters import oci
from rag_engine.config import LlmProviderSettings


class Schema(BaseModel):
    count: int


def synthetic_provider() -> LlmProviderSettings:
    return LlmProviderSettings(provider_id="test", label="test", region="", project_id="", api_key="", model="synthetic")


def invalid_json_output() -> Exception:
    try:
        json.loads('{"private-key":')
    except json.JSONDecodeError as cause:
        error = oci.InvalidStructuredOutput("private-message")
        error.__cause__ = cause
        return error
    raise AssertionError("合成のJSONは閉じていない")


def invalid_output() -> Exception:
    try:
        Schema.model_validate({"count": "private-input"})
    except ValidationError as cause:
        error = oci.InvalidStructuredOutput("private-message")
        error.__cause__ = cause
        return error
    raise AssertionError("合成の入力は型違反になる")


def test_failed_call_logger_keeps_safe_chain_as_extra(caplog: pytest.LogCaptureFixture) -> None:
    provider = synthetic_provider()
    with caplog.at_level(logging.INFO, logger=oci.__name__):
        oci._record_failed_call(invalid_output(), provider, Schema, 0.1)
    event = caplog.records[-1]
    assert event.__dict__["llm_failure"]["category"] == "schema_field"
    assert event.__dict__["llm_failure"]["cause_classes"] == ["ValidationError"]
    assert event.__dict__["llm_status"] == "failed"
    assert "private" not in json.dumps(event.__dict__["llm_failure"])


def test_real_structured_formatter_retains_failure_extra(caplog: pytest.LogCaptureFixture) -> None:
    from pr_backend_core.logging import StructuredFormatter

    with caplog.at_level(logging.INFO, logger=oci.__name__):
        oci._record_failed_call(invalid_output(), synthetic_provider(), Schema, 0.1)
    formatter = StructuredFormatter(
        service_name="test", service_version="test", environment="test", component="test", max_record_bytes=32768
    )
    rendered = json.loads(formatter.format(caplog.records[-1]))
    assert rendered["llm_failure"]["category"] == "schema_field"
    assert "private" not in json.dumps(rendered["llm_failure"])
