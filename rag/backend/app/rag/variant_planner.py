"""variant materialization プランナ — dedup / refcount / GC の決定論ブレイン。

1 文書が複数 KB に所属し、各 KB が異なる取込設定(effective settings)を持つとき、
「重複は共有・差分は複製」をどう実体化するかを **決定論で** 計算する。実際の永続化
(Oracle 表 / chunk / embedding)や VLM 実行は行わず、**どの層をいくつ作り、どれを
参照カウント 0 で GC するか** だけを返す。要 DDL・実 Oracle の永続層はこの計画に従う。

核心:
* 同一 chunk_set_id を参照する KB が複数あれば共有(refcount = 参照 KB 数)。
* chunk 軸が違えば別 chunk_set(差分複製)。下流軸だけ違えば上流層を共有し派生層だけ分裂。
* KB を外す/設定変更で参照が消えた層は refcount 0 として GC 対象になる(他 KB 使用中は残す)。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from app.config import Settings
from app.rag.kb_adapter_config import (
    KnowledgeBaseAdapterConfig,
    apply_adapter_config_or_global,
)
from app.rag.variant_keys import (
    MAX_EXTRACTIONS_PER_DOCUMENT,
    compute_extraction_id,
    compute_layer_ids,
)


@dataclass(frozen=True)
class MaterializationPlan:
    """文書 1 件の望ましい materialization 状態(extraction → chunk_set → 派生層の 2 階層)。

    各 dict は ``層 ID -> それを参照する kb_id 群``。refcount は値集合の要素数。
    ``chunk_set_parents`` は chunk_set_id -> 親 extraction_id(parser グループ単位で extract
    1 回 → 各 chunking で index する実体化に使う)。``truncated_extractions`` は上限超過で
    計画から外した抽出 ID(その配下 KB は計画に含めない。呼び出し側が警告する)。
    """

    extractions: dict[str, frozenset[str]]
    chunk_sets: dict[str, frozenset[str]]
    metadata_layers: dict[str, frozenset[str]]
    graph_layers: dict[str, frozenset[str]]
    nav_layers: dict[str, frozenset[str]]
    chunk_set_parents: dict[str, str]
    truncated_extractions: frozenset[str]

    def _layers(self) -> tuple[dict[str, frozenset[str]], ...]:
        return (
            self.extractions,
            self.chunk_sets,
            self.metadata_layers,
            self.graph_layers,
            self.nav_layers,
        )

    def all_ids(self) -> frozenset[str]:
        """全層の ID 集合(存在すべき materialization)。"""
        ids: set[str] = set()
        for layer in self._layers():
            ids.update(layer)
        return frozenset(ids)

    def refcount(self, layer_id: str) -> int:
        """指定層 ID の参照 KB 数(存在しなければ 0)。"""
        for layer in self._layers():
            if layer_id in layer:
                return len(layer[layer_id])
        return 0

    def extraction_groups(self) -> dict[str, frozenset[str]]:
        """extraction_id -> その配下 chunk_set_id 群(parser グループの index 単位)。"""
        groups: dict[str, set[str]] = defaultdict(set)
        for chunk_set_id, extraction_id in self.chunk_set_parents.items():
            groups[extraction_id].add(chunk_set_id)
        return {ex_id: frozenset(cs_ids) for ex_id, cs_ids in groups.items()}


@dataclass(frozen=True)
class MaterializationDiff:
    """既存状態から望ましい計画への差分。"""

    to_create: frozenset[str]
    to_collect: frozenset[str]


def plan_materializations(
    source_sha256: str,
    consumers: Mapping[str, Settings],
    *,
    owning_extraction_id: str | None = None,
    max_extractions: int = MAX_EXTRACTIONS_PER_DOCUMENT,
) -> MaterializationPlan:
    """各 KB の effective 取込設定から、共有を畳んだ 2 階層 materialization 計画を作る。

    ``consumers`` は ``kb_id -> effective ingestion settings``。同じ層 ID に解決される
    KB は同じ materialization を共有する(refcount = 参照 KB 数)。抽出(parser×preprocess)
    が ``max_extractions`` を超える場合は owning 優先で打ち切り、外した抽出配下の KB は計画
    から除外する(``truncated_extractions`` に記録)。
    """
    per_consumer = {
        kb_id: compute_layer_ids(source_sha256, settings) for kb_id, settings in consumers.items()
    }

    extraction_consumers: dict[str, set[str]] = defaultdict(set)
    for kb_id, ids in per_consumer.items():
        extraction_consumers[ids["extraction_id"]].add(kb_id)

    kept, truncated = _select_extractions_within_limit(
        extraction_consumers, owning_extraction_id, max_extractions
    )

    extractions: dict[str, set[str]] = defaultdict(set)
    chunk_sets: dict[str, set[str]] = defaultdict(set)
    metadata_layers: dict[str, set[str]] = defaultdict(set)
    graph_layers: dict[str, set[str]] = defaultdict(set)
    nav_layers: dict[str, set[str]] = defaultdict(set)
    chunk_set_parents: dict[str, str] = {}

    for kb_id, ids in per_consumer.items():
        extraction_id = ids["extraction_id"]
        if extraction_id not in kept:
            continue
        extractions[extraction_id].add(kb_id)
        chunk_sets[ids["chunk_set_id"]].add(kb_id)
        metadata_layers[ids["metadata_layer_id"]].add(kb_id)
        graph_layers[ids["graph_layer_id"]].add(kb_id)
        nav_layers[ids["nav_layer_id"]].add(kb_id)
        chunk_set_parents[ids["chunk_set_id"]] = extraction_id

    return MaterializationPlan(
        extractions=_freeze(extractions),
        chunk_sets=_freeze(chunk_sets),
        metadata_layers=_freeze(metadata_layers),
        graph_layers=_freeze(graph_layers),
        nav_layers=_freeze(nav_layers),
        chunk_set_parents=chunk_set_parents,
        truncated_extractions=frozenset(truncated),
    )


def _select_extractions_within_limit(
    extraction_consumers: Mapping[str, set[str]],
    owning_extraction_id: str | None,
    max_extractions: int,
) -> tuple[set[str], set[str]]:
    """抽出が上限を超える場合の保持/打ち切りを決める(決定論・再現可能)。

    優先順: owning 抽出 → 参照 KB 数の多い順 → extraction_id 昇順。上位 ``max_extractions``
    を保持し、残りを打ち切る。上限以内なら全保持。
    """
    if len(extraction_consumers) <= max_extractions:
        return set(extraction_consumers), set()
    ordered = sorted(
        extraction_consumers,
        key=lambda ex_id: (
            ex_id != owning_extraction_id,
            -len(extraction_consumers[ex_id]),
            ex_id,
        ),
    )
    return set(ordered[:max_extractions]), set(ordered[max_extractions:])


def plan_document_materializations(
    source_sha256: str,
    global_settings: Settings,
    kb_configs: Mapping[str, KnowledgeBaseAdapterConfig],
) -> MaterializationPlan:
    """文書の所属 KB 群とその KB アダプター設定から materialization 計画を作る。

    取込入口が呼ぶ想定の橋渡し。各 KB の取込上書きを ``apply_adapter_config_or_global``
    でグローバルへ重ねて effective 取込設定を求め(=取込パイプラインが使うのと同じ
    解決)、:func:`plan_materializations` に渡す。同じ effective 設定に解決される KB は
    同じ層を共有する(複製ゼロ)。設定が矛盾する KB はグローバルへ安全縮退する。
    """
    consumers: dict[str, Settings] = {}
    for kb_id, config in kb_configs.items():
        effective, _applied = apply_adapter_config_or_global(
            global_settings, config, scope="ingestion"
        )
        consumers[kb_id] = effective
    # owning 抽出 = グローバル設定の抽出。上限超過時の打ち切りで優先保持する。
    owning_extraction_id = compute_extraction_id(source_sha256, global_settings)
    return plan_materializations(
        source_sha256, consumers, owning_extraction_id=owning_extraction_id
    )


def diff_plan(existing_ids: frozenset[str], plan: MaterializationPlan) -> MaterializationDiff:
    """既存の materialization ID 集合と望ましい計画から、作成すべき/GC すべき ID を出す。

    * ``to_create``: 計画にあって既存にない層(新規 materialize)。
    * ``to_collect``: 既存にあって計画にない層(参照消失 → refcount 0 で GC)。
    """
    desired = plan.all_ids()
    return MaterializationDiff(
        to_create=frozenset(desired - existing_ids),
        to_collect=frozenset(existing_ids - desired),
    )


def _freeze(layer: dict[str, set[str]]) -> dict[str, frozenset[str]]:
    return {layer_id: frozenset(kb_ids) for layer_id, kb_ids in layer.items()}
