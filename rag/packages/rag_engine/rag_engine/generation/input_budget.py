"""描画済み根拠の文字上限と、system・schema・出力予約を含む入力予算。

添付する画像は本文の見積もりに入れず、枚数 × 呼ぶモデルの「画像 1 枚の入力 token」（``LlmProviderSettings.image_input_tokens``。
backend が共通のモデル設定の登録モデルの値で渡す）で数える（#5316）。送信前の測定（``model_input_measurement``）と、
adapter の送信の直前の判定（``request_input_estimate``）は同じ規則で数える。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pr_backend_core.output_budget import online_output_budget
from pr_backend_core.token_estimate import DEFAULT_IMAGE_INPUT_TOKENS, without_image_inputs

from rag_engine.config import LlmProviderSettings, Settings, get_settings
from rag_engine.generation.section_expansion import estimate_tokens


class GenerationInputBudgetExceeded(RuntimeError):
    """固定の指示または必須の根拠が入力予算に入らず、生成を送信しない。"""

    def __init__(self, diagnostic: dict[str, Any]) -> None:
        super().__init__("generation_input_budget_exceeded")
        self.diagnostic = diagnostic


def image_input_tokens(provider: LlmProviderSettings | None) -> int:
    """呼ぶモデルの画像 1 枚の入力 token（登録モデルの値。provider が無ければ platform の既定）。"""
    value = getattr(provider, "image_input_tokens", None) if provider is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else DEFAULT_IMAGE_INPUT_TOKENS


def image_diagnostic(provider: LlmProviderSettings | None, image_count: int) -> dict[str, Any]:
    """画像の分の見積もり（画像が無ければ空。本文だけの記録の形は変えない）。"""
    if image_count <= 0:
        return {}
    per_image = image_input_tokens(provider)
    return {"image_count": image_count, "image_input_tokens_per_image": per_image, "estimated_image_tokens": per_image * image_count}


def request_input_estimate(request: Mapping[str, Any], provider: LlmProviderSettings | None) -> tuple[int, dict[str, Any]]:
    """送信する要求（Responses API の形）の入力 token の見積もりと、画像の分の内訳。

    画像の部品（``input_image`` の data URL）は本文の見積もりから外し、枚数 × 画像 1 枚の入力 token で数える。
    """
    stripped, image_count = without_image_inputs(dict(request))
    images = image_diagnostic(provider, image_count)
    return estimate_tokens(json.dumps(stripped, ensure_ascii=False)) + images.get("estimated_image_tokens", 0), images


def model_input_measurement(
    settings: Settings | None,
    system: str,
    prompt: str,
    schema: Mapping[str, Any],
    *,
    provider_id: str | None = None,
    image_count: int = 0,
) -> dict[str, Any]:
    """1 回の生成の入力（system・prompt・schema と、添付する ``image_count`` 枚の画像）を出力の予約を除いた上限と比べる。"""
    settings = settings or get_settings(environ={}, dotenv_path=None)
    provider = settings.llm_providers.get(provider_id or settings.default_answer_llm)
    reserve = online_output_budget(
        provider.max_output_tokens if provider else settings.answer_max_tokens, configured=settings.online_max_output_tokens
    )
    limit = max(0, settings.answer_max_input_tokens - reserve)
    encoded = json.dumps(
        {
            "input": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "text": {"format": {"type": "json_schema", "strict": True, "schema": schema}},
        },
        ensure_ascii=False,
    )
    images = image_diagnostic(provider, image_count)
    tokens = estimate_tokens(encoded) + images.get("estimated_image_tokens", 0)
    return {
        "model_input_limit_tokens": settings.answer_max_input_tokens,
        "output_reserved_tokens": reserve,
        "available_input_tokens": limit,
        "estimated_input_tokens": tokens,
        "token_count_method": "estimated_ascii_quarter_non_ascii_one",
        **images,
        "fits": tokens <= limit,
    }


def ensure_model_input(settings: Settings, system: str, prompt: str, schema: Mapping[str, Any], *, provider_id: str | None = None) -> None:
    diagnostic = model_input_measurement(settings, system, prompt, schema, provider_id=provider_id)
    if not diagnostic["fits"]:
        diagnostic["reason"] = "final_model_input_exceeds_limit"
        raise GenerationInputBudgetExceeded(diagnostic)
