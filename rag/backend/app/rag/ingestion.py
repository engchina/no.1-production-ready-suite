"""取込: VLM 抽出 -> チャンク分割 -> 埋め込み -> Oracle 26ai へ索引。"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter

from app.clients.oci_enterprise_ai import (
    EnterpriseAiIncompleteResponseError,
    EnterpriseAiTimeoutError,
    OciEnterpriseAiClient,
)
from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.config import Settings, enterprise_ai_vision_model_id, get_settings
from app.rag.audit import record_rag_ingestion_audit
from app.rag.chunking import Chunk, chunk_extraction
from app.rag.ingestion_quality import build_ingestion_quality_report
from app.rag.ingestion_strategy import extraction_strategy_for_source
from app.rag.observability import (
    TraceOutcome,
    elapsed_ms,
    new_trace_id,
    now,
    record_ingestion,
    record_ingestion_stage,
    record_trace_span,
)
from app.rag.pdf_segments import PdfPageSegment, split_pdf_page_segments
from app.schemas.document import DocumentDetail, FileStatus, SourceProfile
from app.schemas.extraction import DocumentElement, StructuredExtraction

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


class IngestionTimeoutError(IngestionUserError):
    """上流 AI 処理の timeout により再実行または設定変更が必要な取込エラー。"""


@dataclass(frozen=True)
class _SegmentExtraction:
    """PDF segment とその抽出結果。"""

    segment: PdfPageSegment
    extraction: StructuredExtraction


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
        source_profile: SourceProfile | None = None,
    ) -> DocumentDetail:
        """1 ドキュメントを取込し、ベクトル索引まで行う。"""
        started_at = now()
        trace_id = new_trace_id()
        strategy = extraction_strategy_for_source(
            source_profile=source_profile,
            base_prompt=prompt,
        )
        await self._oracle.update_document_status(document_id, FileStatus.INGESTING)
        try:
            try:
                extracted = await self._extract_with_vlm(
                    trace_id=trace_id,
                    source_bytes=image_bytes,
                    prompt=strategy.prompt,
                    content_type=content_type,
                    parser_profile=strategy.parser_profile,
                )
            except EnterpriseAiTimeoutError as exc:
                raise IngestionTimeoutError(str(exc)) from exc
            except EnterpriseAiIncompleteResponseError as exc:
                raise IngestionUserError(str(exc)) from exc
            extraction = StructuredExtraction.model_validate(extracted)
            quality_report = build_ingestion_quality_report(
                extraction,
                source_profile=source_profile,
                parser_profile=strategy.parser_profile,
            )
            extraction = extraction.model_copy(update={"quality_report": quality_report})
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
                    "parser_profile": strategy.parser_profile,
                    "quality_risk_level": quality_report.risk_level,
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

    async def _extract_with_vlm(
        self,
        *,
        trace_id: str,
        source_bytes: bytes,
        prompt: str,
        content_type: str,
        parser_profile: str,
    ) -> dict[str, object]:
        """必要なら PDF を segment に分けて VLM 抽出する。"""
        segments = _pdf_segments_for_ingestion(
            source_bytes,
            content_type=content_type,
            max_pages_per_segment=self._settings.rag_pdf_max_pages_per_segment,
            max_segments=self._settings.rag_pdf_max_segments,
            enabled=self._settings.rag_pdf_segmentation_enabled,
        )
        if not segments or (len(segments) == 1 and segments[0].page_count <= 1):
            return await self._extract_single_vlm_input(
                trace_id=trace_id,
                source_bytes=source_bytes,
                prompt=prompt,
                content_type=content_type,
                parser_profile=parser_profile,
                stage="vlm_extraction",
                extra_attributes={"pdf_segmented": False},
            )

        segment_extractions: list[_SegmentExtraction] = []
        for segment in segments:
            segment_extractions.extend(
                await self._extract_pdf_segment(
                    trace_id=trace_id,
                    segment=segment,
                    prompt=prompt,
                    parser_profile=parser_profile,
                    original_source_bytes=len(source_bytes),
                    segment_total=len(segments),
                )
            )
        return _merge_pdf_segment_extractions(segment_extractions).to_document_payload()

    async def _extract_pdf_segment(
        self,
        *,
        trace_id: str,
        segment: PdfPageSegment,
        prompt: str,
        parser_profile: str,
        original_source_bytes: int,
        segment_total: int,
    ) -> list[_SegmentExtraction]:
        """PDF segment を抽出し、出力上限に当たった場合は単ページへ分割して再試行する。"""
        segment_prompt = _pdf_segment_prompt(prompt, segment)
        try:
            extracted = await self._extract_single_vlm_input(
                trace_id=trace_id,
                source_bytes=segment.content,
                prompt=segment_prompt,
                content_type="application/pdf",
                parser_profile=parser_profile,
                stage="vlm_extraction",
                extra_attributes={
                    "pdf_segmented": True,
                    "pdf_segment_index": segment.index,
                    "pdf_segment_total": segment_total,
                    "pdf_page_start": segment.page_start,
                    "pdf_page_end": segment.page_end,
                    "pdf_page_count": segment.page_count,
                    "source_bytes": original_source_bytes,
                    "segment_bytes": len(segment.content),
                },
            )
            return [
                _SegmentExtraction(
                    segment=segment,
                    extraction=StructuredExtraction.model_validate(extracted),
                )
            ]
        except EnterpriseAiIncompleteResponseError:
            if segment.page_count <= 1:
                raise
            page_segments = split_pdf_page_segments(
                segment.content,
                max_pages_per_segment=1,
                page_number_offset=segment.page_start - 1,
            )
            if len(page_segments) <= 1:
                raise
            results: list[_SegmentExtraction] = []
            for page_segment in page_segments:
                results.extend(
                    await self._extract_pdf_segment(
                        trace_id=trace_id,
                        segment=page_segment,
                        prompt=prompt,
                        parser_profile=parser_profile,
                        original_source_bytes=original_source_bytes,
                        segment_total=segment_total,
                    )
                )
            return results

    async def _extract_single_vlm_input(
        self,
        *,
        trace_id: str,
        source_bytes: bytes,
        prompt: str,
        content_type: str,
        parser_profile: str,
        stage: str,
        extra_attributes: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """単一入力を VLM に渡し、trace span を記録する。"""
        attributes = {
            "model": enterprise_ai_vision_model_id(self._settings),
            "source_bytes": len(source_bytes),
            "content_type": _observability_content_type(content_type),
            "parser_profile": parser_profile,
            "prompt_chars": len(prompt),
        }
        attributes.update(extra_attributes or {})
        return await _observe_ingestion_stage(
            trace_id,
            stage,
            self._vlm.extract_with_vlm(
                source_bytes,
                prompt,
                mime_type=content_type,
                parser_profile=parser_profile,
            ),
            attributes=attributes,
            result_attributes=_vlm_result_attributes,
        )

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


def _pdf_segments_for_ingestion(
    source_bytes: bytes,
    *,
    content_type: str,
    max_pages_per_segment: int,
    max_segments: int,
    enabled: bool,
) -> list[PdfPageSegment]:
    """取込対象 PDF を segment 化する。PDF でなければ空 list。"""
    if not enabled or _observability_content_type(content_type) != "application/pdf":
        return []
    segments = split_pdf_page_segments(
        source_bytes,
        max_pages_per_segment=max_pages_per_segment,
    )
    if len(segments) > max_segments:
        raise IngestionUserError(
            "PDF のページ分割数が上限を超えています。"
            f"max_segments={max_segments}, actual={len(segments)}"
        )
    return segments


def _pdf_segment_prompt(prompt: str, segment: PdfPageSegment) -> str:
    """元 PDF 上の page range を VLM へ明示する。"""
    if segment.page_start == segment.page_end:
        page_range = f"{segment.page_start}"
    else:
        page_range = f"{segment.page_start}-{segment.page_end}"
    return (
        f"{prompt}\n\n"
        "PDF 分割抽出指示:\n"
        f"- この入力は元 PDF の page {page_range} だけを含みます。\n"
        "- page_number を出力する場合は、分割後 PDF 内の番号ではなく"
        "元 PDF のページ番号で返してください。\n"
        "- raw_text には読み順の本文だけを入れ、前後の別ページを推測しないでください。"
    )


def _merge_pdf_segment_extractions(
    segment_extractions: Sequence[_SegmentExtraction],
) -> StructuredExtraction:
    """複数 PDF segment の抽出結果を 1 つの StructuredExtraction へ統合する。"""
    if not segment_extractions:
        return StructuredExtraction()

    raw_parts: list[str] = []
    elements: list[DocumentElement] = []
    warnings: list[str] = ["pdf_segmented_extraction"]
    confidence_values: list[float] = []
    document_type = "ドキュメント"
    next_order = 0

    for segment_extraction in segment_extractions:
        segment = segment_extraction.segment
        extraction = segment_extraction.extraction
        if extraction.document_type and extraction.document_type != "ドキュメント":
            document_type = extraction.document_type
        confidence_values.append(extraction.confidence)
        warnings.extend(extraction.warnings)
        text = extraction.raw_text.strip()
        if text:
            raw_parts.append(f"{_page_marker(segment)}\n{text}")
        for element in extraction.elements:
            adjusted = _element_with_absolute_page(element, segment, order=next_order)
            elements.append(adjusted)
            next_order += 1

    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    return StructuredExtraction(
        raw_text="\n\n".join(raw_parts),
        document_type=document_type,
        confidence=confidence,
        warnings=_dedupe_text(warnings),
        elements=elements,
    )


def _page_marker(segment: PdfPageSegment) -> str:
    """raw_text に入れる page marker。infer_document_elements が解釈できる形にする。"""
    return f"--- page {segment.page_start} ---"


def _element_with_absolute_page(
    element: DocumentElement,
    segment: PdfPageSegment,
    *,
    order: int,
) -> DocumentElement:
    """segment 内 page_number を元 PDF の絶対ページ番号へ寄せる。"""
    return element.model_copy(
        update={
            "order": order,
            "page_number": _absolute_page_number(element.page_number, segment),
        }
    )


def _absolute_page_number(page_number: int | None, segment: PdfPageSegment) -> int:
    """VLM が返す local/absolute page_number を元 PDF ページ番号へ正規化する。"""
    if page_number is None:
        return segment.page_start
    if segment.page_start <= page_number <= segment.page_end:
        return page_number
    if 1 <= page_number <= segment.page_count:
        return segment.page_start + page_number - 1
    return page_number


def _dedupe_text(values: Sequence[str]) -> list[str]:
    """空文字を落として順序を保ったまま重複排除する。"""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


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
