"""Grounding アダプター(検索後処理の手動選択プリセット)。

preset→検索後処理段フラグの静的解決は共有パッケージ ``rag_pipeline_core.grounding`` を単一ソース
として使い、backend と grounding マイクロサービスが同一結果を返す。`rag_grounding_service_enabled`
が真のとき preset 解決を pipeline-grounding サービスへ委譲する。無効時は in-process(同一
ロジック)、有効時の未達/失敗は処理停止する。`custom` は backend の legacy `rag_context_*`
設定をそのまま使う(後方互換)。CRAG 的 corrective(confidence-based)は
verified_context/full_governed で surface する。
"""

from __future__ import annotations

from dataclasses import dataclass

from rag_pipeline_core.grounding import (
    GROUNDING_PIPELINES,
    GROUNDING_SPECS,
    PRESET_DIVERSITY_LAMBDA,
    ExpansionMode,
    resolve_grounding,
)
from rag_pipeline_core.grounding import (
    normalize_grounding_pipeline as _core_normalize,
)

from app.config import PostRetrievalPipeline, Settings

PostRetrievalPipelineName = PostRetrievalPipeline
DEFAULT_POST_RETRIEVAL_PIPELINE: PostRetrievalPipelineName = "custom"
GROUNDING_PIPELINE_ORDER: tuple[PostRetrievalPipelineName, ...] = GROUNDING_PIPELINES  # type: ignore[assignment]


@dataclass(frozen=True)
class GroundingAdapterParams:
    """検索後処理段へ渡す解決済み effective パラメータ。"""

    pipeline: PostRetrievalPipelineName
    dependency_promotion_enabled: bool
    diversity_lambda: float
    expansion_mode: ExpansionMode
    neighbor_expansion_enabled: bool
    compression_enabled: bool
    corrective_enabled: bool = False

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
    return _core_normalize(value)  # type: ignore[return-value]


def _settings_expansion_mode(settings: Settings) -> ExpansionMode:
    """既存フラグから custom の第一段 expansion mode(adaptive/group/none)を再現する。"""
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
    resolved = _resolve_static(settings, pipeline)
    return GroundingAdapterParams(
        pipeline=pipeline,
        dependency_promotion_enabled=resolved.dependency_promotion,
        diversity_lambda=PRESET_DIVERSITY_LAMBDA if resolved.diversity else 1.0,
        expansion_mode=resolved.expansion_mode,
        neighbor_expansion_enabled=False,
        compression_enabled=resolved.compression,
        corrective_enabled=resolved.corrective,
    )


def _resolve_static(settings: Settings, pipeline: str):  # type: ignore[no-untyped-def]
    """preset の静的解決を service opt-in + disabled 時 in-process で行う。"""
    from rag_pipeline_core.grounding import GroundingResolved
    from rag_pipeline_core.stage import GroundingStageRequest

    from app.clients.pipeline_stage import PipelineStageClient

    client = PipelineStageClient(settings)
    if client.is_enabled("grounding"):
        response = client.run_grounding(GroundingStageRequest(pipeline=pipeline))
        if response is not None:
            return GroundingResolved(
                pipeline=response.pipeline,
                dependency_promotion=response.dependency_promotion,
                diversity=response.diversity,
                expansion_mode=response.expansion_mode,  # type: ignore[arg-type]
                compression=response.compression,
                corrective=response.corrective,
            )
    return resolve_grounding(pipeline)


def grounding_adapter_runtime_settings(settings: Settings) -> GroundingAdapterRuntimeSettings:
    """Settings から Grounding アダプター readiness snapshot を作る。"""
    params = resolve_grounding_adapter(settings)
    statuses = tuple(
        GroundingPipelineStatus(
            name=spec.name,  # type: ignore[arg-type]
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.pipeline,
            dependency_promotion=spec.dependency_promotion,
            diversity=spec.diversity,
            expansion_mode=spec.expansion_mode,
            compression=spec.compression,
        )
        for spec in (GROUNDING_SPECS[name] for name in GROUNDING_PIPELINES)
    )
    return GroundingAdapterRuntimeSettings(
        pipeline=params.pipeline,
        dependency_promotion_enabled=params.dependency_promotion_enabled,
        diversity_enabled=params.diversity_enabled,
        expansion_mode=params.expansion_mode,
        compression_enabled=params.compression_enabled,
        pipelines=statuses,
    )
