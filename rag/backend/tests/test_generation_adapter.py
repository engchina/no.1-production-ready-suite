"""Generation アダプター(回答生成プロファイル)のテスト。"""

from app.config import Settings
from app.rag.generation_adapter import (
    GENERATION_PROFILE_ORDER,
    generation_adapter_runtime_settings,
    normalize_generation_profile,
    resolve_generation_adapter,
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
