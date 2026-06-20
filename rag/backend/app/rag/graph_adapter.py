"""GraphRAG アダプター(知識グラフ構築の深さプロファイル)。

決定論ロジックは共有パッケージ ``rag_pipeline_core.graph`` を単一ソースとして使い、backend と
graphrag マイクロサービスが同一結果を返す。`rag_graph_service_enabled` が真のとき profile 解決を
pipeline-graphrag サービスへ委譲し、未達/失敗時は in-process(同一ロジック)へ安全縮退する。
legacy `rag_graph_enabled=True` は full 相当(後方互換)。Temporal GraphRAG は
`rag_graph_temporal_enabled`(full のとき timestamp 付与)。外部グラフ DB は導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from rag_pipeline_core.graph import resolve_graph_profile

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
    temporal: bool = False


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
    temporal: bool
    profiles: tuple[GraphProfileStatus, ...]


def normalize_graph_profile(value: object) -> GraphProfileName:
    """未知のプロファイル名は既定 off へ寄せる。"""
    normalized = str(value).casefold()
    if normalized in GRAPH_ADAPTER_SPECS:
        return normalized
    return DEFAULT_GRAPH_PROFILE


def resolve_graph_adapter(settings: Settings) -> GraphAdapterParams:
    """Settings から GraphRAG アダプターの解決済みパラメータを作る。

    `rag_graph_service_enabled` のときは pipeline-graphrag サービスへ委譲し、未達/失敗時は
    in-process(同一 rag_pipeline_core ロジック)へ安全縮退する。
    """
    profile = normalize_graph_profile(
        getattr(settings, "rag_graph_profile", DEFAULT_GRAPH_PROFILE)
    )
    legacy_enabled = bool(getattr(settings, "rag_graph_enabled", False))
    temporal = bool(getattr(settings, "rag_graph_temporal_enabled", False))
    remote = _resolve_remote(settings, profile, legacy_enabled, temporal)
    if remote is not None:
        return remote
    resolved = resolve_graph_profile(profile, legacy_enabled=legacy_enabled, temporal=temporal)
    return GraphAdapterParams(
        profile=resolved.profile,  # type: ignore[arg-type]
        enabled=resolved.build_entities,
        build_claims=resolved.build_claims,
        build_community_summaries=resolved.build_community_summary,
        temporal=resolved.temporal,
    )


def _resolve_remote(
    settings: Settings, profile: str, legacy_enabled: bool, temporal: bool
) -> GraphAdapterParams | None:
    """サービス委譲が有効なら remote 解決する(未達/無効は None)。"""
    from rag_pipeline_core.stage import GraphStageRequest

    from app.clients.pipeline_stage import PipelineStageClient

    client = PipelineStageClient(settings)
    if not client.is_enabled("graphrag"):
        return None
    response = client.run_graph(
        GraphStageRequest(profile=profile, legacy_enabled=legacy_enabled)
    )
    if response is None:
        return None
    return GraphAdapterParams(
        profile=response.profile,  # type: ignore[arg-type]
        enabled=response.build_entities,
        build_claims=response.build_claims,
        build_community_summaries=response.build_community_summary,
        # temporal はサービス未対応版でも backend 設定を尊重する。
        temporal=bool(temporal and response.profile == "full"),
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
        temporal=params.temporal,
        profiles=statuses,
    )
