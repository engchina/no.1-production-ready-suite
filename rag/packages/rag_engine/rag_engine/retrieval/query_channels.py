"""検索文の出所を検索経路へ変換する。質問本文から用途を推測しない。"""

from collections.abc import Mapping, Sequence

from rag_engine.knowledge.runtime_knowledge import ORIGINAL_QUESTION_SOURCE_LABEL, TERMS_ONLY_SOURCE_LABEL, TERMS_SOURCE_LABEL
from rag_engine.retrieval.weighted_rrf import query_key


def expression_channels(query: str, original: str, sources: Mapping[str, Sequence[str]], *, vector_only: bool = False) -> tuple[str, ...]:
    if vector_only:
        return ("vector",)
    if query_key(query) == query_key(original):
        return ("hybrid",)
    labels = {
        part for key, values in sources.items() if query_key(key) == query_key(query) for label in values for part in label.split(" + ")
    } - {ORIGINAL_QUESTION_SOURCE_LABEL}
    terms = {TERMS_SOURCE_LABEL, TERMS_ONLY_SOURCE_LABEL}
    if labels and labels <= terms:
        return ("keyword",)
    return ("vector", "keyword") if labels & terms else ("vector",)
