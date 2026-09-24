"""GraphRAG プロファイルの決定論解決(backend / サービス共有)。

profile(off/entities/full)→ KG 構築フラグを決定論で解決する。legacy `rag_graph_enabled=True`
は off でも full 相当(後方互換)。Temporal GraphRAG(entity/relationship の timestamp 付与)は
``temporal`` フラグで full に時間次元を足す strategy。Settings 非依存(素の値で受け渡す)。
"""

from __future__ import annotations

from dataclasses import dataclass

GRAPH_PROFILES: tuple[str, ...] = ("off", "entities", "full")
DEFAULT_GRAPH_PROFILE = "off"


@dataclass(frozen=True)
class GraphResolved:
    """解決済み KG 構築フラグ。"""

    profile: str
    build_entities: bool
    build_relationships: bool
    build_claims: bool
    build_community_summary: bool
    temporal: bool


def normalize_graph_profile(value: object) -> str:
    normalized = str(value).casefold()
    return normalized if normalized in GRAPH_PROFILES else DEFAULT_GRAPH_PROFILE


def resolve_graph_profile(
    profile: object, *, legacy_enabled: bool = False, temporal: bool = False
) -> GraphResolved:
    """profile + legacy/temporal フラグから KG 構築フラグを解決する。"""
    name = normalize_graph_profile(profile)
    if name == "off" and legacy_enabled:
        name = "full"
    build_entities = name in {"entities", "full"}
    build_relationships = build_entities
    build_claims = name == "full"
    build_community_summary = name == "full"
    return GraphResolved(
        profile=name,
        build_entities=build_entities,
        build_relationships=build_relationships,
        build_claims=build_claims,
        build_community_summary=build_community_summary,
        # temporal は full のときだけ意味を持つ(timestamp 付与)。
        temporal=bool(temporal and name == "full"),
    )
