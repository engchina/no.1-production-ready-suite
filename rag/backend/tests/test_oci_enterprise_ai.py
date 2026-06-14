"""OCI Enterprise AI adapter 境界のテスト。"""

import base64
from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from app.clients.oci_enterprise_ai import OciEnterpriseAiClient, _raise_for_status_with_body
from app.config import EnterpriseAiConfiguredModel, Settings


async def test_oci_vlm_posts_structured_extraction_payload() -> None:
    """OCI VLM adapter は Enterprise AI endpoint へ構造化抽出 payload を送る。"""
    transport = FakeEnterpriseAiTransport(
        {
            "data": {
                "raw_text": "社内規程: 経費申請",
                "document_type": "社内規程",
                "confidence": 0.91,
                "warnings": [],
            }
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    result = await client.extract_with_vlm(
        b"policy-bytes",
        "抽出してください",
        mime_type="application/pdf; charset=binary",
    )

    assert result["raw_text"] == "社内規程: 経費申請"
    assert result["document_type"] == "社内規程"
    assert result["elements"]
    assert "fields" not in result
    assert result["confidence"] == 0.91
    assert transport.uploads[0]["url"] == "https://enterprise-ai.example/files"
    assert transport.uploads[0]["file_name"] == "enterprise-ai-input.pdf"
    assert transport.uploads[0]["content"] == b"policy-bytes"
    assert transport.uploads[0]["mime_type"] == "application/pdf"
    assert transport.uploads[0]["purpose"] == "user_data"
    assert "content-type" not in transport.uploads[0]["headers"]
    assert transport.calls[0]["url"] == "https://enterprise-ai.example/vlm/extract"
    payload = transport.calls[0]["payload"]
    assert payload["model"] == "enterprise-vlm"
    assert payload["instructions"]
    input_payload = payload["input"]
    assert isinstance(input_payload, list)
    content = input_payload[0]["content"]
    assert content[0] == {"type": "input_file", "file_id": "file-test"}
    assert content[1] == {"type": "input_text", "text": "抽出してください"}
    text_format = payload["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["name"] == "structured_extraction"
    assert "elements" in text_format["schema"]["properties"]
    assert transport.deletes[0]["url"] == "https://enterprise-ai.example/files/file-test"


async def test_oci_vlm_posts_image_data_url_without_files_api() -> None:
    """画像入力は OpenAI Responses の base64 data URL として送る。"""
    transport = FakeEnterpriseAiTransport(
        {
            "data": {
                "raw_text": "PNG OCR",
                "document_type": "画像",
                "confidence": 0.9,
                "warnings": [],
            }
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    await client.extract_with_vlm(b"png-bytes", "OCR", mime_type="image/png")

    assert transport.uploads == []
    content = transport.calls[0]["payload"]["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": "OCR"}
    image_url = content[1]["image_url"]
    assert content[1]["type"] == "input_image"
    assert image_url.startswith("data:image/png;base64,")
    assert base64.b64decode(image_url.split(",", maxsplit=1)[1]) == b"png-bytes"


async def test_oci_vision_smoke_test_uses_minimal_openai_image_payload() -> None:
    """Vision 接続確認は JSON schema なしの最小 OpenAI image payload を使う。"""
    transport = FakeEnterpriseAiTransport({"output_text": "画像を確認しました。"})
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    result = await client.generate_from_image(b"png-bytes", "画像を確認", mime_type="image/png")

    assert result == "画像を確認しました。"
    assert transport.uploads == []
    payload = transport.calls[0]["payload"]
    assert payload["model"] == "enterprise-vlm"
    assert "text" not in payload
    content = payload["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": "画像を確認"}
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")


async def test_oci_vlm_accepts_json_string_payload() -> None:
    """VLM response が JSON 文字列の場合も StructuredExtraction として検証する。"""
    transport = FakeEnterpriseAiTransport(
        {
            "output": (
                '{"raw_text":"本文","document_type":"マニュアル",' '"confidence":0.8,"warnings":[]}'
            )
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    result = await client.extract_with_vlm(b"document", "prompt")

    assert result["raw_text"] == "本文"
    assert result["document_type"] == "マニュアル"
    assert result["confidence"] == 0.8


async def test_oci_vlm_accepts_prediction_content_json_payload() -> None:
    """model deployment 風の predictions/content envelope も VLM 結果として受け取る。"""
    transport = FakeEnterpriseAiTransport(
        {
            "predictions": [
                {
                    "content": (
                        '{"raw_text":"規程本文","document_type":"社内規程",'
                        '"confidence":0.87,"warnings":[]}'
                    )
                }
            ]
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    result = await client.extract_with_vlm(b"document", "prompt")

    assert result["raw_text"] == "規程本文"
    assert result["document_type"] == "社内規程"
    assert result["confidence"] == 0.87


async def test_oci_vlm_accepts_choices_message_content_json_payload() -> None:
    """gateway が choices[].message.content に返す JSON も VLM 結果として受け取る。"""
    transport = FakeEnterpriseAiTransport(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"raw_text":"請求書 OCR 本文","document_type":"請求書",'
                            '"confidence":0.92,"warnings":[]}'
                        )
                    }
                }
            ]
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    result = await client.extract_with_vlm(b"invoice", "OCR")

    assert result["raw_text"] == "請求書 OCR 本文"
    assert result["document_type"] == "請求書"
    assert result["confidence"] == 0.92


async def test_oci_vlm_accepts_tool_call_arguments_payload() -> None:
    """tool_calls[].function.arguments に包まれた VLM JSON も受け取る。"""
    transport = FakeEnterpriseAiTransport(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "structured_extraction",
                                    "arguments": (
                                        '{"raw_text":"tool OCR 本文",'
                                        '"document_type":"マニュアル",'
                                        '"confidence":0.88,"warnings":[]}'
                                    ),
                                }
                            }
                        ]
                    }
                }
            ]
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    result = await client.extract_with_vlm(b"manual", "OCR")

    assert result["raw_text"] == "tool OCR 本文"
    assert result["document_type"] == "マニュアル"
    assert result["confidence"] == 0.88


async def test_oci_vlm_accepts_fenced_json_payload() -> None:
    """Markdown fenced JSON に包まれた VLM response も検証する。"""
    transport = FakeEnterpriseAiTransport(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            "```json\n"
                            '{"raw_text":"fenced OCR 本文",'
                            '"document_type":"仕様書",'
                            '"confidence":0.86,"warnings":[]}'
                            "\n```"
                        )
                    }
                }
            ]
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    result = await client.extract_with_vlm(b"spec", "OCR")

    assert result["raw_text"] == "fenced OCR 本文"
    assert result["document_type"] == "仕様書"
    assert result["confidence"] == 0.86


