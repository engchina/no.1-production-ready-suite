"""Retrieval 戦略の決定論解決(backend / サービス共有)。

strategy → 検索挙動(mode_override / strategy_bias / gap_stop / corrective / business_fit /
query_expansion)を決定論で解決する。mode_override / strategy_bias は wire 中立のため **文字列**
(SearchMode / SearchStrategy の値)で受け渡し、backend が enum へ写す。query_expansion は None の
とき settings 既定に従うため、呼び出し側から settings_query_expansion を渡す。Settings 非依存。
外部検索エンジンは導入しない(実 retrieval は Oracle 26ai 経路を backend が実行)。
"""

from __future__ import annotations

from dataclasses import dataclass

RETRIEVAL_STRATEGIES: tuple[str, ...] = (
    "hybrid_rrf",
    "vector",
    "keyword",
    "graph_augmented",
    "select_ai_structured",
    "business_context_strict",
    "corrective_multi_query",
)
DEFAULT_RETRIEVAL_STRATEGY = "hybrid_rrf"


@dataclass(frozen=True)
class RetrievalSpec:
    name: str
    origin: str
    recommended_for: tuple[str, ...]
    mode_override: str | None = None  # SearchMode の値("vector"/"keyword")
    strategy_bias: str | None = None  # SearchStrategy の値("graph_global"/"select_ai")
    query_expansion: bool | None = None  # None は settings 既定に従う
    gap_stop: bool = False
    corrective_retrieval: bool = False
    business_fit_weighting: bool = False


RETRIEVAL_SPECS: dict[str, RetrievalSpec] = {
    "hybrid_rrf": RetrievalSpec(
        "hybrid_rrf", "oracle_hybrid_rrf", ("general", "faq", "policy")
    ),
    "vector": RetrievalSpec(
        "vector",
        "oracle_ai_vector_search",
        ("semantic", "paraphrase"),
        mode_override="vector",
        query_expansion=False,
    ),
    "keyword": RetrievalSpec(
        "keyword",
        "oracle_text",
        ("named_entity", "regulation"),
        mode_override="keyword",
        query_expansion=False,
    ),
    "graph_augmented": RetrievalSpec(
        "graph_augmented",
        "graphrag_lite",
        ("relationship", "cross_document"),
        strategy_bias="graph_global",
    ),
    "select_ai_structured": RetrievalSpec(
        "select_ai_structured",
        "oracle_select_ai",
        ("aggregate", "structured"),
        strategy_bias="select_ai",
    ),
    "business_context_strict": RetrievalSpec(
        "business_context_strict",
        "aidb_business_context",
        ("compliance", "enterprise"),
        gap_stop=True,
        business_fit_weighting=True,
    ),
    "corrective_multi_query": RetrievalSpec(
        "corrective_multi_query",
        "crag_self_rag",
        ("recall_critical", "ambiguous"),
        query_expansion=True,
        corrective_retrieval=True,
    ),
}


@dataclass(frozen=True)
class RetrievalResolved:
    strategy: str
    mode_override: str | None
    strategy_bias: str | None
    query_expansion: bool
    gap_stop: bool
    corrective_retrieval: bool
    business_fit_weighting: bool


def normalize_retrieval_strategy(value: object) -> str:
    normalized = str(value).casefold()
    return normalized if normalized in RETRIEVAL_SPECS else DEFAULT_RETRIEVAL_STRATEGY


def resolve_retrieval(strategy: object, settings_query_expansion: bool) -> RetrievalResolved:
    """strategy + settings 既定 query_expansion から検索挙動を解決する。"""
    name = normalize_retrieval_strategy(strategy)
    spec = RETRIEVAL_SPECS[name]
    query_expansion = (
        spec.query_expansion if spec.query_expansion is not None else settings_query_expansion
    )
    return RetrievalResolved(
        strategy=name,
        mode_override=spec.mode_override,
        strategy_bias=spec.strategy_bias,
        query_expansion=query_expansion,
        gap_stop=spec.gap_stop,
        corrective_retrieval=spec.corrective_retrieval,
        business_fit_weighting=spec.business_fit_weighting,
    )
