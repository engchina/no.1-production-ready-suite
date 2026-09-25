"""外部 GPU parser の native protocol と共通抽出変換のテスト。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import fitz  # type: ignore[import-untyped]
import httpx
import pytest

from app.clients.external_parser import (
    ExternalParserCallError,
    ExternalParserClient,
    _RenderedPage,
)
from app.config import Settings
from app.schemas.document import SourceModality, SourceProfile


def _profile(name: str = "scan.pdf", *, content_type: str = "application/pdf") -> SourceProfile:
    modality = SourceModality.PDF if content_type == "application/pdf" else SourceModality.IMAGE
    return SourceProfile(
        original_file_name=name,
        sanitized_file_name=name,
        content_type=content_type,
        file_size_bytes=3,
        content_sha256="0" * 64,
        modality=modality,
        parser_profile="pdf" if modality == SourceModality.PDF else "image",
    )


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    original = httpx.Client
    transport = httpx.MockTransport(handler)

    def factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs.pop("timeout", None)
        return original(transport=transport)

    monkeypatch.setattr(httpx, "Client", factory)


def _pdf(page_count: int = 2) -> bytes:
    document = fitz.open()
    for index in range(page_count):
        page = document.new_page(width=120, height=80)
        page.insert_text((12, 30), f"page {index + 1}")
    data = document.tobytes()
    document.close()
    return bytes(data)


@pytest.mark.parametrize("language", [None, "english"])
def test_mineru_file_parse_preserves_page_bbox_table_auth_and_language(
    monkeypatch: pytest.MonkeyPatch,
    language: str | None,
) -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": {
                    "scan": {
                        "content_list": [
                            {
                                "type": "text",
                                "text": "第一頁",
                                "page_idx": 0,
                                "bbox": [10, 20, 100, 40],
                            },
                            {
                                "type": "table",
                                "table_body": "<table><tr><td>A</td></tr></table>",
                                "page_idx": 1,
                                "bbox": [5, 8, 110, 70],
                            },
                        ]
                    }
                }
            },
        )

    _install_transport(monkeypatch, handle)
    settings = Settings(
        rag_parser_mineru_api_host="https://mineru.example.com/",
        rag_parser_mineru_api_key="mineru-secret",
    )
    if language is not None:
        settings.rag_parser_mineru_language = language
    result = ExternalParserClient(settings).parse("mineru", b"%PDF", _profile(), "application/pdf")

    assert seen[0].url.path == "/file_parse"
    assert seen[0].headers["authorization"] == "Bearer mineru-secret"
    assert b'name="return_content_list"' in seen[0].content
    assert f'\r\n\r\n{language or "japan"}\r\n'.encode() in seen[0].content
    assert result.extraction is not None
    assert [element.page_number for element in result.extraction.elements] == [1, 2]
    assert result.extraction.elements[0].bbox == [10.0, 20.0, 100.0, 40.0]
    assert result.extraction.elements[1].content_kind == "table"
    assert result.extraction.parser_artifacts["external_protocol"] == "mineru_file_parse"


def test_dots_openai_layout_json_renders_pdf_pages_with_bounded_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                [
                                    {
                                        "bbox": [1, 2, 30, 20],
                                        "category": "Table",
                                        "text": "<table><tr><td>A</td></tr></table>",
                                    }
                                ]
                            )
                        },
                    }
                ]
            },
        )

    _install_transport(monkeypatch, handle)
    result = ExternalParserClient(
        Settings(
            rag_parser_dots_ocr_api_host="https://dots.example.com/v1/",
            rag_parser_dots_ocr_model="dots-model",
            rag_parser_dots_ocr_pdf_workers=2,
        )
    ).parse("dots_ocr", _pdf(), _profile(), "application/pdf")

    assert len(calls) == 2
    assert all(call["model"] == "dots-model" for call in calls)
    assert result.extraction is not None
    assert [element.page_number for element in result.extraction.elements] == [1, 2]
    assert all(element.content_kind == "table" for element in result.extraction.elements)
    assert len(result.extraction.pages) == 2
    assert all(page.width and page.height for page in result.extraction.pages)


def test_dots_picture_without_text_is_kept_only_as_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps([{"bbox": [1, 2, 30, 20], "category": "Picture"}])
                        },
                    }
                ]
            },
        )

    _install_transport(monkeypatch, handle)
    result = ExternalParserClient(
        Settings(
            rag_parser_dots_ocr_api_host="https://dots.example.com/v1/",
            rag_parser_dots_ocr_model="dots-model",
        )
    ).parse("dots_ocr", _pdf(), _profile(), "application/pdf")

    assert result.extraction is not None
    assert result.extraction.raw_text == ""
    assert result.extraction.elements == []
    assert len(result.extraction.assets) == 2
    assert all(asset.kind == "picture" for asset in result.extraction.assets)
    assert all(asset.alt_text is None for asset in result.extraction.assets)
    assert result.extraction.parser_artifacts["adapter_asset_count"] == 2


def test_dots_renders_only_one_worker_batch_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[int] = []
    rendered_at_call: list[int] = []
    parser = ExternalParserClient(
        Settings(
            rag_parser_dots_ocr_api_host="https://dots.example.com",
            rag_parser_dots_ocr_model="dots-model",
            rag_parser_dots_ocr_pdf_workers=2,
        )
    )

    def source_images(*_args: object) -> object:
        for number in range(1, 6):
            rendered.append(number)
            yield _RenderedPage(number, b"png", 120, 80)

    def openai_chat(*_args: object, **_kwargs: object) -> str:
        rendered_at_call.append(len(rendered))
        return json.dumps([{"bbox": [1, 2, 30, 20], "category": "Text", "text": "本文"}])

    monkeypatch.setattr(parser, "_source_images", source_images)
    monkeypatch.setattr(parser, "_openai_chat", openai_chat)

    result = parser.parse("dots_ocr", b"%PDF", _profile(), "application/pdf")

    assert sorted(rendered_at_call) == [2, 2, 4, 4, 5]
    assert result.extraction is not None
    assert [page.page_number for page in result.extraction.pages] == [1, 2, 3, 4, 5]


def test_glm_and_unlimited_openai_responses_keep_pages_and_clean_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            "GLM 第一頁",
            "GLM 第二頁",
            "<|ref|>Unlimited 第一頁<|/ref|><|det|>bbox<|/det|><PAGE>Unlimited 第二頁",
        ]
    )

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": next(responses)}}]},
        )

    _install_transport(monkeypatch, handle)
    settings = Settings(
        rag_parser_glm_ocr_api_host="https://glm.example.com",
        rag_parser_glm_ocr_model="glm-model",
        rag_parser_unlimited_ocr_api_host="https://unlimited.example.com",
        rag_parser_unlimited_ocr_model="unlimited-model",
        rag_parser_unlimited_ocr_pdf_batch_size=2,
    )
    client = ExternalParserClient(settings)

    glm = client.parse("glm_ocr", _pdf(), _profile(), "application/pdf")
    unlimited = client.parse("unlimited_ocr", _pdf(), _profile(), "application/pdf")

    assert glm.extraction is not None
    assert [element.page_number for element in glm.extraction.elements] == [1, 2]
    assert "GLM 第一頁" in glm.extraction.raw_text
    assert unlimited.extraction is not None
    assert [element.page_number for element in unlimited.extraction.elements] == [1, 2]
    assert "<|det|>" not in unlimited.extraction.raw_text
    assert "Unlimited 第二頁" in unlimited.extraction.raw_text


def test_unlimited_renders_only_one_configured_batch_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[int] = []
    rendered_at_call: list[int] = []
    parser = ExternalParserClient(
        Settings(
            rag_parser_unlimited_ocr_api_host="https://unlimited.example.com",
            rag_parser_unlimited_ocr_model="unlimited-model",
            rag_parser_unlimited_ocr_pdf_batch_size=2,
        )
    )

    def source_images(*_args: object) -> object:
        for number in range(1, 6):
            rendered.append(number)
            yield _RenderedPage(number, b"png", 120, 80)

    def openai_chat(
        _connection: object,
        images: list[tuple[bytes, str]],
        _prompt: str,
        **_kwargs: object,
    ) -> str:
        rendered_at_call.append(len(rendered))
        return "<PAGE>".join(f"page-{index}" for index in range(len(images)))

    monkeypatch.setattr(parser, "_source_images", source_images)
    monkeypatch.setattr(parser, "_openai_chat", openai_chat)

    result = parser.parse("unlimited_ocr", b"%PDF", _profile(), "application/pdf")

    assert rendered_at_call == [2, 4, 5]
    assert result.extraction is not None
    assert [page.page_number for page in result.extraction.pages] == [1, 2, 3, 4, 5]


def test_status_health_models_retry_and_missing_model(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "3.4.0"})
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"detail": "loading"})
        return httpx.Response(200, json={"data": [{"id": "served-model"}]})

    _install_transport(monkeypatch, handle)
    settings = Settings(
        rag_parser_mineru_api_host="https://mineru.example.com",
        rag_parser_dots_ocr_api_host="https://dots.example.com/v1",
        rag_parser_dots_ocr_model="missing-model",
        rag_http_service_retry_attempts=2,
        rag_http_service_retry_initial_delay_seconds=0,
    )
    parser = ExternalParserClient(settings)

    mineru = parser.status("mineru")
    dots = parser.status("dots_ocr")

    assert mineru.status == "available"
    assert mineru.version == "3.4.0"
    assert dots.status == "model_missing"
    assert attempts == 2


@pytest.mark.parametrize(
    ("payload", "warning_code"),
    [
        ({"choices": []}, "glm_ocr_external_invalid_response"),
        (
            {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]},
            "glm_ocr_external_truncated",
        ),
        (
            {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
            "glm_ocr_external_invalid_response",
        ),
    ],
)
def test_openai_invalid_truncated_and_empty_results_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    warning_code: str,
) -> None:
    _install_transport(monkeypatch, lambda _request: httpx.Response(200, json=payload))
    parser = ExternalParserClient(
        Settings(
            rag_parser_glm_ocr_api_host="https://glm.example.com",
            rag_parser_glm_ocr_model="glm-model",
        )
    )

    with pytest.raises(ExternalParserCallError) as exc_info:
        parser.parse("glm_ocr", b"png", _profile("scan.png", content_type="image/png"), "image/png")

    assert exc_info.value.warning_code == warning_code


def test_api_key_is_not_exposed_in_error_or_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "never-log-this-key"
    _install_transport(
        monkeypatch,
        lambda _request: httpx.Response(401, json={"detail": f"invalid {secret}"}),
    )
    parser = ExternalParserClient(
        Settings(
            rag_parser_glm_ocr_api_host="https://glm.example.com",
            rag_parser_glm_ocr_model="glm-model",
            rag_parser_glm_ocr_api_key=secret,
            rag_http_service_retry_attempts=1,
        )
    )

    with caplog.at_level(logging.WARNING), pytest.raises(ExternalParserCallError) as exc_info:
        parser.parse("glm_ocr", b"png", _profile("scan.png", content_type="image/png"), "image/png")

    assert secret not in str(exc_info.value)
    assert secret not in caplog.text
