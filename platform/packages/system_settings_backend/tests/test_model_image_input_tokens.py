"""登録モデルごとの画像 1 枚の入力 token `image_input_tokens`（#5316・#5334）。

画像を添付する呼び出しの入力の上限の判定は、画像を枚数 × この値で数える。値の無いモデルは値を
保存せず（None）、共通の既定（実体は `pr_backend_core.token_estimate.DEFAULT_IMAGE_INPUT_TOKENS`。
OCI Generative AI の公式の文書の xAI Grok の 1 枚の最大 1792）を使う。値の解釈は
`image_input_tokens_to_use` の 1 か所。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pr_backend_core.token_estimate import (
    DEFAULT_IMAGE_INPUT_TOKENS,
    DEFAULT_IMAGE_INPUT_TOKENS_SOURCE_URL,
    without_image_inputs,
)
from pydantic import ValidationError
from test_model import PAYLOAD, FakeSettings, make_client, make_store

from pr_system_settings.model import (
    DEFAULT_ENTERPRISE_AI_IMAGE_INPUT_TOKENS,
    ENTERPRISE_AI_IMAGE_INPUT_TOKENS_MAX,
    ENTERPRISE_AI_IMAGE_INPUT_TOKENS_MIN,
    EnterpriseAiConfiguredModel,
    enterprise_ai_image_input_tokens,
    enterprise_ai_model_catalog,
    image_input_tokens_to_use,
)


def _payload_with_image_tokens(value: object) -> dict[str, Any]:
    payload = copy.deepcopy(PAYLOAD)
    payload["enterprise_ai"]["models"][0]["image_input_tokens"] = value
    return payload


def test_default_and_range() -> None:
    assert DEFAULT_ENTERPRISE_AI_IMAGE_INPUT_TOKENS == DEFAULT_IMAGE_INPUT_TOKENS == 1_792
    assert DEFAULT_IMAGE_INPUT_TOKENS_SOURCE_URL.startswith("https://docs.oracle.com/")
    assert (ENTERPRISE_AI_IMAGE_INPUT_TOKENS_MIN, ENTERPRISE_AI_IMAGE_INPUT_TOKENS_MAX) == (
        1,
        2_000_000,
    )
    # 値の無いモデルは値を持たず（保存しない）、使う値は既定。
    assert EnterpriseAiConfiguredModel(model_id="m").image_input_tokens is None
    assert image_input_tokens_to_use(None) == 1_792


@pytest.mark.parametrize(
    "value", [0, 2_000_001, True, "abc"], ids=["zero", "above-max", "bool", "text"]
)
def test_out_of_range_is_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        EnterpriseAiConfiguredModel.model_validate({"model_id": "m", "image_input_tokens": value})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 1_792),
        (2_500, 2_500),
        (" 4784 ", 4_784),
        (0, 1_792),
        (3_000_000, 1_792),
        ("x", 1_792),
        (True, 1_792),
    ],
    ids=["none", "value", "text", "zero", "above", "invalid", "bool"],
)
def test_value_to_use(value: object, expected: int) -> None:
    assert image_input_tokens_to_use(value) == expected


def test_value_is_saved_per_model_and_reloaded(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)

    response = make_client(settings, store).patch(
        "/api/settings/model", json=_payload_with_image_tokens(2_500)
    )

    assert response.status_code == 200
    models = response.json()["data"]["settings"]["enterprise_ai"]["models"]
    # 値を渡さなかった 2 つ目のモデルは値なし（既定を使う）。既定と出典は API が返す。
    assert [model["image_input_tokens"] for model in models] == [2_500, None]
    data = response.json()["data"]
    assert data["default_image_input_tokens"] == 1_792
    assert data["default_image_input_tokens_source_url"] == DEFAULT_IMAGE_INPUT_TOKENS_SOURCE_URL
    document = json.loads((tmp_path / "model-settings.json").read_text(encoding="utf-8"))
    assert document["version"] == 7
    assert [model["image_input_tokens"] for model in document["enterprise_ai"]["models"]] == [
        2_500,
        None,
    ]
    other = FakeSettings()
    make_store(tmp_path).load(other)
    assert enterprise_ai_image_input_tokens(other, "llm-a") == 2_500
    assert enterprise_ai_image_input_tokens(other, " vlm-b ") == 1_792
    # 登録モデルに無い ID は既定。
    assert enterprise_ai_image_input_tokens(other, "unknown") == 1_792


def test_out_of_range_save_is_422(tmp_path: Path) -> None:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)

    response = make_client(settings, store).patch(
        "/api/settings/model", json=_payload_with_image_tokens(0)
    )

    assert response.status_code == 422
    assert not (tmp_path / "model-settings.json").exists()


def test_v6_document_without_value_is_read_as_default(tmp_path: Path) -> None:
    """#5316 より前の JSON（version 6、項目なし）はそのまま読め、既定になる。"""
    (tmp_path / "model-settings.json").write_text(
        json.dumps(
            {
                "version": 6,
                "enterprise_ai": {
                    "models": [{"model_id": "llm-a", "vision_enabled": True}],
                    "default_text_model_id": "llm-a",
                },
            }
        ),
        encoding="utf-8",
    )
    settings = FakeSettings()
    make_store(tmp_path).load(settings)

    assert [model.image_input_tokens for model in enterprise_ai_model_catalog(settings)] == [None]
    assert enterprise_ai_image_input_tokens(settings, "llm-a") == 1_792


def test_without_image_inputs_drops_only_image_parts() -> None:
    """画像の部品だけを除き、数を返す（本文の部品・ほかの値は残す）。"""
    request = {
        "input": [
            {"role": "system", "content": "指示"},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "本文"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                    {"type": "input_image", "image_url": "data:image/png;base64,BBBB"},
                ],
            },
        ],
        "max_output_tokens": 100,
        "reasoning": None,
    }

    stripped, count = without_image_inputs(request)

    assert count == 2
    assert stripped == {
        "input": [
            {"role": "system", "content": "指示"},
            {"role": "user", "content": [{"type": "input_text", "text": "本文"}]},
        ],
        "max_output_tokens": 100,
        "reasoning": None,
    }
    assert without_image_inputs({"input": "本文"}) == ({"input": "本文"}, 0)
