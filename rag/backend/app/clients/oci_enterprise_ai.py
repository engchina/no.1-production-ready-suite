"""OCI Enterprise AI クライアント（LLM / VLM）。

⚠️ 重要: LLM / VLM は **OCI Enterprise AI** を使う。
OCI Generative AI の chat 推論 API は使わない（AGENTS.md 参照）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import (
    Settings,
    enterprise_ai_default_model_id,
    enterprise_ai_vision_model_id,
    get_settings,
)
from app.schemas.extraction import StructuredExtraction

DEFAULT_LLM_PATH = "/responses"
DEFAULT_VLM_PATH = "/responses"
DEFAULT_MIME_TYPE = "application/octet-stream"
JSON_HEADERS = {"accept": "application/json", "content-type": "application/json"}
IMAGE_MIME_TYPES = frozenset({"image/gif", "image/jpeg", "image/jpg", "image/png", "image/webp"})
FILE_EXTENSION_BY_MIME_TYPE = {
    "application/pdf": ".pdf",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "text/plain": ".txt",
}
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
TEMPLATE_TOKEN_RE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)
ENTERPRISE_AI_ENVELOPE_KEYS = (
    "structured_extraction",
    "extraction",
    "answer",
    "text",
    "output_text",
    "generated_text",
    "content",
    "message",
    "prediction",
    "predictions",
    "inference_response",
    "inferenceResponse",
    "response",
    "data",
    "output",
    "outputs",
    "result",
)
LLM_SYSTEM_PROMPT = (
    "あなたは業務文書向け RAG アシスタントです。"
    "回答は日本語で行い、必ず与えられた根拠コンテキストだけに基づいてください。"
    "根拠が不足する場合は、不足していると明示してください。"
    "口座番号や個人番号などの機微情報は必要最小限に留めてください。"
)
STRUCTURED_EXTRACTION_INSTRUCTIONS = (
    "文書を日本語優先で OCR し、raw_text に読み順の本文全体を入れてください。"
    "elements にはページ順・読み順で title/text/list/table/figure/header/footer 等の"
    "構造要素を返し、"
    "表は table として他要素から分離してください。"
    "page_number、section_path、confidence、bbox が分かる場合は付与してください。"
)


class EnterpriseAiHttpTransport(Protocol):
    """Enterprise AI endpoint へ JSON POST する最小 transport。"""

    async def post_json(
        self,
        url: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, Any]:
        """JSON payload を送信し、JSON object response を返す。"""

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
        """Files API へ multipart file を送信し、JSON object response を返す。"""

    async def delete(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, Any]:
        """Enterprise AI endpoint の resource を削除し、JSON object response を返す。"""


@dataclass(frozen=True)
class EnterpriseAiRequestPreview:
    """Enterprise AI request の非機密プレビュー。"""

    surface: str
    url: str
    template_used: bool
    timeout_seconds: float
    max_retries: int
    response_path_set: bool
    payload_keys: list[str]
    payload_shape: dict[str, object]
    payload_json_bytes: int


class OciEnterpriseAiClient:
    """OCI Enterprise AI による LLM / VLM 推論クライアント。"""

    def __init__(
        self,
        settings: Settings | None = None,
        http_transport: EnterpriseAiHttpTransport | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._http_transport = http_transport or _DefaultEnterpriseAiTransport(self._settings)

    async def extract_with_vlm(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = DEFAULT_MIME_TYPE,
    ) -> dict[str, object]:
        """VLM で画像から構造化データを抽出する（OCR）。"""
        return await self._extract_with_enterprise_ai(image_bytes, prompt, mime_type=mime_type)

    async def generate(self, prompt: str, context: str) -> str:
        """LLM で回答を生成する。"""
        return await self._generate_with_enterprise_ai(prompt, context)

    async def generate_from_image(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = DEFAULT_MIME_TYPE,
    ) -> str:
        """Vision smoke test 用に OpenAI Responses 形式で画像から text を生成する。"""
        payload = _build_vision_text_payload(
            self._settings,
            image_bytes,
            prompt,
            mime_type=mime_type,
        )
        response = await self._post_enterprise_ai(
            self._settings.oci_enterprise_ai_vlm_path,
            payload,
        )
        return _parse_generated_text(
            response,
            response_path=self._settings.oci_enterprise_ai_vlm_response_path,
        )

    def preview_llm_request(self, prompt: str, context: str) -> EnterpriseAiRequestPreview:
        """LLM endpoint request の非機密プレビューを返す。"""
        payload = _build_llm_payload(self._settings, prompt, context)
        return _request_preview(
            self._settings,
            surface="llm",
            path=self._settings.oci_enterprise_ai_llm_path,
            payload=payload,
            template_used=bool(self._settings.oci_enterprise_ai_llm_payload_template.strip()),
            response_path_set=bool(self._settings.oci_enterprise_ai_llm_response_path.strip()),
        )

    def preview_vlm_request(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = DEFAULT_MIME_TYPE,
    ) -> EnterpriseAiRequestPreview:
        """VLM endpoint request の非機密プレビューを返す。"""
        file_id = (
            "" if self._settings.oci_enterprise_ai_vlm_payload_template.strip() else "file_preview"
        )
        payload = _build_vlm_payload(
            self._settings,
            image_bytes,
            prompt,
            mime_type=mime_type,
            file_id=file_id,
        )
        return _request_preview(
            self._settings,
            surface="vlm",
            path=self._settings.oci_enterprise_ai_vlm_path,
            payload=payload,
            template_used=bool(self._settings.oci_enterprise_ai_vlm_payload_template.strip()),
            response_path_set=bool(self._settings.oci_enterprise_ai_vlm_response_path.strip()),
        )

    async def _extract_with_enterprise_ai(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str,
    ) -> dict[str, object]:
        """OCI Enterprise AI VLM endpoint を呼び出し、構造化抽出を検証する。"""
        uploaded_file_id = ""
        normalized_mime_type = _normalized_mime_type(mime_type)
        if (
            not self._settings.oci_enterprise_ai_vlm_payload_template.strip()
            and normalized_mime_type not in IMAGE_MIME_TYPES
        ):
            uploaded_file_id = await self._upload_vlm_input_file(image_bytes, mime_type=mime_type)
        try:
            payload = _build_vlm_payload(
                self._settings,
                image_bytes,
                prompt,
                mime_type=mime_type,
                file_id=uploaded_file_id,
            )
            response = await self._post_enterprise_ai(
                self._settings.oci_enterprise_ai_vlm_path,
                payload,
            )
            extraction = _parse_structured_extraction(
                response,
                response_path=self._settings.oci_enterprise_ai_vlm_response_path,
            )
            return extraction.to_document_payload()
        finally:
            if uploaded_file_id:
                await self._delete_uploaded_file(uploaded_file_id)

    async def _upload_vlm_input_file(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> str:
        """OCI Files API へ VLM 入力をアップロードし、Responses 用 file_id を返す。"""
        endpoint = _require_value(
            self._settings.oci_enterprise_ai_endpoint,
            "OCI Enterprise AI endpoint",
        )
        normalized_mime_type = _normalized_mime_type(mime_type)
        response = await self._http_transport.upload_file(
            _join_endpoint_path(endpoint, "/files"),
            _uploaded_file_name(normalized_mime_type),
            image_bytes,
            mime_type=normalized_mime_type,
            purpose=_upload_file_purpose(normalized_mime_type),
            headers=_enterprise_ai_headers(self._settings, json_content_type=False),
            timeout=self._settings.oci_enterprise_ai_timeout_seconds,
        )
        return _uploaded_file_id(response)

    async def _delete_uploaded_file(self, file_id: str) -> None:
        """OCI Files API の一時ファイルを best-effort で削除する。"""
        endpoint = _require_value(
            self._settings.oci_enterprise_ai_endpoint,
            "OCI Enterprise AI endpoint",
        )
        try:
            await self._http_transport.delete(
                _join_endpoint_path(endpoint, f"/files/{file_id}"),
                headers=_enterprise_ai_headers(self._settings, json_content_type=False),
                timeout=self._settings.oci_enterprise_ai_timeout_seconds,
            )
        except Exception:
            return

    async def _generate_with_enterprise_ai(self, prompt: str, context: str) -> str:
        """OCI Enterprise AI LLM endpoint を呼び出し、RAG 回答を生成する。"""
        payload = _build_llm_payload(self._settings, prompt, context)
        response = await self._post_enterprise_ai(
            self._settings.oci_enterprise_ai_llm_path,
            payload,
        )
        return _parse_generated_text(
            response,
            response_path=self._settings.oci_enterprise_ai_llm_response_path,
        )

    async def _post_enterprise_ai(
        self,
        path: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Enterprise AI endpoint へ JSON POST する。"""
        endpoint = _require_value(
            self._settings.oci_enterprise_ai_endpoint,
            "OCI Enterprise AI endpoint",
        )
        url = _join_endpoint_path(endpoint, path)
        return await self._http_transport.post_json(
            url,
            payload,
            headers=_enterprise_ai_headers(self._settings),
            timeout=self._settings.oci_enterprise_ai_timeout_seconds,
        )


