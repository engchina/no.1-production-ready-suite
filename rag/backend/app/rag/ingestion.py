"""取込: VLM 抽出 -> チャンク分割 -> 埋め込み -> Oracle 26ai へ索引。"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter

from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.config import Settings, enterprise_ai_vision_model_id, get_settings
from app.rag.audit import record_rag_ingestion_audit
from app.rag.chunking import Chunk, chunk_extraction
from app.rag.observability import (
    TraceOutcome,
    elapsed_ms,
    new_trace_id,
    now,
    record_ingestion,
    record_ingestion_stage,
    record_trace_span,
)
from app.schemas.document import DocumentDetail, FileStatus
from app.schemas.extraction import StructuredExtraction

INGESTION_INTERNAL_ERROR_MESSAGE = "取込処理に失敗しました。時間をおいて再実行してください。"
# 観測用に正規化する知識文書カテゴリ。特定業務(帳票等)には固定しない。
OBSERVABILITY_DOCUMENT_TYPES = frozenset(
    {
        "ドキュメント",
        "文書",
        "社内規程",
        "マニュアル",
        "FAQ",
        "議事録",
        "報告書",
        "技術文書",
        "仕様書",
        "手順書",
        "ナレッジ",
        "policy",
        "manual",
        "faq",
        "meeting_notes",
        "report",
        "guide",
        "specification",
        "procedure",
        "knowledge_base",
    }
)


class IngestionUserError(ValueError):
    """利用者が入力や設定を直せる取込エラー。"""

    safe_for_user = True


class IngestionPipeline:
    """ドキュメント取込パイプライン。"""

    def __init__(
        self,
        vlm: OciEnterpriseAiClient | None = None,
        genai: OciGenAiClient | None = None,
        oracle: OracleClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._vlm = vlm or OciEnterpriseAiClient(settings=self._settings)
        self._genai = genai or OciGenAiClient(settings=self._settings)
        self._oracle = oracle or OracleClient(settings=self._settings)

    async def ingest(
        self,
        document_id: str,
        image_bytes: bytes,
        prompt: str,
        *,
        content_type: str = "application/octet-stream",
    ) -> DocumentDetail:
        """1 ドキュメントを取込し、ベクトル索引まで行う。"""
        started_at = now()
        trace_id = new_trace_id()
        await self._oracle.update_document_status(document_id, FileStatus.INGESTING)
        try:
            extracted = await _observe_ingestion_stage(
                trace_id,
                "vlm_extraction",
                self._vlm.extract_with_vlm(image_bytes, prompt, mime_type=content_type),
                attributes={
                    "adapter": self._settings.ai_service_adapter,
                    "model": enterprise_ai_vision_model_id(self._settings) or "local",
                    "source_bytes": len(image_bytes),
                    "content_type": _observability_content_type(content_type),
                    "prompt_chars": len(prompt),
                },
                result_attributes=_vlm_result_attributes,
            )
            extraction = StructuredExtraction.model_validate(extracted)
            text = _text_for_chunking(extraction)
            if not text:
                raise IngestionUserError("抽出可能なテキストが見つかりませんでした。")
            chunks = _observe_sync_ingestion_stage(
                trace_id,
                "chunking",
                lambda: chunk_extraction(
                    extraction,
                    chunk_size=self._settings.rag_chunk_size,
                    overlap=self._settings.rag_chunk_overlap,
                ),
                attributes={
                    "chunk_profile": "structure_v1",
                    "chunk_size": self._settings.rag_chunk_size,
                    "chunk_overlap": self._settings.rag_chunk_overlap,
                    "input_chars": len(text),
                    **_extraction_structure_attributes(extraction),
                },
                result_attributes=lambda result: {"chunk_count": len(result)},
            )
            if not chunks:
                raise IngestionUserError("索引用チャンクを作成できませんでした。")
            if len(chunks) > self._settings.rag_max_chunks_per_document:
                raise IngestionUserError(
                    "索引用チャンク数が上限を超えています。"
                    f"max={self._settings.rag_max_chunks_per_document}, actual={len(chunks)}"
                )
            vectors = await _observe_ingestion_stage(
                trace_id,
                "embedding",
                self._genai.embed([chunk.text for chunk in chunks]),
                attributes={
                    "adapter": self._settings.ai_service_adapter,
                    "model": self._settings.oci_genai_embedding_model,
                    "input_count": len(chunks),
                },
                result_attributes=lambda result: {"vector_count": len(result)},
            )
            await _observe_ingestion_stage(
                trace_id,
                "indexing",
                self._save_index(document_id, extraction, chunks, vectors),
                attributes={
                    "chunk_count": len(chunks),
                    "vector_count": len(vectors),
                },
            )
            detail = await self._oracle.update_document_status(document_id, FileStatus.INDEXED)
            record_ingestion("success", len(chunks))
            record_rag_ingestion_audit(
                trace_id=trace_id,
                document_id=document_id,
                outcome="success",
                source_bytes=image_bytes,
                document_type=_observability_document_type(extraction.document_type),
                extraction_confidence=extraction.confidence,
                chunk_count=len(chunks),
                vector_count=len(vectors),
                elapsed_ms=elapsed_ms(started_at),
            )
            return detail
        except Exception as exc:
            record_ingestion("error", 0)
            await self._oracle.update_document_status(
                document_id,
                FileStatus.ERROR,
                _safe_persistent_error_message(exc),
            )
            record_rag_ingestion_audit(
                trace_id=trace_id,
                document_id=document_id,
                outcome="error",
                source_bytes=image_bytes,
                elapsed_ms=elapsed_ms(started_at),
                error=exc,
            )
            raise

    async def _save_index(
        self,
        document_id: str,
        extraction: StructuredExtraction,
        chunks: list[Chunk],
        vectors: list[list[float]],
    ) -> None:
        """抽出本文とチャンクを一貫した indexing stage として保存する。"""
        await self._oracle.save_extraction(document_id, extraction)
        await self._oracle.save_chunks(document_id, chunks, vectors)


def _safe_persistent_error_message(error: Exception) -> str:
    """document table に保存してよい短いエラーメッセージを返す。"""
    if getattr(error, "safe_for_user", False):
        return str(error)[:200]
    return INGESTION_INTERNAL_ERROR_MESSAGE


def _text_for_chunking(extraction: StructuredExtraction) -> str:
    """抽出結果から索引用テキストを作る。"""
    return extraction.raw_text.strip()


async def _observe_ingestion_stage[T](
    trace_id: str,
    stage: str,
    operation: Awaitable[T],
    *,
    attributes: Mapping[str, object] | None = None,
    result_attributes: Callable[[T], Mapping[str, object]] | None = None,
) -> T:
    """非同期の取込 stage を metrics / trace span に記録する。"""
    started_at = perf_counter()
    base_attributes = dict(attributes or {})
    try:
        result = await operation
    except asyncio.CancelledError as exc:
        elapsed = perf_counter() - started_at
        _record_ingestion_stage(trace_id, stage, "cancelled", elapsed, base_attributes, exc)
        raise
    except Exception as exc:
        elapsed = perf_counter() - started_at
        _record_ingestion_stage(trace_id, stage, "error", elapsed, base_attributes, exc)
        raise
    elapsed = perf_counter() - started_at
    if result_attributes is not None:
        base_attributes.update(result_attributes(result))
    _record_ingestion_stage(trace_id, stage, "success", elapsed, base_attributes)
    return result


def _observe_sync_ingestion_stage[T](
    trace_id: str,
    stage: str,
    operation: Callable[[], T],
    *,
    attributes: Mapping[str, object] | None = None,
    result_attributes: Callable[[T], Mapping[str, object]] | None = None,
) -> T:
    """同期の取込 stage を metrics / trace span に記録する。"""
    started_at = perf_counter()
    base_attributes = dict(attributes or {})
    try:
        result = operation()
    except Exception as exc:
        elapsed = perf_counter() - started_at
        _record_ingestion_stage(trace_id, stage, "error", elapsed, base_attributes, exc)
        raise
    elapsed = perf_counter() - started_at
    if result_attributes is not None:
        base_attributes.update(result_attributes(result))
    _record_ingestion_stage(trace_id, stage, "success", elapsed, base_attributes)
    return result


def _record_ingestion_stage(
    trace_id: str,
    stage: str,
    outcome: TraceOutcome,
    elapsed: float,
    attributes: Mapping[str, object],
    error: BaseException | None = None,
) -> None:
    """低 cardinality の stage metric と安全な trace span を記録する。"""
    record_ingestion_stage(stage, outcome, elapsed)
    record_trace_span(
        trace_id=trace_id,
        span_name=stage,
        outcome=outcome,
        seconds=elapsed,
        attributes=dict(attributes),
        error=error,
    )


def _vlm_result_attributes(extracted: dict[str, object]) -> Mapping[str, object]:
    """VLM 結果から本文値を除いた安全な trace attribute を作る。"""
    raw_text = extracted.get("raw_text")
    warnings = extracted.get("warnings")
    document_type = extracted.get("document_type")
    return {
        "document_type": _observability_document_type(document_type),
        "raw_text_chars": len(raw_text) if isinstance(raw_text, str) else 0,
        "warning_count": len(warnings) if isinstance(warnings, list) else 0,
        **_raw_extraction_structure_attributes(extracted),
    }


def _extraction_structure_attributes(extraction: StructuredExtraction) -> Mapping[str, object]:
    """構造化抽出から非機密な件数だけを trace attribute にする。"""
    pages = {
        element.page_number for element in extraction.elements if element.page_number is not None
    }
    return {
        "element_count": len(extraction.elements),
        "table_count": sum(1 for element in extraction.elements if element.kind == "table"),
        "page_count": len(pages),
    }


def _raw_extraction_structure_attributes(extracted: Mapping[str, object]) -> Mapping[str, object]:
    """VLM 生 payload から本文を見ずに構造件数だけを読む。"""
    elements = extracted.get("elements")
    if not isinstance(elements, list):
        return {"element_count": 0, "table_count": 0, "page_count": 0}
    table_count = 0
    pages: set[int] = set()
    for item in elements:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("kind", "")).strip().casefold() == "table":
            table_count += 1
        page = item.get("page_number")
        if isinstance(page, int) and not isinstance(page, bool) and page >= 1:
            pages.add(page)
    return {
        "element_count": len(elements),
        "table_count": table_count,
        "page_count": len(pages),
    }


def _observability_document_type(value: object) -> str | None:
    """ログ・trace 用 document type を既知カテゴリに正規化する。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized in OBSERVABILITY_DOCUMENT_TYPES else "other"


def _observability_content_type(value: str) -> str:
    """ログ・trace 用 MIME type を低 cardinality に正規化する。"""
    normalized = value.split(";", maxsplit=1)[0].strip().lower()
    if normalized in {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "text/plain",
        "application/octet-stream",
    }:
        return normalized
    if normalized.startswith("image/"):
        return "image/other"
    if normalized.startswith("text/"):
        return "text/other"
    if normalized:
        return "other"
    return "application/octet-stream"
