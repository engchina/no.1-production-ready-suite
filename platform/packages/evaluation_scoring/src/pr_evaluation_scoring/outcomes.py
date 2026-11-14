"""回答の対応（outcome）の語彙と、検索の指標の対象外の判定。

対応の語彙は RAG の `rag_search.outcome` と Agent の `RunOutput.outcome` の公開の契約
（`platform/contracts/mcp/rag-tools.json` / `agent-tools.json`）と同じ 6 つ。
"""

from __future__ import annotations

# answered: 資料にもとづいて答えた / conditional: 条件・不足を示して答えた /
# needs_clarification: 利用者に条件を確かめた / needs_environment_data: 現場の値・記録が要る /
# needs_human: 人への引き継ぎ / insufficient_evidence: 資料から答えられない（拒答）。
OUTCOMES: tuple[str, ...] = (
    "answered",
    "conditional",
    "needs_clarification",
    "needs_environment_data",
    "needs_human",
    "insufficient_evidence",
)
# 検索をしない回答の対応（引用が無ければ、検索の指標の対象外）。
NO_RETRIEVAL_OUTCOMES: frozenset[str] = frozenset({"needs_clarification", "needs_human"})


def skips_retrieval(outcome: str | None, *, has_citations: bool) -> bool:
    """確認の質問・人への引き継ぎで引用の無い回答か（検索の指標の対象外）。"""
    return outcome in NO_RETRIEVAL_OUTCOMES and not has_citations
