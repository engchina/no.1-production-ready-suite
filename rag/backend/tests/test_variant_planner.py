"""variant materialization プランナ(variant_planner)の決定論テスト。

dedup(共有 + refcount)・層別共有・GC 差分の核心を固定する。実 Oracle / DDL 不要。
"""

from app.config import Settings, get_settings
from app.rag.variant_keys import compute_chunk_set_id, compute_graph_layer_id
from app.rag.variant_planner import diff_plan, plan_materializations

SRC = "c" * 64


def _settings(**overrides: object) -> Settings:
    return get_settings().model_copy(update=overrides)


def test_identical_settings_share_one_chunk_set_with_refcount() -> None:
    """同一設定の 2 KB は 1 chunk_set を共有し refcount=2(複製ゼロ)。"""
    base = _settings()
    plan = plan_materializations(SRC, {"kb-1": base, "kb-2": base})

    assert len(plan.chunk_sets) == 1
    chunk_set_id = compute_chunk_set_id(SRC, base)
    assert plan.chunk_sets[chunk_set_id] == frozenset({"kb-1", "kb-2"})
    assert plan.refcount(chunk_set_id) == 2


def test_different_chunk_axis_splits_chunk_sets() -> None:
    """chunk_size が違う 2 KB は別 chunk_set(各 refcount=1)。"""
    plan = plan_materializations(
        SRC,
        {
            "kb-1": _settings(rag_chunk_size=1000),
            "kb-2": _settings(rag_chunk_size=2000),
        },
    )
    assert len(plan.chunk_sets) == 2
    assert all(len(kb_ids) == 1 for kb_ids in plan.chunk_sets.values())


def test_graph_axis_shares_chunk_set_splits_graph_layer() -> None:
    """graph だけ違う 2 KB は chunk_set 共有(refcount=2)、graph 層は分裂(各 refcount=1)。"""
    off = _settings(rag_graph_profile="off")
    entities = _settings(rag_graph_profile="entities")
    plan = plan_materializations(SRC, {"kb-1": off, "kb-2": entities})

    # chunk_set は 1 つを共有。
    assert len(plan.chunk_sets) == 1
    chunk_set_id = compute_chunk_set_id(SRC, off)
    assert plan.refcount(chunk_set_id) == 2
    # graph 層は 2 つに分裂し、それぞれ別 KB が参照。
    assert len(plan.graph_layers) == 2
    assert plan.refcount(compute_graph_layer_id(chunk_set_id, off)) == 1
    assert plan.refcount(compute_graph_layer_id(chunk_set_id, entities)) == 1


def test_diff_to_create_for_new_materialization() -> None:
    """既存が空なら計画の全層が to_create、to_collect は空。"""
    plan = plan_materializations(SRC, {"kb-1": _settings()})
    diff = diff_plan(frozenset(), plan)

    assert diff.to_create == plan.all_ids()
    assert diff.to_collect == frozenset()


def test_diff_collects_unreferenced_layers() -> None:
    """参照が消えた既存層は to_collect(GC)、新規は to_create。"""
    plan = plan_materializations(SRC, {"kb-1": _settings()})
    desired = plan.all_ids()
    stale_id = "cs_deadbeefdeadbeef"
    existing = frozenset({stale_id, *list(desired)[:1]})

    diff = diff_plan(existing, plan)

    # 既存の stale は計画に無い → GC 対象。
    assert stale_id in diff.to_collect
    # 既に存在する層は再作成しない。
    assert (set(desired) & existing).isdisjoint(diff.to_create)
    # 計画にあって未作成の層は作成対象。
    assert diff.to_create == frozenset(desired - existing)


def test_shared_layer_not_collected_while_other_kb_references_it() -> None:
    """KB を 1 つ外しても、別 KB が同じ chunk_set を参照していれば GC されない。"""
    base = _settings()
    chunk_set_id = compute_chunk_set_id(SRC, base)

    # 2 KB が共有していた状態 → 1 KB を外した計画。
    plan_after = plan_materializations(SRC, {"kb-2": base})
    existing = plan_materializations(SRC, {"kb-1": base, "kb-2": base}).all_ids()

    diff = diff_plan(existing, plan_after)

    # chunk_set は kb-2 がまだ参照しているので GC されない。
    assert chunk_set_id not in diff.to_collect
    assert plan_after.refcount(chunk_set_id) == 1
