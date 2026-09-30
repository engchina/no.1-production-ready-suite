"""DocRAG(rag_poc)の根拠付き回答フローを backend の検索・rerank・LLM で実行する。

rag_poc の ``answer_question_result``(質問ルーティング / CRAG / 親子文脈 / 生成 + 監査ラウンド)を
そのまま使い、``AnswerDependencies`` の I/O だけを差し替える。

- 検索: backend の Oracle hybrid 検索(業務ビューの KB フィルタとドメインキーワード付き)。
  複数の検索文は原質問主軸の重み付き RRF で融合し、同じ親の兄弟 chunk と
  親本文(``docrag_parent_text``)で親子を復元する。
  質問の理解(``inquiry_conditions``)が名指しした文書名・ページは検索条件に足し、
  profile / business_match のチャネルを RRF に加える(#546)。文書の分類は chunk の
  metadata(``document.classification``)に載せ、業務の候補の絞り込みに使う(#545)。
  文書の 1 ページ目の本文(chunk set ごとに 1 つ)も ``document.first_page_context`` に載せ、
  回答の「文書の背景」に使う(#557)。
  質問が名指しした業務(``business_domains``)は、検索範囲の文書の大分類の語の一覧から
  照合して docrag へ注入する(domain profile の ``business_patterns`` は使わない。#553)。
- 画面目録の連携(``RAG_DOCRAG_SCREEN_LINKING_ENABLED``。#554): 目録は検索範囲の全文書の
  ``section_path`` から DB で作り(範囲と索引の状態ごとに cache)、選ばれた画面の chunk も DB から
  読んで docrag へ注入する(検索結果に出なかった画面も候補に加える)。
- rerank: backend の Cohere rerank。
- LLM: openai SDK(OCI_ENTERPRISE_AI_*)。docrag 内部の Responses API 経路を使う。
回答フローは同期関数のため worker thread で動かし、非同期 I/O はイベントループへ戻して実行する。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TypeVar

from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.config import (
    Settings,
    enterprise_ai_connection_for_model,
    enterprise_ai_default_model_id,
    enterprise_ai_vision_model_id,
)
from app.rag.docrag_chunking import docrag_search_text
from app.rag.docrag_prompts import prompt_overrides
from app.rag.document_crop import DocumentSourceNotFoundError, crop_png, load_parsed_source
from app.schemas.classification import category_label, normalize_category_value
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest

T = TypeVar("T")

logger = logging.getLogger(__name__)

DOCRAG_ANSWER_ENGINE = "docrag"
DOCRAG_SOURCE_RUN_ID = "0" * 16
# 回答フロー 1 件の I/O 待ちの上限(秒)。LLM/検索の個別 timeout は各 client が持つ。
_IO_TIMEOUT_SECONDS = 600.0
# 質問が挙げた文書名のうち、ナレッジベースにあるか確かめる数の上限(1 語ごとに SQL 1 回)。
_MAX_QUESTION_FILE_TERMS = 3
# 質問の業務名として照合する大分類の名前の最短の文字数(1 文字の名前は質問の別の語に偶然含まれる)。
_MIN_BUSINESS_NAME_CHARS = 2
# 画面目録の cache((検索条件, 索引の状態) → 目録)。索引の状態は文書の追加・再索引で変わる(#554)。
_SCREEN_CATALOG_CACHE: dict[tuple[str, str], dict[str, list[str]]] = {}
_SCREEN_CATALOG_CACHE_SIZE = 32
# section_path の見出しの区切り(docrag_chunking が「 > 」でつないで保存する)。
_SECTION_PATH_SEPARATOR = " > "


@dataclass(frozen=True)
class DocragAnswerOutcome:
    """DocRAG 回答の本文・引用・診断(非機密)。"""

    answer: str
    citations: list[RetrievedChunk]
    diagnostics: dict[str, Any]
    context_text: str
    # 標準回答による LLM 評価(evaluate_answer_payload)の入力。回答記録に保存する。
    evaluation_input: dict[str, Any] | None = None


# evaluate_answer_payload が読む回答 payload のキー(answer_result_payload の部分集合)。
EVALUATION_INPUT_KEYS = (
    "question",
    "answer_text",
    "reasoning_summary",
    "insufficient_reason",
    "used_images",
    "external_data_required",
    "external_data_items",
    "evidence_items",
)


@dataclass
class _SearchState:
    """検索で見つけた backend chunk を chunk_id で保持し、引用へ戻すために使う。

    work_dir は docrag の output_dir。根拠画像を ``<work_dir>/<run>/crops/`` へ切り出す。
    """

    chunks: dict[str, RetrievedChunk] = field(default_factory=dict)
    work_dir: Path | None = None
    sources: dict[str, bytes | None] = field(default_factory=dict)
    # 質問の文書名・ページを足した検索条件(#546)。CRAG の各回で同じなので、最初の検索で
    # 1 回だけ決める。
    filters: dict[str, str] | None = None
    # 文書の分類(rag_documents.classification)を document_id ごとに保持する(#545)。
    classifications: dict[str, dict[str, object]] = field(default_factory=dict)
    loaded_classification_ids: set[str] = field(default_factory=set)
    # 画面目録(#554)。CRAG の各回で同じなので、1 回の回答で 1 回だけ作る。
    screen_catalog: dict[str, list[str]] | None = None
    # 文書の 1 ページ目の本文(rag_chunk_sets.first_page_context)を chunk_set_id ごとに持つ(#557)。
    first_page_contexts: dict[str, dict[str, object]] = field(default_factory=dict)
    loaded_first_page_chunk_set_ids: set[str] = field(default_factory=set)


def build_docrag_settings(
    settings: Settings,
    *,
    output_dir: Path,
    runtime_knowledge_path: Path | None = None,
) -> Any:
    """backend Settings から docrag Settings を作る(env や .env は読まない)。"""
    from docrag.config import get_settings as docrag_get_settings

    # 回答のモデルと Vision のモデルは、それぞれのモデルの接続で呼ぶ(#533)。
    answer_model = enterprise_ai_default_model_id(settings)
    vision_model = enterprise_ai_vision_model_id(settings)
    answer = enterprise_ai_connection_for_model(settings, answer_model)
    vision = enterprise_ai_connection_for_model(settings, vision_model)
    environ = {
        "OCI_ENTERPRISE_AI_ENDPOINT": answer.endpoint,
        "OCI_ENTERPRISE_AI_API_KEY": answer.api_key,
        "OCI_ENTERPRISE_AI_PROJECT_OCID": answer.project_ocid,
        "OCI_ENTERPRISE_AI_DEFAULT_MODEL": answer_model,
        "OCI_ENTERPRISE_AI_VLM_MODEL": vision_model,
        "OCI_ENTERPRISE_AI_VLM_ENDPOINT": vision.endpoint,
        "OCI_ENTERPRISE_AI_VLM_API_KEY": vision.api_key,
        "OCI_ENTERPRISE_AI_VLM_PROJECT_OCID": vision.project_ocid,
        # docrag は rerank_model と oci_compartment_id が両方あるときだけ rerank を実行する
        # (answer_records._rerank_configured)。rerank 自体は backend の Cohere client を注入して
        # 呼ぶが、この 2 値を渡さないと RAG_DOCRAG_RERANK_ENABLED=true でも
        # 常に「未実行」になる(#275)。
        "OCI_COMPARTMENT_ID": settings.oci_compartment_id,
        "RERANK_MODEL": settings.oci_genai_rerank_model,
        "DOCRAG_PROFILE": settings.rag_docrag_profile,
        "DOCRAG_OUTPUT_DIR": str(output_dir),
        "LLM_REQUEST_TIMEOUT_SECONDS": str(int(settings.oci_enterprise_ai_timeout_seconds)),
        "LLM_RETRIES": str(int(settings.oci_enterprise_ai_max_retries)),
        # 別プロセスの domain_profile.json を拾わないよう、legacy 指定時も明示パスだけを読む。
        "DOCRAG_DOMAIN_PROFILE_FILE": os.environ.get("DOCRAG_DOMAIN_PROFILE_FILE", ""),
        "DOCRAG_ANSWER_LLM_SUPPORTS_VISION": (
            "1" if settings.rag_docrag_answer_vision_enabled else "0"
        ),
        # 画面目録で操作画面を探す(#554)。docrag は明示した environ だけを読む。
        "DOCRAG_SCREEN_LINKING": "1" if settings.rag_docrag_screen_linking_enabled else "0",
    }
    if runtime_knowledge_path is not None:
        environ["RUNTIME_KNOWLEDGE_PATH"] = str(runtime_knowledge_path)
    docrag_settings = docrag_get_settings(environ=environ, dotenv_path=None)
    return replace(docrag_settings, domain_keywords_override=tuple(settings.rag_domain_keywords))


class DocragAnswerEngine:
    """rag_poc 回答フローを backend の I/O で駆動する。"""

    def __init__(
        self,
        settings: Settings,
        *,
        oracle: OracleClient,
        genai: OciGenAiClient,
        runtime_knowledge_payload: Mapping[str, object] | None = None,
    ) -> None:
        self._settings = settings
        self._oracle = oracle
        self._genai = genai
        self._runtime_knowledge_payload = runtime_knowledge_payload

    async def run(self, request: SearchRequest) -> DocragAnswerOutcome:
        loop = asyncio.get_running_loop()
        state = _SearchState()
        try:
            overrides = await self._oracle.docrag_prompt_overrides()
        except Exception:  # noqa: BLE001 - 編集したプロンプトは補助。既定値で回答を続ける。
            logger.warning("docrag prompt overrides load failed", exc_info=True)
            overrides = {}
        business_names = await self._business_names(request)
        with tempfile.TemporaryDirectory(prefix="docrag-answer-") as work:
            work_dir = Path(work)
            state.work_dir = work_dir
            runtime_path = None
            if self._runtime_knowledge_payload:
                runtime_path = work_dir / "runtime_knowledge.json"
                runtime_path.write_text(
                    json.dumps(self._runtime_knowledge_payload, ensure_ascii=False),
                    encoding="utf-8",
                )
            docrag_settings = build_docrag_settings(
                self._settings, output_dir=work_dir, runtime_knowledge_path=runtime_path
            )
            result = await asyncio.to_thread(
                self._answer_sync,
                request,
                docrag_settings,
                loop,
                state,
                overrides,
                business_names,
            )
        return _outcome_from_result(result, state)

    async def _business_names(self, request: SearchRequest) -> list[str]:
        """検索範囲(``request.filters``)の文書の大分類の語の一覧を、1 回の回答で 1 回読む(#553)。

        読めないときは空(業務の絞り込みをせずに回答を続ける)。
        """
        try:
            return await self._oracle.retrieval_large_categories(dict(request.filters))
        except Exception:  # noqa: BLE001 - 業務の絞り込みは補助。絞らずに回答を続ける。
            logger.warning("docrag business names load failed", exc_info=True)
            return []

    def _answer_sync(
        self,
        request: SearchRequest,
        docrag_settings: Any,
        loop: asyncio.AbstractEventLoop,
        state: _SearchState,
        overrides: Mapping[str, str] | None = None,
        business_names: Sequence[str] = (),
    ) -> Any:
        from docrag.adapters.oci import parse_multimodal_response, parse_text_response
        from docrag.dependencies import AnswerDependencies, bind_dependencies
        from docrag.generation.answering import answer_question_result
        from docrag.knowledge.classification import classification_filter_from_values
        from docrag.retrieval.scope import RETRIEVAL_SCOPE_KNOWLEDGE_BASE

        def run_async(factory: Callable[[], Awaitable[T]]) -> T:
            return asyncio.run_coroutine_threadsafe(_call(factory), loop).result(
                _IO_TIMEOUT_SECONDS
            )

        dependencies = AnswerDependencies(
            search=lambda **kwargs: run_async(lambda: self._search(request, state, **kwargs)),
            check_ready=lambda **kwargs: None,
            parse_text=parse_text_response,
            parse_images=parse_multimodal_response,
            rerank=lambda query, documents, settings, top_n=None: run_async(
                lambda: self._rerank(query, list(documents), top_n)
            ),
            business_domains=lambda question: question_business_domains(question, business_names),
            # 画面目録の連携(#554)。docrag は設定が有効なときだけ呼ぶ。
            screen_catalog=lambda **kwargs: run_async(lambda: self._screen_catalog(request, state)),
            screen_chunks=lambda *, links, existing, **kwargs: run_async(
                lambda: self._screen_chunks(request, state, links, existing)
            ),
        )
        with bind_dependencies(dependencies), prompt_overrides(overrides or {}):
            return answer_question_result(
                request.query,
                DOCRAG_SOURCE_RUN_ID,
                ["docling"],
                docrag_settings,
                chunk_top_k=max(1, int(request.top_k)),
                chunk_neighbor_count=self._settings.rag_docrag_neighbor_child_count,
                query_strategy=self._settings.rag_docrag_query_strategy,
                answer_flow=self._settings.rag_docrag_answer_flow,
                rerank_enabled=self._settings.rag_docrag_rerank_enabled,
                retrieval_scope=RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
                # 絞り込み自体は _search の hybrid_search(request.filters)が行う。
                # ここでは回答のプロンプトと実行記録に条件を載せるために渡す。
                classification_filter=classification_filter_from_values(
                    large_category=request.filters.get("large_category", ""),
                    middle_category=request.filters.get("middle_category", ""),
                    small_category=request.filters.get("small_category", ""),
                    as_of=request.filters.get("as_of", ""),
                ),
            )

    async def _search(
        self,
        request: SearchRequest,
        state: _SearchState,
        *,
        retrieval_queries: Sequence[str],
        candidate_limit: int = 24,
        vector_only_queries: Sequence[str] = (),
        inquiry_conditions: Any = None,
        settings: Any = None,
        **_: object,
    ) -> Any:
        from docrag.models.storage import HybridSearchResult

        queries = [query for query in dict.fromkeys(q.strip() for q in retrieval_queries) if query]
        if not queries:
            return HybridSearchResult(child_chunks=[], all_chunks=[])
        if state.filters is None:
            state.filters = await self._question_filters(dict(request.filters), inquiry_conditions)
        vector_only = {query.strip() for query in vector_only_queries}
        embeddings = await self._genai.embed(queries, input_type="SEARCH_QUERY")
        fused: dict[str, float] = {}
        rankings: list[list[str]] = []
        k = float(self._settings.rag_rrf_k)
        derived_weight = 1.0 / max(1, len(queries) - 1)
        for index, (query, embedding) in enumerate(zip(queries, embeddings, strict=False)):
            mode = SearchMode.VECTOR if query in vector_only else SearchMode.HYBRID
            hits = await self._oracle.hybrid_search(
                query, embedding, candidate_limit, mode=mode, filters=dict(state.filters)
            )
            rankings.append([hit.chunk_id for hit in hits])
            # 原質問を主軸にし、派生検索文は合計で原質問 1 本分の票に抑える(rag_poc #975)。
            weight = 1.0 if index == 0 else derived_weight
            for rank, hit in enumerate(hits, start=1):
                state.chunks.setdefault(hit.chunk_id, hit)
                fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + weight / (k + rank)
        await self._load_classifications([state.chunks[chunk_id] for chunk_id in fused], state)
        if inquiry_conditions is not None:
            pool = [
                _stored_child(
                    state.chunks[chunk_id],
                    rrf_score=0.0,
                    classification=state.classifications.get(state.chunks[chunk_id].document_id),
                )
                for chunk_id in fused
            ]
            channels = _inquiry_channel_rankings(
                pool,
                inquiry_conditions,
                rankings,
                limit=candidate_limit,
                profile_enabled=bool(getattr(settings, "profile_channel_enabled", True)),
            )
            for _channel, channel_weight, ranking in channels:
                for rank, (chunk_id, _score) in enumerate(ranking, start=1):
                    fused[chunk_id] = fused.get(chunk_id, 0.0) + channel_weight / (k + rank)
        ranked = sorted(fused, key=lambda chunk_id: -fused[chunk_id])[:candidate_limit]
        anchors = [state.chunks[chunk_id] for chunk_id in ranked]
        siblings = await self._oracle.context_group_siblings(
            anchors, max_chunks_per_group=max(1, self._settings.rag_context_group_max_chunks)
        )
        for sibling in siblings:
            state.chunks.setdefault(sibling.chunk_id, sibling)
        if self._settings.rag_docrag_answer_vision_enabled:
            for chunk in [*anchors, *siblings]:
                await self._materialize_image_evidence(chunk, state)
            anchors = [state.chunks[chunk.chunk_id] for chunk in anchors]
            siblings = [state.chunks.get(chunk.chunk_id, chunk) for chunk in siblings]
        await self._load_first_page_contexts([*anchors, *siblings], state)
        classifications = state.classifications
        children = [
            _stored_child(
                chunk,
                rrf_score=fused.get(chunk.chunk_id, 0.0),
                classification=classifications.get(chunk.document_id),
                first_page_context=_first_page_context(chunk, state),
            )
            for chunk in anchors
        ]
        all_children = {chunk.chunk_uid: chunk for chunk in children}
        for sibling in siblings:
            all_children.setdefault(
                sibling.chunk_id,
                _stored_child(
                    sibling,
                    rrf_score=0.0,
                    classification=classifications.get(sibling.document_id),
                    first_page_context=_first_page_context(sibling, state),
                ),
            )
        parents = _stored_parents(list(all_children.values()), state)
        return HybridSearchResult(
            child_chunks=children, all_chunks=[*all_children.values(), *parents]
        )

    async def _question_filters(
        self, filters: dict[str, str], inquiry_conditions: Any
    ) -> dict[str, str]:
        """質問が名指しした文書名・ページを検索条件(``hybrid_search`` の filters)へ足す(#546)。

        - 画面で文書(``file_name`` / ``document_id``)を指定したときは、そちらを優先して足さない。
          ページも同じく、画面で指定したときは足さない。
        - ナレッジベースに無い文書名は足さない(0 件にしない。rag_poc の
          ``_resolved_metadata_filter``)。
          ページは文書名と組でだけ使う条件なので、文書名を足さないときは一緒に外す。
        - filters の ``file_name`` は 1 語なので、質問が複数の文書名を挙げたときは、
          最初に見つかったものを使う。
        """
        metadata_filter = getattr(inquiry_conditions, "metadata_filter", None)
        terms = [
            term.strip()
            for term in getattr(metadata_filter, "source_file_terms", ())
            if isinstance(term, str) and term.strip()
        ]
        if not terms or any(filters.get(key, "").strip() for key in ("file_name", "document_id")):
            return filters
        for term in terms[:_MAX_QUESTION_FILE_TERMS]:
            candidate = {**filters, "file_name": term}
            if await self._oracle.has_retrieval_chunks(candidate):
                break
        else:
            return filters
        pages = [
            page
            for page in getattr(metadata_filter, "page_numbers", ())
            if isinstance(page, int) and page > 0
        ]
        if pages and not any(
            filters.get(key, "").strip() for key in ("page_number_min", "page_number_max")
        ):
            candidate["page_number_min"] = str(min(pages))
            candidate["page_number_max"] = str(max(pages))
        return candidate

    async def _screen_catalog(
        self, request: SearchRequest, state: _SearchState
    ) -> dict[str, list[str]]:
        """検索範囲の全文書の見出しから画面目録を作る(範囲と索引の状態ごとに cache。#554)。

        1 回の回答では最初の 1 回だけ作る。読めないときは空(docrag は LLM を呼ばず画面を足さない)。
        """
        if state.screen_catalog is None:
            state.screen_catalog = await self._load_screen_catalog(request)
        return state.screen_catalog

    async def _load_screen_catalog(self, request: SearchRequest) -> dict[str, list[str]]:
        from docrag.retrieval.screen_catalog import catalog_from_section_paths

        filters = dict(request.filters)
        try:
            index_state = await self._oracle.retrieval_scope_state(filters)
            key = (json.dumps(filters, ensure_ascii=False, sort_keys=True), index_state)
            cached = _SCREEN_CATALOG_CACHE.get(key)
            if cached is not None:
                return cached
            sections = await self._oracle.retrieval_screen_sections(filters)
        except Exception:  # noqa: BLE001 - 画面目録は補助。画面を足さずに回答を続ける。
            logger.warning("docrag screen catalog load failed", exc_info=True)
            return {}
        catalog = catalog_from_section_paths(
            (file_name, _section_headings(section_path), count)
            for file_name, section_path, count in sections
        )
        if len(_SCREEN_CATALOG_CACHE) >= _SCREEN_CATALOG_CACHE_SIZE:
            _SCREEN_CATALOG_CACHE.clear()
        _SCREEN_CATALOG_CACHE[key] = catalog
        return catalog

    async def _screen_chunks(
        self,
        request: SearchRequest,
        state: _SearchState,
        links: Sequence[tuple[str, str]],
        existing: set[str],
    ) -> list[Any]:
        """選ばれた画面の child(画面ごとに上限まで)と、その親を検索範囲から読む(#554)。

        検索結果に無かった chunk も引用に戻せるよう ``state.chunks`` に入れる。分類・1 ページ目の
        本文(#557)・根拠画像は検索の候補と同じく付ける。読めない画面は足さない(回答は続ける)。
        """
        from docrag.retrieval.screen_catalog import MAX_CHILDREN_PER_SCREEN

        filters = dict(request.filters)
        added: dict[str, RetrievedChunk] = {}
        for file_name, heading in links:
            try:
                rows = await self._oracle.retrieval_screen_chunks(
                    filters,
                    file_name=file_name,
                    heading=heading,
                    limit=MAX_CHILDREN_PER_SCREEN * 4 + len(existing),
                )
            except Exception:  # noqa: BLE001 - 画面の根拠は補助。この画面を足さずに続ける。
                logger.warning("docrag screen chunks load failed", exc_info=True)
                continue
            matched = [
                chunk
                for chunk in rows
                if chunk.chunk_id not in existing
                and chunk.chunk_id not in added
                and heading in _chunk_headings(chunk)
            ]
            for chunk in matched[:MAX_CHILDREN_PER_SCREEN]:
                added[chunk.chunk_id] = state.chunks.setdefault(chunk.chunk_id, chunk)
        if not added:
            return []
        chunks = list(added.values())
        # 分類と 1 ページ目の本文は、読んでいない文書・chunk set の分だけをまとめて 1 回で読む。
        await self._load_classifications(chunks, state)
        await self._load_first_page_contexts(chunks, state)
        if self._settings.rag_docrag_answer_vision_enabled:
            for chunk in chunks:
                await self._materialize_image_evidence(chunk, state)
            chunks = [state.chunks[chunk.chunk_id] for chunk in chunks]
        children = [
            _stored_child(
                chunk,
                rrf_score=0.0,
                classification=state.classifications.get(chunk.document_id),
                first_page_context=_first_page_context(chunk, state),
            )
            for chunk in chunks
        ]
        return [*children, *_stored_parents(children, state)]

    async def _load_classifications(
        self, chunks: Sequence[RetrievedChunk], state: _SearchState
    ) -> None:
        """まだ読んでいない文書の分類を、ヒットした document_id でまとめて 1 回で読む(#545)。"""
        missing = sorted({chunk.document_id for chunk in chunks} - state.loaded_classification_ids)
        if not missing:
            return
        state.classifications.update(await self._oracle.document_classifications(missing))
        state.loaded_classification_ids.update(missing)

    async def _load_first_page_contexts(
        self, chunks: Sequence[RetrievedChunk], state: _SearchState
    ) -> None:
        """まだ読んでいない chunk set の 1 ページ目の本文を、まとめて 1 回で読む(#557)。"""
        chunk_set_ids = {str(chunk.metadata.get("chunk_set_id") or "") for chunk in chunks}
        missing = sorted(chunk_set_ids - {""} - state.loaded_first_page_chunk_set_ids)
        if not missing:
            return
        state.first_page_contexts.update(await self._oracle.chunk_set_first_page_contexts(missing))
        state.loaded_first_page_chunk_set_ids.update(missing)

    async def _materialize_image_evidence(self, chunk: RetrievedChunk, state: _SearchState) -> None:
        """根拠 chunk の image_evidence を作業ディレクトリへ切り出し、crop_path を差し替える。

        rag_poc の answer_images は ``output_dir / source_run_id / crop_path`` を読むため、
        文書ごとの run ディレクトリへ PNG を書き、metadata(docrag_metadata_json)を書き換える。
        切り出せない画像は添付しない(回答は続ける)。
        """
        metadata = _docrag_metadata(chunk)
        images = metadata.get("image_evidence")
        width = chunk.metadata.get("page_width")
        height = chunk.metadata.get("page_height")
        if not isinstance(images, list) or not images or state.work_dir is None:
            return
        if not isinstance(width, int | float) or not isinstance(height, int | float):
            return
        run_id = _document_run_id(chunk.document_id)
        crop_dir = state.work_dir / run_id / "crops"
        for image in images:
            if not isinstance(image, dict):
                continue
            bbox = image.get("bbox")
            page = _int(image.get("page"))
            image_id = str(image.get("image_id") or image.get("record_id") or "").strip()
            if not image_id or page < 1 or not isinstance(bbox, list) or len(bbox) != 4:
                continue
            name = f"{_safe_name(image_id)}.png"
            target = crop_dir / name
            if not target.is_file():
                source = await self._parsed_source(chunk.document_id, state)
                if source is None:
                    return
                try:
                    png = await asyncio.to_thread(
                        crop_png,
                        source,
                        page,
                        (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
                        (float(width), float(height)),
                    )
                except ValueError:
                    continue
                crop_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(png)
            image["source_run_id"] = run_id
            image["crop_path"] = f"crops/{name}"
        state.chunks[chunk.chunk_id] = chunk.model_copy(
            update={
                "metadata": {
                    **chunk.metadata,
                    "docrag_metadata_json": json.dumps(metadata, ensure_ascii=False, default=str),
                }
            }
        )

    async def _parsed_source(self, document_id: str, state: _SearchState) -> bytes | None:
        if document_id not in state.sources:
            try:
                state.sources[document_id] = await load_parsed_source(self._oracle, document_id)
            except (DocumentSourceNotFoundError, ValueError):
                state.sources[document_id] = None
        return state.sources[document_id]

    async def _rerank(self, query: str, documents: list[str], top_n: int | None) -> list[Any]:
        from docrag.models.llm import RerankTextRank

        if not documents:
            return []
        ranked = await self._genai.rerank(query, documents, top_n or len(documents))
        return [RerankTextRank(index=index, relevance_score=score) for index, score in ranked]


async def _call[R](factory: Callable[[], Awaitable[R]]) -> R:
    return await factory()


def question_business_domains(question: str, large_categories: Sequence[str]) -> list[str]:
    """質問が名指しした大分類を、検索範囲の大分類の語の一覧(保存どおりの値)から返す(#553)。

    - 比べるときは #547 の正規化(NFKC・空白・``category_label`` で番号の接頭辞を外す)と
      大文字・小文字の違いを無視する。返す値は保存どおり(docrag の ``_same_business_records`` は
      候補の大分類と番号の接頭辞を外して完全一致で比べる)。
    - 長い名前から照合し、照合した箇所は短い名前の照合に使わない
      (「業務A」の中の「業務」を拾わない)。
    - 名指しが無ければ空(絞り込まない)。
    """
    text = (normalize_category_value(question) or "").casefold()
    values_by_label: dict[str, list[str]] = {}
    for value in large_categories:
        label = category_label(value).casefold()
        if len(label) >= _MIN_BUSINESS_NAME_CHARS:
            values_by_label.setdefault(label, []).append(value)
    matched: list[str] = []
    for label in sorted(values_by_label, key=lambda item: (-len(item), item)):
        if label in text:
            text = text.replace(label, "\0")
            matched.extend(values_by_label[label])
    return list(dict.fromkeys(matched))


def _json_list(value: object) -> list[Any]:
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except ValueError:
            return []
        return decoded if isinstance(decoded, list) else []
    return list(value) if isinstance(value, list) else []


def _docrag_metadata(chunk: RetrievedChunk) -> dict[str, Any]:
    raw = chunk.metadata.get("docrag_metadata_json")
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict):
            return decoded
    # docrag 以外の分割戦略の chunk も最小の v4 metadata で扱う。
    section = str(chunk.metadata.get("section_path") or "")
    return {
        "schema_version": 4,
        "active": True,
        "atomic": False,
        "content_hash": str(chunk.metadata.get("text_sha256") or ""),
        "classification": {},
        "source_categories": ["Text"],
        "section_path": [part for part in section.split(" > ") if part],
        "section_path_sources": [],
    }


def _document_run_id(document_id: str) -> str:
    return hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:16]


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:120]


def _section_headings(section_path: str) -> list[str]:
    return [part.strip() for part in section_path.split(_SECTION_PATH_SEPARATOR) if part.strip()]


def _chunk_headings(chunk: RetrievedChunk) -> list[str]:
    """chunk の見出しの列(docrag の metadata の section_path。無ければ保存した文字列を分ける)。"""
    path = _docrag_metadata(chunk).get("section_path")
    if isinstance(path, list):
        return [str(part).strip() for part in path]
    return _section_headings(str(chunk.metadata.get("section_path") or ""))


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        return default


def _group_id(chunk: RetrievedChunk) -> str:
    return str(chunk.metadata.get("chunk_group_id") or chunk.chunk_id)


def _inquiry_channel_rankings(
    pool: Sequence[Any],
    inquiry_conditions: Any,
    rankings: Sequence[Sequence[str]],
    *,
    limit: int,
    profile_enabled: bool,
) -> list[tuple[str, float, Sequence[tuple[str, float | None]]]]:
    """質問の理解に合う候補を、RRF に足すチャネル(profile / business_match)の順位で返す(#546)。

    rag_poc の検索(``adapters/oracle/store.py``)と同じ docrag の関数で計算する。対象は今回の検索で
    見つかった候補だけで、ナレッジベースの全 chunk は読まない。文書名・ページは検索条件
    (``_question_filters``)で適用済みか、ナレッジベースに無いため外したので、ここでは使わない。
    """
    from docrag.adapters.oracle.store import (
        _business_match_ranking,
        _chunks_matching_metadata_filter,
    )
    from docrag.retrieval.inquiry_conditions import profile_channel_rankings

    metadata_filter = getattr(inquiry_conditions, "metadata_filter", None)
    if metadata_filter is not None:
        metadata_filter = replace(metadata_filter, source_file_terms=(), page_numbers=())
    matched = _chunks_matching_metadata_filter(pool, metadata_filter)
    channels: list[tuple[str, float, Sequence[tuple[str, float | None]]]] = []
    if profile_enabled:
        channels.extend(profile_channel_rankings(matched, inquiry_conditions, limit=limit))
    channels.extend(
        _business_match_ranking(
            matched,
            metadata_filter,
            [[(chunk_id, None) for chunk_id in ranking] for ranking in rankings],
            [],
            limit=limit,
        )
    )
    return channels


def _first_page_context(chunk: RetrievedChunk, state: _SearchState) -> Mapping[str, object] | None:
    return state.first_page_contexts.get(str(chunk.metadata.get("chunk_set_id") or ""))


def _stored_child(
    chunk: RetrievedChunk,
    *,
    rrf_score: float,
    classification: Mapping[str, object] | None = None,
    first_page_context: Mapping[str, object] | None = None,
) -> Any:
    """backend の chunk を docrag の子 chunk にする。

    ``classification`` は文書の分類(rag_documents.classification)。docrag は
    ``metadata["document"]["classification"]`` で業務の候補を絞る
    (``_same_business_records``。#545)。
    ``first_page_context`` は chunk set の文書の 1 ページ目の本文。docrag は
    ``metadata["document"]["first_page_context"]`` を回答の「文書の背景」にする(#557)。
    chunk 自身が持つ値(#557 より前に保存した chunk)があれば、そちらを使う。
    分類と 1 ページ目の本文は chunk の保存内容(埋め込み・検索文)には入れず、回答のときにだけ付ける。
    """
    from docrag.models.storage import StoredChunk

    metadata = _docrag_metadata(chunk)
    metadata["rrf_score"] = rrf_score
    metadata["document_id"] = chunk.document_id
    if classification:
        document = metadata.get("document")
        metadata["document"] = {
            **(document if isinstance(document, dict) else {}),
            "classification": dict(classification),
        }
    if first_page_context:
        document = metadata.get("document")
        document = document if isinstance(document, dict) else {}
        if "first_page_context" not in document:
            metadata["document"] = {**document, "first_page_context": dict(first_page_context)}
    page_start = _int(chunk.metadata.get("page_start") or chunk.metadata.get("page_number"), 1)
    return StoredChunk(
        chunk_uid=chunk.chunk_id,
        chunk_id=chunk.chunk_id,
        chunk_level="child",
        chunk_seq=_int(chunk.metadata.get("docrag_chunk_seq") or chunk.metadata.get("chunk_index")),
        parent_chunk_uid=f"{chunk.document_id}:{_group_id(chunk)}",
        parent_chunk_id=f"{chunk.document_id}:{_group_id(chunk)}",
        child_chunk_ids=(),
        text=chunk.text,
        retrieval_text=docrag_search_text(chunk.metadata) or chunk.text,
        source_run_id=DOCRAG_SOURCE_RUN_ID,
        source_file_name=chunk.file_name or "",
        source_engine_id="docling",
        source_engine_label="Docling",
        page_start=page_start,
        page_end=_int(chunk.metadata.get("page_end"), page_start),
        source_seq_ranges=tuple(_json_list(chunk.metadata.get("docrag_source_seq_ranges_json"))),
        source_record_refs=tuple(_json_list(chunk.metadata.get("docrag_source_record_refs_json"))),
        metadata=metadata,
    )


def _stored_parents(children: list[Any], state: _SearchState) -> list[Any]:
    """子の metadata に保持した親本文から親 chunk を復元する。"""
    from docrag.models.storage import StoredChunk

    by_parent: dict[str, list[Any]] = {}
    for child in children:
        by_parent.setdefault(child.parent_chunk_uid, []).append(child)
    parents = []
    for parent_uid, members in by_parent.items():
        members.sort(key=lambda child: child.chunk_seq)
        source = state.chunks.get(members[0].chunk_uid)
        parent_text = str((source.metadata if source else {}).get("docrag_parent_text") or "")
        text = parent_text or "\n\n".join(child.text for child in members)
        metadata = dict(members[0].metadata)
        metadata.pop("rrf_score", None)
        parents.append(
            StoredChunk(
                chunk_uid=parent_uid,
                chunk_id=parent_uid,
                chunk_level="parent",
                chunk_seq=members[0].chunk_seq,
                parent_chunk_uid="",
                parent_chunk_id="",
                child_chunk_ids=tuple(child.chunk_id for child in members),
                text=text,
                retrieval_text=text,
                source_run_id=DOCRAG_SOURCE_RUN_ID,
                source_file_name=members[0].source_file_name,
                source_engine_id="docling",
                source_engine_label="Docling",
                page_start=min(child.page_start for child in members),
                page_end=max(child.page_end for child in members),
                source_seq_ranges=tuple(
                    item for child in members for item in child.source_seq_ranges
                ),
                source_record_refs=tuple(
                    item for child in members for item in child.source_record_refs
                ),
                metadata=metadata,
            )
        )
    return parents


def _outcome_from_result(result: Any, state: _SearchState) -> DocragAnswerOutcome:
    """rag_poc の AnswerQuestionResult を backend の回答・引用・診断へ写す。"""
    tree: list[dict[str, Any]] = []
    ordered: list[tuple[int, str, dict[str, Any]]] = []
    for position, parent in enumerate(result.evidence_items or ()):
        children = []
        for child in parent.get("children") or []:
            chunk_id = str(child.get("chunk_id") or child.get("id") or "")
            used = bool(child.get("is_model_used"))
            children.append(
                {
                    "chunk_id": chunk_id,
                    "role": str(child.get("retrieval_role") or ""),
                    "reason": str(child.get("context_reason") or ""),
                    "is_model_used": used,
                    "page": child.get("page_start"),
                }
            )
            rank = _int(child.get("model_usage_rank"), 10_000) if used else 10_000 + position
            ordered.append((rank, chunk_id, children[-1]))
        tree.append(
            {
                "parent_id": str(parent.get("chunk_id") or parent.get("id") or ""),
                "source": str(parent.get("source") or ""),
                "page": parent.get("page_start"),
                "reason": str(parent.get("context_reason") or ""),
                "children": children,
            }
        )
    citations: list[RetrievedChunk] = []
    seen: set[str] = set()
    for _, chunk_id, evidence in sorted(ordered, key=lambda item: item[0]):
        chunk = state.chunks.get(chunk_id)
        if chunk is None or chunk_id in seen:
            continue
        seen.add(chunk_id)
        citations.append(
            chunk.model_copy(
                update={
                    "metadata": {
                        **chunk.metadata,
                        "docrag_role": evidence["role"],
                        "docrag_model_used": evidence["is_model_used"],
                    }
                }
            )
        )
    steps = [
        {
            "name": str(step.get("name") or ""),
            "status": str(step.get("status") or ""),
            "elapsed_seconds": step.get("elapsed_seconds"),
            "llm_calls": step.get("llm_calls"),
        }
        for step in result.execution_steps or ()
        if isinstance(step, Mapping)
    ]
    diagnostics = {
        "answer_flow": result.answer_flow,
        "strategy": result.effective_strategy,
        "confidence": result.confidence,
        "needs_human_review": result.needs_human_review,
        "insufficient_reason": result.insufficient_reason,
        "reasoning_summary": result.reasoning_summary,
        "generated_queries": list(result.generated_queries),
        "text_search_tokens": list(result.text_search_tokens),
        "crag_attempt_count": len(result.crag_attempts or ()),
        "execution_steps": steps,
        "evidence_tree": tree,
    }
    context_text = "\n\n".join(chunk.text for chunk in citations)
    answer = result.answer_text or result.answer
    return DocragAnswerOutcome(
        answer=answer,
        citations=citations,
        diagnostics=diagnostics,
        context_text=context_text,
        evaluation_input=_evaluation_input(result),
    )


def _evaluation_input(result: Any) -> dict[str, Any] | None:
    """rag_poc の回答 payload から、標準回答での評価に使う部分だけを取り出す。"""
    from docrag.generation.answer_payload import answer_result_payload

    try:
        payload = answer_result_payload(result, answer_id="", run_id=DOCRAG_SOURCE_RUN_ID)
    except Exception:  # noqa: BLE001 - 評価の入力は補助。回答の返却を止めない。
        logger.warning("docrag evaluation input build failed", exc_info=True)
        return None
    return {key: payload.get(key) for key in EVALUATION_INPUT_KEYS}


def evaluate_answer_record(
    evaluation_input: Mapping[str, Any], standard_answer: str, settings: Settings
) -> dict[str, Any]:
    """保存した回答を標準回答で評価する(rag_poc の evaluate_answer_payload)。

    同期関数で、LLM を複数回呼ぶ。呼び出し側は worker thread で動かす。
    """
    from docrag.evaluation.answer_eval import evaluate_answer_payload

    with tempfile.TemporaryDirectory(prefix="docrag-eval-") as work:
        docrag_settings = build_docrag_settings(settings, output_dir=Path(work))
        return evaluate_answer_payload(
            {**evaluation_input, "standard_answer": standard_answer}, docrag_settings
        )