class _DefaultEnterpriseAiTransport:
    """httpx による OpenAI-compatible Enterprise AI transport。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def post_json(
        self,
        url: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        attempts = self._settings.oci_enterprise_ai_max_retries + 1
        async with httpx.AsyncClient(follow_redirects=False) as client:
            for attempt in range(attempts):
                response = await client.post(
                    url,
                    content=body,
                    headers=headers,
                    timeout=timeout,
                )
                if response.status_code in RETRYABLE_STATUS_CODES and attempt + 1 < attempts:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                _raise_for_status_with_body(response, "OCI Enterprise AI endpoint")
                return _json_response_object(response)
        raise RuntimeError("OCI Enterprise AI endpoint の呼び出しに失敗しました。")

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
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.post(
                url,
                data={"purpose": purpose},
                files={"file": (file_name, content, mime_type)},
                headers=headers,
                timeout=timeout,
            )
            _raise_for_status_with_body(response, "OCI Enterprise AI Files API")
            return _json_response_object(response)

    async def delete(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, Any]:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.delete(url, headers=headers, timeout=timeout)
            _raise_for_status_with_body(response, "OCI Enterprise AI Files API")
            return _json_response_object(response)


def _rag_user_message(prompt: str, context: str) -> str:
    """Enterprise AI LLM に渡す RAG 用 user message を作る。"""
    return (
        "質問:\n"
        f"{prompt}\n\n"
        "根拠コンテキスト:\n"
        f"{context}\n\n"
        "出力要件:\n"
        "- 日本語で簡潔に回答してください。\n"
        "- 根拠にない内容は推測しないでください。\n"
        "- 回答の判断に使った文書名や chunk id が分かる場合は文中に含めてください。"
    )


def _build_vlm_payload(
    settings: Settings,
    image_bytes: bytes,
    prompt: str,
    *,
    mime_type: str,
    file_id: str = "",
) -> dict[str, Any]:
    """VLM endpoint へ送る payload を標準形または template から作る。"""
    model_id = enterprise_ai_vision_model_id(settings)
    data_base64 = base64.b64encode(image_bytes).decode("ascii")
    response_format = {
        "type": "json_schema",
        "schema": StructuredExtraction.model_json_schema(),
    }
    values = {
        "model": model_id,
        "project": settings.oci_enterprise_ai_project_ocid,
        "project_ocid": settings.oci_enterprise_ai_project_ocid,
        "compartment_id": settings.oci_compartment_id,
        "task": "structured_document_extraction",
        "language": "ja",
        "prompt": prompt,
        "structure_instructions": STRUCTURED_EXTRACTION_INSTRUCTIONS,
        "mime_type": _normalized_mime_type(mime_type),
        "data_base64": data_base64,
        "file_id": file_id,
        "response_format": response_format,
        "structured_extraction_schema": StructuredExtraction.model_json_schema(),
        "structured_extraction_schema_json": json.dumps(
            StructuredExtraction.model_json_schema(),
            ensure_ascii=False,
        ),
    }
    if settings.oci_enterprise_ai_vlm_payload_template.strip():
        _require_template_values(
            settings.oci_enterprise_ai_vlm_payload_template,
            values,
            "OCI Enterprise AI VLM payload template",
        )
        return _render_payload_template(
            settings.oci_enterprise_ai_vlm_payload_template,
            values,
            "OCI Enterprise AI VLM payload template",
        )

    _require_value(model_id, "OCI Enterprise AI Vision model")
    text_format = {
        "type": "json_schema",
        "name": "structured_extraction",
        "schema": StructuredExtraction.model_json_schema(),
    }
    if file_id:
        uploaded_file_content = _responses_uploaded_file_content(values["mime_type"], file_id)
        if uploaded_file_content["type"] == "input_image":
            content = [
                {"type": "input_text", "text": prompt},
                uploaded_file_content,
            ]
        else:
            content = [
                uploaded_file_content,
                {"type": "input_text", "text": prompt},
            ]
    else:
        content = [
            {"type": "input_text", "text": prompt},
            {
                "type": "input_image",
                "image_url": f"data:{values['mime_type']};base64,{data_base64}",
            },
        ]
    return {
        "model": model_id,
        "instructions": STRUCTURED_EXTRACTION_INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "text": {"format": text_format},
    }


def _build_vision_text_payload(
    settings: Settings,
    image_bytes: bytes,
    prompt: str,
    *,
    mime_type: str,
) -> dict[str, Any]:
    """Vision 接続確認用の最小 OpenAI Responses payload を作る。"""
    model_id = enterprise_ai_vision_model_id(settings)
    _require_value(model_id, "OCI Enterprise AI Vision model")
    normalized_mime_type = _normalized_mime_type(mime_type)
    data_base64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "model": model_id,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:{normalized_mime_type};base64,{data_base64}",
                    },
                ],
            }
        ],
    }


def _build_llm_payload(settings: Settings, prompt: str, context: str) -> dict[str, Any]:
    """LLM endpoint へ送る payload を標準形または template から作る。"""
    model_id = enterprise_ai_default_model_id(settings)
    user_message = _rag_user_message(prompt, context)
    messages = [
        {"role": "system", "content": LLM_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    parameters = {
        "temperature": 0.0,
        "max_output_tokens": 1200,
    }
    values = {
        "model": model_id,
        "project": settings.oci_enterprise_ai_project_ocid,
        "project_ocid": settings.oci_enterprise_ai_project_ocid,
        "compartment_id": settings.oci_compartment_id,
        "task": "rag_answer_generation",
        "language": "ja",
        "prompt": prompt,
        "context": context,
        "system_prompt": LLM_SYSTEM_PROMPT,
        "user_message": user_message,
        "input": [{"role": "user", "content": user_message}],
        "instructions": LLM_SYSTEM_PROMPT,
        "messages": messages,
        "parameters": parameters,
        "temperature": parameters["temperature"],
        "max_output_tokens": parameters["max_output_tokens"],
    }
    if settings.oci_enterprise_ai_llm_payload_template.strip():
        _require_template_values(
            settings.oci_enterprise_ai_llm_payload_template,
            values,
            "OCI Enterprise AI LLM payload template",
        )
        return _render_payload_template(
            settings.oci_enterprise_ai_llm_payload_template,
            values,
            "OCI Enterprise AI LLM payload template",
        )

    _require_value(model_id, "OCI Enterprise AI default model")
    return {
        "model": model_id,
        "instructions": LLM_SYSTEM_PROMPT,
        "input": [{"role": "user", "content": user_message}],
        "temperature": parameters["temperature"],
        "max_output_tokens": parameters["max_output_tokens"],
    }


def _render_payload_template(
    template: str,
    values: Mapping[str, object],
    label: str,
) -> dict[str, Any]:
    """JSON object template の `${name}` placeholder を値で置換する。"""
    try:
        parsed = json.loads(template)
    except ValueError as exc:
        raise ValueError(f"{label} は JSON object 文字列である必要があります。") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} は JSON object 文字列である必要があります。")
    rendered = _render_template_node(parsed, values)
    if not isinstance(rendered, dict):
        raise ValueError(f"{label} は JSON object へ展開される必要があります。")
    return rendered


def _render_template_node(value: object, values: Mapping[str, object]) -> object:
    """template node を再帰的に展開する。"""
    if isinstance(value, dict):
        return {str(key): _render_template_node(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_template_node(item, values) for item in value]
    if isinstance(value, str):
        full_match = TEMPLATE_TOKEN_RE.fullmatch(value)
        if full_match:
            return values[full_match.group(1)]
        return TEMPLATE_TOKEN_RE.sub(lambda match: str(values[match.group(1)]), value)
    return value


def _require_template_values(
    template: str,
    values: Mapping[str, object],
    label: str,
) -> None:
    """template が参照する placeholder がすべて利用可能か検証する。"""
    referenced = set(TEMPLATE_TOKEN_RE.findall(template))
    unknown = sorted(referenced.difference(values))
    if unknown:
        raise ValueError(f"{label} に未対応の placeholder があります: {', '.join(unknown)}")


def _normalized_mime_type(value: str) -> str:
    """MIME type を Enterprise AI payload 用に正規化する。"""
    cleaned = value.split(";", maxsplit=1)[0].strip().lower()
    return cleaned or DEFAULT_MIME_TYPE


def _upload_file_purpose(mime_type: str) -> str:
    """Files API の purpose を MIME type から選ぶ。"""
    return "vision" if mime_type in IMAGE_MIME_TYPES else "user_data"


def _uploaded_file_name(mime_type: str) -> str:
    """OCI Files API に渡す一時ファイル名を作る。"""
    extension = FILE_EXTENSION_BY_MIME_TYPE.get(mime_type, ".bin")
    return f"enterprise-ai-input{extension}"


def _uploaded_file_id(response: Mapping[str, Any]) -> str:
    """Files API response から file id を取り出す。"""
    file_id = response.get("id")
    if not isinstance(file_id, str) or not file_id.strip():
        raise ValueError("OCI Enterprise AI Files API response に file id がありません。")
    return file_id.strip()


def _responses_uploaded_file_content(mime_type: object, file_id: str) -> dict[str, str]:
    """OpenAI Responses content item をアップロード済み file 種別ごとに作る。"""
    normalized_mime_type = _normalized_mime_type(str(mime_type))
    if normalized_mime_type in IMAGE_MIME_TYPES:
        return {"type": "input_image", "file_id": file_id}
    return {"type": "input_file", "file_id": file_id}


def _join_endpoint_path(endpoint: str, path: str) -> str:
    """base endpoint と path を安全に結合する。"""
    cleaned_path = path.strip() or DEFAULT_LLM_PATH
    if cleaned_path.startswith("http://") or cleaned_path.startswith("https://"):
        return cleaned_path
    return f"{endpoint.rstrip('/')}/{cleaned_path.lstrip('/')}"


def _enterprise_ai_headers(
    settings: Settings,
    *,
    json_content_type: bool = True,
) -> dict[str, str]:
    """OCI OpenAI-compatible API の project/API key ヘッダーを付与する。"""
    headers = dict(JSON_HEADERS if json_content_type else {"accept": "application/json"})
    if project := settings.oci_enterprise_ai_project_ocid.strip():
        headers["OpenAI-Project"] = project
    api_key = _require_value(settings.oci_enterprise_ai_api_key, "OCI Enterprise AI API key")
    headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _require_value(value: str, label: str) -> str:
    """必須設定文字列を検証する。"""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} が未設定です。")
    return cleaned


def _request_preview(
    settings: Settings,
    *,
    surface: str,
    path: str,
    payload: Mapping[str, Any],
    template_used: bool,
    response_path_set: bool,
) -> EnterpriseAiRequestPreview:
    """Enterprise AI request の非機密プレビューを作る。"""
    endpoint = _require_value(settings.oci_enterprise_ai_endpoint, "OCI Enterprise AI endpoint")
    payload_shape = _payload_shape(payload)
    if not isinstance(payload_shape, dict):
        payload_shape = {}
    return EnterpriseAiRequestPreview(
        surface=surface,
        url=_join_endpoint_path(endpoint, path),
        template_used=template_used,
        timeout_seconds=settings.oci_enterprise_ai_timeout_seconds,
        max_retries=settings.oci_enterprise_ai_max_retries,
        response_path_set=response_path_set,
        payload_keys=sorted(str(key) for key in payload),
        payload_shape=payload_shape,
        payload_json_bytes=len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ),
    )


def _payload_shape(value: object) -> object:
    """payload 値を raw content なしの型・サイズ情報へ変換する。"""
    if isinstance(value, Mapping):
        return {str(key): _payload_shape(item) for key, item in value.items()}
    if isinstance(value, list):
        if not value:
            return []
        return [_payload_shape(value[0])]
    if isinstance(value, str):
        return f"str[{len(value)}]"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    return type(value).__name__


def _retry_delay(attempt: int) -> float:
    """短い指数バックオフ。"""
    return float(min(2.0, 0.25 * (2**attempt)))


def _json_response_object(response: httpx.Response) -> Mapping[str, Any]:
    """httpx response から JSON object を取り出す。"""
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("OCI Enterprise AI response が JSON ではありません。") from exc
    if not isinstance(payload, dict):
        raise ValueError("OCI Enterprise AI response は JSON object である必要があります。")
    return payload


def _raise_for_status_with_body(response: httpx.Response, label: str) -> None:
    """HTTP error に OCI response body を含めて原因調査しやすくする。"""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text.strip()
        if body:
            truncated_body = body[:2000]
            raise httpx.HTTPStatusError(
                f"{label} returned {response.status_code}: {truncated_body}",
                request=exc.request,
                response=exc.response,
            ) from exc
        raise


def _parse_structured_extraction(
    response: Mapping[str, Any],
    *,
    response_path: str = "",
) -> StructuredExtraction:
    """Enterprise AI VLM response から StructuredExtraction を取り出す。"""
    candidate = _select_response_path(
        response,
        response_path,
        "OCI Enterprise AI VLM response path",
    )
    if error_message := _response_error_message(candidate):
        raise ValueError(error_message)
    text = _extract_text_candidate(candidate)
    if text:
        candidate = _json_string_to_object(text)
        return StructuredExtraction.model_validate(dict(candidate))
    candidate = _unwrap_response_payload(candidate)
    if error_message := _response_error_message(candidate):
        raise ValueError(error_message)
    if isinstance(candidate, str):
        candidate = _json_string_to_object(candidate)
    elif not isinstance(candidate, Mapping):
        text = _extract_text_candidate(candidate)
        if text:
            candidate = _json_string_to_object(text)
    if not isinstance(candidate, Mapping):
        raise ValueError("OCI Enterprise AI VLM response に構造化抽出 object がありません。")
    return StructuredExtraction.model_validate(dict(candidate))


def _parse_generated_text(
    response: Mapping[str, Any],
    *,
    response_path: str = "",
) -> str:
    """Enterprise AI LLM response から回答 text を取り出す。"""
    candidate = _select_response_path(
        response,
        response_path,
        "OCI Enterprise AI LLM response path",
    )
    if error_message := _response_error_message(candidate):
        raise ValueError(error_message)
    text = _extract_text_candidate(candidate)
    if text.strip():
        return text.strip()
    candidate = _unwrap_response_payload(candidate)
    if error_message := _response_error_message(candidate):
        raise ValueError(error_message)
    text = _extract_text_candidate(candidate)
    if not text.strip():
        raise ValueError("OCI Enterprise AI LLM response に回答 text がありません。")
    return text.strip()


def _select_response_path(payload: object, path: str, label: str) -> object:
    """JSON Pointer 風 path で response の候補 node を選ぶ。"""
    cleaned = path.strip()
    if not cleaned:
        return payload
    if not cleaned.startswith("/"):
        raise ValueError(f"{label} は / で始まる JSON Pointer 形式で指定してください。")
    current = payload
    for raw_segment in cleaned.split("/")[1:]:
        segment = _decode_json_pointer_segment(raw_segment)
        if isinstance(current, Mapping):
            if segment not in current:
                raise ValueError(f"{label} の key が見つかりません: {segment}")
            current = current[segment]
            continue
        if isinstance(current, list):
            if not segment.isdigit():
                raise ValueError(f"{label} の list index が不正です: {segment}")
            index = int(segment)
            if index >= len(current):
                raise ValueError(f"{label} の list index が範囲外です: {segment}")
            current = current[index]
            continue
        raise ValueError(f"{label} は途中で object/list 以外に到達しました。")
    return current


def _decode_json_pointer_segment(segment: str) -> str:
    """JSON Pointer segment の ~0 / ~1 escape を戻す。"""
    return segment.replace("~1", "/").replace("~0", "~")


def _unwrap_response_payload(payload: object) -> object:
    """よくある Enterprise/custom endpoint response 形状を順に剥がす。"""
    current = payload
    for _ in range(6):
        if isinstance(current, Mapping):
            for key in ENTERPRISE_AI_ENVELOPE_KEYS:
                if key in current:
                    current = current[key]
                    break
            else:
                if choices := current.get("choices"):
                    current = choices
                else:
                    tool_payload = _extract_tool_call_payload(current)
                    if tool_payload is not None:
                        current = tool_payload
                        continue
                    return current
                continue
            continue
        if isinstance(current, list) and current:
            if _looks_like_text_parts(current):
                return current
            current = current[0]
            continue
        return current
    return current


def _extract_text_candidate(candidate: object) -> str:
    """OpenAI 風/custom 風の候補から text を取り出す。"""
    if isinstance(candidate, str):
        return _extract_text_from_json_string(candidate) or candidate
    if isinstance(candidate, Mapping):
        tool_payload = _extract_tool_call_payload(candidate)
        if tool_payload is not None:
            return _extract_text_candidate(tool_payload)
        if message := candidate.get("message"):
            return _extract_text_candidate(message)
        for key in ("content", "text", "answer", "output_text", "generated_text"):
            value = candidate.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                parts = [_extract_text_candidate(item) for item in value]
                return "\n".join(part for part in parts if part)
        for key in ("output", "outputs", "data", "response", "result"):
            value = candidate.get(key)
            if isinstance(value, list | Mapping):
                text = _extract_text_candidate(value)
                if text:
                    return text
        if isinstance(candidate.get("parts"), list):
            parts = [_extract_text_candidate(part) for part in candidate["parts"]]
            return "\n".join(part for part in parts if part)
    if isinstance(candidate, list):
        parts = [_extract_text_candidate(item) for item in candidate]
        return "\n".join(part for part in parts if part)
    return ""


def _extract_text_from_json_string(value: str) -> str:
    """JSON 文字列に answer/text が入っている場合は text として取り出す。"""
    cleaned = _strip_json_fence(value)
    object_text = _extract_json_object_text(cleaned)
    if object_text is None:
        return ""
    try:
        parsed = json.loads(object_text)
    except ValueError:
        return ""
    if not isinstance(parsed, Mapping):
        return ""
    return _extract_text_candidate(parsed)


def _looks_like_text_parts(candidate: list[object]) -> bool:
    """LLM content parts の list は後段で結合するため残す。"""
    return all(
        isinstance(item, Mapping)
        and isinstance(item.get("text"), str)
        and "message" not in item
        and "tool_calls" not in item
        and "function" not in item
        for item in candidate
    )


def _extract_tool_call_payload(candidate: Mapping[str, Any]) -> object | None:
    """tool/function call 形式の arguments payload を取り出す。"""
    function_call = candidate.get("function_call")
    if isinstance(function_call, Mapping):
        return _extract_function_arguments(function_call)
    tool_calls = candidate.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        first_tool_call = tool_calls[0]
        if isinstance(first_tool_call, Mapping):
            function = first_tool_call.get("function")
            if isinstance(function, Mapping):
                return _extract_function_arguments(function)
            return _extract_function_arguments(first_tool_call)
    return None


def _response_error_message(payload: object) -> str:
    """OpenAI Responses 互換の error/status を空 text より先に失敗理由へ変換する。"""
    if not isinstance(payload, Mapping):
        return ""
    if error := payload.get("error"):
        return _format_response_error(error, "OCI Enterprise AI response error")
    incomplete_details = payload.get("incomplete_details")
    status = str(payload.get("status", "")).strip().lower()
    if status in {"failed", "incomplete", "cancelled"}:
        detail = _format_response_error(
            incomplete_details,
            f"OCI Enterprise AI response status={status}",
        )
        return detail
    return ""


def _format_response_error(value: object, prefix: str) -> str:
    """response error object をユーザーに見える短い文字列へ整形する。"""
    if isinstance(value, Mapping):
        message = value.get("message") or value.get("reason") or value.get("code")
        error_type = value.get("type") or value.get("code")
        parts = [prefix]
        if error_type:
            parts.append(str(error_type))
        if message:
            parts.append(str(message))
        return ": ".join(parts)
    if value:
        return f"{prefix}: {value}"
    return prefix


def _extract_function_arguments(candidate: Mapping[str, Any]) -> object | None:
    """function/tool call でよく使われる arguments key を見る。"""
    for key in ("arguments", "arguments_json", "parameters", "input"):
        value = candidate.get(key)
        if isinstance(value, str | Mapping):
            return value
    return None


def _json_string_to_object(value: str) -> Mapping[str, Any]:
    """JSON 文字列を object に変換する。"""
    cleaned = _strip_json_fence(value)
    try:
        parsed = json.loads(cleaned)
    except ValueError as exc:
        if object_text := _extract_json_object_text(cleaned):
            try:
                parsed = json.loads(object_text)
            except ValueError:
                raise ValueError(
                    "OCI Enterprise AI VLM response の JSON 文字列を解析できません。"
                ) from exc
        else:
            raise ValueError(
                "OCI Enterprise AI VLM response の JSON 文字列を解析できません。"
            ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            "OCI Enterprise AI VLM response の JSON 文字列は object である必要があります。"
        )
    return parsed


def _strip_json_fence(value: str) -> str:
    """Markdown fenced JSON を endpoint response から取り除く。"""
    stripped = value.strip()
    if match := JSON_FENCE_RE.match(stripped):
        return match.group(1).strip()
    return stripped


def _extract_json_object_text(value: str) -> str | None:
    """説明文に包まれた最初の JSON object だけを切り出す。"""
    start = value.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(value[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return value[start : index + 1]
    return None
