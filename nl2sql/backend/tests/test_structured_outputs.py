"""Responses strict schema と応答境界の回帰テスト。"""

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field

from app.features.nl2sql.enterprise_ai_client import (
    EnterpriseAiDirectError,
    _build_image_payload,
    _build_payload,
    _parse_response,
)
from app.features.nl2sql.ontology_models import OntologyBuildExtraction
from app.features.nl2sql.ontology_router import (
    _decode_question_intent_output,
    _question_intent_minimum_example,
    _question_intent_response_format,
)
from app.features.nl2sql.structured_outputs import (
    CommentsOutput,
    SqlOutput,
    TextOutput,
    response_format,
    strict_schema,
    validate_json_output,
)
from app.settings import Settings


def test_nested_schema_is_closed_required_nullable_without_mutating_domain() -> None:
    class Item(BaseModel):
        default: str = "kept"
        note: str | None = None

    class Output(BaseModel):
        items: list[Item] = []

    schema = response_format(Output)["schema"]
    item = schema["$defs"]["Item"]
    assert item["additionalProperties"] is False
    assert item["required"] == ["default", "note"]
    assert "default" not in item["properties"]["default"]
    assert {"type": "null"} in item["properties"]["note"]["anyOf"]
    assert Output().items == []
    assert Item().default == "kept"
    with pytest.raises(ValueError):
        strict_schema({"type": "object", "additionalProperties": {"type": "string"}})


@pytest.mark.parametrize(
    "raw",
    [
        "SELECT 1 FROM DUAL",
        '```json\n{"sql":"SELECT 1", "explanation":""}\n```',
        '{"sql":"SELECT 1"}',
        '{"sql":1,"explanation":""}',
        '{"sql":"SELECT 1","explanation":"","extra":true}',
        '{"sql":"SELECT 1","explanation":""} trailing',
    ],
)
def test_invalid_json_is_not_repaired_or_coerced(raw: str) -> None:
    with pytest.raises(ValueError, match="JSON Schema") as error:
        validate_json_output(raw, response_format(SqlOutput))
    assert raw not in str(error.value)


def test_nested_extra_field_is_rejected() -> None:
    raw = json.dumps(
        {
            "suggestions": [
                {
                    "object_name": "ID",
                    "object_type": "column",
                    "suggested_comment": "識別子",
                    "secret": "must not leak",
                }
            ]
        }
    )
    with pytest.raises(ValueError):
        validate_json_output(raw, response_format(CommentsOutput))


def test_json_text_field_is_preserved_and_reasoning_is_excluded() -> None:
    raw = '{"text":"抽出した日本語\\n次の行"}'
    response = {
        "status": "completed",
        "output": [
            {"type": "reasoning", "text": "private reasoning"},
            {"type": "message", "content": [{"type": "output_text", "text": raw}]},
        ],
    }
    assert (
        _parse_response(response, response_path="", response_format=response_format(TextOutput))
        == raw
    )


@pytest.mark.parametrize(
    "response,code",
    [
        ({"status": "incomplete", "output_text": '{"text":"partial"}'}, "incomplete"),
        ({"output": [{"content": [{"type": "refusal", "refusal": "declined"}]}]}, "refusal"),
        ({"output_text": "not JSON"}, "response_format"),
    ],
)
def test_failures_are_typed_and_never_downgraded(response: dict[str, Any], code: str) -> None:
    with pytest.raises(EnterpriseAiDirectError) as error:
        _parse_response(response, response_path="", response_format=response_format(TextOutput))
    assert error.value.code == code
    assert error.value.retryable is (code == "response_format")


def test_custom_template_cannot_omit_strict_format() -> None:
    settings = Settings(
        oci_enterprise_ai_llm_payload_template='{"input":"${prompt}","text":{"verbosity":"low"}}'
    )
    fmt = response_format(TextOutput)
    payload = _build_payload(
        settings=settings,
        model_id="synthetic",
        prompt="text",
        context="",
        system_prompt="",
        response_format=fmt,
    )
    assert payload["text"] == {"verbosity": "low", "format": fmt}


class _DescribedOutput(BaseModel):
    """型の説明（実装のメモ）。"""

    sql: str = Field(description="合成の欄の書き方。")
    plain: str = ""


@pytest.mark.parametrize(
    "template", ["", '{"input":"${prompt}","instructions":"${system_prompt}"}']
)
def test_schema_descriptions_are_sent_in_the_instructions_step_only(template: str) -> None:
    """text.format の schema の項目の説明は、指示の最後の Step にして送る（#5238）。"""
    fmt = response_format(_DescribedOutput)
    payload = _build_payload(
        settings=Settings(oci_enterprise_ai_llm_payload_template=template),
        model_id="synthetic",
        prompt="質問",
        context="",
        system_prompt="## Step 1: 読む\n合成の指示。",
        response_format=fmt,
    )
    assert payload["instructions"].endswith(
        "\n\n## Step 2: 応答の各項目を書く\n応答の JSON の各項目は、次の説明に従って書く。"
        "\n- `sql`: 合成の欄の書き方。"
    )
    sent = payload["text"]["format"]
    assert "description" not in sent["schema"]
    assert "description" not in sent["schema"]["properties"]["sql"]
    assert sent["schema"]["required"] == fmt["schema"]["required"]
    assert sent["strict"] is True
    # 呼び出し側の形式は変えない（応答の検査は元の schema で行う）。
    assert fmt["schema"]["properties"]["sql"]["description"] == "合成の欄の書き方。"


def test_complex_schemas_are_valid_and_intent_example_matches() -> None:
    Draft202012Validator.check_schema(response_format(OntologyBuildExtraction)["schema"])
    fmt = _question_intent_response_format()
    example = _question_intent_minimum_example(
        question="件数", ontology_revision_id="r", profile_view_id="v"
    )
    validate_json_output(json.dumps(example), fmt)
    fields = fmt["schema"]["$defs"]["IntentFilter"]["properties"]
    assert fields["value_json"]["type"] == "string"
    assert "value" not in fields
    assert "created_at" not in fmt["schema"]["properties"]


@pytest.mark.parametrize("value", ["001", 42, 2.5, True, None, [1, "2"], {"nested": [None]}])
def test_intent_filter_wire_values_preserve_json_types(value: Any) -> None:
    payload = _decode_question_intent_output(
        json.dumps({"filters": [{"value_json": json.dumps(value)}]})
    )
    decoded = payload["filters"][0]["value"]
    assert decoded == value
    assert type(decoded) is type(value)
    assert "value_json" not in payload["filters"][0]


@pytest.mark.parametrize(
    "raw", ["[]", '{"filters":[null]}', '{"filters":[{"value_json":"invalid"}]}']
)
def test_invalid_intent_wire_value_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        _decode_question_intent_output(raw)


@pytest.mark.parametrize("template", ["", '{"input":"${prompt}"}'])
def test_vision_payload_always_contains_requested_schema(template: str) -> None:
    fmt = response_format(TextOutput)
    payload = _build_image_payload(
        settings=Settings(oci_enterprise_ai_vlm_payload_template=template),
        model_id="synthetic",
        image_bytes=b"synthetic",
        prompt="OCR",
        mime_type="image/png",
        response_format=fmt,
    )
    assert payload["text"] == {"format": fmt}


def test_refusal_outside_configured_response_pointer_is_detected() -> None:
    with pytest.raises(EnterpriseAiDirectError) as error:
        _parse_response(
            {"output_text": '{"text":""}', "output": [{"content": [{"type": "refusal"}]}]},
            response_path="/output_text",
            response_format=response_format(TextOutput),
        )
    assert error.value.code == "refusal"