async def test_oci_vlm_uses_configured_response_path() -> None:
    """VLM response が深い custom envelope でも JSON Pointer で候補を選べる。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_vlm_response_path = "/payload/results/0/document"
    transport = FakeEnterpriseAiTransport(
        {
            "payload": {
                "results": [
                    {
                        "document": (
                            '{"raw_text":"深い VLM 本文",'
                            '"document_type":"契約書",'
                            '"confidence":0.83,"warnings":[]}'
                        )
                    }
                ]
            }
        }
    )
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    result = await client.extract_with_vlm(b"document", "OCR")

    assert result["raw_text"] == "深い VLM 本文"
    assert result["document_type"] == "契約書"
    assert result["confidence"] == 0.83


async def test_oci_vlm_uses_configured_payload_template() -> None:
    """VLM endpoint 固有の request shape は JSON template で差し替えられる。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_vlm_model = ""
    settings.oci_enterprise_ai_vlm_payload_template = (
        '{"servingMode":"custom","inputs":[{"mimeType":"${mime_type}",'
        '"bytes":"${data_base64}"}],"schema":"${structured_extraction_schema}",'
        '"prompt":"${prompt}"}'
    )
    transport = FakeEnterpriseAiTransport(
        {
            "data": {
                "raw_text": "本文",
                "document_type": "ドキュメント",
                "confidence": 0.7,
                "warnings": [],
            }
        }
    )
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    await client.extract_with_vlm(b"pdf", "OCR", mime_type="application/pdf")

    payload = transport.calls[0]["payload"]
    assert payload["servingMode"] == "custom"
    assert payload["inputs"][0]["mimeType"] == "application/pdf"
    assert base64.b64decode(payload["inputs"][0]["bytes"]) == b"pdf"
    assert payload["schema"]["title"] == "StructuredExtraction"
    assert payload["prompt"] == "OCR"
    assert transport.uploads == []
    assert transport.deletes == []


