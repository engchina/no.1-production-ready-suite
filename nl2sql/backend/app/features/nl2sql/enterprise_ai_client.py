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
from pr_system_settings.enterprise_ai_errors import (
    ENTERPRISE_AI_RETRYABLE_STATUS_CODES,
    EnterpriseAiCallFailureKind,
    EnterpriseAiHttpFailure,
    classify_enterprise_ai_http_failure,
    enterprise_ai_endpoint_host,
    enterprise_ai_failure_actions,
    enterprise_ai_provider_message,
)

from app.settings import (
    EnterpriseAiConnection,
    Settings,
    enterprise_ai_connection_for_model,
    enterprise_ai_default_model_id,
    enterprise_ai_reasoning,
    enterprise_ai_vision_model_id,
    seed_payload,
)

from .structured_outputs import validate_json_output

# 推論(思考)の部分の type(#1423)。Responses の `output` の item(`type=reasoning`)と、content の
# block(`reasoning_text` / `summary_text`、Anthropic 形式の `thinking` / `redacted_thinking`)。
# 本文・JSON の解析には使わず捨てる(思考は利用者に見せず、log にも出さない)。
REASONING_PART_TYPES = frozenset(
    {"reasoning", "reasoning_text", "summary_text", "thinking", "redacted_thinking"}
)
# 本文の先頭に思考をタグで入れるモデル(`<think>…</think>` / `<thinking>…</thinking>`)の思考の部分。
LEADING_THINKING_TAG_RE = re.compile(r"^\s*<(think|thinking)>.*?</\1>\s*", re.I | re.S)


class EnterpriseAiDirectError(RuntimeError):
    """Enterprise AI direct 呼び出しの実行時エラー。

    HTTP の失敗は `http_failure`（platform の共通の対応表の分類。#1684）と、呼んだモデル・接続の
    ホストを持つ。ジョブの失敗の案内は、これで設定の誤り（時間をおいても直らない）を見分ける。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
        http_failure: EnterpriseAiHttpFailure | None = None,
        model_id: str = "",
        endpoint_host: str = "",
        provider_message: str = "",
        upstream_request_id: str = "",
        attempts: int = 0,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_failure = http_failure
        self.model_id = model_id
        self.endpoint_host = endpoint_host
        # 画面の「詳細」に出す値（#1715）。provider_message は応答の本文の理由（秘密の値を
        # 伏せた短い文）、upstream_request_id は OCI の `opc-request-id`（無ければ応答の `id`）。
        self.provider_message = provider_message
        self.upstream_request_id = upstream_request_id
        self.attempts = attempts
        self.timeout_seconds = timeout_seconds
        self.elapsed_ms: int | None = None
        self.max_output_tokens: int | None = None
        self.incomplete_reason = ""

    @property
    def failure_kind(self) -> EnterpriseAiCallFailureKind | None:
        """失敗の種類（platform の共通の表。#1715）。分からなければ None。"""
        if self.http_failure is not None:
            return self.http_failure.kind
        return _CALL_FAILURE_KINDS_BY_CODE.get(self.code)

    @property
    def configuration_error(self) -> bool:
        """設定（モデル・接続・API key・IAM policy）の誤りによる失敗か。"""
        return self.http_failure is not None and self.http_failure.configuration_error

    def failure_actions(self) -> list[str]:
        """確かめる所の案内（HTTP の失敗でなければ空）。"""
        if self.http_failure is None:
            return []
        return enterprise_ai_failure_actions(
            self.http_failure, model_id=self.model_id, endpoint_host=self.endpoint_host
        )


# HTTP 以外の失敗の code → 共通の種類（#1715）。
_CALL_FAILURE_KINDS_BY_CODE: dict[str, EnterpriseAiCallFailureKind] = {
    "timeout": "timeout",
    "connection": "connection",
    "incomplete": "incomplete",
    "response_format": "response_format",
    "refusal": "refusal",
}


# アプリが直接呼ぶ OCI Enterprise AI のモデルの決め方（#1684）。失敗の案内に添える。
ENTERPRISE_AI_DIRECT_MODEL_NOTE = (
    "NL2SQL がアプリから呼ぶ OCI Enterprise AI（生成方法の Enterprise AI Direct・"
    "AI要件確認など）は、システム設定 › モデル の既定のテキストモデルと、"
    "そのモデルを登録した接続を使います。業務プロファイルの Region・LLM Model は "
    "Select AI・Select AI Agent 用で、この呼び出しには使いません。"
)


