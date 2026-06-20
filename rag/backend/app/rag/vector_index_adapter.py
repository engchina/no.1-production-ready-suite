"""Vector Index アダプター(索引/検索精度の手動選択プリセット)。

決定論ロジック・spec は共有パッケージ ``rag_pipeline_core.vector_index`` を単一ソースとして使い、
backend と vector_index マイクロサービスが同一結果を返す。`rag_vector_index_service_enabled` が
真のとき profile 解決を pipeline-vector-index サービスへ委譲し、未達/失敗時は in-process(同一
ロジック)へ安全縮退する。外部ベクトル DB は導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from rag_pipeline_core.vector_index import (
    DISTANCE,
    VECTOR_INDEX_PROFILES,
    VECTOR_INDEX_SPECS,
    profile_target_accuracy,
    resolve_vector_index,
)
from rag_pipeline_core.vector_index import (
    normalize_vector_index_profile as _core_normalize,
)

from app.config import Settings, VectorIndexProfile

VectorIndexProfileName = VectorIndexProfile
DEFAULT_VECTOR_INDEX_PROFILE: VectorIndexProfileName = "balanced"
VECTOR_INDEX_PROFILE_ORDER: tuple[VectorIndexProfileName, ...] = VECTOR_INDEX_PROFILES  # type: ignore[assignment]


@dataclass(frozen=True)
class VectorIndexParams:
    """ベクトル検索/索引へ渡す解決済みパラメータ。"""

    profile: VectorIndexProfileName
    target_accuracy: int
    neighbors: int
    efconstruction: int
    distance: str
    requires_reprovision: bool


@dataclass(frozen=True)
class VectorIndexProfileStatus:
    """1 索引プロファイルの選択状態と accuracy/build 推奨値。"""

    name: VectorIndexProfileName
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    target_accuracy: int
    neighbors: int
    efconstruction: int
    distance: str


@dataclass(frozen=True)
class VectorIndexAdapterRuntimeSettings:
    """Vector Index アダプターの非機密 runtime snapshot。"""

    profile: VectorIndexProfileName
    target_accuracy: int
    neighbors: int
    efconstruction: int
    distance: str
    requires_reprovision: bool
    profiles: tuple[VectorIndexProfileStatus, ...]


def normalize_vector_index_profile(value: object) -> VectorIndexProfileName:
    """未知のプロファイル名は既定 balanced へ寄せる。"""
    return _core_normalize(value)  # type: ignore[return-value]


def _settings_target_accuracy(settings: Settings) -> int:
    return int(getattr(settings, "oracle_vector_target_accuracy", 95))


def resolve_vector_index_adapter(settings: Settings) -> VectorIndexParams:
    """Settings から Vector Index アダプターの解決済みパラメータを作る。

    `rag_vector_index_service_enabled` のときは pipeline-vector-index サービスへ委譲し、
    未達/失敗時は in-process(同一 rag_pipeline_core ロジック)へ安全縮退する。
    """
    profile = normalize_vector_index_profile(
        getattr(settings, "rag_vector_index_profile", DEFAULT_VECTOR_INDEX_PROFILE)
    )
    settings_accuracy = _settings_target_accuracy(settings)
    remote = _resolve_remote(settings, profile, settings_accuracy)
    if remote is not None:
        return remote
    resolved = resolve_vector_index(profile, settings_accuracy)
    return VectorIndexParams(
        profile=resolved.profile,  # type: ignore[arg-type]
        target_accuracy=resolved.target_accuracy,
        neighbors=resolved.neighbors,
        efconstruction=resolved.efconstruction,
        distance=resolved.distance,
        requires_reprovision=resolved.requires_reprovision,
    )


def _resolve_remote(
    settings: Settings, profile: str, settings_accuracy: int
) -> VectorIndexParams | None:
    """サービス委譲が有効なら remote 解決する(未達/無効は None)。"""
    from rag_pipeline_core.stage import VectorIndexStageRequest

    from app.clients.pipeline_stage import PipelineStageClient

    client = PipelineStageClient(settings)
    if not client.is_enabled("vector_index"):
        return None
    response = client.run_vector_index(
        VectorIndexStageRequest(profile=profile, settings_target_accuracy=settings_accuracy)
    )
    if response is None:
        return None
    return VectorIndexParams(
        profile=response.profile,  # type: ignore[arg-type]
        target_accuracy=response.target_accuracy,
        neighbors=response.neighbors,
        efconstruction=response.efconstruction,
        distance=response.distance,
        requires_reprovision=response.requires_reprovision,
    )


def vector_index_adapter_runtime_settings(
    settings: Settings,
) -> VectorIndexAdapterRuntimeSettings:
    """Settings から Vector Index アダプター readiness snapshot を作る。"""
    params = resolve_vector_index_adapter(settings)
    settings_accuracy = _settings_target_accuracy(settings)
    statuses = tuple(
        VectorIndexProfileStatus(
            name=spec.name,  # type: ignore[arg-type]
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.profile,
            target_accuracy=profile_target_accuracy(spec, settings_accuracy),
            neighbors=spec.neighbors,
            efconstruction=spec.efconstruction,
            distance=DISTANCE,
        )
        for spec in (VECTOR_INDEX_SPECS[name] for name in VECTOR_INDEX_PROFILES)
    )
    return VectorIndexAdapterRuntimeSettings(
        profile=params.profile,
        target_accuracy=params.target_accuracy,
        neighbors=params.neighbors,
        efconstruction=params.efconstruction,
        distance=params.distance,
        requires_reprovision=params.requires_reprovision,
        profiles=statuses,
    )
