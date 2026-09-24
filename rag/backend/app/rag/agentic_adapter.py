"""Agentic アダプター(LLM 補助のクエリ計画プロファイル)。

profile→クエリ計画の挙動フラグの静的解決は共有パッケージ ``rag_pipeline_core.agentic`` を単一
ソースとして使い、backend と agentic マイクロサービスが同一結果を返す。
`rag_agentic_service_enabled` が真のとき静的解決を pipeline-agentic サービスへ委譲する。無効時は
in-process(同一ロジック)、remote 未到達時も in-process へ縮退する。応答済み remote の
HTTP error / 不正応答は処理停止する。max_subqueries は backend
設定由来のため解決後に上乗せ。off 以外は OCI Enterprise AI への追加呼び出しを伴う opt-in。
外部 LLM provider は導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from rag_pipeline_core.agentic import (
    AGENTIC_PROFILES,
    AGENTIC_SPECS,
    resolve_agentic,
)
from rag_pipeline_core.agentic import (
    normalize_agentic_profile as _core_normalize,
)

from app.config import AgenticProfile, Settings

AgenticProfileName = AgenticProfile
DEFAULT_AGENTIC_PROFILE: AgenticProfileName = "off"
AGENTIC_PROFILE_ORDER: tuple[AgenticProfileName, ...] = AGENTIC_PROFILES  # type: ignore[assignment]


@dataclass(frozen=True)
class AgenticAdapterParams:
    """クエリ計画へ渡す解決済みパラメータ。"""

    profile: AgenticProfileName
    enabled: bool
    rewrite: bool
    decompose: bool
    multi_hop: bool
    max_subqueries: int
    smart_routing: bool = False
    hyde: bool = False


@dataclass(frozen=True)
class AgenticProfileStatus:
    """1 クエリ計画プロファイルの選択状態と挙動。"""

    name: AgenticProfileName
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    enabled: bool
    rewrite: bool
    decompose: bool
    multi_hop: bool
    hyde: bool = False


@dataclass(frozen=True)
class AgenticAdapterRuntimeSettings:
    """Agentic アダプターの非機密 runtime snapshot。"""

    profile: AgenticProfileName
    enabled: bool
    rewrite: bool
    decompose: bool
    multi_hop: bool
    max_subqueries: int
    profiles: tuple[AgenticProfileStatus, ...]


def normalize_agentic_profile(value: object) -> AgenticProfileName:
    """未知のプロファイル名は既定 off へ寄せる。"""
    return _core_normalize(value)  # type: ignore[return-value]


def resolve_agentic_adapter(settings: Settings) -> AgenticAdapterParams:
    """Settings から Agentic アダプターの解決済みパラメータを作る。

    静的な挙動フラグは core / サービスで解決し、max_subqueries を backend 設定から上乗せする。
    """
    profile = normalize_agentic_profile(
        getattr(settings, "rag_agentic_profile", DEFAULT_AGENTIC_PROFILE)
    )
    resolved = _resolve_static(settings, profile)
    return AgenticAdapterParams(
        profile=profile,
        enabled=resolved.enabled,
        rewrite=resolved.rewrite,
        decompose=resolved.decompose,
        multi_hop=resolved.multi_hop,
        smart_routing=resolved.smart_routing,
        hyde=resolved.hyde,
        max_subqueries=int(getattr(settings, "rag_agentic_max_subqueries", 3)),
    )


def _resolve_static(settings: Settings, profile: str):  # type: ignore[no-untyped-def]
    """静的な挙動フラグを service opt-in + disabled 時 in-process で解決する。"""
    from rag_pipeline_core.agentic import AgenticResolved
    from rag_pipeline_core.stage import AgenticStageRequest

    from app.clients.pipeline_stage import PipelineStageClient

    client = PipelineStageClient(settings)
    if client.is_enabled("agentic"):
        response = client.run_agentic(AgenticStageRequest(profile=profile))
        if response is not None:
            return AgenticResolved(
                profile=response.profile,
                enabled=response.enabled,
                rewrite=response.rewrite,
                decompose=response.decompose,
                multi_hop=response.multi_hop,
                smart_routing=response.smart_routing,
                hyde=response.hyde,
            )
    return resolve_agentic(profile)


def agentic_adapter_runtime_settings(settings: Settings) -> AgenticAdapterRuntimeSettings:
    """Settings から Agentic アダプター readiness snapshot を作る。"""
    params = resolve_agentic_adapter(settings)
    statuses = tuple(
        AgenticProfileStatus(
            name=spec.name,  # type: ignore[arg-type]
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.profile,
            enabled=spec.enabled,
            rewrite=spec.rewrite,
            decompose=spec.decompose,
            multi_hop=spec.multi_hop,
            hyde=spec.hyde,
        )
        for spec in (AGENTIC_SPECS[name] for name in AGENTIC_PROFILES)
    )
    return AgenticAdapterRuntimeSettings(
        profile=params.profile,
        enabled=params.enabled,
        rewrite=params.rewrite,
        decompose=params.decompose,
        multi_hop=params.multi_hop,
        max_subqueries=params.max_subqueries,
        profiles=statuses,
    )
