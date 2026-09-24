"""openai SDK(OCI OpenAI 互換 Responses API)で構造化出力を得る同期ヘルパー。

rag_poc(DocRAG)由来の Vision 説明・根拠付き生成・監査・CRAG 評価などが共通で使う。
接続先・認証は OciEnterpriseAiConfig(backend 設定 / parser サービス env)から解決する。
全呼び出しで temperature=0 / seed=42 を渡し、出力上限による未完了だけ予算を 2 倍にして
1 回送り直す。スキーマ不一致は 1 回だけ再送する。
"""

from __future__ import annotations

import base64
import mimetypes
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from rag_parser_core.oci_enterprise_ai import OciEnterpriseAiConfig

LLM_TEMPERATURE = 0.0
LLM_SEED = 42
INCOMPLETE_OUTPUT_REASON = "max_output_tokens"
INCOMPLETE_OUTPUT_BUDGET_FACTOR = 2
INVALID_OUTPUT_RETRIES = 1

StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)
ImageInput = Path | bytes


class IncompleteResponseError(RuntimeError):
    """Responses API が未完了(incomplete)で返ったことを表す。"""

    def __init__(self, reason: str) -> None:
        super().__init__(f"LLM response is incomplete: {reason or 'unknown'}")
        self.reason = reason


class InvalidStructuredOutputError(RuntimeError):
    """構造化出力が得られない・スキーマに合わないことを表す。"""


def openai_client(config: OciEnterpriseAiConfig) -> openai.OpenAI:
    """設定から同期 openai client を作る(URL / API key / project)。"""
    endpoint = config.oci_enterprise_ai_endpoint.strip()
    api_key = config.oci_enterprise_ai_api_key.strip()
    if not endpoint or not api_key:
        raise ValueError("OCI Enterprise AI の endpoint / API key が未設定です。")
    return openai.OpenAI(
        api_key=api_key,
        base_url=endpoint,
        project=config.oci_enterprise_ai_project_ocid.strip() or None,
        max_retries=config.oci_enterprise_ai_max_retries,
        timeout=config.oci_enterprise_ai_timeout_seconds,
    )


def image_data_url(image: ImageInput, mime_type: str | None = None) -> str:
    """画像 path / bytes を Responses API の data URL にする。"""
    if isinstance(image, Path):
        data = image.read_bytes()
        mime_type = mime_type or mimetypes.guess_type(image.name)[0]
    else:
        data = image
    return f"data:{mime_type or 'image/png'};base64,{base64.b64encode(data).decode('ascii')}"


def parse_structured(
    config: OciEnterpriseAiConfig,
    *,
    system_prompt: str,
    prompt: str,
    text_format: type[StructuredOutputT],
    model: str | None = None,
    images: Sequence[ImageInput] = (),
    max_output_tokens: int = 4096,
    client: openai.OpenAI | None = None,
) -> StructuredOutputT:
    """system / user(+画像)を送り、text_format で検証済みの出力を返す。"""
    model_id = (model or config.default_model_id).strip()
    if not model_id:
        raise ValueError("LLM model ID が未設定です。")
    content: Any = str(prompt or "")
    if images:
        content = [{"type": "input_text", "text": str(prompt or "")}]
        content.extend({"type": "input_image", "image_url": image_data_url(i)} for i in images)
    messages = [
        {"role": "system", "content": str(system_prompt or "")},
        {"role": "user", "content": content},
    ]
    sdk = client or openai_client(config)
    for attempt in range(INVALID_OUTPUT_RETRIES + 1):
        try:
            return _parsed_with_budget(sdk, model_id, messages, text_format, max_output_tokens)
        except (InvalidStructuredOutputError, ValidationError):
            if attempt >= INVALID_OUTPUT_RETRIES:
                raise
    raise AssertionError("unreachable")


def _parsed_with_budget(
    sdk: openai.OpenAI,
    model_id: str,
    messages: list[dict[str, Any]],
    text_format: type[StructuredOutputT],
    max_output_tokens: int,
) -> StructuredOutputT:
    budget = max(1, int(max_output_tokens))
    for attempt in range(2):
        response = sdk.responses.parse(
            model=model_id,
            input=messages,  # type: ignore[arg-type]
            text_format=text_format,
            max_output_tokens=budget,
            temperature=LLM_TEMPERATURE,
            extra_body={"seed": LLM_SEED},
        )
        try:
            return _parsed_output(response, text_format)
        except IncompleteResponseError as exc:
            if attempt or exc.reason != INCOMPLETE_OUTPUT_REASON:
                raise
            budget *= INCOMPLETE_OUTPUT_BUDGET_FACTOR
    raise AssertionError("unreachable")


def _parsed_output(response: Any, text_format: type[StructuredOutputT]) -> StructuredOutputT:
    if getattr(response, "status", None) == "incomplete":
        details = getattr(response, "incomplete_details", None)
        raise IncompleteResponseError(str(getattr(details, "reason", "") or ""))
    parsed = getattr(response, "output_parsed", None)
    if isinstance(parsed, text_format):
        return parsed
    if isinstance(parsed, BaseModel):
        return text_format.model_validate(parsed.model_dump())
    text = str(getattr(response, "output_text", "") or "").strip()
    if not text:
        raise InvalidStructuredOutputError("LLM が構造化出力を返しませんでした。")
    return text_format.model_validate_json(text)
