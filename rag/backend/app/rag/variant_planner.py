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
from app.rag.variant_keys import compute_layer_ids


@dataclass(frozen=True)
class MaterializationPlan:
    """文書 1 件の望ましい materialization 状態。

    各 dict は ``層 ID -> それを参照する kb_id 群``。refcount は値集合の要素数。
    """

    chunk_sets: dict[str, frozenset[str]]
    metadata_layers: dict[str, frozenset[str]]
    graph_layers: dict[str, frozenset[str]]
    nav_layers: dict[str, frozenset[str]]

    def all_ids(self) -> frozenset[str]:
        """全層の ID 集合(存在すべき materialization)。"""
        ids: set[str] = set()
        for layer in (self.chunk_sets, self.metadata_layers, self.graph_layers, self.nav_layers):
            ids.update(layer)
        return frozenset(ids)

    def refcount(self, layer_id: str) -> int:
        """指定層 ID の参照 KB 数(存在しなければ 0)。"""
        for layer in (self.chunk_sets, self.metadata_layers, self.graph_layers, self.nav_layers):
            if layer_id in layer:
                return len(layer[layer_id])
        return 0


@dataclass(frozen=True)
class MaterializationDiff:
    """既存状態から望ましい計画への差分。"""

    to_create: frozenset[str]
    to_collect: frozenset[str]


def plan_materializations(
    source_sha256: str,
    consumers: Mapping[str, Settings],
) -> MaterializationPlan:
    """各 KB の effective 取込設定から、共有を畳んだ materialization 計画を作る。

    ``consumers`` は ``kb_id -> effective ingestion settings``。同じ層 ID に解決される
    KB は同じ materialization を共有する(refcount = 参照 KB 数)。
    """
    chunk_sets: dict[str, set[str]] = defaultdict(set)
    metadata_layers: dict[str, set[str]] = defaultdict(set)
    graph_layers: dict[str, set[str]] = defaultdict(set)
    nav_layers: dict[str, set[str]] = defaultdict(set)

    for kb_id, settings in consumers.items():
        ids = compute_layer_ids(source_sha256, settings)
        chunk_sets[ids["chunk_set_id"]].add(kb_id)
        metadata_layers[ids["metadata_layer_id"]].add(kb_id)
        graph_layers[ids["graph_layer_id"]].add(kb_id)
        nav_layers[ids["nav_layer_id"]].add(kb_id)

    return MaterializationPlan(
        chunk_sets=_freeze(chunk_sets),
        metadata_layers=_freeze(metadata_layers),
        graph_layers=_freeze(graph_layers),
        nav_layers=_freeze(nav_layers),
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
