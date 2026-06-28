"""Generation アダプター(回答生成プロファイル)のテスト。"""

import json

import pytest

from app.config import Settings
from app.rag.generation_adapter import (
    GENERATION_PROFILE_ORDER,
    generation_adapter_runtime_settings,
    normalize_generation_profile,
    resolve_generation_adapter,
    validate_structured_answer,
)


def test_default_profile_uses_client_default_system_prompt() -> None:
    """既定 grounded_concise は system_prompt=None(client 既定)で挙動不変。"""
    params = resolve_generation_adapter(Settings())
    assert params.profile == "grounded_concise"
    assert params.system_prompt is None
    assert params.structured_output is False


def test_non_default_profiles_set_explicit_system_prompt() -> None:
    for profile in ("detailed_cited", "strict_extractive", "structured_json", "bilingual_ja_en"):
        params = resolve_generation_adapter(Settings(rag_generation_profile=profile))
        assert params.profile == profile
        assert params.system_prompt is not None and params.system_prompt.strip()


def test_structured_json_marks_structured_output() -> None:
    params = resolve_generation_adapter(Settings(rag_generation_profile="structured_json"))
    assert params.structured_output is True
    assert "JSON" in (params.system_prompt or "")


def test_runtime_settings_orders_and_marks_selected() -> None:
    runtime = generation_adapter_runtime_settings(
        Settings(rag_generation_profile="strict_extractive")
    )
    assert tuple(status.name for status in runtime.profiles) == GENERATION_PROFILE_ORDER
    selected = [status.name for status in runtime.profiles if status.selected]
    assert selected == ["strict_extractive"]


def test_normalize_generation_profile_defaults() -> None:
    assert normalize_generation_profile("nope") == "grounded_concise"
    assert normalize_generation_profile("structured_json") == "structured_json"


def test_validate_structured_answer_accepts_plain_json() -> None:
    raw = '{"answer": "東京です", "evidence": ["e1"], "sources": ["doc#1"]}'
    parsed = json.loads(validate_structured_answer(raw))
    assert parsed == {"answer": "東京です", "evidence": ["e1"], "sources": ["doc#1"]}


def test_validate_structured_answer_tolerates_fence_and_prose() -> None:
    raw = '説明文\n```json\n{"answer": "A", "sources": ["s#1"]}\n```\n以上'
    parsed = json.loads(validate_structured_answer(raw))
    # 欠落フィールドは既定値で埋まり、フェンス/前後文を剥がして検証する。
    assert parsed == {"answer": "A", "evidence": [], "sources": ["s#1"]}


def test_validate_structured_answer_rejects_non_json() -> None:
    with pytest.raises(ValueError):
        validate_structured_answer("これは JSON ではありません。")


def test_validate_structured_answer_rejects_schema_mismatch() -> None:
    # answer が必須なのに欠落 → スキーマ不一致で ValueError。
    with pytest.raises(ValueError):
        validate_structured_answer('{"evidence": ["e1"]}')
