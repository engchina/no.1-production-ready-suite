"""外部で運用される GPU 文書解析エンジンの native API クライアント。"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import batched
from typing import Any, Literal

import httpx
from rag_parser_core.extraction import ExtractionMetadataValue, ExtractionPage
from rag_parser_core.result import ParserRegistryResult

from app.clients.http_retry import request_with_retry, retry_config_from_settings
from app.config import Settings
from app.schemas.document import SourceProfile

logger = logging.getLogger(__name__)

ExternalParserBackend = Literal["unlimited_ocr", "mineru", "dots_ocr", "glm_ocr"]
ExternalParserProtocol = Literal["mineru_file_parse", "openai_chat_completions"]
ExternalParserStatusValue = Literal[
    "available", "unconfigured", "unreachable", "model_missing", "invalid_response"
]


@dataclass(frozen=True)
class ExternalParserEngineSpec:
    backend: ExternalParserBackend
    protocol: ExternalParserProtocol
    capabilities: tuple[str, ...]
    endpoint_field: str
    model_field: str | None
    api_key_field: str
    call: Callable[..., tuple[object, list[ExtractionPage]]]
    convert: Callable[..., ParserRegistryResult]


ENGINE_SPECS: dict[ExternalParserBackend, ExternalParserEngineSpec]
EXTERNAL_PARSER_BACKENDS: tuple[ExternalParserBackend, ...]


@dataclass(frozen=True)
class ExternalParserConnection:
    backend: ExternalParserBackend
    protocol: ExternalParserProtocol
    endpoint: str
    model: str | None
    api_key: str

    @property
    def configured(self) -> bool:
        model_configured = self.model if self.protocol == "openai_chat_completions" else True
        return bool(self.endpoint and model_configured)


@dataclass(frozen=True)
class ExternalParserStatus:
    backend: ExternalParserBackend
    status: ExternalParserStatusValue
    version: str | None = None
    warning_code: str | None = None


class ExternalParserCallError(RuntimeError):
    safe_for_user = True

    def __init__(
        self,
        reason: str,
        *,
        status_code: int | None = None,
        warning_code: str | None = None,
    ) -> None:
        self.reason = reason
        self.status_code = status_code
        self.warning_code = warning_code
        super().__init__(warning_code or reason)


@dataclass(frozen=True)
class _RenderedPage:
    number: int
    png: bytes
    width: int
    height: int
    mime_type: str = "image/png"


def external_parser_connection(
    settings: Settings, backend: ExternalParserBackend
) -> ExternalParserConnection:
    spec = ENGINE_SPECS[backend]
    model = str(getattr(settings, spec.model_field, "") or "").strip() if spec.model_field else None
    return ExternalParserConnection(
        backend=backend,
        protocol=spec.protocol,
        endpoint=str(getattr(settings, spec.endpoint_field, "") or "").strip().rstrip("/"),
        model=model,
        api_key=str(getattr(settings, spec.api_key_field, "") or "").strip(),
    )


class ExternalParserClient:
    """設定済み外部 parser を呼び、共通抽出 schema へ変換する。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._timeout = float(settings.rag_parser_service_timeout_seconds)
        self._retry = retry_config_from_settings(settings)

    def status(self, backend: ExternalParserBackend) -> ExternalParserStatus:
        connection = external_parser_connection(self._settings, backend)
        if not connection.configured:
            return ExternalParserStatus(
                backend,
                "unconfigured",
                warning_code="external_parser_unconfigured",
            )
        try:
            if connection.protocol == "mineru_file_parse":
                payload = self._request_json(
                    "GET",
                    f"{connection.endpoint}/health",
                    connection,
                    timeout=self._settings.rag_service_status_probe_timeout_seconds,
                )
                if not isinstance(payload, dict):
                    raise ExternalParserCallError(
                        "invalid_response",
                        warning_code=f"{backend}_external_invalid_response",
                    )
                version = _optional_text(payload.get("version"))
                return ExternalParserStatus(backend, "available", version=version)
            payload = self._request_json(
                "GET",
                f"{_openai_base(connection.endpoint)}/models",
                connection,
                timeout=self._settings.rag_service_status_probe_timeout_seconds,
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ExternalParserCallError(
                    "invalid_response",
                    warning_code=f"{backend}_external_invalid_response",
                )
            model_ids = {
                str(item.get("id"))
                for item in payload["data"]
                if isinstance(item, dict) and item.get("id")
            }
            if connection.model not in model_ids:
                return ExternalParserStatus(
                    backend, "model_missing", warning_code="external_parser_model_missing"
                )
            return ExternalParserStatus(backend, "available", version=connection.model)
        except ExternalParserCallError as exc:
            status: ExternalParserStatusValue = (
                "invalid_response" if exc.reason == "invalid_response" else "unreachable"
            )
            return ExternalParserStatus(backend, status, warning_code=exc.warning_code)

    def parse(
        self,
        backend: ExternalParserBackend,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
    ) -> ParserRegistryResult:
        connection = external_parser_connection(self._settings, backend)
        if not connection.configured:
            raise ExternalParserCallError("unconfigured", warning_code=f"{backend}_unconfigured")
        try:
            spec = ENGINE_SPECS[backend]
            rendered, pages = spec.call(
                self, source_bytes, source_profile, content_type, connection
            )
            result = spec.convert(
                rendered,
                backend=backend,
                source_profile=source_profile,
                parser_version=f"external:{connection.model or 'native'}",
                pages=pages,
                extra_artifacts={
                    "external_protocol": connection.protocol,
                    "external_model": connection.model,
                },
            )
            if result.extraction is None:
                raise ExternalParserCallError(
                    "adapter_empty_result", warning_code=f"{backend}_adapter_empty"
                )
            return result
        except ExternalParserCallError:
            raise
        except httpx.TimeoutException as exc:
            raise ExternalParserCallError(
                "timeout", warning_code=f"{backend}_external_timeout"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ExternalParserCallError(
                "http_error",
                status_code=exc.response.status_code,
                warning_code=f"{backend}_external_http_error",
            ) from exc
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ExternalParserCallError(
                "invalid_response", warning_code=f"{backend}_external_invalid_response"
            ) from exc

    def _parse_mineru(
        self,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
        connection: ExternalParserConnection,
    ) -> tuple[object, list[ExtractionPage]]:
        name = source_profile.sanitized_file_name if source_profile else "upload"
        payload = self._request_json(
            "POST",
            f"{connection.endpoint}/file_parse",
            connection,
            files={"files": (name, source_bytes, content_type or "application/octet-stream")},
            data={
                "lang_list": self._settings.rag_parser_mineru_language,
                "backend": "pipeline",
                "effort": "medium",
                "parse_method": "auto",
                "formula_enable": "true",
                "table_enable": "true",
                "return_md": "true",
                "return_content_list": "true",
                "return_images": "false",
            },
        )
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, dict) or not results:
            raise ValueError("mineru results is empty")
        document = next(iter(results.values()))
        if not isinstance(document, dict):
            raise ValueError("mineru result is invalid")
        blocks = document.get("content_list") or []
        if isinstance(blocks, str):
            blocks = json.loads(blocks)
        rendered: list[dict[str, object]] = []
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                rendered_block = dict(block)
                rendered_block["page_number"] = _positive_int(block.get("page_idx"), offset=1)
                text = _mineru_block_text(block)
                if text:
                    rendered_block["text"] = text
                if block.get("table_body"):
                    rendered_block["text_as_html"] = str(block["table_body"])
                rendered.append(rendered_block)
        if rendered:
            return rendered, _pages_from_elements(rendered)
        markdown = str(document.get("md_content") or "").strip()
        if not markdown:
            raise ValueError("mineru output is empty")
        return markdown, []

    def _parse_dots(
        self,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
        connection: ExternalParserConnection,
    ) -> tuple[object, list[ExtractionPage]]:
        rendered_pages = self._source_images(
            source_bytes, source_profile, content_type, self._settings.rag_parser_dots_ocr_dpi
        )
        workers = self._settings.rag_parser_dots_ocr_pdf_workers

        def parse_page(page: _RenderedPage) -> list[dict[str, object]]:
            text = self._openai_chat(
                connection,
                [(page.png, page.mime_type)],
                _DOTS_IMAGE_TOKENS + _DOTS_PROMPT,
                max_tokens=32768,
                max_tokens_key="max_completion_tokens",
                temperature=0.1,
                extra={"top_p": 0.9},
                text_after_images=True,
            )
            return _dots_elements(text, page.number)

        elements: list[dict[str, object]] = []
        pages: list[ExtractionPage] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for page_batch in batched(rendered_pages, workers):
                pages.extend(_extraction_pages(page_batch))
                elements.extend(
                    element
                    for page_elements in pool.map(parse_page, page_batch)
                    for element in page_elements
                )
        if not elements:
            raise ValueError("dots output is empty")
        return elements, pages

    def _parse_glm(
        self,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
        connection: ExternalParserConnection,
    ) -> tuple[object, list[ExtractionPage]]:
        rendered_pages = self._source_images(
            source_bytes, source_profile, content_type, self._settings.rag_parser_glm_ocr_dpi
        )
        elements: list[dict[str, object]] = []
        pages: list[ExtractionPage] = []
        for page in rendered_pages:
            pages.extend(_extraction_pages((page,)))
            text = _strip_fences(
                self._openai_chat(
                    connection,
                    [(page.png, page.mime_type)],
                    "Text Recognition:",
                    max_tokens=8192,
                )
            )
            if any(char.isalnum() for char in text):
                elements.append({"type": "text", "text": text, "page_number": page.number})
        if not elements:
            raise ValueError("glm output is empty")
        return elements, pages

    def _parse_unlimited(
        self,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
        connection: ExternalParserConnection,
    ) -> tuple[object, list[ExtractionPage]]:
        rendered_pages = self._source_images(
            source_bytes,
            source_profile,
            content_type,
            self._settings.rag_parser_unlimited_ocr_dpi,
        )
        batch_size = self._settings.rag_parser_unlimited_ocr_pdf_batch_size
        elements: list[dict[str, object]] = []
        pages: list[ExtractionPage] = []
        for batch in batched(rendered_pages, batch_size):
            pages.extend(_extraction_pages(batch))
            prompt = "<image>document parsing." if len(batch) == 1 else "<image>Multi page parsing."
            text = self._openai_chat(
                connection,
                [(page.png, page.mime_type) for page in batch],
                prompt,
                max_tokens=8192,
                extra={
                    "skip_special_tokens": False,
                    "vllm_xargs": {
                        "ngram_size": 35,
                        "window_size": 128 if len(batch) == 1 else 1024,
                    },
                },
            )
            parts = _unlimited_pages(text)
            if len(batch) > 1 and len(parts) != len(batch):
                raise ValueError("unlimited page count mismatch")
            if len(parts) == len(batch):
                elements.extend(
                    {"type": "text", "text": part, "page_number": page.number}
                    for page, part in zip(batch, parts, strict=True)
                    if part
                )
            elif parts:
                elements.append(
                    {"type": "text", "text": "\n\n".join(parts), "page_number": batch[0].number}
                )
        if not elements:
            raise ValueError("unlimited output is empty")
        return elements, pages

    def _source_images(
        self,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
        dpi: int,
    ) -> Iterator[_RenderedPage]:
        is_pdf = content_type.split(";", 1)[0].strip().casefold() == "application/pdf" or (
            source_profile is not None and (source_profile.extension or "").casefold() == ".pdf"
        )
        if not is_pdf:
            mime_type = content_type.split(";", 1)[0].strip().casefold()
            if not mime_type.startswith("image/"):
                mime_type = "image/png"
            yield _RenderedPage(1, source_bytes, 0, 0, mime_type)
            return
        try:
            import fitz  # type: ignore[import-untyped]

            rendered = False
            with fitz.open(stream=source_bytes, filetype="pdf") as document:
                for index, page in enumerate(document, 1):
                    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
                    rendered = True
                    yield _RenderedPage(
                        number=index,
                        png=pixmap.tobytes("png"),
                        width=pixmap.width,
                        height=pixmap.height,
                    )
        except Exception as exc:
            raise ExternalParserCallError(
                "adapter_invalid_input", warning_code="external_parser_pdf_render_failed"
            ) from exc
        if not rendered:
            raise ExternalParserCallError(
                "adapter_invalid_input", warning_code="external_parser_pdf_empty"
            )

    def _openai_chat(
        self,
        connection: ExternalParserConnection,
        images: list[tuple[bytes, str]],
        prompt: str,
        *,
        max_tokens: int,
        max_tokens_key: str = "max_tokens",
        temperature: float = 0.0,
        extra: dict[str, object] | None = None,
        text_after_images: bool = False,
    ) -> str:
        content: list[dict[str, object]] = []
        text_part: dict[str, object] = {"type": "text", "text": prompt}
        if not text_after_images:
            content.append(text_part)
        content.extend(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
                },
            }
            for data, mime in images
        )
        if text_after_images:
            content.append(text_part)
        body: dict[str, object] = {
            "model": connection.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
            max_tokens_key: max_tokens,
            **(extra or {}),
        }
        payload = self._request_json(
            "POST",
            f"{_openai_base(connection.endpoint)}/chat/completions",
            connection,
            json=body,
        )
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("openai choices is empty")
        if choices[0].get("finish_reason") == "length":
            raise ExternalParserCallError(
                "invalid_response", warning_code=f"{connection.backend}_external_truncated"
            )
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("openai message is invalid")
        return _message_text(message.get("content"))

    def _request_json(
        self,
        method: str,
        url: str,
        connection: ExternalParserConnection,
        *,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> object:
        headers = dict(kwargs.pop("headers", {}) or {})
        if connection.api_key:
            headers["Authorization"] = f"Bearer {connection.api_key}"
        try:
            with httpx.Client(timeout=timeout or self._timeout) as client:
                response = request_with_retry(
                    client,
                    method,
                    url,
                    retry=self._retry,
                    logger=logger,
                    log_extra={
                        "parser_backend": connection.backend,
                        "service_url": connection.endpoint,
                    },
                    headers=headers,
                    **kwargs,
                )
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise ExternalParserCallError(
                "timeout", warning_code=f"{connection.backend}_external_timeout"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ExternalParserCallError(
                "http_error",
                status_code=exc.response.status_code,
                warning_code=f"{connection.backend}_external_http_error",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalParserCallError(
                "unreachable", warning_code=f"{connection.backend}_external_unreachable"
            ) from exc
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ExternalParserCallError(
                "invalid_response", warning_code=f"{connection.backend}_external_invalid_response"
            ) from exc


def _openai_base(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    return endpoint if endpoint.endswith("/v1") else f"{endpoint}/v1"


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _positive_int(value: object, *, offset: int = 0) -> int:
    if not isinstance(value, str | bytes | bytearray | int | float):
        return 1
    try:
        return max(1, int(value) + offset)
    except (TypeError, ValueError):
        return 1


def _mineru_block_text(block: dict[str, object]) -> str:
    values: list[str] = []
    for key in ("text", "table_body", "latex", "equation", "image_caption", "image_footnote"):
        value = block.get(key)
        if isinstance(value, list):
            value = " ".join(str(item) for item in value if item)
        if value and str(value).strip():
            values.append(str(value).strip())
    return "\n".join(values)


def _pages_from_elements(elements: list[dict[str, object]]) -> list[ExtractionPage]:
    return [
        ExtractionPage(page_number=number, label=f"page {number}")
        for number in sorted({_positive_int(element.get("page_number")) for element in elements})
    ]


def _extraction_pages(pages: Iterable[_RenderedPage]) -> list[ExtractionPage]:
    return [
        ExtractionPage(
            page_number=page.number,
            label=f"page {page.number}",
            width=float(page.width) if page.width else None,
            height=float(page.height) if page.height else None,
        )
        for page in pages
    ]


def _message_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            str(item.get("text") or "") for item in value if isinstance(item, dict)
        ).strip()
    raise ValueError("openai content is invalid")


def _strip_fences(value: str) -> str:
    return "\n".join(
        line for line in value.splitlines() if not line.strip().startswith("```")
    ).strip()


def _dots_elements(value: str, page_number: int) -> list[dict[str, object]]:
    parsed = json.loads(_strip_fences(value))
    if isinstance(parsed, dict) and {"bbox", "category"} <= parsed.keys():
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError("dots layout must be a list")
    allowed = {
        "Caption",
        "Footnote",
        "Formula",
        "List-item",
        "Page-footer",
        "Page-header",
        "Picture",
        "Section-header",
        "Table",
        "Text",
        "Title",
    }
    result: list[dict[str, object]] = []
    for item in parsed:
        if not isinstance(item, dict) or item.get("category") not in allowed:
            raise ValueError("dots layout category is invalid")
        bbox = item.get("bbox")
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or not all(
                isinstance(number, int | float) and not isinstance(number, bool) for number in bbox
            )
        ):
            raise ValueError("dots layout bbox is invalid")
        if item["category"] != "Picture" and not isinstance(item.get("text"), str):
            raise ValueError("dots layout text is missing")
        mapped = dict(item)
        mapped["page_number"] = page_number
        if item["category"] == "Table":
            mapped["text_as_html"] = item.get("text")
        result.append(mapped)
    return result


_UNLIMITED_REF = re.compile(r"<\|ref\|>(.*?)<\|/ref\|>", re.S)
_UNLIMITED_DET = re.compile(r"<\|det\|>.*?<\|/det\|>", re.S)


def _unlimited_pages(value: str) -> list[str]:
    cleaned = _UNLIMITED_REF.sub(lambda match: match.group(1), value or "")
    cleaned = _UNLIMITED_DET.sub("", cleaned).replace("<｜end▁of▁sentence｜>", "").strip()
    if not cleaned:
        return []
    if "<PAGE>" not in cleaned:
        return [cleaned]
    return [part.strip() for part in cleaned.split("<PAGE>") if part.strip()]


_DOTS_PROMPT = (
    "Please output the layout information from the PDF image, including each layout "
    "element's bbox, its category, and the corresponding text content within the bbox.\n\n"
    "1. Bbox format: [x1, y1, x2, y2]\n\n"
    "2. Layout Categories: The possible categories are ['Caption', 'Footnote', "
    "'Formula', 'List-item', 'Page-footer', 'Page-header', 'Picture', "
    "'Section-header', 'Table', 'Text', 'Title'].\n\n"
    "3. Text Extraction & Formatting Rules:\n"
    "    - Picture: For the 'Picture' category, the text field should be omitted.\n"
    "    - Formula: Format its text as LaTeX.\n"
    "    - Table: Format its text as HTML.\n"
    "    - All Others (Text, Title, etc.): Format their text as Markdown.\n\n"
    "4. Constraints:\n"
    "    - The output text must be the original text from the image, with no translation.\n"
    "    - All layout elements must be sorted according to human reading order.\n\n"
    "5. Final Output: The entire output must be a single JSON object.\n"
)
_DOTS_IMAGE_TOKENS = "<|img|><|imgpad|><|endofimg|>"


def _convert_external_output(
    rendered: object,
    *,
    backend: ExternalParserBackend,
    source_profile: SourceProfile | None,
    parser_version: str,
    pages: list[ExtractionPage],
    extra_artifacts: dict[str, ExtractionMetadataValue],
) -> ParserRegistryResult:
    # backend 起動時は重い registry を load せず、実際の解析時だけ変換実装を読む。
    from rag_parser_core.registry import remap_external_ocr_output

    return remap_external_ocr_output(
        backend,
        rendered,
        source_profile=source_profile,
        parser_version=parser_version,
        pages=pages,
        extra_artifacts=extra_artifacts,
    )


# ponytail: 4 実装に必要な差分だけを登録し、動的 plugin/DSL は持たない。
ENGINE_SPECS = {
    "mineru": ExternalParserEngineSpec(
        backend="mineru",
        protocol="mineru_file_parse",
        capabilities=("pdf", "image", "office"),
        endpoint_field="rag_parser_mineru_api_host",
        model_field=None,
        api_key_field="rag_parser_mineru_api_key",
        call=ExternalParserClient._parse_mineru,
        convert=_convert_external_output,
    ),
    "dots_ocr": ExternalParserEngineSpec(
        backend="dots_ocr",
        protocol="openai_chat_completions",
        capabilities=("pdf", "image"),
        endpoint_field="rag_parser_dots_ocr_api_host",
        model_field="rag_parser_dots_ocr_model",
        api_key_field="rag_parser_dots_ocr_api_key",
        call=ExternalParserClient._parse_dots,
        convert=_convert_external_output,
    ),
    "glm_ocr": ExternalParserEngineSpec(
        backend="glm_ocr",
        protocol="openai_chat_completions",
        capabilities=("pdf", "image"),
        endpoint_field="rag_parser_glm_ocr_api_host",
        model_field="rag_parser_glm_ocr_model",
        api_key_field="rag_parser_glm_ocr_api_key",
        call=ExternalParserClient._parse_glm,
        convert=_convert_external_output,
    ),
    "unlimited_ocr": ExternalParserEngineSpec(
        backend="unlimited_ocr",
        protocol="openai_chat_completions",
        capabilities=("pdf", "image"),
        endpoint_field="rag_parser_unlimited_ocr_api_host",
        model_field="rag_parser_unlimited_ocr_model",
        api_key_field="rag_parser_unlimited_ocr_api_key",
        call=ExternalParserClient._parse_unlimited,
        convert=_convert_external_output,
    ),
}
EXTERNAL_PARSER_BACKENDS = tuple(ENGINE_SPECS)
