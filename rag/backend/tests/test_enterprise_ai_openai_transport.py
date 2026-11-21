"""既定 Enterprise AI transport が openai SDK 経由で呼ぶことの契約テスト。"""

import json
from collections.abc import Callable

import httpx
import openai
import pytest
from pr_backend_core.generation_input import (
    GENERATION_INPUT_LIMIT,
    GenerationInputLimitError,
    complete_input_measurement,
)
from rag_parser_core.oci_enterprise_ai import (
    EnterpriseAiTimeoutError,
    OciEnterpriseAiClient,
    OciEnterpriseAiConfig,
    _DefaultEnterpriseAiTransport,
)

ENDPOINT = "https://enterprise-ai.example/openai/v1"


def _config(max_retries: int = 0) -> OciEnterpriseAiConfig:
    return OciEnterpriseAiConfig(
        oci_enterprise_ai_endpoint=ENDPOINT,
        oci_enterprise_ai_api_key="test-key",
        oci_enterprise_ai_project_ocid="ocid1.project.test",
        default_model_id="test-llm",
        oci_enterprise_ai_max_retries=max_retries,
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 0,
) -> OciEnterpriseAiClient:
    """実 transport(openai SDK)を使い、HTTP 層だけ MockTransport に差し替える。"""

    def _sdk_client(self: _DefaultEnterpriseAiTransport, timeout: float) -> openai.AsyncOpenAI:
        return openai.AsyncOpenAI(
            api_key="test-key",
            base_url=ENDPOINT,
            max_retries=max_retries,
            timeout=timeout,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(_DefaultEnterpriseAiTransport, "_client", _sdk_client)
    return OciEnterpriseAiClient(_config(max_retries))


async def test_generate_posts_responses_via_openai_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"output_text": "回答です"})

    answer = await _client(monkeypatch, handler).generate(
        "質問", "根拠", system_prompt="合成の指示"
    )

    assert answer == "回答です"
    request = requests[0]
    assert str(request.url) == f"{ENDPOINT}/responses"
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["openai-project"] == "ocid1.project.test"
    assert json.loads(request.content)["model"] == "test-llm"
    # SDK が付けるヘッダーを重ねて送らない(重複を連結する gateway は 400。#3527)
    assert request.headers.get_list("content-type") == ["application/json"]
    assert request.headers.get_list("accept") == ["application/json"]


async def test_generate_retries_retryable_status(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses = iter([503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        headers = {"retry-after": "0"}
        if status == 200:
            return httpx.Response(200, json={"output_text": "ok"}, headers=headers)
        return httpx.Response(status, json={"error": "busy"}, headers=headers)

    answer = await _client(monkeypatch, handler, max_retries=1).generate(
        "q", "c", system_prompt="合成の指示"
    )

    assert answer == "ok"


async def test_http_error_keeps_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "model not found"}})

    with pytest.raises(httpx.HTTPStatusError, match="model not found"):
        await _client(monkeypatch, handler).generate("q", "c", system_prompt="合成の指示")


async def test_timeout_maps_to_enterprise_ai_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(EnterpriseAiTimeoutError):
        await _client(monkeypatch, handler).generate("q", "c", system_prompt="合成の指示")


@pytest.mark.parametrize(
    ("status", "usage", "actual", "budget_status"),
    [
        (200, {"input_tokens": 0}, 0, "within_limit"),
        (200, None, None, "unreported"),
        (
            200,
            {"input_tokens": GENERATION_INPUT_LIMIT + 1},
            GENERATION_INPUT_LIMIT + 1,
            "over_limit",
        ),
        (400, {"prompt_tokens": 32}, 32, "within_limit"),
    ],
)
async def test_text_request_measures_sdk_body_and_reported_usage(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    status: int,
    usage: dict[str, int] | None,
    actual: int | None,
    budget_status: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = {"output_text": "回答"} if status == 200 else {"error": {"message": "入力エラー"}}
        return httpx.Response(
            status, json={**body, **({"usage": usage} if usage is not None else {})}
        )

    caplog.set_level("INFO", logger="rag_parser_core.oci_enterprise_ai")
    client = _client(monkeypatch, handler)
    generation = client.generate(
        "質問",
        "原文",
        system_prompt="合成の指示",
        response_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
        response_schema_name="synthetic",
    )
    if status == 200:
        assert await generation == "回答"
    else:
        with pytest.raises(httpx.HTTPStatusError):
            await generation
    assert len(requests) == 1
    sent = json.loads(requests[0].content)
    assert sent["text"]["format"]["schema"]["properties"]["answer"]["type"] == "string"
    fields = vars(next(r for r in caplog.records if r.getMessage() == "llm_input_budget"))
    assert fields["pr.input.estimated_tokens"] == complete_input_measurement(sent).estimated_tokens
    assert fields["pr.input.limit_tokens"] == GENERATION_INPUT_LIMIT
    assert fields["pr.input.usage_status"] == budget_status
    assert fields.get("gen_ai.usage.input_tokens") == actual


async def test_schema_can_exceed_limit_before_sdk_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_request(request: httpx.Request) -> httpx.Response:
        pytest.fail("入力の上限を超えた要求が送信されました。")

    with pytest.raises(GenerationInputLimitError) as caught:
        await _client(monkeypatch, no_request).generate(
            "質問",
            "原文",
            system_prompt="合成の指示",
            response_schema={"type": "string", "enum": ["x" * GENERATION_INPUT_LIMIT]},
        )
    assert caught.value.estimated_tokens > caught.value.limit_tokens


async def test_upload_file_sends_multipart(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "file-1"})

    transport = _DefaultEnterpriseAiTransport(_config())
    _client(monkeypatch, handler)  # _client の差し替えだけを使う
    response = await transport.upload_file(
        f"{ENDPOINT}/files",
        "input.pdf",
        b"%PDF",
        mime_type="application/pdf",
        purpose="user_data",
        headers={"accept": "application/json", "Authorization": "Bearer test-key"},
        timeout=5,
    )

    assert response == {"id": "file-1"}
    request = requests[0]
    (content_type,) = request.headers.get_list("content-type")
    assert content_type.startswith("multipart/form-data; boundary=")
    assert request.headers.get_list("accept") == ["application/json"]
    assert b'name="purpose"' in request.content
    assert b"user_data" in request.content
    assert b'filename="input.pdf"' in request.content
