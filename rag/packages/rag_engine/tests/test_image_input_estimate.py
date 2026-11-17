"""画像を添付する生成の入力の見積もり（#5316）。

画像の data URL（base64）を本文の token として数えず、枚数 × 呼ぶモデルの「画像 1 枚の入力 token」（登録モデルの値。
無ければ platform の既定）で数える。本文だけの見積もりは変えない。fixture は合成の画像（中身の無い PNG）。
"""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pr_backend_core.token_estimate import DEFAULT_IMAGE_INPUT_TOKENS, without_image_inputs

from rag_engine.adapters import oci
from rag_engine.config import get_llm_provider, get_settings
from rag_engine.generation.evidence_selection import select_evidence
from rag_engine.generation.input_budget import GenerationInputBudgetExceeded, model_input_measurement, request_input_estimate
from rag_engine.generation.section_expansion import estimate_tokens
from rag_engine.models.llm import TextOutput

# 3 枚で base64 が約 160 万字（本文として数えると約 40 万 token）になる合成の画像。
IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(400_000)
LIMIT = 256_000


def _provider(**values):
    return SimpleNamespace(**{"model": "stub-model", "label": "stub", "reasoning_effort": "", "max_output_tokens": 65536, **values})


class _Client:
    def __init__(self):
        self.budgets: list[int] = []
        self.responses = SimpleNamespace(parse=self._parse)

    def with_options(self, **_options):
        return self

    def _parse(self, **kwargs):
        self.budgets.append(kwargs["max_output_tokens"])
        return SimpleNamespace(
            status="completed",
            output_text=TextOutput(text="ok").model_dump_json(),
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, input_tokens_details=None, output_tokens_details=None),
        )


@pytest.fixture
def images(tmp_path: Path) -> list[Path]:
    paths = []
    for index in range(3):
        path = tmp_path / f"figure-{index}.png"
        path.write_bytes(IMAGE_BYTES)
        paths.append(path)
    return paths


def _text_tokens(prompt: str) -> int:
    """同じ要求から画像の部品を除いた本文の見積もり。"""
    wire = oci._response_wire_request(
        {
            "model": "stub-model",
            "input": [{"role": "system", "content": "指示"}, {"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            "text_format": TextOutput,
            "max_output_tokens": 65536,
            "temperature": oci.LLM_TEMPERATURE,
        }
    )
    return estimate_tokens(json.dumps(wire, ensure_ascii=False))


def test_three_images_fit_and_count_per_image_from_model_setting(monkeypatch, images):
    """画像 3 枚の wire でも上限を超えず、見積もりは本文 + 3 × 登録モデルの値（base64 の長さに比例しない）。"""
    client = _Client()
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *_: client)
    provider = _provider(image_input_tokens=2_000)
    text = _text_tokens("合成の本文")
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_input_tokens=text + 3 * 2_000 + 100)

    assert oci._parse_openai_multimodal_once("指示", "合成の本文", images, settings, TextOutput, provider).text == "ok"
    # 出力の上限は入力の残り（上限 − 本文 − 画像 3 枚）。
    assert client.budgets == [100]


def test_images_over_limit_by_model_setting_are_not_sent(monkeypatch, images):
    """画像の分が登録モデルの値で上限を超えるなら送らず、内訳を診断に残す。"""
    client = _Client()
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *_: client)
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_input_tokens=LIMIT)

    with pytest.raises(GenerationInputBudgetExceeded) as raised:
        oci._parse_openai_multimodal_once("指示", "合成の本文", images, settings, TextOutput, _provider(image_input_tokens=90_000))

    diagnostic = raised.value.diagnostic
    assert diagnostic["reason"] == "final_model_input_exceeds_limit"
    assert (diagnostic["image_count"], diagnostic["image_input_tokens_per_image"], diagnostic["estimated_image_tokens"]) == (
        3,
        90_000,
        270_000,
    )
    assert diagnostic["estimated_input_tokens"] == _text_tokens("合成の本文") + 270_000
    assert client.budgets == []


