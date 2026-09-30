"""Vector Index アダプター(索引/検索精度)のテスト。"""

import pytest

from app.config import Settings
from app.rag.vector_index_adapter import (
    VECTOR_INDEX_PROFILE_ORDER,
    normalize_vector_index_profile,
    resolve_vector_index_adapter,
    vector_index_adapter_runtime_settings,
)


def test_balanced_respects_existing_target_accuracy_and_current_build() -> None:
    """balanced は既存 oracle_vector_target_accuracy と現行 HNSW ビルドを使う(挙動不変)。"""
    params = resolve_vector_index_adapter(
        Settings(rag_vector_index_profile="balanced", oracle_vector_target_accuracy=95)
    )
    assert params.profile == "balanced"
    assert params.target_accuracy == 95
    assert params.neighbors == 32
    assert params.efconstruction == 500
    assert params.distance == "COSINE"
    assert params.requires_reprovision is False


def test_balanced_honors_custom_target_accuracy_setting() -> None:
    params = resolve_vector_index_adapter(
        Settings(rag_vector_index_profile="balanced", oracle_vector_target_accuracy=90)
    )
    assert params.target_accuracy == 90
    assert params.requires_reprovision is False


def test_accurate_overrides_accuracy_and_requires_reprovision() -> None:
    params = resolve_vector_index_adapter(
        Settings(rag_vector_index_profile="accurate", oracle_vector_target_accuracy=95)
    )
    assert params.target_accuracy == 98
    assert params.neighbors == 48
    assert params.efconstruction == 800
    assert params.requires_reprovision is True


def test_fast_lowers_accuracy_and_requires_reprovision() -> None:
    params = resolve_vector_index_adapter(Settings(rag_vector_index_profile="fast"))
    assert params.target_accuracy == 85
    assert params.neighbors == 16
    assert params.requires_reprovision is True


def test_runtime_settings_orders_and_marks_selected() -> None:
    runtime = vector_index_adapter_runtime_settings(Settings(rag_vector_index_profile="accurate"))
    assert tuple(status.name for status in runtime.profiles) == VECTOR_INDEX_PROFILE_ORDER
    selected = [status.name for status in runtime.profiles if status.selected]
    assert selected == ["accurate"]


def test_runtime_settings_matches_actual_index() -> None:
    """実際の索引が推奨ビルドと同じなら再作成は不要(#562)。"""
    runtime = vector_index_adapter_runtime_settings(
        Settings(rag_vector_index_profile="accurate"), (48, 800)
    )
    assert runtime.index_status == "match"
    assert runtime.requires_reprovision is False
    assert (runtime.actual_neighbors, runtime.actual_efconstruction) == (48, 800)
    statuses = {status.name: status.index_status for status in runtime.profiles}
    assert statuses == {"balanced": "reprovision", "accurate": "match", "fast": "reprovision"}


def test_runtime_settings_requires_reprovision_when_actual_differs() -> None:
    runtime = vector_index_adapter_runtime_settings(
        Settings(rag_vector_index_profile="accurate"), (32, 500)
    )
    assert runtime.index_status == "reprovision"
    assert runtime.requires_reprovision is True
    assert (runtime.actual_neighbors, runtime.actual_efconstruction) == (32, 500)


def test_runtime_settings_unknown_when_actual_unreadable() -> None:
    """実際の値を読めないときは「確認できない」で、再作成が必要とは断定しない。"""
    runtime = vector_index_adapter_runtime_settings(Settings(rag_vector_index_profile="fast"))
    assert runtime.index_status == "unknown"
    assert runtime.requires_reprovision is False
    assert runtime.actual_neighbors is None and runtime.actual_efconstruction is None
    assert {status.index_status for status in runtime.profiles} == {"unknown"}


def test_normalize_vector_index_profile_defaults() -> None:
    # 未知の値は既定（高精度。#272）に倒す。
    assert normalize_vector_index_profile("nope") == "accurate"
    assert normalize_vector_index_profile("fast") == "fast"


@pytest.mark.usefixtures("oracle_db")
async def test_vector_index_build_params_on_real_oracle() -> None:
    """実 Oracle AI Database で主検索索引の実際の NEIGHBORS / EFCONSTRUCTION を読める(#562)。

    アプリのユーザーが v$vector_graph_index を読める環境(ローカルの ADMIN)を前提にする。
    """
    from app.clients.oracle import OracleClient

    actual = await OracleClient().get_vector_index_build_params()

    assert actual is not None
    neighbors, efconstruction = actual
    assert neighbors > 0 and efconstruction > 0
