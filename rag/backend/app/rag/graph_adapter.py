"""関係情報の構築アダプター(構築する / しない)。

決定論ロジックは共有パッケージ ``rag_pipeline_core.graph`` を単一ソースとして使い、backend と
graphrag マイクロサービスが同一結果を返す。`rag_graph_service_enabled` が真のとき profile 解決を
pipeline-graphrag サービスへ委譲する。無効時は in-process(同一ロジック)、有効時の
未到達時も in-process へ縮退する。応答済み remote の HTTP error / 不正応答は処理停止する。

- ``off``(既定): 構築しない。
- ``entities``: 文書と章・節の見出しのつながり(entity + relationship)を構築する。LLM は使わない
  (``app/rag/graph_index.py``)。構築した関係情報はナレッジベースの「関係情報グラフ」で見るだけで、
  回答の検索には使わない(#595)。

claims / community summary まで作る ``full`` と legacy の ``RAG_GRAPH_ENABLED`` は、読む経路が
無かったため #621 で削除した。外部グラフ DB は導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from rag_pipeline_core.graph import resolve_graph_profile

from app.config import GraphProfile, Settings

GraphProfileName = GraphProfile
DEFAULT_GRAPH_PROFILE: GraphProfileName = "off"
GRAPH_PROFILE_ORDER: tuple[GraphProfileName, ...] = ("off", "entities")


@dataclass(frozen=True)
class GraphAdapterParams:
    """関係情報の構築へ渡す解決済みパラメータ。"""

    profile: GraphProfileName
    enabled: bool


@dataclass(frozen=True)
class GraphProfileStatus:
    """1 プロファイルの選択状態。"""

    name: GraphProfileName
    selected: bool


@dataclass(frozen=True)
class GraphAdapterRuntimeSettings:
    """関係情報の構築の非機密 runtime snapshot。"""

    profile: GraphProfileName
    enabled: bool
    profiles: tuple[GraphProfileStatus, ...]


def normalize_graph_profile(value: object) -> GraphProfileName:
    """未知のプロファイル名は既定 off へ寄せる。"""
    normalized = str(value).casefold()
    for name in GRAPH_PROFILE_ORDER:
        if normalized == name:
            return name
    return DEFAULT_GRAPH_PROFILE


def resolve_graph_adapter(settings: Settings) -> GraphAdapterParams:
    """Settings から関係情報の構築の解決済みパラメータを作る。

    `rag_graph_service_enabled` のときは pipeline-graphrag サービスへ委譲する。
    無効時と remote 未到達時は in-process(同一 rag_pipeline_core ロジック)へ縮退する。
    """
    profile = normalize_graph_profile(getattr(settings, "rag_graph_profile", DEFAULT_GRAPH_PROFILE))
    remote = _resolve_remote(settings, profile)
    if remote is not None:
        return remote
    resolved = resolve_graph_profile(profile)
    return GraphAdapterParams(
        profile=normalize_graph_profile(resolved.profile),
        enabled=resolved.build_entities,
    )


def _resolve_remote(settings: Settings, profile: str) -> GraphAdapterParams | None:
    """サービス委譲が有効なら remote 解決する(未達/無効は None)。"""
    from rag_pipeline_core.stage import GraphStageRequest

    from app.clients.pipeline_stage import PipelineStageClient

    client = PipelineStageClient(settings)
    if not client.is_enabled("graphrag"):
        return None
    response = client.run_graph(GraphStageRequest(profile=profile))
    if response is None:
        return None
    return GraphAdapterParams(
        profile=normalize_graph_profile(response.profile),
        enabled=response.build_entities,
    )


def graph_adapter_runtime_settings(settings: Settings) -> GraphAdapterRuntimeSettings:
    """Settings から関係情報の構築の snapshot を作る。"""
    params = resolve_graph_adapter(settings)
    return GraphAdapterRuntimeSettings(
        profile=params.profile,
        enabled=params.enabled,
        profiles=tuple(
            GraphProfileStatus(name=name, selected=name == params.profile)
            for name in GRAPH_PROFILE_ORDER
        ),
    )
