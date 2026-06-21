"""OCI Enterprise AI direct NL2SQL client.

OCI Generative AI chat API は使わず、Enterprise AI の OpenAI-compatible
HTTP endpoint contract だけを扱う。未設定の local/CI では service 側が
deterministic fallback を使う。
"""

from __future__ import annotations

import json
import re
import time
from base64 import b64encode
from collections.abc import Mapping
from typing import Any, Protocol, cast

import httpx

from app.settings import Settings, enterprise_ai_default_model_id, enterprise_ai_vision_model_id


class EnterpriseAiDirectError(RuntimeError):
    """Enterprise AI direct 呼び出しの実行時エラー。"""


class EnterpriseAiDirectClient(Protocol):
    """Service が必要とする Enterprise AI direct 境界。"""

    def is_configured(self) -> bool:
        """Return whether the endpoint, API key, and model are configured."""
        ...

    def model_id(self) -> str:
        """Return the configured text model id."""
        ...

    def generate(self, *, prompt: str, context: str, system_prompt: str) -> str:
        """Return raw generated text from Enterprise AI."""
        ...


class OciEnterpriseAiDirectClient:
    """Small synchronous HTTP client for OCI Enterprise AI LLM endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_configured(self) -> bool:
        return bool(
            self.settings.oci_enterprise_ai_endpoint.strip()
            and self.settings.oci_enterprise_ai_api_key.strip()
            and self.model_id()
        )

    def model_id(self) -> str:
        return (
            enterprise_ai_default_model_id(self.settings)
            or self.settings.oci_enterprise_ai_default_model.strip()
            or self.settings.oci_enterprise_ai_llm_model.strip()
        )

    def generate(self, *, prompt: str, context: str, system_prompt: str) -> str:
        if not self.is_configured():
            raise EnterpriseAiDirectError("OCI Enterprise AI Direct が未設定です。")
        payload = _build_payload(
            settings=self.settings,
            model_id=self.model_id(),
            prompt=prompt,
            context=context,
            system_prompt=system_prompt,
        )
        response = self._post_json(payload, path=self.settings.oci_enterprise_ai_llm_path)
        return _parse_generated_text(
            response,
            response_path=self.settings.oci_enterprise_ai_llm_response_path,
        )

    def vision_model_id(self) -> str:
        return enterprise_ai_vision_model_id(self.settings) or self.model_id()

    def generate_from_image(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "image/jpeg",
    ) -> str:
        if not self.is_configured():
            raise EnterpriseAiDirectError("OCI Enterprise AI Direct が未設定です。")
        model_id = self.vision_model_id()
        if not model_id:
            raise EnterpriseAiDirectError("OCI Enterprise AI Vision model が未設定です。")
        payload = _build_image_payload(
            settings=self.settings,
            model_id=model_id,
            image_bytes=image_bytes,
            prompt=prompt,
            mime_type=mime_type,
        )
        response = self._post_json(
            payload,
            path=getattr(self.settings, "oci_enterprise_ai_vlm_path", "")
            or self.settings.oci_enterprise_ai_llm_path,
        )
        return _parse_generated_text(
            response,
            response_path=self.settings.oci_enterprise_ai_vlm_response_path,
        )

    def _post_json(self, payload: Mapping[str, Any], *, path: str) -> Mapping[str, Any]:
        url = _join_endpoint_path(
            self.settings.oci_enterprise_ai_endpoint,
            path,
        )
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {self.settings.oci_enterprise_ai_api_key.strip()}",
        }
        if project := self.settings.oci_enterprise_ai_project_ocid.strip():
            headers["OpenAI-Project"] = project
        timeout = float(self.settings.oci_enterprise_ai_timeout_seconds)
        max_retries = max(int(self.settings.oci_enterprise_ai_max_retries), 0)
        retryable = {429, 500, 502, 503, 504}
        last_error = ""
        with httpx.Client(timeout=timeout) as client:
            for attempt in range(max_retries + 1):
                try:
                    response = client.post(url, headers=headers, json=dict(payload))
                except httpx.TimeoutException as exc:
                    last_error = f"timeout after {timeout:.1f}s"
                    if attempt < max_retries:
                        time.sleep(min(0.2 * (attempt + 1), 1.0))
                        continue
                    raise EnterpriseAiDirectError(last_error) from exc
                except httpx.HTTPError as exc:
                    last_error = str(exc)
                    if attempt < max_retries:
                        time.sleep(min(0.2 * (attempt + 1), 1.0))
                        continue
                    raise EnterpriseAiDirectError(f"OCI Enterprise AI HTTP error: {exc}") from exc
                if response.status_code in retryable and attempt < max_retries:
                    last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                    time.sleep(min(0.2 * (attempt + 1), 1.0))
                    continue
                if response.status_code >= 400:
                    raise EnterpriseAiDirectError(
                        f"OCI Enterprise AI HTTP {response.status_code}: {response.text[:500]}"
                    )
                try:
                    parsed = response.json()
                except ValueError as exc:
                    raise EnterpriseAiDirectError(
                        "OCI Enterprise AI response が JSON ではありません。"
                    ) from exc
                if not isinstance(parsed, Mapping):
                    raise EnterpriseAiDirectError(
                        "OCI Enterprise AI response が object ではありません。"
                    )
                return parsed
        raise EnterpriseAiDirectError(last_error or "OCI Enterprise AI call failed.")


def _build_payload(
    *,
    settings: Settings,
    model_id: str,
    prompt: str,
    context: str,
    system_prompt: str,
) -> Mapping[str, Any]:
    values: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "context": context,
        "system_prompt": system_prompt,
        "instructions": system_prompt,
        "user_message": f"{context}\n\n質問:\n{prompt}",
        "max_output_tokens": int(settings.oci_enterprise_ai_llm_max_output_tokens),
        "temperature": 0,
    }
    if settings.oci_enterprise_ai_llm_payload_template.strip():
        return _render_payload_template(
            settings.oci_enterprise_ai_llm_payload_template,
            values,
        )
    return {
        "model": model_id,
        "instructions": system_prompt,
        "input": [{"role": "user", "content": values["user_message"]}],
        "temperature": values["temperature"],
        "max_output_tokens": values["max_output_tokens"],
    }


def _build_image_payload(
    *,
    settings: Settings,
    model_id: str,
    image_bytes: bytes,
    prompt: str,
    mime_type: str,
) -> Mapping[str, Any]:
    image_base64 = b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{image_base64}"
    values: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "image_base64": image_base64,
        "image_data_url": data_url,
        "mime_type": mime_type,
        "max_output_tokens": int(
            getattr(settings, "oci_enterprise_ai_vlm_max_output_tokens", 65536)
        ),
        "temperature": 0,
    }
    if settings.oci_enterprise_ai_vlm_payload_template.strip():
        return _render_payload_template(settings.oci_enterprise_ai_vlm_payload_template, values)
    return {
        "model": model_id,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
        "temperature": values["temperature"],
        "max_output_tokens": values["max_output_tokens"],
    }


def _render_payload_template(template: str, values: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        parsed = json.loads(template)
    except ValueError as exc:
        raise EnterpriseAiDirectError(
            "OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE は JSON object で指定してください。"
        ) from exc
    rendered = _render_template_value(parsed, values)
    if not isinstance(rendered, Mapping):
        raise EnterpriseAiDirectError(
            "OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE は JSON object を返す必要があります。"
        )
    return rendered


def _render_template_value(value: object, values: Mapping[str, Any]) -> object:
    if isinstance(value, str):
        for key, replacement in values.items():
            token = "${" + key + "}"
            if value == token:
                return replacement
            value = value.replace(token, str(replacement))
        return value
    if isinstance(value, list):
        return [_render_template_value(item, values) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _render_template_value(item, values) for key, item in value.items()}
    return value


def _parse_generated_text(response: Mapping[str, Any], *, response_path: str) -> str:
    candidate = _select_response_path(response, response_path)
    _raise_for_response_error(candidate)
    text = _extract_text_candidate(candidate)
    if text.strip():
        return text.strip()
    raise EnterpriseAiDirectError("OCI Enterprise AI response に text がありません。")


def _select_response_path(payload: object, path: str) -> object:
    cleaned = path.strip()
    if not cleaned:
        return payload
    if not cleaned.startswith("/"):
        raise EnterpriseAiDirectError(
            "OCI_ENTERPRISE_AI_LLM_RESPONSE_PATH は / で始まる JSON Pointer 形式です。"
        )
    current = payload
    for raw_segment in cleaned.split("/")[1:]:
        segment = raw_segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if segment not in current:
                raise EnterpriseAiDirectError(f"response path の key が見つかりません: {segment}")
            current = current[segment]
            continue
        if isinstance(current, list):
            if not segment.isdigit():
                raise EnterpriseAiDirectError(f"response path の list index が不正です: {segment}")
            index = int(segment)
            if index >= len(current):
                raise EnterpriseAiDirectError(
                    f"response path の list index が範囲外です: {segment}"
                )
            current = current[index]
            continue
        raise EnterpriseAiDirectError("response path が object/list 以外に到達しました。")
    return current


def _raise_for_response_error(candidate: object) -> None:
    if not isinstance(candidate, Mapping):
        return
    error = candidate.get("error")
    if not error:
        return
    if isinstance(error, Mapping):
        message = str(error.get("message") or error.get("code") or error)
    else:
        message = str(error)
    raise EnterpriseAiDirectError(f"OCI Enterprise AI response error: {message}")


def _extract_text_candidate(candidate: object) -> str:
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
                return "\n".join(
                    part for part in (_extract_text_candidate(item) for item in value) if part
                )
        for key in ("output", "outputs", "data", "response", "result", "choices"):
            value = candidate.get(key)
            if isinstance(value, (Mapping, list)):
                text = _extract_text_candidate(value)
                if text:
                    return text
    if isinstance(candidate, list):
        return "\n".join(
            part for part in (_extract_text_candidate(item) for item in candidate) if part
        )
    return ""


def _extract_text_from_json_string(value: str) -> str:
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


def _extract_tool_call_payload(candidate: Mapping[str, Any]) -> object | None:
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


def _extract_function_arguments(function: Mapping[str, Any]) -> object | None:
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            return cast(object, json.loads(arguments))
        except ValueError:
            return arguments
    return cast(object | None, arguments)


def _strip_json_fence(value: str) -> str:
    match = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", value, flags=re.I | re.S)
    return match.group(1).strip() if match else value.strip()


def _extract_json_object_text(value: str) -> str | None:
    start = value.find("{")
    end = value.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return value[start : end + 1]


def _join_endpoint_path(endpoint: str, path: str) -> str:
    return f"{endpoint.rstrip('/')}/{(path or '/responses').lstrip('/')}"
