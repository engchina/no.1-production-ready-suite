"""OCI Enterprise AI direct client の payload 契約テスト。"""

from __future__ import annotations

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
