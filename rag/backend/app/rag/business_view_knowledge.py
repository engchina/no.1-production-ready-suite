"""業務ビュー単位の知識(ドメインキーワード等)を Oracle の JSON payload で管理する。

payload 形式と正規化・候補生成は rag_poc(DocRAG)の ``docrag.knowledge`` をそのまま使う。
保存先はファイルではなく ``rag_business_view_knowledge``(業務ビュー × 種別)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from docrag.knowledge.domain_keyword_candidates import (
    DomainKeywordCandidate,
    DomainKeywordSourceText,
    suggest_domain_keyword_candidates,
)
from docrag.knowledge.domain_keywords import (
    DOMAIN_KEYWORDS_SCHEMA_VERSION,
    MAX_DOMAIN_KEYWORDS,
    normalize_domain_keywords,
)
from docrag.retrieval.text_search_tokenizer import (
    TEXT_SEARCH_TOKENIZER_SUDACHI,
    TextSearchTokenizerConfig,
)

DOMAIN_KEYWORDS_KIND = "domain_keywords"
DEFAULT_CANDIDATE_LIMIT = 50
DEFAULT_CANDIDATE_SOURCE_CHUNKS = 2000


class BusinessViewKnowledgeStore(Protocol):
    async def get_business_view_knowledge(
        self, business_view_id: str, kind: str
    ) -> dict[str, object] | None: ...

    async def save_business_view_knowledge(
        self, business_view_id: str, kind: str, payload: dict[str, object]
    ) -> None: ...

    async def list_business_view_chunk_texts(
        self, knowledge_base_ids: list[str], *, limit: int
    ) -> list[tuple[str, str, str]]: ...


@dataclass(frozen=True)
class DomainKeywordSuggestion:
    candidates: list[DomainKeywordCandidate]
    processed_chunk_count: int


async def load_domain_keywords(
    store: BusinessViewKnowledgeStore, business_view_id: str
) -> list[str]:
    """保存済みドメインキーワード(正規化済み)を返す。未登録は空。"""
    payload = await store.get_business_view_knowledge(business_view_id, DOMAIN_KEYWORDS_KIND)
    if payload is None:
        return []
    return normalize_domain_keywords(payload.get("keywords"))


async def save_domain_keywords(
    store: BusinessViewKnowledgeStore,
    business_view_id: str,
    keywords: list[str],
) -> list[str]:
    """正規化(重複・空白・長さ・件数上限)して保存し、保存後の一覧を返す。"""
    normalized = normalize_domain_keywords(keywords)
    if len(normalized) > MAX_DOMAIN_KEYWORDS:
        raise ValueError(f"ドメインキーワードは {MAX_DOMAIN_KEYWORDS} 件までです。")
    await store.save_business_view_knowledge(
        business_view_id,
        DOMAIN_KEYWORDS_KIND,
        {
            "schema_version": DOMAIN_KEYWORDS_SCHEMA_VERSION,
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "keywords": normalized,
        },
    )
    return normalized


async def suggest_domain_keywords(
    store: BusinessViewKnowledgeStore,
    business_view_id: str,
    knowledge_base_ids: list[str],
    *,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
    max_source_chunks: int = DEFAULT_CANDIDATE_SOURCE_CHUNKS,
) -> DomainKeywordSuggestion:
    """参照 KB の配信中 chunk から TF-IDF でキーワード候補を作る(LLM は使わない)。"""
    rows = await store.list_business_view_chunk_texts(knowledge_base_ids, limit=max_source_chunks)
    sources = [
        DomainKeywordSourceText(chunk_id=chunk_id, document_id=document_id, text=text)
        for chunk_id, document_id, text in rows
        if text.strip()
    ]
    existing = await load_domain_keywords(store, business_view_id)
    candidates = suggest_domain_keyword_candidates(
        sources,
        existing_keywords=existing,
        tokenizer_config=TextSearchTokenizerConfig(mode=TEXT_SEARCH_TOKENIZER_SUDACHI),
        limit=limit,
    )
    return DomainKeywordSuggestion(candidates=candidates, processed_chunk_count=len(sources))
