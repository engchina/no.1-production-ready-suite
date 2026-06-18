"""Vector Index アダプター(索引/検索精度の手動選択プリセット)。

`retrieval_adapter.py` と同型で、選択された Oracle 26ai AI Vector Search の
accuracy/latency プロファイルと利用可能なプリセット一覧を非機密の runtime snapshot として返す。
機能レバーは検索時 target accuracy(runtime 即時)で、HNSW ビルドパラメータは推奨値の表示に
留める(適用には索引再作成が必要)。外部ベクトル DB は導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, VectorIndexProfile

VectorIndexProfileName = VectorIndexProfile
DEFAULT_VECTOR_INDEX_PROFILE: VectorIndexProfileName = "balanced"
VECTOR_INDEX_PROFILE_ORDER: tuple[VectorIndexProfileName, ...] = (
    "balanced",
    "accurate",
    "fast",
)
# 現行 schema DDL のビルドパラメータ(balanced 基準)。
_CURRENT_NEIGHBORS = 32
_CURRENT_EFCONSTRUCTION = 500
_DISTANCE = "COSINE"


@dataclass(frozen=True)
class VectorIndexProfileSpec:
    """1 索引プロファイルの由来と accuracy/build 推奨値。"""

    name: VectorIndexProfileName
    origin: str
    recommended_for: tuple[str, ...]
    # None は balanced(既存 oracle_vector_target_accuracy を使う)を意味する。
    target_accuracy: int | None
    neighbors: int
    efconstruction: int


VECTOR_INDEX_ADAPTER_SPECS: dict[VectorIndexProfileName, VectorIndexProfileSpec] = {
    "balanced": VectorIndexProfileSpec(
        name="balanced",
        origin="current_hnsw_default",
        recommended_for=("general", "default"),
        target_accuracy=None,
        neighbors=_CURRENT_NEIGHBORS,
        efconstruction=_CURRENT_EFCONSTRUCTION,
    ),
    "accurate": VectorIndexProfileSpec(
        name="accurate",
        origin="high_recall",
        recommended_for=("compliance", "high_recall"),
        target_accuracy=98,
        neighbors=48,
        efconstruction=800,
    ),
    "fast": VectorIndexProfileSpec(
        name="fast",
        origin="low_latency",
        recommended_for=("low_latency", "interactive"),
        target_accuracy=85,
        neighbors=16,
        efconstruction=300,
    ),
}


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
    normalized = str(value).casefold()
    if normalized in VECTOR_INDEX_ADAPTER_SPECS:
        return normalized
    return DEFAULT_VECTOR_INDEX_PROFILE


def _settings_target_accuracy(settings: Settings) -> int:
    return int(getattr(settings, "oracle_vector_target_accuracy", 95))


def _profile_target_accuracy(spec: VectorIndexProfileSpec, settings: Settings) -> int:
    if spec.target_accuracy is None:
        return _settings_target_accuracy(settings)
    return spec.target_accuracy


def resolve_vector_index_adapter(settings: Settings) -> VectorIndexParams:
    """Settings から Vector Index アダプターの解決済みパラメータを作る。"""
    profile = normalize_vector_index_profile(
        getattr(settings, "rag_vector_index_profile", DEFAULT_VECTOR_INDEX_PROFILE)
    )
    spec = VECTOR_INDEX_ADAPTER_SPECS[profile]
    return VectorIndexParams(
        profile=profile,
        target_accuracy=_profile_target_accuracy(spec, settings),
        neighbors=spec.neighbors,
        efconstruction=spec.efconstruction,
        distance=_DISTANCE,
        # balanced 以外はビルドパラメータが現行 DDL と異なるため索引再作成が必要。
        requires_reprovision=(
            spec.neighbors != _CURRENT_NEIGHBORS
            or spec.efconstruction != _CURRENT_EFCONSTRUCTION
        ),
    )


def vector_index_adapter_runtime_settings(
    settings: Settings,
) -> VectorIndexAdapterRuntimeSettings:
    """Settings から Vector Index アダプター readiness snapshot を作る。"""
    params = resolve_vector_index_adapter(settings)
    statuses = tuple(
        VectorIndexProfileStatus(
            name=spec.name,
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.profile,
            target_accuracy=_profile_target_accuracy(spec, settings),
            neighbors=spec.neighbors,
            efconstruction=spec.efconstruction,
            distance=_DISTANCE,
        )
        for spec in (VECTOR_INDEX_ADAPTER_SPECS[name] for name in VECTOR_INDEX_PROFILE_ORDER)
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
