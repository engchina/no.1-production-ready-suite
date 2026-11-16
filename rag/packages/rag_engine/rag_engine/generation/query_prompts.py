"""検索文の正規化と検索計画の補助。LLM は呼ばない。"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Any

from rag_engine.config import Settings
from rag_engine.generation.answer_models import QueryExpansionResult
from rag_engine.knowledge.classification import ClassificationFilter
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext
from rag_engine.retrieval.query_identity import dedupe_queries, dedupe_query_key, normalized_query_key

MAX_GENERATED_QUERY_CHARS = 900


def _text_search_query_source(runtime_knowledge: RuntimeKnowledgeContext, expansion: QueryExpansionResult) -> str:
    parts = ["原質問"]
    if runtime_knowledge.expanded_question != runtime_knowledge.original_question:
        parts.extend(runtime_knowledge.expansion_sources)
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

    各検索文は ``_normalized_query_text`` の形（NFKC・空白の正規化・制御文字と書式文字の除去）にする。
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
    """NFKC にし、空白（改行・タブを含む）を 1 つの空白にまとめ、残りの制御文字・書式文字を除く。

    改行・タブは語の区切りなので消さない（消すと前後の語がつながり、全文検索の語が変わる。#5160）。
    空白の扱いは全文検索の語の正規化（``normalize_text_search_text``）・検索文の同一性のキーと同じ。
    """
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = "".join(
        character for character in normalized if character.isspace() or unicodedata.category(character) not in {"Cc", "Cf"}
    )
    return normalized_query_key(normalized)


def _retrieval_queries(question: str, retrieval_queries: Sequence[str] | None) -> tuple[str, ...]:
    if retrieval_queries is None:
        return (question,)
    queries = dedupe_queries(retrieval_queries)
    if not queries:
        return (question,)
    if dedupe_query_key(question) != dedupe_query_key(queries[0]):
        return dedupe_queries((question, *queries))
    return queries
