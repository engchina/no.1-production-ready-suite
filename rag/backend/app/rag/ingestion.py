"""取込: VLM 抽出 -> チャンク分割 -> 埋め込み -> Oracle 26ai へ索引。"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter

from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.config import Settings, get_settings
from app.rag.audit import record_rag_ingestion_audit
from app.rag.chunking import Chunk, chunk_text
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
OBSERVABILITY_DOCUMENT_TYPES = frozenset(
    {
        "請求書",
        "領収書",
        "見積書",
        "注文書",
        "発注書",
        "納品書",
        "伝票",
        "invoice",
        "receipt",
        "estimate",
        "purchase_order",
        "delivery_note",
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
        self._vlm = vlm or OciEnterpriseAiClient()
        self._genai = genai or OciGenAiClient()
        self._oracle = oracle or OracleClient()

    async def ingest(self, document_id: str, image_bytes: bytes, prompt: str) -> DocumentDetail:
        """1 ドキュメントを取り込み、ベクトル索引まで行う。"""
        started_at = now()
        trace_id = new_trace_id()
        await self._oracle.update_document_status(document_id, FileStatus.ANALYZING)
        try:
            extracted = await _observe_ingestion_stage(
                trace_id,
                "vlm_extraction",
                self._vlm.extract_with_vlm(image_bytes, prompt),
                attributes={
                    "adapter": self._settings.ai_service_adapter,
                    "model": self._settings.oci_enterprise_ai_vlm_model or "local",
                    "source_bytes": len(image_bytes),
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
                lambda: chunk_text(
                    text,
                    chunk_size=self._settings.rag_chunk_size,
                    overlap=self._settings.rag_chunk_overlap,
                ),
                attributes={
                    "chunk_size": self._settings.rag_chunk_size,
                    "chunk_overlap": self._settings.rag_chunk_overlap,
                    "input_chars": len(text),
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
                    "field_count": len(extraction.fields),
                },
            )
            detail = await self._oracle.update_document_status(document_id, FileStatus.ANALYZED)
            record_ingestion("success", len(chunks))
            record_rag_ingestion_audit(
                trace_id=trace_id,
                document_id=document_id,
                outcome="success",
                source_bytes=image_bytes,
                document_type=_observability_document_type(extraction.document_type),
                extraction_confidence=extraction.confidence,
                field_count=len(extraction.fields),
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
        """抽出結果とチャンクを一貫した indexing stage として保存する。"""
        await self._oracle.save_extraction(document_id, extraction)
        await self._oracle.save_chunks(document_id, chunks, vectors)


def _safe_persistent_error_message(error: Exception) -> str:
    """document table に保存してよい短いエラーメッセージを返す。"""
    if getattr(error, "safe_for_user", False):
        return str(error)[:200]
    return INGESTION_INTERNAL_ERROR_MESSAGE


def _text_for_chunking(extraction: StructuredExtraction) -> str:
    """抽出結果から索引用テキストを作る。"""
    field_text = " ".join(
        f"{key}: {value}" for key, value in extraction.fields.items() if value is not None
    )
    return "\n".join(part for part in [extraction.raw_text, field_text] if part).strip()


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
    """VLM 結果から本文や field 値を除いた安全な trace attribute を作る。"""
    fields = extracted.get("fields")
    raw_text = extracted.get("raw_text")
    warnings = extracted.get("warnings")
    document_type = extracted.get("document_type")
    return {
        "document_type": _observability_document_type(document_type),
        "field_count": len(fields) if isinstance(fields, dict) else 0,
        "raw_text_chars": len(raw_text) if isinstance(raw_text, str) else 0,
        "warning_count": len(warnings) if isinstance(warnings, list) else 0,
    }


def _observability_document_type(value: object) -> str | None:
    """ログ・trace 用 document type を既知カテゴリに正規化する。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized in OBSERVABILITY_DOCUMENT_TYPES else "other"
