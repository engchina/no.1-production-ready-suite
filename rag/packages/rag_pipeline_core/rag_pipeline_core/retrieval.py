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
    "business_context_strict",
    "corrective_multi_query",
    "reasoning_tree_search",
    "colpali_visual_retrieval",
)
DEFAULT_RETRIEVAL_STRATEGY = "hybrid_rrf"

# 検索モード(排他選択)。legacy 複合戦略(business_context_strict /
# corrective_multi_query)は「モード + 合成トグル」へ分解して読み取る。
RETRIEVAL_MODES: tuple[str, ...] = (
    "hybrid_rrf",
    "vector",
    "keyword",
    "graph_augmented",
)

# legacy 複合戦略 -> 分解先の強制トグル(モードは hybrid_rrf 固定)。
_LEGACY_STRATEGY_FORCED_TOGGLES: dict[str, frozenset[str]] = {
    "business_context_strict": frozenset({"gap_stop", "business_fit_weighting"}),
    "corrective_multi_query": frozenset({"query_expansion", "corrective_retrieval"}),
}


@dataclass(frozen=True)
class RetrievalSpec:
    name: str
    origin: str
    recommended_for: tuple[str, ...]
    mode_override: str | None = None  # SearchMode の値("vector"/"keyword")
    strategy_bias: str | None = None  # SearchStrategy の値("graph_global" など)
    query_expansion: bool | None = None  # None は settings 既定に従う
    gap_stop: bool = False
    corrective_retrieval: bool = False
    business_fit_weighting: bool = False
    # 実行が GPU/専用索引/LLM を要し、未配線のうちは hybrid 検索へ安全縮退する戦略
    # (strategy_bias=None のため resolve_retrieval_strategy は HYBRID を使う)。
    pending_execution: bool = False


RETRIEVAL_SPECS: dict[str, RetrievalSpec] = {
    "hybrid_rrf": RetrievalSpec(
        "hybrid_rrf", "oracle_hybrid_rrf", ("general", "faq", "policy")
    ),
    # vector/keyword でも query expansion(多 variant RRF 融合)はモード非依存に機能する
    # ため強制 False にせず settings のトグルへ従う(合成可能トグル化)。
    "vector": RetrievalSpec(
        "vector",
        "oracle_ai_vector_search",
        ("semantic", "paraphrase"),
        mode_override="vector",
    ),
    "keyword": RetrievalSpec(
        "keyword",
        "oracle_text",
        ("named_entity", "regulation"),
        mode_override="keyword",
    ),
    "graph_augmented": RetrievalSpec(
        "graph_augmented",
        "graphrag_lite",
        ("relationship", "cross_document"),
        strategy_bias="graph_global",
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
    # --- 段階導入(現状は hybrid へ安全縮退。実行配線は docs/pipeline-advanced-strategies.md)---
    "reasoning_tree_search": RetrievalSpec(
        "reasoning_tree_search",
        "pageindex_pending",
        ("manual", "compliance", "long_document"),
        pending_execution=True,
    ),
    "colpali_visual_retrieval": RetrievalSpec(
        "colpali_visual_retrieval",
        "colpali_pending_gpu",
        ("scanned_pdf", "complex_layout"),
        pending_execution=True,
    ),
}


# 実行が配線済み(pending_execution=False)で、設定 API から選択・保存できる戦略。
# 未配線戦略は hybrid へ黙縮退するため、検索方法設定の表面(GET 一覧 / PATCH 受理)から外す。
WIRED_RETRIEVAL_STRATEGIES: tuple[str, ...] = tuple(
    name for name in RETRIEVAL_STRATEGIES if not RETRIEVAL_SPECS[name].pending_execution
)

# 設定 API が保存を受理する検索モード(すべて配線済み)。
WIRED_RETRIEVAL_MODES: tuple[str, ...] = RETRIEVAL_MODES


@dataclass(frozen=True)
class RetrievalDecomposed:
    """strategy 値を「モード + 強制トグル」へ分解した結果。

    legacy 複合戦略のとき forced_* が立ち、呼び出し側が settings トグルと OR 合成する
    (legacy 挙動の厳密再現)。legacy_strategy は分解元の複合戦略名(新形式なら None)。
    """

    mode: str
    forced_query_expansion: bool = False
    forced_gap_stop: bool = False
    forced_corrective_retrieval: bool = False
    forced_business_fit_weighting: bool = False
    legacy_strategy: str | None = None


def decompose_retrieval_strategy(value: object) -> RetrievalDecomposed:
    """strategy 値(legacy 複合値・未知値込み)を検索モード + 強制トグルへ分解する。

    保存は新形式(モード + トグル)のみだが、.env / Business View JSON /
    per-request に残る legacy 値は読み取り互換で受ける。未配線・未知値は
    既定モードへ縮退する(従来の normalize と同じ寛容さ)。
    """
    name = normalize_retrieval_strategy(value)
    if name in RETRIEVAL_MODES:
        return RetrievalDecomposed(mode=name)
    forced = _LEGACY_STRATEGY_FORCED_TOGGLES.get(name, frozenset())
    return RetrievalDecomposed(
        mode=DEFAULT_RETRIEVAL_STRATEGY,
        forced_query_expansion="query_expansion" in forced,
        forced_gap_stop="gap_stop" in forced,
        forced_corrective_retrieval="corrective_retrieval" in forced,
        forced_business_fit_weighting="business_fit_weighting" in forced,
        legacy_strategy=name if forced else None,
    )


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
