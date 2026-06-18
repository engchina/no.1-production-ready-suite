"""Agentic アダプター(LLM 補助のクエリ計画プロファイル)。

`vector_index_adapter.py` と同型で、選択されたクエリ計画モードと利用可能なプリセット一覧を
非機密の runtime snapshot として返す。off 以外は OCI Enterprise AI への追加呼び出しを伴うため、
明示 opt-in とする。既定 off は LLM 計画なし(現行挙動)。外部 LLM provider は導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import AgenticProfile, Settings

AgenticProfileName = AgenticProfile
DEFAULT_AGENTIC_PROFILE: AgenticProfileName = "off"
AGENTIC_PROFILE_ORDER: tuple[AgenticProfileName, ...] = (
    "off",
    "query_rewrite",
    "decompose",
    "multi_hop",
)


@dataclass(frozen=True)
class AgenticProfileSpec:
    """1 クエリ計画プロファイルの由来と挙動。"""

    name: AgenticProfileName
    origin: str
    recommended_for: tuple[str, ...]
    enabled: bool
    rewrite: bool
    decompose: bool
    multi_hop: bool


AGENTIC_ADAPTER_SPECS: dict[AgenticProfileName, AgenticProfileSpec] = {
    "off": AgenticProfileSpec(
        name="off",
        origin="disabled",
        recommended_for=("default", "low_cost"),
        enabled=False,
        rewrite=False,
        decompose=False,
        multi_hop=False,
    ),
    "query_rewrite": AgenticProfileSpec(
        name="query_rewrite",
        origin="query_rewriting",
        recommended_for=("noisy_query", "conversational"),
        enabled=True,
        rewrite=True,
        decompose=False,
        multi_hop=False,
    ),
    "decompose": AgenticProfileSpec(
        name="decompose",
        origin="sub_question_decomposition",
        recommended_for=("multi_part", "comparison"),
        enabled=True,
        rewrite=False,
        decompose=True,
        multi_hop=False,
    ),
    "multi_hop": AgenticProfileSpec(
        name="multi_hop",
        origin="iterative_rag",
        recommended_for=("multi_hop", "complex"),
        enabled=True,
        rewrite=False,
        decompose=True,
        multi_hop=True,
    ),
}


@dataclass(frozen=True)
class AgenticAdapterParams:
    """クエリ計画へ渡す解決済みパラメータ。"""

    profile: AgenticProfileName
    enabled: bool
    rewrite: bool
    decompose: bool
    multi_hop: bool
    max_subqueries: int


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
    normalized = str(value).casefold()
    if normalized in AGENTIC_ADAPTER_SPECS:
        return normalized
    return DEFAULT_AGENTIC_PROFILE


def resolve_agentic_adapter(settings: Settings) -> AgenticAdapterParams:
    """Settings から Agentic アダプターの解決済みパラメータを作る。"""
    profile = normalize_agentic_profile(
        getattr(settings, "rag_agentic_profile", DEFAULT_AGENTIC_PROFILE)
    )
    spec = AGENTIC_ADAPTER_SPECS[profile]
    return AgenticAdapterParams(
        profile=profile,
        enabled=spec.enabled,
        rewrite=spec.rewrite,
        decompose=spec.decompose,
        multi_hop=spec.multi_hop,
        max_subqueries=int(getattr(settings, "rag_agentic_max_subqueries", 3)),
    )


def agentic_adapter_runtime_settings(settings: Settings) -> AgenticAdapterRuntimeSettings:
    """Settings から Agentic アダプター readiness snapshot を作る。"""
    params = resolve_agentic_adapter(settings)
    statuses = tuple(
        AgenticProfileStatus(
            name=spec.name,
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.profile,
            enabled=spec.enabled,
            rewrite=spec.rewrite,
            decompose=spec.decompose,
            multi_hop=spec.multi_hop,
        )
        for spec in (AGENTIC_ADAPTER_SPECS[name] for name in AGENTIC_PROFILE_ORDER)
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
