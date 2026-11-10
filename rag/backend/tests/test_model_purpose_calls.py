"""実際の画像読取の呼出点で固定用途を引き継ぐ。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pr_backend_core.observability.model_purpose import (
    model_call_purpose,
    model_purpose_attributes,
)
from rag_engine.adapters import oci as engine_oci


@pytest.mark.parametrize("multimodal", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_picture_reading_sets_purpose_for_both_image_paths_and_restores_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, multimodal: bool, failed: bool
) -> None:
    target, context = tmp_path / "target.png", tmp_path / "context.png"
    target.write_bytes(b"synthetic")
    context.write_bytes(b"synthetic")
    provider = object()
    settings = SimpleNamespace(default_vision_llm="model-a")
    result = {"retrieval_text": "合成の画像の説明"}
    failure = RuntimeError("合成の失敗")
    calls: list[tuple[object, ...]] = []

    def parse(
        system: str, prompt: str, images: object, active: object, schema: object, selected: object
    ) -> Any:
        assert model_purpose_attributes() == {"pr.gen_ai.purpose": "document.read"}
        calls.append((system, prompt, images, active, schema, selected))
        if failed:
            raise failure
        return SimpleNamespace(model_dump=lambda **kwargs: result)

    monkeypatch.setattr(engine_oci, "_validated_llm_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(engine_oci, "_parse_image_with_retry", parse)
    monkeypatch.setattr(engine_oci, "_parse_multimodal_with_retry", parse)
    with model_call_purpose("answer.audit"):
        kwargs: dict[str, Any] = {"context_image_paths": [context]} if multimodal else {}
        if failed:
            with pytest.raises(RuntimeError) as raised:
                engine_oci.describe_picture(
                    target, {}, cast(Any, settings), rendered_prompt="固定の合成指示", **kwargs
                )
            assert raised.value is failure
        else:
            assert (
                engine_oci.describe_picture(
                    target, {}, cast(Any, settings), rendered_prompt="固定の合成指示", **kwargs
                )
                is result
            )
        assert model_purpose_attributes() == {"pr.gen_ai.purpose": "answer.audit"}
    assert len(calls) == 1
    assert calls[0][1] == "固定の合成指示" and calls[0][3] is settings and calls[0][5] is provider
    assert calls[0][2] == ([target, context] if multimodal else target)
    assert model_purpose_attributes() == {}
