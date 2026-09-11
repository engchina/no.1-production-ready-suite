"""OCI Enterprise AI direct client の payload 契約テスト。"""

from __future__ import annotations

import pytest

from app.features.nl2sql.enterprise_ai_client import _build_payload
from app.settings import Settings


def test_enterprise_ai_payload_includes_responses_structured_output_format() -> None:
    response_format = {
        "type": "json_schema",
        "name": "question_intent_graph_v1",
        "schema": {"type": "object", "additionalProperties": False},
        "strict": True,
    }

    payload = _build_payload(
        settings=Settings(),
        model_id="enterprise-intent",
        prompt="受注件数を表示",
        context="{}",
        system_prompt="JSON だけを返してください。",
        response_format=response_format,
    )

    assert payload["text"] == {"format": response_format}


def test_enterprise_ai_payload_template_receives_response_format_object() -> None:
    response_format = {
        "type": "json_schema",
        "name": "question_intent_graph_v1",
        "schema": {"type": "object", "additionalProperties": False},
        "strict": True,
    }
    settings = Settings(
        oci_enterprise_ai_llm_payload_template=(
            '{"model":"${model}","format":"${response_format}","input":"${prompt}"}'
        )
    )

    payload = _build_payload(
        settings=settings,
        model_id="enterprise-intent",
        prompt="受注件数を表示",
        context="{}",
        system_prompt="JSON だけを返してください。",
        response_format=response_format,
    )

    assert payload["format"] == response_format


def test_reverse_retry_uses_typed_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.features.nl2sql import enterprise_ai_client as module

    settings = Settings(
        oci_enterprise_ai_endpoint="https://enterprise.example.test",
        oci_enterprise_ai_api_key="synthetic-test-key",
    )
    real_client = httpx.Client
    for status, code, retryable in [
        (401, "authentication", False),
        (403, "authentication", False),
        (400, "request", False),
        (429, "rate_limit", True),
        (503, "unavailable", True),
    ]:
        transport = httpx.MockTransport(
            lambda request, status=status: httpx.Response(status, json={"error": "synthetic"})
        )
        monkeypatch.setattr(
            httpx,
            "Client",
            lambda transport=transport, **kwargs: real_client(transport=transport, **kwargs),
        )
        with pytest.raises(module.EnterpriseAiDirectError) as error:
            module.OciEnterpriseAiDirectClient(settings)._post_json(
                {}, path="/responses", max_retries=0
            )
        assert error.value.code == code
        assert error.value.retryable is retryable