async def test_oci_generate_posts_rag_generation_payload() -> None:
    """OCI LLM adapter は RAG 専用 prompt を Enterprise AI endpoint へ送る。"""
    transport = FakeEnterpriseAiTransport(
        {"choices": [{"message": {"content": "根拠に基づく回答です。"}}]}
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    answer = await client.generate("承認条件は？", "[policy.txt#doc-1:0]\n承認条件: 120,000")

    assert answer == "根拠に基づく回答です。"
    assert transport.calls[0]["url"] == "https://enterprise-ai.example/llm/generate"
    payload = transport.calls[0]["payload"]
    assert payload["model"] == "enterprise-llm"
    assert payload["instructions"]
    assert payload["temperature"] == 0.0
    assert payload["max_output_tokens"] == 1200
    input_items = payload["input"]
    assert isinstance(input_items, list)
    assert input_items[0]["role"] == "user"
    assert "OCI Generative AI" not in str(input_items)
    assert "承認条件は？" in input_items[0]["content"]
    assert "[policy.txt#doc-1:0]" in input_items[0]["content"]


async def test_oci_generate_uses_configured_default_model() -> None:
    """複数 LLM 登録時は既定モデルを回答生成に使う。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_models = [
        EnterpriseAiConfiguredModel(model_id="enterprise-small", display_name="Small"),
        EnterpriseAiConfiguredModel(
            model_id="enterprise-default",
            display_name="Default",
            vision_enabled=True,
        ),
    ]
    settings.oci_enterprise_ai_default_model = "enterprise-default"
    transport = FakeEnterpriseAiTransport({"answer": "回答"})
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    assert await client.generate("質問", "根拠") == "回答"

    assert transport.calls[0]["payload"]["model"] == "enterprise-default"


async def test_oci_vlm_uses_default_model_when_it_supports_vision() -> None:
    """既定モデルが Vision 対応なら OCR でも既定モデルを使う。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_models = [
        EnterpriseAiConfiguredModel(model_id="enterprise-text", display_name="Text"),
        EnterpriseAiConfiguredModel(
            model_id="enterprise-default",
            display_name="Default Vision",
            vision_enabled=True,
        ),
    ]
    settings.oci_enterprise_ai_default_model = "enterprise-default"
    transport = FakeEnterpriseAiTransport(
        {
            "data": {
                "raw_text": "画像本文",
                "document_type": "ドキュメント",
                "confidence": 0.9,
                "warnings": [],
            }
        }
    )
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    await client.extract_with_vlm(b"image", "OCR")

    assert transport.calls[0]["payload"]["model"] == "enterprise-default"


async def test_oci_vlm_uses_first_vision_model_when_default_is_text_only() -> None:
    """既定モデルが text-only の場合は Vision 対応モデルへ切り替える。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_models = [
        EnterpriseAiConfiguredModel(model_id="enterprise-default", display_name="Default"),
        EnterpriseAiConfiguredModel(
            model_id="enterprise-vision",
            display_name="Vision",
            vision_enabled=True,
        ),
    ]
    settings.oci_enterprise_ai_default_model = "enterprise-default"
    transport = FakeEnterpriseAiTransport(
        {
            "data": {
                "raw_text": "画像本文",
                "document_type": "ドキュメント",
                "confidence": 0.9,
                "warnings": [],
            }
        }
    )
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    await client.extract_with_vlm(b"image", "OCR")

    assert transport.calls[0]["payload"]["model"] == "enterprise-vision"


async def test_oci_adapter_adds_project_and_api_key_headers() -> None:
    """API key 認証では project OCID と Bearer token を header に載せる。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_api_key = "sk-test-secret"
    transport = FakeEnterpriseAiTransport({"answer": "回答"})
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    assert await client.generate("質問", "根拠") == "回答"

    headers = transport.calls[0]["headers"]
    assert headers["OpenAI-Project"] == "ocid1.generativeaiproject.oc1..example"
    assert headers["Authorization"] == "Bearer sk-test-secret"


async def test_oci_adapter_requires_api_key_before_endpoint_call() -> None:
    """Enterprise AI gateway の API key 未設定は HTTP 送信前に検出する。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_api_key = ""
    transport = FakeEnterpriseAiTransport({"answer": "回答"})
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    with pytest.raises(ValueError, match="OCI Enterprise AI API key"):
        await client.generate("質問", "根拠")

    assert transport.calls == []


async def test_oci_generate_accepts_inference_response_content_parts() -> None:
    """LLM response は inference_response / content parts 形式も text として解釈する。"""
    transport = FakeEnterpriseAiTransport(
        {
            "inference_response": {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"text": "根拠1に基づく回答です。"},
                                {"text": "補足は根拠2です。"},
                            ]
                        }
                    }
                ]
            }
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    answer = await client.generate("質問", "根拠")

    assert answer == "根拠1に基づく回答です。\n補足は根拠2です。"


async def test_oci_generate_accepts_openai_responses_output_text() -> None:
    """標準 OpenAI Responses object は top-level text ではなく output から本文を読む。"""
    transport = FakeEnterpriseAiTransport(
        {
            "id": "resp_test",
            "object": "response",
            "status": "completed",
            "text": {"format": {"type": "text"}},
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Responses API 由来の回答です。",
                        }
                    ],
                }
            ],
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    answer = await client.generate("質問", "根拠")

    assert answer == "Responses API 由来の回答です。"


async def test_oci_vlm_accepts_openai_responses_output_json() -> None:
    """標準 Responses object の output_text JSON を StructuredExtraction として読む。"""
    transport = FakeEnterpriseAiTransport(
        {
            "id": "resp_vlm",
            "object": "response",
            "status": "completed",
            "text": {"format": {"type": "json_schema"}},
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                '{"raw_text":"Responses OCR 本文",'
                                '"document_type":"画像",'
                                '"confidence":0.82,"warnings":[]}'
                            ),
                        }
                    ],
                }
            ],
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    result = await client.extract_with_vlm(b"image", "OCR")

    assert result["raw_text"] == "Responses OCR 本文"
    assert result["document_type"] == "画像"


async def test_oci_generate_reports_openai_responses_error_before_empty_text() -> None:
    """Responses API の failed status/error は空 text ではなく実エラーとして返す。"""
    transport = FakeEnterpriseAiTransport(
        {
            "id": "resp_failed",
            "object": "response",
            "status": "failed",
            "error": {
                "type": "invalid_request_error",
                "message": "Unsupported request field: parameters",
            },
            "output": [],
            "text": {"format": {"type": "text"}},
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    with pytest.raises(ValueError, match="Unsupported request field"):
        await client.generate("質問", "根拠")


def test_oci_http_status_error_includes_response_body() -> None:
    """HTTP 400 は OCI の response body を含む例外にする。"""
    request = httpx.Request("POST", "https://enterprise-ai.example/responses")
    response = httpx.Response(
        400,
        text='{"error":{"message":"image input must be uploaded with Files API"}}',
        request=request,
    )

    with pytest.raises(httpx.HTTPStatusError, match="Files API"):
        _raise_for_status_with_body(response, "OCI Enterprise AI endpoint")


async def test_oci_generate_accepts_tool_call_answer_payload() -> None:
    """LLM の tool/function arguments にある answer も回答として扱う。"""
    transport = FakeEnterpriseAiTransport(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"arguments": '{"answer":"tool call 由来の回答です。"}'}}
                        ]
                    }
                }
            ]
        }
    )
    client = OciEnterpriseAiClient(settings=_oci_settings(), http_transport=transport)

    answer = await client.generate("質問", "根拠")

    assert answer == "tool call 由来の回答です。"


async def test_oci_generate_uses_configured_response_path() -> None:
    """LLM response が深い custom envelope でも JSON Pointer で候補を選べる。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_llm_response_path = "/payload/results/0/generated/text"
    transport = FakeEnterpriseAiTransport(
        {"payload": {"results": [{"generated": {"text": "深い envelope 由来の回答です。"}}]}}
    )
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    answer = await client.generate("質問", "根拠")

    assert answer == "深い envelope 由来の回答です。"


async def test_oci_generate_uses_configured_payload_template() -> None:
    """LLM endpoint 固有の request shape は JSON template で差し替えられる。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_llm_model = ""
    settings.oci_enterprise_ai_llm_payload_template = (
        '{"input":{"messages":"${messages}","params":"${parameters}"},'
        '"metadata":{"task":"${task}","language":"${language}"}}'
    )
    transport = FakeEnterpriseAiTransport({"output_text": "テンプレート回答"})
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    answer = await client.generate("質問", "根拠")

    assert answer == "テンプレート回答"
    payload = transport.calls[0]["payload"]
    assert payload["metadata"] == {"task": "rag_answer_generation", "language": "ja"}
    assert payload["input"]["params"] == {"temperature": 0.0, "max_output_tokens": 1200}
    assert payload["input"]["messages"][0]["role"] == "system"
    assert payload["input"]["messages"][1]["role"] == "user"
    assert "質問" in payload["input"]["messages"][1]["content"]
    assert "根拠" in payload["input"]["messages"][1]["content"]


async def test_oci_generate_rejects_empty_text_response() -> None:
    """LLM response に text がない場合は fail fast する。"""
    client = OciEnterpriseAiClient(
        settings=_oci_settings(),
        http_transport=FakeEnterpriseAiTransport({"data": {"unexpected": "shape"}}),
    )

    with pytest.raises(ValueError, match="回答 text"):
        await client.generate("質問", "根拠")


async def test_oci_adapter_requires_endpoint_before_transport_call() -> None:
    """Enterprise AI endpoint 未設定は外部呼び出し前に検出する。"""
    transport = FakeEnterpriseAiTransport({"answer": "unused"})
    settings = _oci_settings()
    settings.oci_enterprise_ai_endpoint = ""
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    with pytest.raises(ValueError, match="endpoint"):
        await client.generate("質問", "根拠")

    assert transport.calls == []


async def test_oci_adapter_uses_absolute_path_override() -> None:
    """path に完全 URL を指定した場合は endpoint base より優先する。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_llm_path = "https://private.example/custom/generate"
    transport = FakeEnterpriseAiTransport({"answer": "回答"})
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    assert await client.generate("質問", "根拠") == "回答"
    assert transport.calls[0]["url"] == "https://private.example/custom/generate"


async def test_oci_adapter_rejects_unknown_payload_template_placeholder() -> None:
    """payload template の placeholder typo は外部呼び出し前に検出する。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_llm_payload_template = '{"input":"${missing}"}'
    transport = FakeEnterpriseAiTransport({"answer": "unused"})
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    with pytest.raises(ValueError, match="未対応の placeholder"):
        await client.generate("質問", "根拠")

    assert transport.calls == []


async def test_oci_adapter_rejects_invalid_response_path() -> None:
    """response path typo は空回答として扱わず明示的に失敗する。"""
    settings = _oci_settings()
    settings.oci_enterprise_ai_llm_response_path = "/missing/text"
    transport = FakeEnterpriseAiTransport({"payload": {"text": "unused"}})
    client = OciEnterpriseAiClient(settings=settings, http_transport=transport)

    with pytest.raises(ValueError, match="key が見つかりません"):
        await client.generate("質問", "根拠")

    assert transport.calls


class FakeEnterpriseAiTransport:
    """Enterprise AI HTTP transport の fake。"""

    def __init__(self, response: Mapping[str, Any]) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []
        self.uploads: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []

    async def post_json(
        self,
        url: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "url": url,
                "payload": dict(payload),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return self._response

    async def upload_file(
        self,
        url: str,
        file_name: str,
        content: bytes,
        *,
        mime_type: str,
        purpose: str,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, Any]:
        self.uploads.append(
            {
                "url": url,
                "file_name": file_name,
                "content": content,
                "mime_type": mime_type,
                "purpose": purpose,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return {"id": "file-test"}

    async def delete(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, Any]:
        self.deletes.append({"url": url, "headers": dict(headers), "timeout": timeout})
        return {"id": "file-test", "deleted": True}


def _oci_settings() -> Settings:
    return Settings.model_construct(
        oci_region="ap-osaka-1",
        oci_compartment_id="ocid1.compartment.oc1..example",
        oci_enterprise_ai_endpoint="https://enterprise-ai.example",
        oci_enterprise_ai_project_ocid="ocid1.generativeaiproject.oc1..example",
        oci_enterprise_ai_api_key="sk-test-secret",
        oci_enterprise_ai_llm_model="enterprise-llm",
        oci_enterprise_ai_vlm_model="enterprise-vlm",
        oci_enterprise_ai_llm_path="/llm/generate",
        oci_enterprise_ai_vlm_path="/vlm/extract",
        oci_enterprise_ai_llm_payload_template="",
        oci_enterprise_ai_vlm_payload_template="",
        oci_enterprise_ai_llm_response_path="",
        oci_enterprise_ai_vlm_response_path="",
        oci_enterprise_ai_timeout_seconds=12.0,
        oci_enterprise_ai_max_retries=0,
    )
