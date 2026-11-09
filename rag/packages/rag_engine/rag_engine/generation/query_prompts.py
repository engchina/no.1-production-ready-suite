"""検索文の正規化と検索計画の補助。LLM は呼ばない。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any

from rag_engine.config import Settings
from rag_engine.generation.answer_models import QueryExpansionResult, _dedupe_queries, _dedupe_query_key
from rag_engine.knowledge.classification import ClassificationFilter
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext

MAX_GENERATED_QUERY_CHARS = 900


def _text_search_query_source(runtime_knowledge: RuntimeKnowledgeContext, expansion: QueryExpansionResult) -> str:
    parts = ["原質問"]
    if runtime_knowledge.expanded_question != runtime_knowledge.original_question:
        parts.extend(runtime_knowledge.expansion_sources)
    if expansion.generated_queries:
        parts.append("呼び出し元の検索文")
    return " + ".join(parts)


def _retrieval_metadata_filter(
    classification_filter: ClassificationFilter,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    classification_payload = classification_filter.to_metadata()
    if classification_payload:
        payload["classification"] = classification_payload
    return payload


def _limited_text_search_query_variants(
    question: str,
    query_variants: Sequence[str] | None,
    settings: Settings,
) -> tuple[str, ...]:
    raw_variants = query_variants if query_variants is not None else (question,)
    variants = _retrieval_queries(question, raw_variants)
    limit = max(1, int(getattr(settings, "text_search_query_variant_limit", 6) or 6))
    return tuple(variants[:limit])


def _merge_retrieval_queries(*queries: str) -> tuple[str, ...]:
    """検索文を渡した順に並べ、空と重複を除く。

    各検索文は NFKC・制御文字の除去・空白の正規化をした形にする（元の質問の先読みの検索と文書検索の先頭の検索文を
    同じ形にし、検索の結果の cache に当てる）。
    """
    merged: list[str] = []
    seen: set[str] = set()
    for query in queries:
        text = _normalized_query_text(query)
        key = text.casefold()
        if not text or key in seen:
            continue
        merged.append(text)
        seen.add(key)
    return tuple(merged)


def _normalized_query_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = "".join(character for character in normalized if unicodedata.category(character) not in {"Cc", "Cf"})
    return re.sub(r"\s+", " ", normalized).strip()


def _retrieval_queries(question: str, retrieval_queries: Sequence[str] | None) -> tuple[str, ...]:
    if retrieval_queries is None:
        return (question,)
    queries = _dedupe_queries(retrieval_queries)
    if not queries:
        return (question,)
    if _dedupe_query_key(question) != _dedupe_query_key(queries[0]):
        return _dedupe_queries((question, *queries))
    return queries
