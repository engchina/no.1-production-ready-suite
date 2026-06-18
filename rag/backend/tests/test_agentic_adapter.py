"""Agentic アダプター(LLM 補助のクエリ計画プロファイル)のテスト。"""

from app.clients.oci_enterprise_ai import _parse_planned_queries
from app.config import Settings
from app.rag.agentic_adapter import (
    AGENTIC_PROFILE_ORDER,
    agentic_adapter_runtime_settings,
    normalize_agentic_profile,
    resolve_agentic_adapter,
)


def test_off_disables_query_planning() -> None:
    """既定 off は LLM 計画なし(現行挙動・追加コストなし)。"""
    params = resolve_agentic_adapter(Settings(rag_agentic_profile="off"))
    assert params.profile == "off"
    assert params.enabled is False
    assert params.rewrite is False
    assert params.decompose is False
    assert params.multi_hop is False


def test_query_rewrite_enables_rewrite_only() -> None:
    params = resolve_agentic_adapter(Settings(rag_agentic_profile="query_rewrite"))
    assert params.enabled is True
    assert params.rewrite is True
    assert params.decompose is False
    assert params.multi_hop is False


def test_decompose_enables_decomposition_only() -> None:
    params = resolve_agentic_adapter(Settings(rag_agentic_profile="decompose"))
    assert params.enabled is True
    assert params.rewrite is False
    assert params.decompose is True
    assert params.multi_hop is False


def test_multi_hop_enables_decompose_and_multi_hop() -> None:
    params = resolve_agentic_adapter(Settings(rag_agentic_profile="multi_hop"))
    assert params.enabled is True
    assert params.decompose is True
    assert params.multi_hop is True


def test_max_subqueries_is_honored() -> None:
    params = resolve_agentic_adapter(
        Settings(rag_agentic_profile="decompose", rag_agentic_max_subqueries=5)
    )
    assert params.max_subqueries == 5


def test_runtime_settings_orders_and_marks_selected() -> None:
    runtime = agentic_adapter_runtime_settings(Settings(rag_agentic_profile="decompose"))
    assert tuple(status.name for status in runtime.profiles) == AGENTIC_PROFILE_ORDER
    selected = [status.name for status in runtime.profiles if status.selected]
    assert selected == ["decompose"]


def test_normalize_agentic_profile_defaults() -> None:
    assert normalize_agentic_profile("nope") == "off"
    assert normalize_agentic_profile("multi_hop") == "multi_hop"


def test_parse_planned_queries_extracts_json_array_with_surrounding_text() -> None:
    raw = 'ここに計画です: ["再構成クエリA", "サブ質問B"] 以上。'
    assert _parse_planned_queries(raw, limit=3) == ["再構成クエリA", "サブ質問B"]


def test_parse_planned_queries_dedupes_and_caps_at_limit() -> None:
    raw = '["a", "a", "b", "c"]'
    assert _parse_planned_queries(raw, limit=2) == ["a", "b"]


def test_parse_planned_queries_returns_empty_on_non_json() -> None:
    assert _parse_planned_queries("解析できないテキスト", limit=3) == []
