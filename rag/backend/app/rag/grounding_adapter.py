"""Grounding アダプター(検索後処理の手動選択プリセット)。

PDF Step5/6(取得結果を検証し根拠化する)に対応する検索後処理段を束ねる。dedupe /
Resolver-Verifier / Context Builder は常時実行で、任意段(dependency promotion / MMR
diversity / context expansion / compression)を preset で選ぶ。`custom` のときだけ既存の
`rag_context_*` フラグをそのまま使い、後方互換を保つ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.config import PostRetrievalPipeline, Settings

PostRetrievalPipelineName = PostRetrievalPipeline
ExpansionMode = Literal["none", "neighbor", "group", "adaptive"]
DEFAULT_POST_RETRIEVAL_PIPELINE: PostRetrievalPipelineName = "custom"
GROUNDING_PIPELINE_ORDER: tuple[PostRetrievalPipelineName, ...] = (
    "custom",
    "lean",
    "verified_context",
    "context_enrich",
    "compact",
    "full_governed",
)
_PRESET_DIVERSITY_LAMBDA = 0.7


@dataclass(frozen=True)
class GroundingPipelineSpec:
    """1 検索後処理プリセットの由来と束ねる段。"""

    name: PostRetrievalPipelineName
    origin: str
    recommended_for: tuple[str, ...]
    dependency_promotion: bool = False
    diversity: bool = False
    expansion_mode: ExpansionMode = "none"
    compression: bool = False


GROUNDING_ADAPTER_SPECS: dict[PostRetrievalPipelineName, GroundingPipelineSpec] = {
    "custom": GroundingPipelineSpec(
        name="custom",
        origin="legacy_flags",
        recommended_for=("advanced", "manual"),
    ),
    "lean": GroundingPipelineSpec(
        name="lean",
        origin="verify_only",
        recommended_for=("low_latency", "simple"),
    ),
    "verified_context": GroundingPipelineSpec(
        name="verified_context",
        origin="aidb_step5_6",
        recommended_for=("general", "balanced"),
        diversity=True,
    ),
    "context_enrich": GroundingPipelineSpec(
        name="context_enrich",
        origin="scar_m3docdep",
        recommended_for=("multi_page", "dependency"),
        dependency_promotion=True,
        diversity=True,
        expansion_mode="adaptive",
    ),
    "compact": GroundingPipelineSpec(
        name="compact",
        origin="contextual_compression",
        recommended_for=("token_budget", "long_context"),
        diversity=True,
        compression=True,
    ),
    "full_governed": GroundingPipelineSpec(
        name="full_governed",
        origin="aidb_full_governance",
        recommended_for=("compliance", "max_quality"),
        dependency_promotion=True,
        diversity=True,
        expansion_mode="adaptive",
        compression=True,
    ),
}


@dataclass(frozen=True)
class GroundingAdapterParams:
    """検索後処理段へ渡す解決済み effective パラメータ。"""

    pipeline: PostRetrievalPipelineName
    dependency_promotion_enabled: bool
    diversity_lambda: float
    expansion_mode: ExpansionMode
    neighbor_expansion_enabled: bool
    compression_enabled: bool

    @property
    def diversity_enabled(self) -> bool:
        return self.diversity_lambda < 1.0


@dataclass(frozen=True)
class GroundingPipelineStatus:
    """1 検索後処理プリセットの選択状態と束ねる段。"""

    name: PostRetrievalPipelineName
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    dependency_promotion: bool
    diversity: bool
    expansion_mode: ExpansionMode
    compression: bool


@dataclass(frozen=True)
class GroundingAdapterRuntimeSettings:
    """Grounding アダプターの非機密 runtime snapshot。"""

    pipeline: PostRetrievalPipelineName
    dependency_promotion_enabled: bool
    diversity_enabled: bool
    expansion_mode: ExpansionMode
    compression_enabled: bool
    pipelines: tuple[GroundingPipelineStatus, ...]


def normalize_post_retrieval_pipeline(value: object) -> PostRetrievalPipelineName:
    """未知のプリセット名は既定 custom へ寄せる。"""
    normalized = str(value).casefold()
    if normalized in GROUNDING_ADAPTER_SPECS:
        return normalized
    return DEFAULT_POST_RETRIEVAL_PIPELINE


def _settings_expansion_mode(settings: Settings) -> ExpansionMode:
    """既存フラグから custom の第一段 expansion mode(adaptive/group/none)を再現する。

    neighbor は original では adaptive 以外のとき独立に併走するため別フラグで扱う。
    """
    if bool(getattr(settings, "rag_context_adaptive_expansion_enabled", False)):
        return "adaptive"
    if bool(getattr(settings, "rag_context_group_expansion_enabled", False)):
        return "group"
    return "none"


def _settings_neighbor_enabled(settings: Settings) -> bool:
    """original の `not adaptive and neighbor_window > 0` を再現する。"""
    if bool(getattr(settings, "rag_context_adaptive_expansion_enabled", False)):
        return False
    return int(getattr(settings, "rag_context_neighbor_window", 0)) > 0


def resolve_grounding_adapter(settings: Settings) -> GroundingAdapterParams:
    """Settings から Grounding アダプターの effective パラメータを作る。"""
    pipeline = normalize_post_retrieval_pipeline(
        getattr(settings, "rag_post_retrieval_pipeline", DEFAULT_POST_RETRIEVAL_PIPELINE)
    )
    if pipeline == "custom":
        return GroundingAdapterParams(
            pipeline="custom",
            dependency_promotion_enabled=bool(
                getattr(settings, "rag_context_dependency_promotion_enabled", False)
            ),
            diversity_lambda=float(getattr(settings, "rag_context_diversity_lambda", 1.0)),
            expansion_mode=_settings_expansion_mode(settings),
            neighbor_expansion_enabled=_settings_neighbor_enabled(settings),
            compression_enabled=bool(getattr(settings, "rag_context_compression_enabled", False)),
        )
    spec = GROUNDING_ADAPTER_SPECS[pipeline]
    return GroundingAdapterParams(
        pipeline=pipeline,
        dependency_promotion_enabled=spec.dependency_promotion,
        diversity_lambda=_PRESET_DIVERSITY_LAMBDA if spec.diversity else 1.0,
        expansion_mode=spec.expansion_mode,
        neighbor_expansion_enabled=False,
        compression_enabled=spec.compression,
    )


def grounding_adapter_runtime_settings(settings: Settings) -> GroundingAdapterRuntimeSettings:
    """Settings から Grounding アダプター readiness snapshot を作る。"""
    params = resolve_grounding_adapter(settings)
    statuses = tuple(
        GroundingPipelineStatus(
            name=spec.name,
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.pipeline,
            dependency_promotion=spec.dependency_promotion,
            diversity=spec.diversity,
            expansion_mode=spec.expansion_mode,
            compression=spec.compression,
        )
        for spec in (GROUNDING_ADAPTER_SPECS[name] for name in GROUNDING_PIPELINE_ORDER)
    )
    return GroundingAdapterRuntimeSettings(
        pipeline=params.pipeline,
        dependency_promotion_enabled=params.dependency_promotion_enabled,
        diversity_enabled=params.diversity_enabled,
        expansion_mode=params.expansion_mode,
        compression_enabled=params.compression_enabled,
        pipelines=statuses,
    )
