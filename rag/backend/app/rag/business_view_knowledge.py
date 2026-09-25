"""業務ビュー単位の知識(ドメインキーワード等)を Oracle の JSON payload で管理する。

payload 形式と正規化・候補生成は rag_poc(DocRAG)の ``docrag.knowledge`` をそのまま使う。
保存先はファイルではなく ``rag_business_view_knowledge``(業務ビュー × 種別)。
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from docrag.knowledge.approved_faq import (
    APPROVED_FAQ_IMPORT_MODES,
    DEFAULT_APPROVED_FAQ_DIRECT_MATCH_MIN_SCORE,
    DEFAULT_APPROVED_FAQ_SUGGESTION_LIMIT,
    ApprovedFaqImportRow,
    ApprovedFaqMutationResult,
    ApprovedFaqRecord,
    ApprovedFaqSemanticIndex,
    ApprovedFaqSuggestion,
    _approved_faq_semantic_questions,
    add_approved_faq_record,
    apply_approved_faq_import_rows,
    approved_faq_records_signature,
    build_approved_faq_semantic_index,
    delete_approved_faq_records,
    load_approved_faq_excel_rows,
    load_approved_faq_payload,
    load_approved_faq_records,
    load_approved_faq_semantic_index,
    suggest_approved_faq_questions,
)
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
from docrag.knowledge.runtime_knowledge import (
    RuntimeKnowledgeContext,
    build_runtime_knowledge_context,
)
from docrag.knowledge.runtime_knowledge_management import edit_knowledge, load_knowledge_snapshot
from docrag.retrieval.text_search_tokenizer import (
    TEXT_SEARCH_TOKENIZER_SUDACHI,
    TextSearchTokenizerConfig,
)

logger = logging.getLogger(__name__)

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


# --- Approved FAQ(類似問)-----------------------------------------------------

APPROVED_FAQ_KIND = "approved_faq"
FAQ_SEMANTIC_CACHE_KEY = "semantic_index_cache"
# (texts, input_type) -> vectors。backend の Cohere embedding(OciGenAiClient.embed)を渡す。
FaqEmbedInputType = Literal["SEARCH_DOCUMENT", "SEARCH_QUERY"]
FaqEmbedder = Callable[[list[str], FaqEmbedInputType], Awaitable[list[list[float]]]]
APPROVED_FAQ_PREVIEW_ROWS = 10


@dataclass(frozen=True)
class ApprovedFaqMutation:
    records: list[ApprovedFaqRecord]
    inserted_count: int
    deleted_count: int


async def load_approved_faq(
    store: BusinessViewKnowledgeStore, business_view_id: str
) -> list[ApprovedFaqRecord]:
    """業務ビューの承認済み FAQ を返す。"""
    payload = await store.get_business_view_knowledge(business_view_id, APPROVED_FAQ_KIND)
    if payload is None:
        return []
    with _faq_file(payload) as path:
        return load_approved_faq_records(path)


async def mutate_approved_faq(
    store: BusinessViewKnowledgeStore,
    business_view_id: str,
    operation: Callable[[Path], ApprovedFaqMutationResult],
) -> ApprovedFaqMutation:
    """rag_poc の FAQ 更新関数(ファイル前提)を一時ファイル上で実行し、結果を DB へ保存する。

    ponytail: 同時編集は後勝ち。競合検出が必要になったら revision を条件に MERGE する。
    """
    payload = await store.get_business_view_knowledge(business_view_id, APPROVED_FAQ_KIND)
    with _faq_file(payload) as path:
        result = operation(path)
        updated = load_approved_faq_payload(path)
        records = load_approved_faq_records(path)
    await store.save_business_view_knowledge(business_view_id, APPROVED_FAQ_KIND, updated)
    return ApprovedFaqMutation(
        records=records,
        inserted_count=result.inserted_count,
        deleted_count=result.deleted_count,
    )


async def add_approved_faq(
    store: BusinessViewKnowledgeStore, business_view_id: str, *, question: str, answer: str
) -> ApprovedFaqMutation:
    return await mutate_approved_faq(
        store,
        business_view_id,
        lambda path: add_approved_faq_record(path, question=question, approved_answer=answer),
    )


async def delete_approved_faq(
    store: BusinessViewKnowledgeStore, business_view_id: str, ids: list[str]
) -> ApprovedFaqMutation:
    return await mutate_approved_faq(
        store, business_view_id, lambda path: delete_approved_faq_records(path, ids)
    )


def read_approved_faq_excel(content: bytes, file_name: str) -> list[ApprovedFaqImportRow]:
    """QUESTION / ANSWER 列の Excel を FAQ 取込行へ読む(不正な形式は ValueError)。"""
    suffix = Path(file_name or "").suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        raise ValueError("Excel ファイル(.xlsx / .xls)を指定してください。")
    with tempfile.TemporaryDirectory(prefix="approved-faq-") as work:
        path = Path(work) / Path(file_name).name
        path.write_bytes(content)
        return load_approved_faq_excel_rows(path)


async def import_approved_faq(
    store: BusinessViewKnowledgeStore,
    business_view_id: str,
    rows: list[ApprovedFaqImportRow],
    *,
    mode: str,
) -> ApprovedFaqMutation:
    if mode not in APPROVED_FAQ_IMPORT_MODES:
        raise ValueError(f"取込モードが不正です: {mode}")
    return await mutate_approved_faq(
        store,
        business_view_id,
        lambda path: apply_approved_faq_import_rows(path, rows, mode=mode),
    )


async def suggest_approved_faq(
    store: BusinessViewKnowledgeStore,
    business_view_id: str,
    question: str,
    *,
    limit: int = DEFAULT_APPROVED_FAQ_SUGGESTION_LIMIT,
    embed: FaqEmbedder | None = None,
    embedding_model: str = "",
    embedding_dimensions: int = 1536,
) -> list[ApprovedFaqSuggestion]:
    """質問に近い承認済み FAQ を返す。

    文字列類似度に加え、embed 指定時は rag_poc の意味照合(FAQ 質問の embedding index)を使う。
    index は FAQ payload の ``semantic_index_cache`` に保持し、FAQ 集合の署名が変われば作り直す。
    embedding に失敗しても文字列照合で続ける。
    """
    payload = await store.get_business_view_knowledge(business_view_id, APPROVED_FAQ_KIND)
    if payload is None or not question.strip():
        return []
    with _faq_file(payload) as path:
        records = load_approved_faq_records(path)
    if not records:
        return []
    semantic_index: ApprovedFaqSemanticIndex | None = None
    query_embedding: list[float] | None = None
    if embed is not None:
        try:
            semantic_index, cache = await _faq_semantic_index(
                payload, records, embed, model=embedding_model, dimensions=embedding_dimensions
            )
            if cache is not None:
                await store.save_business_view_knowledge(
                    business_view_id,
                    APPROVED_FAQ_KIND,
                    {**payload, FAQ_SEMANTIC_CACHE_KEY: cache},
                )
            query_embedding = (await embed([question], "SEARCH_QUERY"))[0]
        except Exception as exc:  # 意味照合は補助。文字列照合で続ける。
            logger.warning("approved faq semantic matching failed", extra={"error": str(exc)})
            semantic_index, query_embedding = None, None
    return suggest_approved_faq_questions(
        question,
        records,
        semantic_index=semantic_index,
        semantic_query_embedding=query_embedding,
        limit=limit,
    )


async def _faq_semantic_index(
    payload: Mapping[str, object],
    records: list[ApprovedFaqRecord],
    embed: FaqEmbedder,
    *,
    model: str,
    dimensions: int,
) -> tuple[ApprovedFaqSemanticIndex, dict[str, object] | None]:
    """保存済み index を再利用し、無効なら FAQ 質問を embedding して作り直す(新 cache を返す)。"""
    with tempfile.TemporaryDirectory(prefix="approved-faq-semantic-") as work:
        cache_path = Path(work) / "semantic_index.json"
        cached = payload.get(FAQ_SEMANTIC_CACHE_KEY)
        if isinstance(cached, dict):
            cache_path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
            index = load_approved_faq_semantic_index(
                cache_path,
                model=model,
                dimensions=dimensions,
                records_signature=approved_faq_records_signature(records),
            )
            if index is not None:
                return index, None
        questions = [question for _, question in _approved_faq_semantic_questions(records)]
        vectors = await embed(questions, "SEARCH_DOCUMENT") if questions else []
        index = build_approved_faq_semantic_index(
            cache_path,
            records,
            model=model,
            dimensions=dimensions,
            embedder=lambda texts, settings: vectors,
            settings=None,
        )
        return index, json.loads(cache_path.read_text(encoding="utf-8"))


def is_direct_faq_match(suggestion: ApprovedFaqSuggestion) -> bool:
    """rag_poc と同じく 0.92 以上を FAQ 回答をそのまま使える候補とみなす。"""
    return suggestion.score >= DEFAULT_APPROVED_FAQ_DIRECT_MATCH_MIN_SCORE


@contextmanager
def _faq_file(payload: Mapping[str, object] | None) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="approved-faq-") as work:
        path = Path(work) / "approved_faq.json"
        if payload is not None:
            path.write_text(json.dumps(dict(payload), ensure_ascii=False), encoding="utf-8")
        yield path


# --- 用語・ルール(runtime knowledge)-------------------------------------------

RUNTIME_KNOWLEDGE_KIND = "runtime_knowledge"


async def load_runtime_knowledge_payload(
    store: BusinessViewKnowledgeStore, business_view_id: str
) -> dict[str, object]:
    """業務ビューの用語・ルール payload を返す(未登録は空の標準形式)。"""
    payload = await store.get_business_view_knowledge(business_view_id, RUNTIME_KNOWLEDGE_KIND)
    return payload or {"schema_version": 1, "terms": [], "rules": []}


async def edit_runtime_knowledge(
    store: BusinessViewKnowledgeStore,
    business_view_id: str,
    *,
    kind: str,
    selected: str | None,
    name: str = "",
    title: str = "",
    labels: str = "",
    content: str = "",
    source: str = "",
    enabled: bool = True,
    delete: bool = False,
) -> dict[str, object]:
    """rag_poc の edit_knowledge を一時ファイル上で実行し、結果を DB へ保存する。"""
    payload = await load_runtime_knowledge_payload(store, business_view_id)
    with _runtime_knowledge_dir(payload) as (work_dir, path):
        snapshot = load_knowledge_snapshot(work_dir, path)
        updated, _ = edit_knowledge(
            snapshot,
            kind,
            selected,
            name=name,
            title=title,
            labels=labels,
            content=content,
            source=source,
            enabled=enabled,
            delete=delete,
            confirmed=delete,
        )
    await store.save_business_view_knowledge(
        business_view_id, RUNTIME_KNOWLEDGE_KIND, dict(updated.payload)
    )
    return dict(updated.payload)


def preview_runtime_knowledge(
    payload: Mapping[str, object], question: str
) -> RuntimeKnowledgeContext:
    """質問に一致する用語・ルールと拡張後の検索文を返す(保存しない)。"""
    with _runtime_knowledge_dir(payload) as (work_dir, path):
        return build_runtime_knowledge_context(question, work_dir, path)


@contextmanager
def _runtime_knowledge_dir(payload: Mapping[str, object]) -> Iterator[tuple[Path, Path]]:
    with tempfile.TemporaryDirectory(prefix="runtime-knowledge-") as work:
        work_dir = Path(work)
        path = work_dir / "runtime_knowledge.json"
        path.write_text(json.dumps(dict(payload), ensure_ascii=False), encoding="utf-8")
        yield work_dir, path