def find_enterprise_ai_error(exc: BaseException | None) -> EnterpriseAiDirectError | None:
    """例外（原因の連鎖を含む）から、HTTP の失敗を持つ Enterprise AI の失敗を探す。"""
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, EnterpriseAiDirectError) and current.http_failure is not None:
            return current
        current = current.__cause__ or current.__context__
    return None


def enterprise_ai_configuration_failure_message(
    error: EnterpriseAiDirectError, subject: str
) -> str:
    """設定の誤りによる失敗の、利用者向けの文（時間をおいても直らないことと、確かめる所）。"""
    return " ".join(
        [
            f"{subject}OCI Enterprise AI の呼び出しが設定の誤りで失敗しました。"
            "時間をおいても直りません。管理者に次の確認を依頼してください。",
            *error.failure_actions(),
            ENTERPRISE_AI_DIRECT_MODEL_NOTE,
        ]
    )


class EnterpriseAiDirectClient(Protocol):
    """Service が必要とする Enterprise AI direct 境界。"""

    def is_configured(self) -> bool:
        """Return whether the endpoint, API key, and model are configured."""
        ...

    def model_id(self) -> str:
        """Return the configured text model id."""
        ...

    def generate(
        self,
        *,
        prompt: str,
        context: str,
        system_prompt: str,
        timeout_seconds: float | None = None,
        max_output_tokens: int | None = None,
        max_retries: int | None = None,
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        """Return raw generated text from Enterprise AI."""
        ...


class OciEnterpriseAiDirectClient:
    """Small synchronous HTTP client for OCI Enterprise AI LLM endpoint.

    接続（endpoint / API key / project）は、呼ぶモデルの接続を使う（#533）。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_configured(self) -> bool:
        model_id = self.model_id()
        return bool(model_id and self.connection(model_id).is_configured())

    def connection(self, model_id: str) -> EnterpriseAiConnection:
        """モデルを呼ぶときの接続。"""
        return enterprise_ai_connection_for_model(self.settings, model_id)

    def model_id(self) -> str:
        """画像を扱わない呼び出しのモデル（既定のテキストモデル、なければ Vision）。"""
        return enterprise_ai_default_model_id(self.settings)

    def generate(
        self,
        *,
        prompt: str,
        context: str,
        system_prompt: str,
        timeout_seconds: float | None = None,
        max_output_tokens: int | None = None,
        max_retries: int | None = None,
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        if not self.is_configured():
            raise EnterpriseAiDirectError("OCI Enterprise AI Direct が未設定です。")
        payload = _build_payload(
            settings=self.settings,
            model_id=self.model_id(),
            prompt=prompt,
            context=context,
            system_prompt=system_prompt,
            max_output_tokens=max_output_tokens,
            response_format=response_format,
        )
        # 既定のテキストモデル（システム設定の「モデル」）とその接続で呼ぶ。業務プロファイルの
        # LLM Model・Region は Select AI の AI profile 用で、ここでは使わない（#1684）。
        model_id = self.model_id()
        started = time.perf_counter()
        try:
            response = self._post_json(
                payload,
                connection=self.connection(model_id),
                path=self.settings.oci_enterprise_ai_llm_path,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                model_id=model_id,
            )
            return _parse_response(
                response,
                response_path=self.settings.oci_enterprise_ai_llm_response_path,
                response_format=response_format,
            )
        except EnterpriseAiDirectError as exc:
            # 画面の「詳細」に出す値を足す（#1715）。
            exc.model_id = exc.model_id or model_id
            exc.elapsed_ms = int((time.perf_counter() - started) * 1000)
            raw_max_tokens = payload.get("max_output_tokens")
            if isinstance(raw_max_tokens, int):
                exc.max_output_tokens = raw_max_tokens
            raise

    def vision_model_id(self) -> str:
        return enterprise_ai_vision_model_id(self.settings) or self.model_id()

    def generate_from_image(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "image/jpeg",
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        model_id = self.vision_model_id()
        if not model_id:
            raise EnterpriseAiDirectError("OCI Enterprise AI の既定の画像対応モデルが未設定です。")
        connection = self.connection(model_id)
        if not connection.is_configured():
            raise EnterpriseAiDirectError("OCI Enterprise AI Direct が未設定です。")
        payload = _build_image_payload(
            settings=self.settings,
            model_id=model_id,
            image_bytes=image_bytes,
            prompt=prompt,
            mime_type=mime_type,
            response_format=response_format,
        )
        response = self._post_json(
            payload,
            connection=connection,
            path=getattr(self.settings, "oci_enterprise_ai_vlm_path", "")
            or self.settings.oci_enterprise_ai_llm_path,
            model_id=model_id,
        )
        return _parse_response(
            response,
            response_path=self.settings.oci_enterprise_ai_vlm_response_path,
            response_format=response_format,
        )

    def _post_json(
        self,
        payload: Mapping[str, Any],
        *,
        connection: EnterpriseAiConnection,
        path: str,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        model_id: str = "",
    ) -> Mapping[str, Any]:
        # Endpoint URL は入力どおり使い、API path を付ける(#1440。3 製品で platform の関数)。
        url = connection.request_url(path)
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {connection.api_key}",
        }
        if project := connection.project_ocid:
            headers["OpenAI-Project"] = project
        timeout = float(
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.oci_enterprise_ai_timeout_seconds
        )
        retry_count = max(
            int(
                max_retries
                if max_retries is not None
                else self.settings.oci_enterprise_ai_max_retries
            ),
            0,
        )
        retryable = ENTERPRISE_AI_RETRYABLE_STATUS_CODES
        last_error = ""
        with httpx.Client(timeout=timeout) as client:
            for attempt in range(retry_count + 1):
                try:
                    response = client.post(url, headers=headers, json=dict(payload))
                except httpx.TimeoutException as exc:
                    last_error = f"timeout after {timeout:.1f}s"
                    if attempt < retry_count:
                        time.sleep(min(0.2 * (attempt + 1), 1.0))
                        continue
                    raise EnterpriseAiDirectError(
                        last_error,
                        code="timeout",
                        retryable=True,
                        model_id=model_id,
                        endpoint_host=enterprise_ai_endpoint_host(connection.endpoint),
                        attempts=attempt + 1,
                        timeout_seconds=timeout,
                    ) from exc
                except httpx.HTTPError as exc:
                    last_error = str(exc)
                    if attempt < retry_count:
                        time.sleep(min(0.2 * (attempt + 1), 1.0))
                        continue
                    raise EnterpriseAiDirectError(
                        f"OCI Enterprise AI HTTP error: {exc}",
                        code="connection",
                        retryable=True,
                        model_id=model_id,
                        endpoint_host=enterprise_ai_endpoint_host(connection.endpoint),
                        provider_message=type(exc).__name__,
                        attempts=attempt + 1,
                        timeout_seconds=timeout,
                    ) from exc
                if response.status_code in retryable and attempt < retry_count:
                    last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                    time.sleep(min(0.2 * (attempt + 1), 1.0))
                    continue
                if response.status_code >= 400:
                    # 分類は 3 製品で共通の対応表（#1684）。OCI の 404 は認可の失敗で、
                    # モデルが無いときは 400 になる。設定の誤りは再試行しない。
                    body = response.text[:500]
                    failure = classify_enterprise_ai_http_failure(response.status_code, body)
                    raise EnterpriseAiDirectError(
                        f"OCI Enterprise AI HTTP {response.status_code}: {body}",
                        code=failure.kind,
                        retryable=failure.retryable,
                        http_failure=failure,
                        model_id=model_id or str(payload.get("model") or ""),
                        endpoint_host=enterprise_ai_endpoint_host(connection.endpoint),
                        provider_message=enterprise_ai_provider_message(body),
                        upstream_request_id=_upstream_request_id(response),
                        attempts=attempt + 1,
                        timeout_seconds=timeout,
                    )
                try:
                    parsed = response.json()
                except ValueError as exc:
                    raise EnterpriseAiDirectError(
                        "OCI Enterprise AI response が JSON ではありません。",
                        code="response_format",
                        retryable=True,
                        upstream_request_id=_upstream_request_id(response),
                        attempts=attempt + 1,
                    ) from exc
                if not isinstance(parsed, Mapping):
                    raise EnterpriseAiDirectError(
                        "OCI Enterprise AI response が object ではありません。",
                        code="response_format",
                        retryable=True,
                        upstream_request_id=_upstream_request_id(response),
                        attempts=attempt + 1,
                    )
                # 応答の検査の失敗（未完了・形式の誤り）の「詳細」に request id を出す（#1715）。
                return _ResponseBody(parsed, upstream_request_id=_upstream_request_id(response))
        raise EnterpriseAiDirectError(last_error or "OCI Enterprise AI call failed.")


class _ResponseBody(dict[str, Any]):
    """応答の本文（JSON object）と、HTTP の header の OCI の request id。"""

    def __init__(self, body: Mapping[str, Any], *, upstream_request_id: str = "") -> None:
        super().__init__(body)
        self.upstream_request_id = upstream_request_id


def _upstream_request_id(response: httpx.Response) -> str:
    """OCI の request id（`opc-request-id`。無ければ一般の `x-request-id`）。"""
    for name in ("opc-request-id", "x-request-id", "request-id"):
        value = response.headers.get(name)
        if value:
            return str(value)[:200]
    return ""


def _build_payload(
    *,
    settings: Settings,
    model_id: str,
    prompt: str,
    context: str,
    system_prompt: str,
    max_output_tokens: int | None = None,
    response_format: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    if response_format is not None:
        system_prompt += (
            "\n出力形式は text.format の JSON Schema を最優先する。"
            "指定された JSON object だけを返す。"
            "SQL・Markdown・自然言語は対応する文字列 field 内に格納する。"
        )
    values: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "context": context,
        "system_prompt": system_prompt,
        "instructions": system_prompt,
        "user_message": f"{context}\n\n質問:\n{prompt}",
        "max_output_tokens": int(
            max_output_tokens
            if max_output_tokens is not None
            else settings.oci_enterprise_ai_llm_max_output_tokens
        ),
        "temperature": 0,
        "response_format": dict(response_format) if response_format is not None else "",
    }
    if settings.oci_enterprise_ai_llm_payload_template.strip():
        return _with_output_format(
            _render_payload_template(
                settings.oci_enterprise_ai_llm_payload_template,
                values,
            ),
            response_format,
        )
    payload: dict[str, Any] = {
        "model": model_id,
        "instructions": system_prompt,
        "input": [{"role": "user", "content": values["user_message"]}],
        "temperature": values["temperature"],
        "max_output_tokens": values["max_output_tokens"],
    }
    return _with_output_format(_with_reasoning(payload, settings, model_id), response_format)


def _build_image_payload(
    *,
    settings: Settings,
    model_id: str,
    image_bytes: bytes,
    prompt: str,
    mime_type: str,
    response_format: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    if response_format is not None:
        prompt += (
            "\n出力形式は text.format の JSON Schema を最優先する。"
            "抽出した全文を text に格納した JSON object だけを返す。"
        )
    image_base64 = b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{image_base64}"
    values: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "response_format": dict(response_format) if response_format is not None else "",
        "image_base64": image_base64,
        "image_data_url": data_url,
        "mime_type": mime_type,
        "max_output_tokens": int(
            getattr(settings, "oci_enterprise_ai_vlm_max_output_tokens", 65536)
        ),
        "temperature": 0,
    }
    if settings.oci_enterprise_ai_vlm_payload_template.strip():
        return _with_output_format(
            _render_payload_template(settings.oci_enterprise_ai_vlm_payload_template, values),
            response_format,
        )
    return _with_output_format(
        _with_reasoning(
            {
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
            },
            settings,
            model_id,
        ),
        response_format,
    )


def _with_reasoning(payload: dict[str, Any], settings: Settings, model_id: str) -> dict[str, Any]:
    """モデルの推論（思考）の量と seed を入れる。

    - 推論の量: Responses API の `reasoning`（#1425。none は送らない）
    - seed: top-level の `seed`（#1434。全モデルで共通、既定 42。Responses API に seed の field が
      無いため、OpenAI SDK の `extra_body` と同じ形）

    標準の payload だけに入れる（payload template の構成は template に任せる）。
    """
    reasoning = enterprise_ai_reasoning(settings, model_id)
    if reasoning is not None:
        payload["reasoning"] = reasoning
    payload.update(seed_payload(settings))
    return payload


def _with_output_format(
    payload: Mapping[str, Any], response_format: Mapping[str, Any] | None
) -> Mapping[str, Any]:
    if response_format is None:
        return payload
    result = dict(payload)
    text = dict(result.get("text") or {})
    text["format"] = dict(response_format)
    result["text"] = text
    return result


def _render_payload_template(template: str, values: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        parsed = json.loads(template)
    except ValueError as exc:
        raise EnterpriseAiDirectError(
            "PLATFORM_OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE は JSON object で指定してください。"
        ) from exc
    rendered = _render_template_value(parsed, values)
    if not isinstance(rendered, Mapping):
        raise EnterpriseAiDirectError(
            "PLATFORM_OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE は JSON object を返す必要があります。"
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


def _parse_response(
    response: Mapping[str, Any], *, response_path: str, response_format: Mapping[str, Any] | None
) -> str:
    if response_format is None:
        return _parse_generated_text(response, response_path=response_path)
    _raise_for_response_error(response)
    request_id = str(getattr(response, "upstream_request_id", "") or response.get("id") or "")[:200]
    status = response.get("status")
    if status in {"incomplete", "failed", "cancelled", "queued", "in_progress"}:
        details = response.get("incomplete_details")
        reason = str(details.get("reason") or "") if isinstance(details, Mapping) else ""
        error = EnterpriseAiDirectError(
            "Structured Outputs の応答が未完了です。出力上限とモデル設定を確認してください。",
            code="incomplete",
            provider_message=f"status={status}" + (f", reason={reason}" if reason else ""),
            upstream_request_id=request_id,
        )
        error.incomplete_reason = reason
        raise error

    def check_refusal(value: object) -> None:
        if isinstance(value, Mapping):
            if value.get("type") == "refusal" or value.get("refusal"):
                raise EnterpriseAiDirectError(
                    "Enterprise AI が応答を拒否しました。入力内容を確認してください。",
                    code="refusal",
                )
            for key in ("output", "content", "choices", "message"):
                check_refusal(value.get(key))
        elif isinstance(value, list):
            for item in value:
                check_refusal(item)

    check_refusal(response)
    candidate = _select_response_path(response, response_path)

    def text(value: object) -> str:
        if isinstance(value, str):
            return _strip_leading_thinking(value)
        if isinstance(value, Mapping):
            if _is_reasoning_part(value) or value.get("type") in {"function_call", "tool_call"}:
                return ""
            for key in ("output_text", "text", "content", "output", "choices", "message"):
                if key in value:
                    found = text(value[key])
                    if found:
                        return found
        if isinstance(value, list):
            return "".join(text(item) for item in value)
        return ""

    try:
        return validate_json_output(text(candidate), response_format)
    except ValueError as exc:
        raise EnterpriseAiDirectError(
            str(exc),
            code="response_format",
            retryable=True,
            provider_message=str(exc)[:300],
            upstream_request_id=request_id,
        ) from exc


def _parse_generated_text(response: Mapping[str, Any], *, response_path: str) -> str:
    candidate = _select_response_path(response, response_path)
    _raise_for_response_error(candidate)
    text = _extract_text_candidate(candidate)
    if text.strip():
        return text.strip()
    raise EnterpriseAiDirectError(
        "OCI Enterprise AI response に text がありません。",
        code="response_format",
        retryable=True,
    )


def _select_response_path(payload: object, path: str) -> object:
    cleaned = path.strip()
    if not cleaned:
        return payload
    if not cleaned.startswith("/"):
        raise EnterpriseAiDirectError(
            "PLATFORM_OCI_ENTERPRISE_AI_LLM_RESPONSE_PATH は / で始まる JSON Pointer 形式です。"
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


def _is_reasoning_part(candidate: object) -> bool:
    """Responses の reasoning item や思考の content block か(本文に使わない。#1423)。"""
    if not isinstance(candidate, Mapping):
        return False
    return str(candidate.get("type", "")).strip().lower() in REASONING_PART_TYPES


def _strip_leading_thinking(text: str) -> str:
    """本文の先頭の `<think>…</think>` / `<thinking>…</thinking>` を除く(#1423)。"""
    return LEADING_THINKING_TAG_RE.sub("", text, count=1)


def _extract_text_candidate(candidate: object) -> str:
    """応答の候補から本文を取り出す。

    思考の部分(Responses の `type=reasoning` の item、`reasoning_text` / `thinking` などの
    content block、Chat Completions の `reasoning_content`、本文の先頭の `<think>` タグ)は
    使わない(#1423)。
    """
    if _is_reasoning_part(candidate):
        return ""
    if isinstance(candidate, str):
        text = _strip_leading_thinking(candidate)
        return _extract_text_from_json_string(text) or text
    if isinstance(candidate, Mapping):
        tool_payload = _extract_tool_call_payload(candidate)
        if tool_payload is not None:
            return _extract_text_candidate(tool_payload)
        if message := candidate.get("message"):
            return _extract_text_candidate(message)
        for key in ("content", "text", "answer", "output_text", "generated_text"):
            value = candidate.get(key)
            if isinstance(value, str):
                return _strip_leading_thinking(value)
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
