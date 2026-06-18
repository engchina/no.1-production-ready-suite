"""GraphRAG アダプター(知識グラフ構築の深さプロファイル)。

`vector_index_adapter.py` と同型で、選択された KG 構築プロファイルと利用可能なプリセット一覧を
非機密の runtime snapshot として返す。ビルド側(ingest)の構築深度を決め、検索側 routing は
Retrieval アダプターの `graph_augmented` が担う(両者は合成)。外部グラフ DB は導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import GraphProfile, Settings

GraphProfileName = GraphProfile
DEFAULT_GRAPH_PROFILE: GraphProfileName = "off"
GRAPH_PROFILE_ORDER: tuple[GraphProfileName, ...] = ("off", "entities", "full")


@dataclass(frozen=True)
class GraphProfileSpec:
    """1 KG 構築プロファイルの由来と構築深度。"""

    name: GraphProfileName
    origin: str
    recommended_for: tuple[str, ...]
    enabled: bool
    build_claims: bool
    build_community_summaries: bool


GRAPH_ADAPTER_SPECS: dict[GraphProfileName, GraphProfileSpec] = {
    "off": GraphProfileSpec(
        name="off",
        origin="disabled",
        recommended_for=("default", "simple"),
        enabled=False,
        build_claims=False,
        build_community_summaries=False,
    ),
    "entities": GraphProfileSpec(
        name="entities",
        origin="lightweight_kg",
        recommended_for=("relationship", "lightweight"),
        enabled=True,
        build_claims=False,
        build_community_summaries=False,
    ),
    "full": GraphProfileSpec(
        name="full",
        origin="graphrag_community",
        recommended_for=("summary", "cross_document"),
        enabled=True,
        build_claims=True,
        build_community_summaries=True,
    ),
}


@dataclass(frozen=True)
class GraphAdapterParams:
    """KG 構築へ渡す解決済みパラメータ。"""

    profile: GraphProfileName
    enabled: bool
    build_claims: bool
    build_community_summaries: bool


@dataclass(frozen=True)
class GraphProfileStatus:
    """1 KG 構築プロファイルの選択状態と構築深度。"""

    name: GraphProfileName
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    enabled: bool
    build_claims: bool
    build_community_summaries: bool


@dataclass(frozen=True)
class GraphAdapterRuntimeSettings:
    """GraphRAG アダプターの非機密 runtime snapshot。"""

    profile: GraphProfileName
    enabled: bool
    build_claims: bool
    build_community_summaries: bool
    profiles: tuple[GraphProfileStatus, ...]


def normalize_graph_profile(value: object) -> GraphProfileName:
    """未知のプロファイル名は既定 off へ寄せる。"""
    normalized = str(value).casefold()
    if normalized in GRAPH_ADAPTER_SPECS:
        return normalized
    return DEFAULT_GRAPH_PROFILE


def resolve_graph_adapter(settings: Settings) -> GraphAdapterParams:
    """Settings から GraphRAG アダプターの解決済みパラメータを作る。

    legacy の `rag_graph_enabled=True` は profile が off でも full 相当として扱い、後方互換を保つ。
    """
    profile = normalize_graph_profile(
        getattr(settings, "rag_graph_profile", DEFAULT_GRAPH_PROFILE)
    )
    spec = GRAPH_ADAPTER_SPECS[profile]
    legacy_enabled = bool(getattr(settings, "rag_graph_enabled", False))
    if profile == "off" and legacy_enabled:
        full = GRAPH_ADAPTER_SPECS["full"]
        return GraphAdapterParams(
            profile="full",
            enabled=True,
            build_claims=full.build_claims,
            build_community_summaries=full.build_community_summaries,
        )
    return GraphAdapterParams(
        profile=profile,
        enabled=spec.enabled,
        build_claims=spec.build_claims,
        build_community_summaries=spec.build_community_summaries,
    )


def graph_adapter_runtime_settings(settings: Settings) -> GraphAdapterRuntimeSettings:
    """Settings から GraphRAG アダプター readiness snapshot を作る。"""
    params = resolve_graph_adapter(settings)
    statuses = tuple(
        GraphProfileStatus(
            name=spec.name,
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.profile,
            enabled=spec.enabled,
            build_claims=spec.build_claims,
            build_community_summaries=spec.build_community_summaries,
        )
        for spec in (GRAPH_ADAPTER_SPECS[name] for name in GRAPH_PROFILE_ORDER)
    )
    return GraphAdapterRuntimeSettings(
        profile=params.profile,
        enabled=params.enabled,
        build_claims=params.build_claims,
        build_community_summaries=params.build_community_summaries,
        profiles=statuses,
    )
