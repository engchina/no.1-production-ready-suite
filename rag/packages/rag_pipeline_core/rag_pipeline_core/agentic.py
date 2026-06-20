"""Agentic クエリ計画プロファイルの決定論解決(backend / サービス共有)。

profile → クエリ計画の挙動フラグ(rewrite/decompose/multi_hop/smart_routing)を決定論で解決する。
off 以外は OCI Enterprise AI への追加呼び出しを伴うため明示 opt-in。max_subqueries は backend
設定由来のため backend 側で上乗せする。Settings 非依存。外部 LLM provider は導入しない。

smart_routing(v1): query を LLM で理解・書き換えして検索向けに正規化する(query-type aware
routing の入口)。現状は query_rewrite 同等の LLM 計画経路を使う。
"""

from __future__ import annotations

from dataclasses import dataclass

AGENTIC_PROFILES: tuple[str, ...] = (
    "off",
    "smart_routing",
    "query_rewrite",
    "hyde",
    "decompose",
    "multi_hop",
)
DEFAULT_AGENTIC_PROFILE = "off"


@dataclass(frozen=True)
class AgenticSpec:
    name: str
    origin: str
    recommended_for: tuple[str, ...]
    enabled: bool
    rewrite: bool
    decompose: bool
    multi_hop: bool
    smart_routing: bool = False
    hyde: bool = False


AGENTIC_SPECS: dict[str, AgenticSpec] = {
    "off": AgenticSpec("off", "disabled", ("default", "low_cost"), False, False, False, False),
    "smart_routing": AgenticSpec(
        "smart_routing",
        "query_type_aware_routing",
        ("mixed", "auto"),
        enabled=True,
        rewrite=True,
        decompose=False,
        multi_hop=False,
        smart_routing=True,
    ),
    "query_rewrite": AgenticSpec(
        "query_rewrite",
        "query_rewriting",
        ("noisy_query", "conversational"),
        enabled=True,
        rewrite=True,
        decompose=False,
        multi_hop=False,
    ),
    "hyde": AgenticSpec(
        "hyde",
        "hypothetical_document_embeddings",
        ("semantic_gap", "exploratory"),
        enabled=True,
        rewrite=True,
        decompose=False,
        multi_hop=False,
        hyde=True,
    ),
    "decompose": AgenticSpec(
        "decompose",
        "sub_question_decomposition",
        ("multi_part", "comparison"),
        enabled=True,
        rewrite=False,
        decompose=True,
        multi_hop=False,
    ),
    "multi_hop": AgenticSpec(
        "multi_hop",
        "iterative_rag",
        ("multi_hop", "complex"),
        enabled=True,
        rewrite=False,
        decompose=True,
        multi_hop=True,
    ),
}


@dataclass(frozen=True)
class AgenticResolved:
    profile: str
    enabled: bool
    rewrite: bool
    decompose: bool
    multi_hop: bool
    smart_routing: bool
    hyde: bool = False


def normalize_agentic_profile(value: object) -> str:
    normalized = str(value).casefold()
    return normalized if normalized in AGENTIC_SPECS else DEFAULT_AGENTIC_PROFILE


def resolve_agentic(profile: object) -> AgenticResolved:
    """profile からクエリ計画の挙動フラグを解決する(max_subqueries は backend)。"""
    name = normalize_agentic_profile(profile)
    spec = AGENTIC_SPECS[name]
    return AgenticResolved(
        profile=name,
        enabled=spec.enabled,
        rewrite=spec.rewrite,
        decompose=spec.decompose,
        multi_hop=spec.multi_hop,
        smart_routing=spec.smart_routing,
        hyde=spec.hyde,
    )
