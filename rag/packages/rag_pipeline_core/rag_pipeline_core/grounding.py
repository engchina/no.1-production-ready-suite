"""Grounding(検索後処理)プリセットの決定論解決(backend / サービス共有)。

preset → 検索後処理段のフラグ(dependency_promotion / diversity / expansion_mode / compression)を
決定論で解決する。custom は backend の legacy `rag_context_*` 設定をそのまま使うため backend 側で
処理し、本 core は preset の静的解決だけを担う。Settings 非依存。外部依存なし。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExpansionMode = Literal["none", "neighbor", "group", "adaptive"]
GROUNDING_PIPELINES: tuple[str, ...] = (
    "custom",
    "lean",
    "verified_context",
    "context_enrich",
    "compact",
    "full_governed",
)
DEFAULT_GROUNDING_PIPELINE = "custom"
PRESET_DIVERSITY_LAMBDA = 0.7


@dataclass(frozen=True)
class GroundingSpec:
    name: str
    origin: str
    recommended_for: tuple[str, ...]
    dependency_promotion: bool = False
    diversity: bool = False
    expansion_mode: ExpansionMode = "none"
    compression: bool = False
    # CRAG 的 confidence-based corrective retrieval(verified_context/full_governed)。
    corrective: bool = False


GROUNDING_SPECS: dict[str, GroundingSpec] = {
    "custom": GroundingSpec("custom", "legacy_flags", ("advanced", "manual")),
    "lean": GroundingSpec("lean", "verify_only", ("low_latency", "simple")),
    "verified_context": GroundingSpec(
        "verified_context",
        "aidb_step5_6",
        ("general", "balanced"),
        diversity=True,
        corrective=True,
    ),
    "context_enrich": GroundingSpec(
        "context_enrich",
        "scar_m3docdep",
        ("multi_page", "dependency"),
        dependency_promotion=True,
        diversity=True,
        expansion_mode="adaptive",
    ),
    "compact": GroundingSpec(
        "compact",
        "contextual_compression",
        ("token_budget", "long_context"),
        diversity=True,
        compression=True,
    ),
    "full_governed": GroundingSpec(
        "full_governed",
        "aidb_full_governance",
        ("compliance", "max_quality"),
        dependency_promotion=True,
        diversity=True,
        expansion_mode="adaptive",
        compression=True,
        corrective=True,
    ),
}


@dataclass(frozen=True)
class GroundingResolved:
    pipeline: str
    dependency_promotion: bool
    diversity: bool
    expansion_mode: ExpansionMode
    compression: bool
    corrective: bool


def normalize_grounding_pipeline(value: object) -> str:
    normalized = str(value).casefold()
    return normalized if normalized in GROUNDING_SPECS else DEFAULT_GROUNDING_PIPELINE


def resolve_grounding(pipeline: object) -> GroundingResolved:
    """preset から検索後処理段フラグを解決する(custom は backend 側で legacy 設定を使う)。"""
    name = normalize_grounding_pipeline(pipeline)
    spec = GROUNDING_SPECS[name]
    return GroundingResolved(
        pipeline=name,
        dependency_promotion=spec.dependency_promotion,
        diversity=spec.diversity,
        expansion_mode=spec.expansion_mode,
        compression=spec.compression,
        corrective=spec.corrective,
    )
