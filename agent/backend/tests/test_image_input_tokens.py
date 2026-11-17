"""ツールの結果の画像の入力 token を、Run のモデルの共通のモデル設定で見積もる（#5334）。

画像 1 枚の入力 token は、システム設定 > モデル の登録モデルの `image_input_tokens`（値の無い
モデル・登録に無いモデルは共通の既定 `DEFAULT_IMAGE_INPUT_TOKENS`）。Agent はこの値を Run の
モデルの解決で読み、入力の見積もり（`context_window`）に「入力に残した画像の枚数 × 1 枚の値」で
使う。画像の大きさから数える別の計算は持たない。

画像は合成のバイト列、モデルは差し替え、外部への通信はしない。業務の内容は含まない。
"""

from __future__ import annotations

import json
import struct
from typing import Any

import pytest
from pr_backend_core.token_estimate import DEFAULT_IMAGE_INPUT_TOKENS
from pr_system_settings.model import EnterpriseAiConfiguredModel

from app.features.agent import builtin_runtime, runtime
from app.features.agent.context_window import (
    ContextBudget,
    ContextLimits,
    compact_items,
    estimate_tokens,
)
from app.features.agent.runtime import AgentRuntimeRepository, RunCreateRequest
from app.features.agent.tool_media import ToolImage
from app.settings import Settings

ENDPOINT = "https://primary.example/openai/v1"


def _image(width: int, height: int, marker: bytes) -> ToolImage:
    data = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
        + marker
    )
    return ToolImage(data=data, mime_type="image/png")


def _items(*images: ToolImage) -> list[Any]:
    return [
        {"role": "user", "content": "質問"},
        {"type": "function_call", "call_id": "c1", "name": "read", "arguments": "{}"},
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": [
                {"type": "input_text", "text": json.dumps({"document_id": "doc-a"})},
                *({"type": "input_image", "image_url": image.data_url()} for image in images),
            ],
        },
    ]


def _budget(**limits: Any) -> ContextBudget:
    return ContextBudget(
        ContextLimits(model_id="m", max_input_tokens=1_000_000, max_output_tokens=1, **limits),
        margin_ratio=0.0,
    )


def test_image_tokens_use_the_image_input_tokens_of_the_run_model() -> None:
    # 大きさの違う 2 枚でも、1 枚の値は登録モデルの値（画像の大きさから数えない）。
    images = (_image(64, 64, b"a"), _image(4000, 3000, b"b"))
    with_images = compact_items(
        _items(*images), budget=_budget(image_input_tokens=2_500), fixed_text=""
    )
    without = compact_items(_items(), budget=_budget(image_input_tokens=2_500), fixed_text="")

    assert with_images.images == 2
    assert with_images.image_tokens == 2 * 2_500
    assert with_images.tokens_after - without.tokens_after == 2 * 2_500


def test_image_tokens_default_to_the_shared_default_when_the_model_has_no_value() -> None:
    result = compact_items(_items(_image(640, 480, b"c")), budget=_budget(), fixed_text="")

    assert ContextLimits(
        model_id="m", max_input_tokens=1, max_output_tokens=1
    ).image_input_tokens == (DEFAULT_IMAGE_INPUT_TOKENS)
    assert result.image_tokens == DEFAULT_IMAGE_INPUT_TOKENS


def test_image_tokens_follow_the_provider_estimate_scale() -> None:
    budget = _budget(image_input_tokens=1_000)
    budget.estimate_scale = 1.5

    result = compact_items(_items(_image(64, 64, b"d")), budget=budget, fixed_text="")

    assert result.image_tokens == 1_500


def test_input_without_images_is_estimated_from_text_only() -> None:
    items = _items()
    small = estimate_tokens(items, budget=_budget(image_input_tokens=1), fixed_text="指示")
    large = estimate_tokens(items, budget=_budget(image_input_tokens=2_000_000), fixed_text="指示")
    result = compact_items(items, budget=_budget(image_input_tokens=2_000_000), fixed_text="指示")

    # 画像の無い入力は、画像 1 枚の値に依らない（本文の見積もりだけ）。
    assert small == large == result.tokens_after
    assert (result.images, result.image_tokens) == (0, 0)


def test_resolve_model_target_reads_image_input_tokens_of_the_agent_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        oci_enterprise_ai_endpoint=ENDPOINT,
        oci_enterprise_ai_api_key="sk-test",
        oci_enterprise_ai_models=[
            EnterpriseAiConfiguredModel(
                model_id="vision-a", vision_enabled=True, image_input_tokens=2_500
            ),
            EnterpriseAiConfiguredModel(model_id="vision-b", vision_enabled=True),
        ],
        oci_enterprise_ai_default_text_model="vision-a",
    )
    monkeypatch.setattr(builtin_runtime, "get_settings", lambda: settings)

    assert builtin_runtime.resolve_model_target("").image_input_tokens == 2_500
    # 値を書かない登録モデルは共通の既定。
    assert (
        builtin_runtime.resolve_model_target("vision-b").image_input_tokens
        == DEFAULT_IMAGE_INPUT_TOKENS
    )


def test_sdk_agent_context_limits_carry_the_image_input_tokens_of_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AgentRuntimeRepository()
    monkeypatch.setattr(runtime, "runtime_repository", repository)
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": builtin_runtime.ModelTarget(
            model_id=model_id or "synthetic",
            endpoint=ENDPOINT,
            project_ocid="",
            api_key="synthetic",
            image_input_tokens=3_000,
        ),
    )
    item = repository.create_builtin_run(RunCreateRequest(goal="質問"))

    sdk_agent = builtin_runtime.build_sdk_agent(
        item.id, name="合成", instructions="", skill_ids=[], model_id="synthetic"
    )

    limits = getattr(sdk_agent, builtin_runtime._CONTEXT_LIMITS_ATTR)  # noqa: SLF001
    assert isinstance(limits, ContextLimits)
    assert limits.image_input_tokens == 3_000
