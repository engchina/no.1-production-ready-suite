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
        if self._settings.ai_service_adapter == "oci":
            return await self._extract_with_enterprise_ai(image_bytes, prompt, mime_type=mime_type)
        text = _decode_document_bytes(image_bytes)
        extraction = StructuredExtraction(
            raw_text=text,
            document_type=_guess_document_type(text),
            confidence=0.62 if text else 0.0,
            warnings=[] if text else ["ローカル抽出ではテキストを取得できませんでした。"],
        )
        return extraction.to_document_payload()

    async def generate(self, prompt: str, context: str) -> str:
        """LLM で回答を生成する。"""
        if self._settings.ai_service_adapter == "oci":
            return await self._generate_with_enterprise_ai(prompt, context)
        if not context.strip():
            return "該当する根拠は見つかりませんでした。条件やキーワードを変えて検索してください。"
        snippets = _extract_context_snippets(context)
        joined = " / ".join(snippets[:3])
        return (
            "検索された根拠に基づく要約です。"
            f"質問「{prompt}」に関連する内容として、{joined} が見つかりました。"
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
        payload = _build_vlm_payload(self._settings, image_bytes, prompt, mime_type=mime_type)
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
        payload = _build_vlm_payload(self._settings, image_bytes, prompt, mime_type=mime_type)
        response = await self._post_enterprise_ai(
            self._settings.oci_enterprise_ai_vlm_path,
            payload,
        )
        extraction = _parse_structured_extraction(
            response,
            response_path=self._settings.oci_enterprise_ai_vlm_response_path,
        )
        return extraction.to_document_payload()

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
                response.raise_for_status()
                return _json_response_object(response)
        raise RuntimeError("OCI Enterprise AI endpoint の呼び出しに失敗しました。")


def _decode_document_bytes(data: bytes) -> str:
    """ローカル参照実装用にアップロード内容をテキスト化する。"""
    for encoding in ("utf-8", "cp932", "shift_jis"):
        try:
            decoded = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        normalized = decoded.replace("\r\n", "\n").replace("\r", "\n")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.splitlines()]
        return "\n".join(line for line in lines if line).strip()
    return ""


def _guess_document_type(text: str) -> str:
    """文書種別をざっくり推定する。"""
    if "FAQ" in text or "よくある質問" in text:
        return "FAQ"
    if "議事録" in text:
        return "議事録"
    if "規程" in text or "ポリシー" in text:
        return "社内規程"
    if "マニュアル" in text or "手順" in text:
        return "マニュアル"
    if "仕様" in text or "要件" in text:
        return "仕様書"
    if "報告" in text or "レポート" in text:
        return "報告書"
    if "ナレッジ" in text or "knowledge" in text.lower():
        return "ナレッジ"
    return "ドキュメント"


def _extract_context_snippets(context: str) -> list[str]:
    """生成に渡したコンテキストから短い根拠文を抜き出す。"""
    snippets: list[str] = []
    for line in context.splitlines():
        cleaned = line.strip().removeprefix("-").strip()
        if len(cleaned) >= 12:
            snippets.append(cleaned[:160])
    return snippets or [context[:160]]


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
    return {
        "model": model_id,
        "compartment_id": settings.oci_compartment_id,
        "task": "structured_document_extraction",
        "language": "ja",
        "prompt": prompt,
        "instructions": STRUCTURED_EXTRACTION_INSTRUCTIONS,
        "input": {
            "mime_type": values["mime_type"],
            "data_base64": data_base64,
        },
        "response_format": response_format,
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
        "compartment_id": settings.oci_compartment_id,
        "task": "rag_answer_generation",
        "language": "ja",
        "messages": messages,
        "parameters": parameters,
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


def _join_endpoint_path(endpoint: str, path: str) -> str:
    """base endpoint と path を安全に結合する。"""
    cleaned_path = path.strip() or DEFAULT_LLM_PATH
    if cleaned_path.startswith("http://") or cleaned_path.startswith("https://"):
        return cleaned_path
    return f"{endpoint.rstrip('/')}/{cleaned_path.lstrip('/')}"


def _enterprise_ai_headers(settings: Settings) -> dict[str, str]:
    """OCI OpenAI-compatible API の project/API key ヘッダーを付与する。"""
    headers = dict(JSON_HEADERS)
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
    candidate = _unwrap_response_payload(candidate)
    if isinstance(candidate, str):
        candidate = _json_string_to_object(candidate)
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
    candidate = _unwrap_response_payload(candidate)
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
