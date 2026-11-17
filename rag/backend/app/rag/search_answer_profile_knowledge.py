"""検索・回答プロファイル単位の知識(ドメインキーワード等)を Oracle の JSON payload で管理する。

payload 形式と正規化・候補生成は rag_poc の ``rag_engine.knowledge`` をそのまま使う。
保存先はファイルではなく ``rag_search_answer_profile_knowledge``(検索・回答プロファイル × 種別)。
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Protocol

from pr_backend_core.logging import safe_exception_fields
from rag_engine.knowledge.approved_faq import (
    APPROVED_FAQ_APPROVED_STATUS,
    APPROVED_FAQ_EXCEL_COLUMNS,
    APPROVED_FAQ_IMPORT_MODES,
    ApprovedFaqImportRow,
    ApprovedFaqMutationResult,
    ApprovedFaqRecord,
    ApprovedFaqSemanticIndex,
    ApprovedFaqSuggestion,
    add_approved_faq_record,
    apply_approved_faq_import_rows,
    approved_faq_records_signature,
    approved_faq_semantic_questions,
    build_approved_faq_semantic_index,
    delete_approved_faq_records,
    load_approved_faq_excel_rows,
    load_approved_faq_payload,
    load_approved_faq_records,
    load_approved_faq_semantic_index,
    suggest_approved_faq_questions,
)
from rag_engine.knowledge.domain_keyword_candidates import (
    DomainKeywordCandidate,
    DomainKeywordSourceText,
    suggest_domain_keyword_candidates,
)
from rag_engine.knowledge.domain_keywords import (
    DOMAIN_KEYWORDS_SCHEMA_VERSION,
    MAX_DOMAIN_KEYWORDS,
    normalize_domain_keyword,
    normalize_domain_keywords,
)
from rag_engine.knowledge.runtime_knowledge import (
    MAX_TERMS,
    RuntimeKnowledgeContext,
    build_runtime_knowledge_context,
)
from rag_engine.knowledge.runtime_knowledge_management import (
    edit_knowledge,
    load_knowledge_snapshot,
)
from rag_engine.retrieval.text_search_tokenizer import TextSearchTokenizerConfig

from app.config import Settings

logger = logging.getLogger(__name__)

DOMAIN_KEYWORDS_KIND = "domain_keywords"
DEFAULT_CANDIDATE_LIMIT = 50
DEFAULT_CANDIDATE_SOURCE_CHUNKS = 2000


class SearchAnswerProfileKnowledgeStore(Protocol):
    async def get_search_answer_profile_knowledge(
        self, search_answer_profile_id: str, kind: str
    ) -> dict[str, object] | None: ...

    async def save_search_answer_profile_knowledge(
        self, search_answer_profile_id: str, kind: str, payload: dict[str, object]
    ) -> None: ...

    async def list_search_answer_profile_chunk_texts(
        self, knowledge_base_ids: list[str], *, limit: int
    ) -> list[tuple[str, str, str]]: ...


@dataclass(frozen=True)
class DomainKeywordSuggestion:
    candidates: list[DomainKeywordCandidate]
    processed_chunk_count: int


async def load_domain_keywords(
    store: SearchAnswerProfileKnowledgeStore, search_answer_profile_id: str
) -> list[str]:
    """保存済みドメインキーワード(正規化済み)を返す。未登録は空。"""
    payload = await store.get_search_answer_profile_knowledge(
        search_answer_profile_id, DOMAIN_KEYWORDS_KIND
    )
    if payload is None:
        return []
    return normalize_domain_keywords(payload.get("keywords"))


def validate_domain_keywords(values: list[str]) -> list[str]:
    """既存の正規化で重複を除き、上限超過は切り捨てず拒否する。"""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = normalize_domain_keyword(value)
        key = keyword.casefold()
        if keyword and key not in seen:
            seen.add(key)
            result.append(keyword)
            if len(result) > MAX_DOMAIN_KEYWORDS:
                raise ValueError(f"ドメインキーワードは {MAX_DOMAIN_KEYWORDS} 件までです。")
    return result


async def save_domain_keywords(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    keywords: list[str],
) -> list[str]:
    """正規化(重複・空白・長さ・件数上限)して保存し、保存後の一覧を返す。"""
    normalized = validate_domain_keywords(keywords)
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id,
        DOMAIN_KEYWORDS_KIND,
        {
            "schema_version": DOMAIN_KEYWORDS_SCHEMA_VERSION,
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "keywords": normalized,
        },
    )
    return normalized


async def suggest_domain_keywords(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    knowledge_base_ids: list[str],
    *,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
    max_source_chunks: int = DEFAULT_CANDIDATE_SOURCE_CHUNKS,
) -> DomainKeywordSuggestion:
    """参照 KB の配信中 chunk から TF-IDF でキーワード候補を作る(LLM は使わない)。"""
    rows = await store.list_search_answer_profile_chunk_texts(
        knowledge_base_ids, limit=max_source_chunks
    )
    sources = [
        DomainKeywordSourceText(chunk_id=chunk_id, document_id=document_id, text=text)
        for chunk_id, document_id, text in rows
        if text.strip()
    ]
    existing = await load_domain_keywords(store, search_answer_profile_id)
    candidates = suggest_domain_keyword_candidates(
        sources,
        existing_keywords=existing,
        tokenizer_config=TextSearchTokenizerConfig(),
        limit=limit,
    )
    return DomainKeywordSuggestion(candidates=candidates, processed_chunk_count=len(sources))


# --- 登録済みQ&A（質問と回答。#5107）---------------------------------------------

APPROVED_FAQ_KIND = "approved_faq"
FAQ_SEMANTIC_CACHE_KEY = "semantic_index_cache"
# (texts, input_type) -> vectors。backend の Cohere embedding(OciGenAiClient.embed)を渡す。
FaqEmbedInputType = Literal["SEARCH_DOCUMENT", "SEARCH_QUERY"]
FaqEmbedder = Callable[[list[str], FaqEmbedInputType], Awaitable[list[list[float]]]]
APPROVED_FAQ_PREVIEW_ROWS = 10
# FAQ の payload に持つ、検索・回答プロファイルごとの設定（回答の前に候補を提示するか）。
FAQ_ENABLED_KEY = "enabled"
APPROVED_FAQ_TEMPLATE_FILE_NAME = "approved_faq_template.xlsx"


@dataclass(frozen=True)
class ApprovedFaqMutation:
    records: list[ApprovedFaqRecord]
    inserted_count: int
    deleted_count: int


def approved_faq_enabled(payload: Mapping[str, object] | None) -> bool:
    """回答の前に FAQ の候補を提示するか(検索・回答プロファイルごと。未設定はオン。#684)。

    オフのプロファイルは FAQ を一切使わない（候補を出さず、選んだ FAQ の指定も受けない）。
    """
    return (payload or {}).get(FAQ_ENABLED_KEY) is not False


async def load_approved_faq(
    store: SearchAnswerProfileKnowledgeStore, search_answer_profile_id: str
) -> tuple[dict[str, object] | None, list[ApprovedFaqRecord]]:
    """FAQ の payload と record（未登録は None と空）。"""
    payload = await store.get_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND
    )
    if payload is None:
        return None, []
    with _faq_file(payload) as path:
        return payload, load_approved_faq_records(path)


async def save_approved_faq_settings(
    store: SearchAnswerProfileKnowledgeStore, search_answer_profile_id: str, *, enabled: bool
) -> None:
    """プロファイルごとの FAQ の設定を保存する(FAQ の登録内容は変えない)。"""
    payload = await store.get_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND
    )
    with _faq_file(payload) as path:
        # 未登録なら FAQ の標準形式(空)に設定だけを持たせる。
        base = load_approved_faq_payload(path)
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND, {**base, FAQ_ENABLED_KEY: enabled}
    )


async def find_approved_faq(
    store: SearchAnswerProfileKnowledgeStore, search_answer_profile_id: str, faq_id: str
) -> ApprovedFaqRecord | None:
    """利用者が選んだ FAQ を、検索・回答プロファイルの登録済みQ&Aから引き直す。

    提示がオフ・承認済みでない・無い FAQ は None。
    """
    payload, records = await load_approved_faq(store, search_answer_profile_id)
    if payload is None or not approved_faq_enabled(payload):
        return None
    return next(
        (
            record
            for record in records
            if record.id == faq_id and record.status == APPROVED_FAQ_APPROVED_STATUS
        ),
        None,
    )


async def mutate_approved_faq(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    operation: Callable[[Path], ApprovedFaqMutationResult],
) -> ApprovedFaqMutation:
    """FAQ の更新関数(ファイル前提)を一時ファイル上で実行し、結果を DB へ保存する。

    ponytail: 同時編集は後勝ち。競合検出が必要になったら revision を条件に MERGE する。
    """
    payload = await store.get_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND
    )
    with _faq_file(payload) as path:
        result = operation(path)
        updated = load_approved_faq_payload(path)
        records = load_approved_faq_records(path)
    if payload is not None and FAQ_ENABLED_KEY in payload:
        # FAQ の更新関数は設定を知らないので、プロファイルごとの設定を引き継ぐ。
        updated = {**updated, FAQ_ENABLED_KEY: payload[FAQ_ENABLED_KEY]}
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND, updated
    )
    return ApprovedFaqMutation(
        records=records,
        inserted_count=result.inserted_count,
        deleted_count=result.deleted_count,
    )


async def add_approved_faq(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    *,
    question: str,
    answer: str,
) -> ApprovedFaqMutation:
    """手入力の FAQ を追加する(同じ質問は追加しない)。"""
    return await mutate_approved_faq(
        store,
        search_answer_profile_id,
        lambda path: add_approved_faq_record(path, question=question, approved_answer=answer),
    )


async def delete_approved_faq(
    store: SearchAnswerProfileKnowledgeStore, search_answer_profile_id: str, ids: list[str]
) -> ApprovedFaqMutation:
    return await mutate_approved_faq(
        store, search_answer_profile_id, lambda path: delete_approved_faq_records(path, ids)
    )


def read_approved_faq_excel(content: bytes, file_name: str) -> list[ApprovedFaqImportRow]:
    """Excel の QUESTION / ANSWER 列を FAQ 取込行へ読む(ほかの列は読まない。不正は ValueError)。"""
    suffix = Path(file_name or "").suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        raise ValueError("Excel ファイル(.xlsx / .xls)を指定してください。")
    with tempfile.TemporaryDirectory(prefix="approved-faq-") as work:
        path = Path(work) / Path(file_name).name
        path.write_bytes(content)
        return load_approved_faq_excel_rows(path)


def approved_faq_template_xlsx() -> bytes:
    """FAQ の取込のテンプレート(QUESTION・ANSWER の 2 列の見出しだけの Excel)。"""
    from openpyxl import Workbook  # type: ignore[import-untyped]
    from openpyxl.styles import Font  # type: ignore[import-untyped]

    book = Workbook()
    sheet = book.active or book.create_sheet()
    sheet.title = "FAQ"
    sheet.append(list(APPROVED_FAQ_EXCEL_COLUMNS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.column_dimensions["A"].width = 60
    sheet.column_dimensions["B"].width = 80
    sheet.freeze_panes = "A2"
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def import_approved_faq(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    rows: list[ApprovedFaqImportRow],
    *,
    mode: str,
) -> ApprovedFaqMutation:
    """FAQ 行を取り込む(INSERT は同じ質問をスキップ、DELETE_THEN_INSERT は置き換え)。"""
    if mode not in APPROVED_FAQ_IMPORT_MODES:
        raise ValueError(f"取込モードが不正です: {mode}")
    return await mutate_approved_faq(
        store,
        search_answer_profile_id,
        lambda path: apply_approved_faq_import_rows(path, rows, mode=mode),
    )


async def suggest_approved_faq(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    question: str,
    *,
    min_score: float,
    limit: int,
    embed: FaqEmbedder | None = None,
    embedding_model: str = "",
    embedding_dimensions: int = 1536,
    loaded: tuple[dict[str, object] | None, list[ApprovedFaqRecord]] | None = None,
) -> list[ApprovedFaqSuggestion]:
    """質問に近い登録済みQ&Aを、一致度が ``min_score`` 以上のものだけ最大 ``limit`` 件返す。

    提示がオフ・FAQ が無いときは空。文字列類似度に加え、embed 指定時は意味照合(FAQ の質問の
    embedding index)を使う。index は FAQ payload の ``semantic_index_cache`` に保持し、FAQ 集合の
    署名が変われば作り直す。embedding に失敗しても文字列照合で続ける。``loaded`` は呼び出し元が
    読んだ FAQ(``load_approved_faq`` の結果。読み直さない)。
    """
    if not question.strip():
        return []
    payload, records = loaded or await load_approved_faq(store, search_answer_profile_id)
    if payload is None or not approved_faq_enabled(payload):
        return []
    usable = [record for record in records if record.status == APPROVED_FAQ_APPROVED_STATUS]
    if not usable:
        return []
    semantic_index: ApprovedFaqSemanticIndex | None = None
    query_embedding: list[float] | None = None
    if embed is not None:
        try:
            semantic_index, cache = await _faq_semantic_index(
                payload, usable, embed, model=embedding_model, dimensions=embedding_dimensions
            )
            if cache is not None:
                await store.save_search_answer_profile_knowledge(
                    search_answer_profile_id,
                    APPROVED_FAQ_KIND,
                    {**payload, FAQ_SEMANTIC_CACHE_KEY: cache},
                )
            query_embedding = (await embed([question], "SEARCH_QUERY"))[0]
        except Exception as exc:  # 意味照合は補助。文字列照合で続ける。
            logger.warning(
                "approved faq semantic matching failed", extra={**safe_exception_fields(exc)}
            )
            semantic_index, query_embedding = None, None
    return suggest_approved_faq_questions(
        question,
        usable,
        semantic_index=semantic_index,
        semantic_query_embedding=query_embedding,
        limit=limit,
        min_score=min_score,
    )


class FaqEmbeddingClient(Protocol):
    async def embed(
        self, texts: list[str], *, input_type: FaqEmbedInputType = "SEARCH_DOCUMENT"
    ) -> list[list[float]]: ...


def approved_faq_match_options(
    settings: Settings, genai: Callable[[], FaqEmbeddingClient]
) -> dict[str, Any]:
    """全体設定から決まる FAQ の候補の照合の引数(下限・件数と、意味照合の embedding)。

    画面の候補・MCP の候補で同じ値を使う。``genai`` は embedding を呼ぶときにだけ作る。
    """
    embed: FaqEmbedder | None = None
    if settings.rag_approved_faq_semantic_enabled:

        async def embed(texts: list[str], input_type: FaqEmbedInputType) -> list[list[float]]:
            return await genai().embed(texts, input_type=input_type)

    return {
        "min_score": settings.rag_approved_faq_min_score,
        "limit": settings.rag_approved_faq_max_suggestions,
        "embed": embed,
        "embedding_model": settings.oci_genai_embedding_model,
        "embedding_dimensions": settings.oci_genai_embedding_dim,
    }


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
        questions = [question for _, question in approved_faq_semantic_questions(records)]
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


@contextmanager
def _faq_file(payload: Mapping[str, object] | None) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="approved-faq-") as work:
        path = Path(work) / "approved_faq.json"
        if payload is not None:
            path.write_text(json.dumps(dict(payload), ensure_ascii=False), encoding="utf-8")
        yield path


# --- 用語・同義語(runtime knowledge)-------------------------------------------

RUNTIME_KNOWLEDGE_KIND = "runtime_knowledge"


async def load_runtime_knowledge_payload(
    store: SearchAnswerProfileKnowledgeStore, search_answer_profile_id: str
) -> dict[str, object]:
    """検索・回答プロファイルの用語・同義語の payload を返す(未登録は空の標準形式)。"""
    payload = await store.get_search_answer_profile_knowledge(
        search_answer_profile_id, RUNTIME_KNOWLEDGE_KIND
    )
    return payload or {"schema_version": 1, "terms": []}


async def edit_runtime_knowledge(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    *,
    selected: str | None,
    name: str = "",
    labels: str = "",
    content: str = "",
    source: str = "",
    enabled: bool = True,
    delete: bool = False,
) -> dict[str, object]:
    """rag_poc の edit_knowledge を一時ファイル上で実行し、結果を DB へ保存する。"""
    payload = await load_runtime_knowledge_payload(store, search_answer_profile_id)
    with _runtime_knowledge_dir(payload) as (work_dir, path):
        snapshot = load_knowledge_snapshot(work_dir, path)
        updated, _ = edit_knowledge(
            snapshot,
            selected,
            name=name,
            labels=labels,
            content=content,
            source=source,
            enabled=enabled,
            delete=delete,
            confirmed=delete,
        )
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id, RUNTIME_KNOWLEDGE_KIND, dict(updated.payload)
    )
    return dict(updated.payload)


def preview_runtime_knowledge(
    payload: Mapping[str, object], question: str
) -> RuntimeKnowledgeContext:
    """質問に一致する用語・同義語と拡張後の検索文を返す(保存しない)。"""
    with _runtime_knowledge_dir(payload) as (work_dir, path):
        return build_runtime_knowledge_context(
            question,
            work_dir,
            path,
            terms_search_enabled=True,
            terms_answer_enabled=True,
        )


def match_runtime_knowledge(
    payload: Mapping[str, object], question: str
) -> RuntimeKnowledgeContext:
    """質問に当たる用語を返す（保存しない）。照合・有効状態の判定は回答生成と同じ。

    件数・字数で切って省いた数を示すのは呼び出し元(MCP の根拠の取得。#2701)。
    """
    with _runtime_knowledge_dir(payload) as (work_dir, path):
        return build_runtime_knowledge_context(question, work_dir, path, term_limit=MAX_TERMS)


@contextmanager
def _runtime_knowledge_dir(payload: Mapping[str, object]) -> Iterator[tuple[Path, Path]]:
    with tempfile.TemporaryDirectory(prefix="runtime-knowledge-") as work:
        work_dir = Path(work)
        path = work_dir / "runtime_knowledge.json"
        path.write_text(json.dumps(dict(payload), ensure_ascii=False), encoding="utf-8")
        yield work_dir, path