def test_model_without_setting_uses_platform_default(images):
    """値の無いモデル（属性が無い provider）は platform の既定で数える。"""
    wire = oci._response_wire_request(
        {
            "model": "stub-model",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "合成の本文"}]
                    + [{"type": "input_image", "image_url": oci._image_data_url(path)} for path in images],
                }
            ],
            "text_format": TextOutput,
            "max_output_tokens": 1,
            "temperature": oci.LLM_TEMPERATURE,
        }
    )
    tokens, detail = request_input_estimate(wire, _provider())
    stripped, count = without_image_inputs(wire)
    assert count == 3
    assert "base64" not in json.dumps(stripped)
    assert detail == {
        "image_count": 3,
        "image_input_tokens_per_image": DEFAULT_IMAGE_INPUT_TOKENS,
        "estimated_image_tokens": 3 * DEFAULT_IMAGE_INPUT_TOKENS,
    }
    assert tokens == estimate_tokens(json.dumps(stripped, ensure_ascii=False)) + 3 * DEFAULT_IMAGE_INPUT_TOKENS
    assert tokens < LIMIT


def test_engine_settings_take_image_tokens_from_env():
    """backend が渡す登録モデルの値を使い、渡さなければ platform の既定。"""
    default = get_settings(environ={}, dotenv_path=None)
    assert get_llm_provider(default, default.default_answer_llm).image_input_tokens == DEFAULT_IMAGE_INPUT_TOKENS
    configured = get_settings(environ={"OCI_ENTERPRISE_AI_IMAGE_INPUT_TOKENS": "3000"}, dotenv_path=None)
    assert get_llm_provider(configured, configured.default_answer_llm).image_input_tokens == 3_000
    # Vision のモデルを指定しないときは回答のモデルの値。
    assert get_llm_provider(configured, configured.default_vision_llm).image_input_tokens == 3_000


def test_measurement_adds_images_and_text_only_is_unchanged():
    """送信前の測定も同じ規則: 本文 + 枚数 × 1 枚の値。画像が無ければ従来と同じ記録。"""
    base = get_settings(environ={}, dotenv_path=None)
    providers = {key: replace(value, image_input_tokens=1_500) for key, value in base.llm_providers.items()}
    config = replace(base, llm_providers=providers, answer_max_input_tokens=LIMIT)

    text_only = model_input_measurement(config, "指示", "合成の本文", {"type": "object"})
    with_images = model_input_measurement(config, "指示", "合成の本文", {"type": "object"}, image_count=3)

    assert set(text_only) == {
        "model_input_limit_tokens",
        "output_reserved_tokens",
        "available_input_tokens",
        "estimated_input_tokens",
        "token_count_method",
        "fits",
    }
    assert with_images["estimated_input_tokens"] == text_only["estimated_input_tokens"] + 4_500
    assert (with_images["image_count"], with_images["estimated_image_tokens"]) == (3, 4_500)


def test_evidence_selection_reserves_attached_images():
    """根拠の選択も添付する画像の分を測る（本文だけでは入っても、画像の分で上限を超えれば送らない）。"""
    base = get_settings(environ={}, dotenv_path=None)
    providers = {key: replace(value, image_input_tokens=50_000, max_output_tokens=100) for key, value in base.llm_providers.items()}
    config = replace(base, llm_providers=providers, answer_max_input_tokens=100_000)

    def unused(*_args, **_kwargs):
        pytest.fail("固定の入力が入らないときは読まない")

    arguments = {
        "question": "合成の質問",
        "render": lambda _values: "合成の本文",
        "evidence_text": lambda _values: "",
        "tag": list,
        "system": "指示",
        "schema_for": lambda _values: {"type": "object"},
        "read": unused,
        "unusable_output": lambda _exc: False,
    }
    _spans, _prompt, diagnostic = select_evidence([], config, **arguments, image_count=1)
    assert diagnostic["estimated_image_tokens"] == 50_000
    with pytest.raises(GenerationInputBudgetExceeded) as raised:
        select_evidence([], config, **arguments, image_count=2)
    assert raised.value.diagnostic["reason"] == "fixed_input_exceeds_limit"
    assert raised.value.diagnostic["image_count"] == 2
