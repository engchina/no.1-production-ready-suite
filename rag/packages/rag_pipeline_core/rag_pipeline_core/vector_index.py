"""Vector Index プロファイルの決定論解決(backend / サービス共有)。

profile(balanced/accurate/fast)→ Oracle 26ai AI Vector Search の target accuracy + HNSW
ビルド推奨値を決定論で解決する。Settings 依存を持たず、balanced の target accuracy は
呼び出し側から ``settings_target_accuracy`` で渡す(backend と service が同一結果を返す)。
"""

from __future__ import annotations

from dataclasses import dataclass

VECTOR_INDEX_PROFILES: tuple[str, ...] = ("balanced", "accurate", "fast")
DEFAULT_VECTOR_INDEX_PROFILE = "balanced"

# 現行 schema DDL のビルドパラメータ(balanced 基準)。
CURRENT_NEIGHBORS = 32
CURRENT_EFCONSTRUCTION = 500
DISTANCE = "COSINE"


@dataclass(frozen=True)
class VectorIndexSpec:
    """1 索引プロファイルの由来と accuracy/build 推奨値。"""

    name: str
    origin: str
    recommended_for: tuple[str, ...]
    # None は balanced(呼び出し側の settings_target_accuracy を使う)を意味する。
    target_accuracy: int | None
    neighbors: int
    efconstruction: int


VECTOR_INDEX_SPECS: dict[str, VectorIndexSpec] = {
    "balanced": VectorIndexSpec(
        name="balanced",
        origin="current_hnsw_default",
        recommended_for=("general", "default"),
        target_accuracy=None,
        neighbors=CURRENT_NEIGHBORS,
        efconstruction=CURRENT_EFCONSTRUCTION,
    ),
    "accurate": VectorIndexSpec(
        name="accurate",
        origin="high_recall",
        recommended_for=("compliance", "high_recall"),
        target_accuracy=98,
        neighbors=48,
        efconstruction=800,
    ),
    "fast": VectorIndexSpec(
        name="fast",
        origin="low_latency",
        recommended_for=("low_latency", "interactive"),
        target_accuracy=85,
        neighbors=16,
        efconstruction=300,
    ),
}


@dataclass(frozen=True)
class VectorIndexResolved:
    """解決済みパラメータ(wire/内部共通の素の値)。"""

    profile: str
    target_accuracy: int
    neighbors: int
    efconstruction: int
    distance: str
    requires_reprovision: bool


def normalize_vector_index_profile(value: object) -> str:
    normalized = str(value).casefold()
    return normalized if normalized in VECTOR_INDEX_SPECS else DEFAULT_VECTOR_INDEX_PROFILE


def profile_target_accuracy(spec: VectorIndexSpec, settings_target_accuracy: int) -> int:
    return spec.target_accuracy if spec.target_accuracy is not None else settings_target_accuracy


def resolve_vector_index(profile: object, settings_target_accuracy: int) -> VectorIndexResolved:
    """profile + balanced 用 target accuracy から解決済みパラメータを返す。"""
    name = normalize_vector_index_profile(profile)
    spec = VECTOR_INDEX_SPECS[name]
    return VectorIndexResolved(
        profile=name,
        target_accuracy=profile_target_accuracy(spec, settings_target_accuracy),
        neighbors=spec.neighbors,
        efconstruction=spec.efconstruction,
        distance=DISTANCE,
        requires_reprovision=(
            spec.neighbors != CURRENT_NEIGHBORS or spec.efconstruction != CURRENT_EFCONSTRUCTION
        ),
    )
