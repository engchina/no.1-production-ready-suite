"""variant materialization プランナ(variant_planner)の決定論テスト。

dedup(共有 + refcount)・層別共有・GC 差分の核心を固定する。実 Oracle / DDL 不要。
"""

from app.config import Settings, get_settings
from app.rag.kb_adapter_config import KnowledgeBaseAdapterConfig
from app.rag.variant_keys import (
    compute_chunk_set_id,
    compute_extraction_id,
    compute_graph_layer_id,
)
from app.rag.variant_planner import (
    _select_extractions_within_limit,
    diff_plan,
    plan_document_materializations,
    plan_materializations,
)

SRC = "c" * 64


def _settings(**overrides: object) -> Settings:
    return get_settings().model_copy(update=overrides)


def _config(**ingestion: object) -> KnowledgeBaseAdapterConfig:
    return KnowledgeBaseAdapterConfig.model_validate({"ingestion": ingestion})


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


def test_plan_from_kb_configs_shares_when_both_inherit() -> None:
    """取込上書きが無い 2 KB は同じ effective 設定 → 1 chunk_set を共有(refcount=2)。"""
    settings = get_settings()
    plan = plan_document_materializations(
        SRC,
        settings,
        {"kb-1": KnowledgeBaseAdapterConfig(), "kb-2": KnowledgeBaseAdapterConfig()},
    )
    assert len(plan.chunk_sets) == 1
    assert next(iter(plan.chunk_sets.values())) == frozenset({"kb-1", "kb-2"})


def test_plan_from_kb_configs_splits_on_chunk_override() -> None:
    """一方の KB が chunk_size を上書きすると chunk_set が分裂する。"""
    settings = get_settings()
    plan = plan_document_materializations(
        SRC,
        settings,
        {
            "kb-1": KnowledgeBaseAdapterConfig(),
            "kb-2": _config(chunk_size=settings.rag_chunk_size + 512),
        },
    )
    assert len(plan.chunk_sets) == 2
    assert all(len(kb_ids) == 1 for kb_ids in plan.chunk_sets.values())


def test_plan_from_kb_configs_graph_override_shares_chunk_set() -> None:
    """graph_profile だけ違う上書きでも chunk_set は共有、graph 層だけ分裂。"""
    settings = get_settings()
    plan = plan_document_materializations(
        SRC,
        settings,
        {"kb-1": KnowledgeBaseAdapterConfig(), "kb-2": _config(graph_profile="entities")},
    )
    assert len(plan.chunk_sets) == 1
    assert len(plan.graph_layers) == 2


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


# --- P2: extraction 層(2 階層化)---


def test_chunking_axis_shares_extraction_but_splits_chunk_set() -> None:
    """chunking だけ違う 2 KB は extraction を共有(extract 1 回)、chunk_set だけ分裂。

    #6 の核心: parser グループごとに extract 1 回、chunking 変種はその抽出を再利用する。
    """
    plan = plan_materializations(
        SRC,
        {"kb-1": _settings(rag_chunk_size=1000), "kb-2": _settings(rag_chunk_size=2000)},
    )
    extraction_id = compute_extraction_id(SRC, _settings())
    # 抽出は 1 つを共有(refcount=2)。
    assert len(plan.extractions) == 1
    assert plan.refcount(extraction_id) == 2
    # chunk_set は 2 つに分裂し、両者とも同じ親抽出を指す。
    assert len(plan.chunk_sets) == 2
    assert set(plan.chunk_set_parents.values()) == {extraction_id}
    assert plan.extraction_groups()[extraction_id] == frozenset(plan.chunk_sets)


