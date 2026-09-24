"""DocRAG(rag_poc)の根拠付き回答フローを backend の検索・rerank・LLM で実行する。

rag_poc の ``answer_question_result``(質問ルーティング / CRAG / 親子文脈 / 生成 + 監査ラウンド)を
そのまま使い、``AnswerDependencies`` の I/O だけを差し替える。

- 検索: backend の Oracle hybrid 検索(業務ビューの KB フィルタとドメインキーワード付き)。
  複数の検索文は原質問主軸の重み付き RRF で融合し、同じ親の兄弟 chunk と
  親本文(``docrag_parent_text``)で親子を復元する。
- rerank: backend の Cohere rerank。
- LLM: openai SDK(OCI_ENTERPRISE_AI_*)。docrag 内部の Responses API 経路を使う。
回答フローは同期関数のため worker thread で動かし、非同期 I/O はイベントループへ戻して実行する。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
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
    enterprise_ai_default_model_id,
    enterprise_ai_vision_model_id,
)
from app.rag.docrag_chunking import docrag_search_text
from app.rag.document_crop import DocumentSourceNotFoundError, crop_png, load_parsed_source
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest

T = TypeVar("T")

DOCRAG_ANSWER_ENGINE = "docrag"
DOCRAG_SOURCE_RUN_ID = "0" * 16
# 回答フロー 1 件の I/O 待ちの上限(秒)。LLM/検索の個別 timeout は各 client が持つ。
_IO_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True)
class DocragAnswerOutcome:
    """DocRAG 回答の本文・引用・診断(非機密)。"""

    answer: str
    citations: list[RetrievedChunk]
    diagnostics: dict[str, Any]
    context_text: str


@dataclass
class _SearchState:
    """検索で見つけた backend chunk を chunk_id で保持し、引用へ戻すために使う。

    work_dir は docrag の output_dir。根拠画像を ``<work_dir>/<run>/crops/`` へ切り出す。
    """

    chunks: dict[str, RetrievedChunk] = field(default_factory=dict)
    work_dir: Path | None = None
    sources: dict[str, bytes | None] = field(default_factory=dict)


def build_docrag_settings(
    settings: Settings,
    *,
    output_dir: Path,
    runtime_knowledge_path: Path | None = None,
) -> Any:
    """backend Settings から docrag Settings を作る(env や .env は読まない)。"""
    from docrag.config import get_settings as docrag_get_settings

    environ = {
        "OCI_ENTERPRISE_AI_ENDPOINT": settings.oci_enterprise_ai_endpoint,
        "OCI_ENTERPRISE_AI_API_KEY": settings.oci_enterprise_ai_api_key,
        "OCI_ENTERPRISE_AI_PROJECT_OCID": settings.oci_enterprise_ai_project_ocid,
        "OCI_ENTERPRISE_AI_DEFAULT_MODEL": enterprise_ai_default_model_id(settings),
        "OCI_ENTERPRISE_AI_VLM_MODEL": enterprise_ai_vision_model_id(settings),
        "DOCRAG_PROFILE": settings.rag_docrag_profile,
        "DOCRAG_OUTPUT_DIR": str(output_dir),
        "LLM_REQUEST_TIMEOUT_SECONDS": str(int(settings.oci_enterprise_ai_timeout_seconds)),
        "LLM_RETRIES": str(int(settings.oci_enterprise_ai_max_retries)),
        # 別プロセスの domain_profile.json を拾わないよう、legacy 指定時も明示パスだけを読む。
        "DOCRAG_DOMAIN_PROFILE_FILE": os.environ.get("DOCRAG_DOMAIN_PROFILE_FILE", ""),
        "DOCRAG_ANSWER_LLM_SUPPORTS_VISION": (
            "1" if settings.rag_docrag_answer_vision_enabled else "0"
        ),
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
                self._answer_sync, request, docrag_settings, loop, state
            )
        return _outcome_from_result(result, state)

    def _answer_sync(
        self,
        request: SearchRequest,
        docrag_settings: Any,
        loop: asyncio.AbstractEventLoop,
        state: _SearchState,
    ) -> Any:
        from docrag.adapters.oci import parse_multimodal_response, parse_text_response
        from docrag.dependencies import AnswerDependencies, bind_dependencies
        from docrag.generation.answering import answer_question_result
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
        )
        with bind_dependencies(dependencies):
            return answer_question_result(
                request.query,
                DOCRAG_SOURCE_RUN_ID,
                ["docling"],
                docrag_settings,
                chunk_top_k=max(1, int(request.top_k)),
                retrieval_scope=RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
            )

    async def _search(
        self,
        request: SearchRequest,
        state: _SearchState,
        *,
        retrieval_queries: Sequence[str],
        candidate_limit: int = 24,
        vector_only_queries: Sequence[str] = (),
        **_: object,
    ) -> Any:
        from docrag.models.storage import HybridSearchResult

        queries = [query for query in dict.fromkeys(q.strip() for q in retrieval_queries) if query]
        if not queries:
            return HybridSearchResult(child_chunks=[], all_chunks=[])
        vector_only = {query.strip() for query in vector_only_queries}
        embeddings = await self._genai.embed(queries, input_type="SEARCH_QUERY")
        fused: dict[str, float] = {}
        k = float(self._settings.rag_rrf_k)
        derived_weight = 1.0 / max(1, len(queries) - 1)
        for index, (query, embedding) in enumerate(zip(queries, embeddings, strict=False)):
            mode = SearchMode.VECTOR if query in vector_only else SearchMode.HYBRID
            hits = await self._oracle.hybrid_search(
                query, embedding, candidate_limit, mode=mode, filters=dict(request.filters)
            )
            # 原質問を主軸にし、派生検索文は合計で原質問 1 本分の票に抑える(rag_poc #975)。
            weight = 1.0 if index == 0 else derived_weight
            for rank, hit in enumerate(hits, start=1):
                state.chunks.setdefault(hit.chunk_id, hit)
                fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + weight / (k + rank)
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
        children = [
            _stored_child(chunk, rrf_score=fused.get(chunk.chunk_id, 0.0)) for chunk in anchors
        ]
        all_children = {chunk.chunk_uid: chunk for chunk in children}
        for sibling in siblings:
            all_children.setdefault(sibling.chunk_id, _stored_child(sibling, rrf_score=0.0))
        parents = _stored_parents(list(all_children.values()), state)
        return HybridSearchResult(
            child_chunks=children, all_chunks=[*all_children.values(), *parents]
        )

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


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        return default


def _group_id(chunk: RetrievedChunk) -> str:
    return str(chunk.metadata.get("chunk_group_id") or chunk.chunk_id)


def _stored_child(chunk: RetrievedChunk, *, rrf_score: float) -> Any:
    from docrag.models.storage import StoredChunk

    metadata = _docrag_metadata(chunk)
    metadata["rrf_score"] = rrf_score
    metadata["document_id"] = chunk.document_id
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
    )