def test_parser_axis_splits_extractions() -> None:
    """parser が違う 2 KB は別 extraction(parser 軸が効く #6 の核心)。"""
    plan = plan_materializations(
        SRC,
        {
            "kb-1": _settings(rag_parser_adapter_backend="docling"),
            "kb-2": _settings(rag_parser_adapter_backend="marker"),
        },
    )
    assert len(plan.extractions) == 2
    # 親が違うので chunk_set も別、各 chunk_set は別の親抽出を指す。
    assert len(plan.chunk_sets) == 2
    assert len(set(plan.chunk_set_parents.values())) == 2


def test_select_extractions_within_limit_priority_order() -> None:
    """打ち切りは owning → refcount 大 → id 昇順の決定論順。上限以内なら全保持。"""
    consumers = {"ex_a": {"k1"}, "ex_b": {"k2", "k3"}, "ex_c": {"k4"}}
    # 上限以内 → 全保持・打ち切り無し。
    kept, truncated = _select_extractions_within_limit(consumers, None, 3)
    assert kept == {"ex_a", "ex_b", "ex_c"}
    assert truncated == set()
    # 上限 2・owning=ex_a → owning + refcount 最大(ex_b)を保持、ex_c 打ち切り。
    kept, truncated = _select_extractions_within_limit(consumers, "ex_a", 2)
    assert kept == {"ex_a", "ex_b"}
    assert truncated == {"ex_c"}


def test_extraction_limit_truncates_owning_kept() -> None:
    """抽出が上限を超えると owning 優先で打ち切り、超過分が truncated(配下 chunk_set も除外)。"""
    owning = _settings(rag_parser_adapter_backend="docling")
    owning_id = compute_extraction_id(SRC, owning)
    marker_id = compute_extraction_id(SRC, _settings(rag_parser_adapter_backend="marker"))
    consumers = {
        "kb-own": owning,
        "kb-marker-1": _settings(rag_parser_adapter_backend="marker"),
        "kb-marker-2": _settings(rag_parser_adapter_backend="marker"),
        "kb-unstr": _settings(rag_parser_adapter_backend="unstructured"),
    }
    plan = plan_materializations(SRC, consumers, owning_extraction_id=owning_id, max_extractions=2)

    assert len(plan.extractions) == 2
    assert len(plan.truncated_extractions) == 1
    # owning は必ず保持、次点は refcount 最大(marker=2)。
    assert owning_id in plan.extractions
    assert marker_id in plan.extractions
    assert owning_id not in plan.truncated_extractions
    # 打ち切られた抽出配下の chunk_set は計画に残らない(親はすべて保持抽出)。
    assert all(parent in plan.extractions for parent in plan.chunk_set_parents.values())


def test_diff_creates_and_collects_extraction_tier() -> None:
    """diff は extraction 層も対象(新規 to_create / 参照消失 to_collect)。"""
    plan = plan_materializations(SRC, {"kb-1": _settings()})
    extraction_id = compute_extraction_id(SRC, _settings())
    # 新規: extraction も to_create に含まれる。
    assert extraction_id in diff_plan(frozenset(), plan).to_create
    # 既存に居た別抽出が計画から消えれば to_collect(GC)、参照中の抽出は残す。
    stale_ex = "ex_deadbeefdeadbeef"
    diff_gc = diff_plan(frozenset({stale_ex, extraction_id}), plan)
    assert stale_ex in diff_gc.to_collect
    assert extraction_id not in diff_gc.to_collect


def test_plan_from_kb_configs_shares_extraction_when_only_chunk_differs() -> None:
    """KB 設定経由でも chunk 上書きだけなら extraction 共有(grouping が効く)。"""
    settings = get_settings()
    plan = plan_document_materializations(
        SRC,
        settings,
        {
            "kb-1": KnowledgeBaseAdapterConfig(),
            "kb-2": _config(chunk_size=settings.rag_chunk_size + 512),
        },
    )
    # 前処理/parser は両者既定 → 抽出 1 つ共有、chunk_set は 2 つ。
    assert len(plan.extractions) == 1
    assert len(plan.chunk_sets) == 2
    assert len(plan.extraction_groups()[next(iter(plan.extractions))]) == 2
