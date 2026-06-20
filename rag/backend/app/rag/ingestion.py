"""取込: VLM 抽出 -> チャンク分割 -> 埋め込み -> Oracle 26ai へ索引。"""

import asyncio
import hashlib
import json
import logging
import re
import zipfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from time import perf_counter
from uuid import uuid4

from pydantic import ValidationError
from rag_parser_core.preprocess import ConvertOutcome, SourceDerivation
from rag_pipeline_core.raptor import build_raptor_summaries
from rag_pipeline_core.stage import ChunkingStageRequest

from app.clients.object_storage import ObjectStorageClient
from app.clients.oci_document_understanding import OciDocumentUnderstandingClient
from app.clients.oci_enterprise_ai import (
    EnterpriseAiIncompleteResponseError,
    EnterpriseAiTimeoutError,
    EnterpriseAiValidationError,
    OciEnterpriseAiClient,
)
from app.clients.oci_genai import OciGenAiClient
from app.clients.oci_speech import OciSpeechClient
from app.clients.oracle import OracleClient
from app.clients.parser_service import ParserServiceClient
from app.clients.pipeline_stage import PipelineStageClient
from app.clients.preprocess_service import PreprocessServiceClient
from app.config import Settings, enterprise_ai_vision_model_id, get_settings
from app.rag.asset_summary import summarize_assets
from app.rag.audit import record_rag_ingestion_audit
from app.rag.chunking import Chunk, chunk_extraction_with_strategy
from app.rag.chunking_strategy import resolve_chunking_params
from app.rag.extraction_field_adapter import (
    FieldDefinition,
    extract_fields_from_extraction,
    field_definitions_prompt,
    load_field_schema,
    parse_extraction_fields,
)
from app.rag.graph_adapter import resolve_graph_adapter
from app.rag.graph_index import GraphIndex, build_graph_index
from app.rag.ingestion_quality import build_ingestion_quality_report
from app.rag.ingestion_strategy import extraction_strategy_for_source
from app.rag.navigation import (
    build_navigation_tree,
    navigation_summary_elements,
    summarize_navigation_nodes,
)
from app.rag.observability import (
    TraceOutcome,
    elapsed_ms,
    new_trace_id,
    now,
    record_ingestion,
    record_ingestion_stage,
    record_trace_span,
)
from app.rag.parsers import (
    SERVICE_ADAPTER_BACKENDS,
    OfficeSegmentExtraction,
    OfficeSegmentFailure,
    ParserRegistryResult,
    parse_openxml_office_segment_extractions,
    parse_with_registry,
    template_for_source_profile,
)
from app.rag.pdf_segments import PdfPageSegment, split_pdf_page_segments
from app.rag.preprocess_strategy import resolve_preprocess_profile
from app.schemas.document import DocumentDetail, FileStatus, IngestionSegment, SourceProfile
from app.schemas.extraction import (
    DocumentElement,
    ExtractionArtifactValue,
    ExtractionAsset,
    ExtractionField,
    ExtractionMetadataValue,
    ExtractionPage,
    ExtractionTable,
    IngestionQualityReport,
    StructuredExtraction,
)

INGESTION_INTERNAL_ERROR_MESSAGE = "取込処理に失敗しました。時間をおいて再実行してください。"
INGESTION_JOB_CANCELLED_MESSAGE = "利用者によりキャンセルされました。"
EXTRACTION_ARTIFACT_SCHEMA_VERSION = 1
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
STRUCTURE_TABLE_COUNT_KEYS = ("table_count", "adapter_table_count")
STRUCTURE_FIGURE_COUNT_KEYS = (
    "figure_count",
    "image_count",
    "picture_count",
    "chart_count",
    "asset_count",
)
STRUCTURE_FORMULA_COUNT_KEYS = ("formula_count", "equation_count")
STRUCTURE_PAGE_COUNT_KEYS = ("page_count", "page_total")
STRUCTURE_ASSET_COUNT_KEYS = ("asset_count", "picture_count", "image_count")
FIGURE_ASSET_KINDS = {"figure", "image", "picture", "chart", "diagram"}
logger = logging.getLogger(__name__)


class IngestionUserError(ValueError):
    """利用者が入力や設定を直せる取込エラー。"""

    safe_for_user = True


class IngestionTimeoutError(IngestionUserError):
    """上流 AI 処理の timeout により再実行または設定変更が必要な取込エラー。"""


class IngestionCancelledError(IngestionUserError):
    """cooperative cancellation により取込を中断した。"""


@dataclass(frozen=True)
class _SegmentExtraction:
    """PDF segment とその抽出結果。"""

    segment: PdfPageSegment
    extraction: StructuredExtraction


@dataclass(frozen=True)
class _CachedSegmentLoadResult:
    """成功済み segment artifact の読み戻し結果。"""

    cached: list[_SegmentExtraction]
    missing_ranges: set[tuple[int, int]]


class IngestionPipeline:
    """ドキュメント取込パイプライン。"""

    def __init__(
        self,
        vlm: OciEnterpriseAiClient | None = None,
        genai: OciGenAiClient | None = None,
        oracle: OracleClient | None = None,
        object_storage: ObjectStorageClient | None = None,
        document_understanding: OciDocumentUnderstandingClient | None = None,
        speech: OciSpeechClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._vlm = vlm or OciEnterpriseAiClient(settings=self._settings)
        self._genai = genai or OciGenAiClient(settings=self._settings)
        self._oracle = oracle or OracleClient(settings=self._settings)
        self._object_storage = object_storage or ObjectStorageClient(settings=self._settings)
        # 外部 adapter は同一プロセスで import せず parser マイクロサービスへ HTTP 委譲する。
        self._parser_service = ParserServiceClient(settings=self._settings)
        # pipeline ステージ(chunking 等)のプラグイン委譲。未達は in-process へ縮退する。
        self._pipeline_stage = PipelineStageClient(self._settings)
        # 前処理(parse 前の原本変換)。軽量正規化は in-process、重い変換はサービスへ委譲。
        self._preprocess = PreprocessServiceClient(self._settings)
        # service 系 backend(OCI Document Understanding)を backend から直接呼ぶ。
        self._document_understanding = document_understanding or OciDocumentUnderstandingClient(
            settings=self._settings
        )
        # 音声/動画の文字起こし(OCI AI Speech 優先、未設定/失敗は parser-asr へ縮退)。
        self._speech = speech or OciSpeechClient(settings=self._settings)

    async def ingest(
        self,
        document_id: str,
        image_bytes: bytes,
        prompt: str,
        *,
        content_type: str = "application/octet-stream",
        source_profile: SourceProfile | None = None,
        cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    ) -> DocumentDetail:
        """1 ドキュメントを取込し、ベクトル索引まで行う。"""
        started_at = now()
        trace_id = new_trace_id()
        strategy = extraction_strategy_for_source(
            source_profile=source_profile,
            base_prompt=prompt,
        )
        await self._oracle.update_document_status(document_id, FileStatus.INGESTING)
        checkpoint_segments: list[IngestionSegment] = []
        try:
            await _raise_if_cancelled(cancel_checker)
            # 前処理(Preprocess): parse の前に原本を一度だけ canonical な中間物へ変換し、
            # 派生系譜(SourceDerivation)を残す。passthrough(既定)は原本そのまま。
            parse_bytes, parse_content_type, source_derivation = await self._preprocess_source(
                trace_id=trace_id,
                document_id=document_id,
                source_bytes=image_bytes,
                content_type=content_type,
                source_profile=source_profile,
            )
            await _raise_if_cancelled(cancel_checker)
            parser_result = await _observe_cpu_ingestion_stage(
                trace_id,
                "source_partition",
                lambda: parse_with_registry(
                    parse_bytes,
                    source_profile=source_profile,
                    content_type=parse_content_type,
                    adapter_backend=getattr(
                        self._settings,
                        "rag_parser_adapter_backend",
                        "local",
                    ),
                    docling_enabled=getattr(
                        self._settings,
                        "rag_parser_docling_enabled",
                        False,
                    ),
                    marker_enabled=getattr(
                        self._settings,
                        "rag_parser_marker_enabled",
                        False,
                    ),
                    unstructured_enabled=getattr(
                        self._settings,
                        "rag_parser_unstructured_enabled",
                        False,
                    ),
                    external_adapter_runner=self._parser_service.runner,
                ),
                attributes={
                    "content_type": _observability_content_type(parse_content_type),
                    "source_modality": (
                        source_profile.modality.value if source_profile is not None else "unknown"
                    ),
                    "parser_profile": strategy.parser_profile,
                    "preprocess_profile": source_derivation.preprocess_profile,
                    "preprocess_converted": source_derivation.converted,
                },
                result_attributes=_parser_result_attributes,
            )
            audio_extraction: StructuredExtraction | None = None
            if parser_result.unsupported_reason == "audio_transcription_not_configured":
                # 音声/動画は OCI AI Speech → ローカル faster-whisper の順で文字起こしする。
                audio_extraction = await self._transcribe_audio(
                    trace_id=trace_id,
                    document_id=document_id,
                    source_bytes=parse_bytes,
                    content_type=parse_content_type,
                    source_profile=source_profile,
                    cancel_checker=cancel_checker,
                )
                if audio_extraction is None:
                    raise IngestionUserError(_unsupported_parser_message(parser_result))
            elif parser_result.unsupported_reason:
                raise IngestionUserError(_unsupported_parser_message(parser_result))
            checkpoint_segments = await self._prepare_segment_checkpoints(
                document_id=document_id,
                source_bytes=parse_bytes,
                content_type=parse_content_type,
                source_profile=source_profile,
                parser_backend=_checkpoint_parser_backend(parser_result, source_profile),
                parser_profile=strategy.parser_profile,
            )
            await _raise_if_cancelled(cancel_checker)
            # 音声文字起こしが成功していれば以降の parser/VLM 経路は短絡する。
            extraction: StructuredExtraction | None = audio_extraction
            service_backend = _service_parser_backend(parser_result.parser_backend)
            try:
                if service_backend is not None:
                    # 明示選択された service backend(OCI Enterprise AI VLM /
                    # Document Understanding)はローカル/外部 adapter を飛ばして直接呼ぶ。
                    # DU が利用不可/失敗のときは None を返し、下の標準フローへ安全に縮退する。
                    extraction = await self._extract_with_service_backend(
                        service_backend,
                        trace_id=trace_id,
                        document_id=document_id,
                        source_bytes=parse_bytes,
                        prompt=strategy.prompt,
                        content_type=parse_content_type,
                        parser_profile=strategy.parser_profile,
                        checkpoint_segments=checkpoint_segments,
                        cancel_checker=cancel_checker,
                    )
                if extraction is not None:
                    pass
                elif parser_result.extraction is not None and _is_external_adapter_backend(
                    parser_result.parser_backend
                ):
                    extraction = parser_result.extraction
                else:
                    office_extraction = await self._extract_local_office_segments(
                        document_id=document_id,
                        trace_id=trace_id,
                        source_bytes=parse_bytes,
                        source_profile=source_profile,
                        checkpoint_segments=checkpoint_segments,
                        cancel_checker=cancel_checker,
                    )
                    if office_extraction is not None:
                        extraction = office_extraction
                    elif parser_result.extraction is not None:
                        extraction = parser_result.extraction
                    else:
                        cached_extraction = await self._load_cached_full_extraction(
                            checkpoint_segments
                        )
                        if cached_extraction is not None:
                            extraction = cached_extraction
                        else:
                            extracted = await self._extract_with_vlm(
                                trace_id=trace_id,
                                document_id=document_id,
                                source_bytes=parse_bytes,
                                prompt=strategy.prompt,
                                content_type=parse_content_type,
                                parser_profile=strategy.parser_profile,
                                checkpoint_segments=checkpoint_segments,
                                cancel_checker=cancel_checker,
                            )
                            extraction = _validate_structured_extraction_payload(extracted)
            except EnterpriseAiTimeoutError as exc:
                raise IngestionTimeoutError(str(exc)) from exc
            except EnterpriseAiIncompleteResponseError as exc:
                raise IngestionUserError(str(exc)) from exc
            # 上の分岐はいずれかで必ず extraction を確定させる。
            assert extraction is not None
            await _raise_if_cancelled(cancel_checker)
            extraction = _extraction_with_parser_context(
                extraction,
                parser_result=parser_result,
                fallback_template=template_for_source_profile(source_profile),
                source_parser=strategy.parser_profile,
            )
            # 派生系譜(溯源)を抽出 metadata へ刻む。artifact cache / document payload 経由で永続。
            extraction = _extraction_with_source_derivation(extraction, source_derivation)
            quality_report = build_ingestion_quality_report(
                extraction,
                source_profile=source_profile,
                parser_profile=strategy.parser_profile,
                parser_backend=parser_result.parser_backend,
                parser_version=parser_result.parser_version,
                fallback_used=parser_result.fallback_used,
            )
            extraction = extraction.model_copy(update={"quality_report": quality_report})
            await _raise_if_cancelled(cancel_checker)
            extraction, extraction_artifact_path = await self._cache_extraction_artifact(
                document_id=document_id,
                trace_id=trace_id,
                extraction=extraction,
            )
            quality_report = build_ingestion_quality_report(
                extraction,
                source_profile=source_profile,
                parser_profile=strategy.parser_profile,
                parser_backend=parser_result.parser_backend,
                parser_version=parser_result.parser_version,
                fallback_used=parser_result.fallback_used,
            )
            extraction = extraction.model_copy(update={"quality_report": quality_report})
            extraction = await self._attach_asset_summaries(trace_id, extraction)
            extraction = await self._attach_extraction_fields(trace_id, extraction)
            extraction = await self._attach_navigation_tree(trace_id, extraction)
            checkpoint_segments = await self._mark_segments_succeeded(
                checkpoint_segments,
                artifact_path=extraction_artifact_path,
            )
            text = _text_for_chunking(extraction)
            if not text:
                raise IngestionUserError("抽出可能なテキストが見つかりませんでした。")
            if self._settings.rag_review_gate_enabled:
                # REVIEW で停止する前に抽出本文を永続化し、プレビュー・後段 index で再利用する。
                await self._oracle.save_extraction(document_id, extraction)
                detail = await self._oracle.update_document_status(document_id, FileStatus.REVIEW)
                record_ingestion("review", 0)
                return detail
            return await self._run_index_phase(
                trace_id=trace_id,
                document_id=document_id,
                extraction=extraction,
                quality_report=quality_report,
                parser_profile=strategy.parser_profile,
                checkpoint_segments=checkpoint_segments,
                source_bytes=image_bytes,
                started_at=started_at,
                cancel_checker=cancel_checker,
            )
        except IngestionCancelledError as exc:
            await self._mark_segments_cancelled(checkpoint_segments)
            record_ingestion("cancelled", 0)
            record_rag_ingestion_audit(
                trace_id=trace_id,
                document_id=document_id,
                outcome="error",
                source_bytes=image_bytes,
                segment_count=len(checkpoint_segments),
                failed_segment_count=_failed_checkpoint_count(checkpoint_segments),
                elapsed_ms=elapsed_ms(started_at),
                error=exc,
            )
            raise
        except Exception as exc:
            record_ingestion("error", 0)
            await self._mark_segments_failed(checkpoint_segments, error=exc)
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
                segment_count=len(checkpoint_segments),
                failed_segment_count=_failed_checkpoint_count(checkpoint_segments),
                elapsed_ms=elapsed_ms(started_at),
                error=exc,
            )
            raise

    async def index_reviewed(
        self,
        document_id: str,
        *,
        cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    ) -> DocumentDetail:
        """REVIEW で承認済みの文書を後段(chunk→embed→index)だけ実行する。

        前段(parse/抽出)は再実行せず、保存済み抽出本文を再利用する。
        2 段階処理(parse → 人がプレビュー確認 → index)の INDEX フェーズ。
        """
        started_at = now()
        trace_id = new_trace_id()
        detail = await self._oracle.get_document(document_id)
        if detail is None:
            raise IngestionUserError("ドキュメントが見つかりません。")
        if not detail.extraction:
            raise IngestionUserError("索引対象の抽出結果が見つかりません。")
        extraction = _validate_structured_extraction_payload(detail.extraction)
        quality_report = extraction.quality_report or build_ingestion_quality_report(extraction)
        await self._oracle.update_document_status(document_id, FileStatus.INDEXING)
        checkpoint_segments = await self._safe_list_ingestion_segments(document_id)
        try:
            return await self._run_index_phase(
                trace_id=trace_id,
                document_id=document_id,
                extraction=extraction,
                quality_report=quality_report,
                parser_profile=quality_report.parser_profile,
                checkpoint_segments=checkpoint_segments,
                source_bytes=b"",
                started_at=started_at,
                cancel_checker=cancel_checker,
            )
        except IngestionCancelledError as exc:
            record_ingestion("cancelled", 0)
            record_rag_ingestion_audit(
                trace_id=trace_id,
                document_id=document_id,
                outcome="error",
                source_bytes=b"",
                segment_count=len(checkpoint_segments),
                failed_segment_count=_failed_checkpoint_count(checkpoint_segments),
                elapsed_ms=elapsed_ms(started_at),
                error=exc,
            )
            raise
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
                source_bytes=b"",
                segment_count=len(checkpoint_segments),
                failed_segment_count=_failed_checkpoint_count(checkpoint_segments),
                elapsed_ms=elapsed_ms(started_at),
                error=exc,
            )
            raise

    async def _run_index_phase(
        self,
        *,
        trace_id: str,
        document_id: str,
        extraction: StructuredExtraction,
        quality_report: IngestionQualityReport,
        parser_profile: str,
        checkpoint_segments: list[IngestionSegment],
        source_bytes: bytes,
        started_at: float,
        cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    ) -> DocumentDetail:
        """抽出結果から chunk→embed→index を実行し INDEXED まで進める後段。

        例外はそのまま呼び出し側(ingest / index_reviewed)の except へ伝播させる。
        """
        text = _text_for_chunking(extraction)
        if not text:
            raise IngestionUserError("抽出可能なテキストが見つかりませんでした。")
        await _raise_if_cancelled(cancel_checker)
        chunking_params = resolve_chunking_params(self._settings)

        def _run_chunking() -> list[Chunk]:
            # remote(chunking マイクロサービス)優先。未達/無効は同一ロジックで in-process 実行。
            request = ChunkingStageRequest(
                extraction=extraction,
                strategy=chunking_params.strategy,
                chunk_size=chunking_params.chunk_size,
                overlap=chunking_params.overlap,
                child_size=chunking_params.child_size,
                sentence_window_size=chunking_params.sentence_window_size,
                min_chars=chunking_params.min_chars,
            )
            remote = self._pipeline_stage.run_chunking(request)
            if remote is not None:
                return remote
            return chunk_extraction_with_strategy(
                extraction,
                strategy=chunking_params.strategy,
                chunk_size=chunking_params.chunk_size,
                overlap=chunking_params.overlap,
                child_size=chunking_params.child_size,
                sentence_window_size=chunking_params.sentence_window_size,
                min_chars=chunking_params.min_chars,
            )

        chunks = await _observe_cpu_ingestion_stage(
            trace_id,
            "chunking",
            _run_chunking,
            attributes={
                "chunk_profile": "structure_v1",
                "chunk_strategy": chunking_params.strategy,
                "chunk_size": chunking_params.chunk_size,
                "chunk_overlap": chunking_params.overlap,
                "chunk_child_size": chunking_params.child_size,
                "chunk_sentence_window_size": chunking_params.sentence_window_size,
                "chunk_min_chars": chunking_params.min_chars,
                "input_chars": len(text),
                "parser_profile": parser_profile,
                "parser_backend": quality_report.parser_backend,
                "fallback_used": quality_report.fallback_used,
                "quality_risk_level": quality_report.risk_level,
                **_extraction_structure_attributes(extraction),
            },
            result_attributes=lambda result: {"chunk_count": len(result)},
        )
        if not chunks:
            raise IngestionUserError("索引用チャンクを作成できませんでした。")
        # RAPTOR 再帰要約索引(opt-in)。leaf に summary node を足して索引する。要約失敗は leaf のみ。
        chunks = await self._augment_with_raptor(trace_id, chunks, cancel_checker)
        # 派生系譜(溯源)を chunk metadata へ貫通させ、citation → 派生原本 → 原本を追跡可能にする。
        chunks = _chunks_with_source_derivation(chunks, _source_derivation_id(extraction))
        await _raise_if_cancelled(cancel_checker)
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
        await _raise_if_cancelled(cancel_checker)
        await _observe_ingestion_stage(
            trace_id,
            "indexing",
            self._save_index(
                trace_id,
                document_id,
                extraction,
                chunks,
                vectors,
                cancel_checker=cancel_checker,
            ),
            attributes={
                "chunk_count": len(chunks),
                "vector_count": len(vectors),
            },
        )
        await _raise_if_cancelled(cancel_checker)
        detail = await self._oracle.update_document_status(document_id, FileStatus.INDEXED)
        record_ingestion("success", len(chunks))
        record_rag_ingestion_audit(
            trace_id=trace_id,
            document_id=document_id,
            outcome="success",
            source_bytes=source_bytes,
            document_type=_observability_document_type(extraction.document_type),
            extraction_confidence=extraction.confidence,
            parser_backend=quality_report.parser_backend,
            parser_profile=quality_report.parser_profile,
            segment_count=len(checkpoint_segments),
            fallback_count=1 if quality_report.fallback_used else 0,
            failed_segment_count=quality_report.failed_segment_count,
            chunk_count=len(chunks),
            vector_count=len(vectors),
            elapsed_ms=elapsed_ms(started_at),
        )
        return detail

    async def _preprocess_source(
        self,
        *,
        trace_id: str,
        document_id: str,
        source_bytes: bytes,
        content_type: str,
        source_profile: SourceProfile | None,
    ) -> tuple[bytes, str, SourceDerivation]:
        """parse の前に原本を canonical な中間物へ変換し、(canonical bytes, content_type,
        派生系譜)を返す。

        passthrough(既定)は変換せず原本をそのまま返す(現行挙動と一致)。それ以外は
        in-process(text_normalize)または前処理マイクロサービスへ委譲し、変換物を
        Object Storage へ保存して派生系譜(SourceDerivation)に object path / sha を残す。
        失敗・未対応は passthrough へ安全に縮退する。
        """
        profile = resolve_preprocess_profile(self._settings)
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        if profile == "passthrough":
            return (
                source_bytes,
                content_type,
                _passthrough_derivation(
                    profile=profile, source_sha=source_sha, content_type=content_type
                ),
            )
        outcome: ConvertOutcome = await _observe_cpu_ingestion_stage(
            trace_id,
            "preprocess",
            lambda: self._preprocess.convert(
                source_bytes,
                content_type=content_type,
                source_profile=source_profile,
                profile=profile,
            ),
            attributes={
                "preprocess_profile": profile,
                "content_type": _observability_content_type(content_type),
                "source_bytes": len(source_bytes),
            },
            result_attributes=lambda result: {
                "preprocess_converted": result.converted,
                "preprocess_converter": result.converter_name,
            },
        )
        if not outcome.converted or outcome.derived_bytes is None:
            return (
                source_bytes,
                content_type,
                _passthrough_derivation(
                    profile=profile,
                    source_sha=source_sha,
                    content_type=content_type,
                    converter_name=outcome.converter_name,
                    warnings=list(outcome.warnings),
                ),
            )
        derived_bytes = outcome.derived_bytes
        derived_content_type = outcome.derived_content_type or content_type
        derived_sha = hashlib.sha256(derived_bytes).hexdigest()
        derived_path = await self._cache_canonical_artifact(
            document_id=document_id,
            trace_id=trace_id,
            derived_bytes=derived_bytes,
            content_type=derived_content_type,
        )
        derivation = SourceDerivation(
            derivation_id=uuid4().hex,
            preprocess_profile=profile,
            converted=True,
            converter_name=outcome.converter_name,
            converter_version=outcome.converter_version,
            source_content_type=content_type,
            source_sha256=source_sha,
            derived_object_path=derived_path,
            derived_content_type=derived_content_type,
            derived_sha256=derived_sha,
            page_map={str(key): int(value) for key, value in outcome.page_map.items()},
            warnings=list(outcome.warnings),
        )
        return derived_bytes, derived_content_type, derivation

    async def _cache_canonical_artifact(
        self,
        *,
        document_id: str,
        trace_id: str,
        derived_bytes: bytes,
        content_type: str,
    ) -> str | None:
        """前処理で生成した正規化原本(canonical source)を Object Storage へ保存する。"""
        if not getattr(self._settings, "rag_extraction_artifact_cache_enabled", True):
            return None
        key_prefix = _safe_artifact_prefix(
            getattr(self._settings, "rag_canonical_artifact_prefix", "artifacts/canonical")
        )
        document_key = _safe_artifact_key_part(document_id)
        trace_key = _safe_artifact_key_part(trace_id)
        extension = _canonical_artifact_extension(content_type)
        key = f"{key_prefix}/{document_key}/{trace_key}/canonical{extension}"
        try:
            return await self._object_storage.put(
                key,
                derived_bytes,
                content_type=content_type or "application/octet-stream",
            )
        except Exception as exc:  # noqa: BLE001 - 保存失敗は派生系譜を path 無しで残し取込を続ける
            logger.warning(
                "canonical_artifact_cache_failed",
                extra={"document_id": document_id, "error_type": type(exc).__name__},
            )
            return None

    async def _prepare_segment_checkpoints(
        self,
        *,
        document_id: str,
        source_bytes: bytes,
        content_type: str,
        source_profile: SourceProfile | None,
        parser_backend: str,
        parser_profile: str,
    ) -> list[IngestionSegment]:
        """segment checkpoint を作成し、既存 failed segment があれば再試行対象にする。"""
        if not getattr(self._settings, "rag_segment_checkpoint_enabled", True):
            return []
        existing = await self._safe_list_ingestion_segments(document_id)
        failed = [segment for segment in existing if segment.status == "FAILED"]
        if failed:
            failed_ids = {segment.segment_id for segment in failed}
            for segment in failed:
                await self._safe_update_ingestion_segment(
                    segment.segment_id,
                    status="QUEUED",
                    error_code="retry_failed_segment",
                    error_message="失敗 segment のみ再試行します。",
                )
            return [
                (
                    segment.model_copy(
                        update={"status": "QUEUED", "error_code": "retry_failed_segment"}
                    )
                    if segment.segment_id in failed_ids
                    else segment
                )
                for segment in existing
            ]
        if existing and all(segment.status == "SUCCEEDED" for segment in existing):
            return existing
        segments = await asyncio.to_thread(
            _checkpoint_segments_for_source,
            document_id=document_id,
            source_bytes=source_bytes,
            content_type=content_type,
            source_profile=source_profile,
            parser_backend=parser_backend,
            parser_profile=parser_profile,
            max_pages_per_segment=self._settings.rag_pdf_max_pages_per_segment,
            max_segments=self._settings.rag_pdf_max_segments,
            segmentation_enabled=self._settings.rag_pdf_segmentation_enabled,
        )
        await self._safe_replace_ingestion_segments(document_id, segments)
        return segments

    async def _attach_extraction_fields(
        self,
        trace_id: str,
        extraction: StructuredExtraction,
    ) -> StructuredExtraction:
        """`rag_field_extraction_enabled` が真なら field schema に従い named field を抽出する。

        定義済み field を OCI Enterprise AI の structured output で抽出し、`ExtractionField`
        へ Pydantic 検証して保存する（best-effort、既定 OFF）。
        """
        if not getattr(self._settings, "rag_field_extraction_enabled", False):
            return extraction
        schema = load_field_schema()
        if not schema.fields:
            return extraction

        async def _extract(text: str, field_defs: list[FieldDefinition]) -> list[ExtractionField]:
            prompt = (
                "次の文書から、指定された field を抽出してください。"
                '各 field は {"name", "value", "value_type", "confidence"} の '
                "JSON 配列だけで出力し、見つからない field は省略してください。"
                f"抽出する field 定義: {field_definitions_prompt(field_defs)}"
            )
            raw = await self._vlm.generate(prompt, text)
            return parse_extraction_fields(raw, field_defs)

        try:
            return await extract_fields_from_extraction(extraction, schema.fields, _extract)
        except Exception:  # noqa: BLE001 - field 抽出失敗は取込を妨げない（best-effort）
            logger.warning("field_extraction_failed", extra={"trace_id": trace_id})
            return extraction

    async def _attach_asset_summaries(
        self,
        trace_id: str,
        extraction: StructuredExtraction,
    ) -> StructuredExtraction:
        """`rag_asset_summary_enabled` が真なら図・表・chart を要約して紐付ける。

        object_path がある asset は Object Storage から画像を取得し OCI Enterprise AI VLM で、
        画像が取得できない asset は alt_text を OCI Enterprise AI LLM で要約する（best-effort）。
        既定 OFF。
        """
        if not getattr(self._settings, "rag_asset_summary_enabled", False):
            return extraction
        if not extraction.assets:
            return extraction

        async def _summarize(asset: ExtractionAsset) -> str | None:
            prompt = (
                "この図表の内容を日本語で1〜2文に要約してください。"
                "読み取れない場合は空で返してください。"
            )
            if asset.object_path:
                try:
                    image_bytes = await self._object_storage.get(asset.object_path)
                except Exception:
                    image_bytes = None
                if image_bytes:
                    return await self._vlm.generate_from_image(image_bytes, prompt)
            caption = (asset.alt_text or "").strip()
            if not caption:
                return None
            return await self._vlm.generate(prompt, caption)

        try:
            return await summarize_assets(
                extraction,
                _summarize,
                max_assets=getattr(self._settings, "rag_asset_summary_max_assets", 24),
            )
        except Exception:  # noqa: BLE001 - 要約失敗は取込を妨げない（best-effort）
            logger.warning("asset_summary_failed", extra={"trace_id": trace_id})
            return extraction

    async def _attach_navigation_tree(
        self,
        trace_id: str,
        extraction: StructuredExtraction,
    ) -> StructuredExtraction:
        """章節 navigation tree を決定論的に構築し、任意で node 要約を付与する。

        tree 構築は LLM 不要で常に実行する。node 要約は
        `rag_navigation_summary_enabled` が真のときだけ OCI Enterprise AI LLM で行う。
        """
        nodes = build_navigation_tree(extraction)
        if not nodes:
            return extraction
        if getattr(self._settings, "rag_navigation_summary_enabled", False):

            async def _summarize(text: str) -> str:
                return await self._vlm.generate(
                    "次の章節の内容を日本語で1〜2文に要約してください。"
                    "原文にない情報は補わないでください。",
                    text,
                )

            try:
                nodes = await summarize_navigation_nodes(
                    nodes,
                    extraction,
                    _summarize,
                    max_nodes=getattr(self._settings, "rag_navigation_summary_max_nodes", 24),
                )
            except Exception:  # noqa: BLE001 - 要約失敗は tree 構築を妨げない（best-effort）
                logger.warning("navigation_summary_failed", extra={"trace_id": trace_id})
        # summary がある node は検索可能な section_summary element にして、
        # Knowhere の Navigate / progressive disclosure を hybrid retrieval へつなぐ。
        next_order = max((element.order for element in extraction.elements), default=0) + 1
        summary_elements = navigation_summary_elements(nodes, start_order=next_order)
        updates: dict[str, object] = {"navigation": nodes}
        if summary_elements:
            updates["elements"] = [*extraction.elements, *summary_elements]
        return extraction.model_copy(update=updates)

    async def _cache_extraction_artifact(
        self,
        *,
        document_id: str,
        trace_id: str,
        extraction: StructuredExtraction,
    ) -> tuple[StructuredExtraction, str | None]:
        """抽出 JSON artifact を Object Storage に保存し、path を metadata へ戻す。"""
        if not getattr(self._settings, "rag_extraction_artifact_cache_enabled", True):
            return extraction, None
        key_prefix = _safe_artifact_prefix(
            getattr(
                self._settings,
                "rag_extraction_artifact_prefix",
                "artifacts/extractions",
            )
        )
        document_key = _safe_artifact_key_part(document_id)
        trace_key = _safe_artifact_key_part(trace_id)
        key = f"{key_prefix}/{document_key}/{trace_key}.json"
        cache_extraction = _extraction_with_artifact_cache_metadata(
            extraction,
            artifact_kind="full",
            document_id=document_id,
            trace_id=trace_id,
        )
        payload = cache_extraction.to_document_payload()
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            artifact_path = await self._object_storage.put(
                key,
                data,
                content_type="application/json",
            )
        except Exception as exc:
            logger.warning(
                "extraction_artifact_cache_failed",
                extra={"document_id": document_id, "error_type": type(exc).__name__},
            )
            parser_artifacts = {
                **extraction.parser_artifacts,
                "extraction_artifact_cache_failed": True,
            }
            warnings = _dedupe_text([*extraction.warnings, "extraction_artifact_cache_failed"])
            return (
                extraction.model_copy(
                    update={"parser_artifacts": parser_artifacts, "warnings": warnings}
                ),
                None,
            )
        parser_artifacts = {
            **cache_extraction.parser_artifacts,
            "extraction_artifact_path": artifact_path,
            "extraction_artifact_content_type": "application/json",
        }
        return (
            cache_extraction.model_copy(update={"parser_artifacts": parser_artifacts}),
            artifact_path,
        )

    async def _cache_segment_extraction_artifact(
        self,
        *,
        document_id: str,
        trace_id: str,
        segment: IngestionSegment | None,
        extraction: StructuredExtraction,
    ) -> str | None:
        """成功した PDF segment の抽出 JSON を個別 artifact として保存する。"""
        if segment is None or not getattr(
            self._settings,
            "rag_extraction_artifact_cache_enabled",
            True,
        ):
            return None
        key_prefix = _safe_artifact_prefix(
            getattr(
                self._settings,
                "rag_extraction_artifact_prefix",
                "artifacts/extractions",
            )
        )
        document_key = _safe_artifact_key_part(document_id)
        trace_key = _safe_artifact_key_part(trace_id)
        segment_key = _safe_artifact_key_part(segment.segment_id)
        key = f"{key_prefix}/{document_key}/{trace_key}/segments/{segment_key}.json"
        cache_extraction = _extraction_with_artifact_cache_metadata(
            extraction,
            artifact_kind="segment",
            document_id=document_id,
            trace_id=trace_id,
            segment=segment,
        )
        data = json.dumps(
            cache_extraction.to_document_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            return await self._object_storage.put(
                key,
                data,
                content_type="application/json",
            )
        except Exception as exc:
            logger.info(
                "segment_extraction_artifact_cache_skipped",
                extra={"segment_id": segment.segment_id, "error_type": type(exc).__name__},
            )
            return None

    async def _load_cached_segment_extractions(
        self,
        checkpoint_segments: Sequence[IngestionSegment],
        *,
        target_segments: Sequence[PdfPageSegment],
        available_ranges: set[tuple[int, int]] | None = None,
    ) -> _CachedSegmentLoadResult:
        """成功済み segment artifact を読み戻し、欠落分は再処理へ回す。"""
        target_ranges = {(segment.page_start, segment.page_end) for segment in target_segments}
        cached: list[_SegmentExtraction] = []
        missing_ranges: set[tuple[int, int]] = set()
        for checkpoint in checkpoint_segments:
            if checkpoint.status != "SUCCEEDED":
                continue
            if checkpoint.page_start is None or checkpoint.page_end is None:
                continue
            checkpoint_range = (checkpoint.page_start, checkpoint.page_end)
            if available_ranges is not None and checkpoint_range not in available_ranges:
                continue
            if checkpoint_range in target_ranges:
                continue
            if not checkpoint.artifact_path:
                missing_ranges.add(checkpoint_range)
                continue
            try:
                data = await self._object_storage.get(checkpoint.artifact_path)
                extraction = StructuredExtraction.model_validate_json(data)
            except Exception as exc:
                logger.info(
                    "segment_extraction_artifact_load_skipped",
                    extra={
                        "segment_id": checkpoint.segment_id,
                        "error_type": type(exc).__name__,
                    },
                )
                missing_ranges.add(checkpoint_range)
                continue
            if not _cached_segment_artifact_matches(extraction, checkpoint):
                logger.info(
                    "segment_extraction_artifact_identity_mismatch",
                    extra={"segment_id": checkpoint.segment_id},
                )
                missing_ranges.add(checkpoint_range)
                continue
            cached.append(
                _SegmentExtraction(
                    segment=PdfPageSegment(
                        index=len(cached),
                        page_start=checkpoint.page_start,
                        page_end=checkpoint.page_end,
                        content=b"",
                    ),
                    extraction=extraction,
                )
            )
        return _CachedSegmentLoadResult(cached=cached, missing_ranges=missing_ranges)

    async def _load_cached_full_extraction(
        self,
        checkpoint_segments: Sequence[IngestionSegment],
    ) -> StructuredExtraction | None:
        """全 segment が同じ full artifact を指す場合は抽出結果を再利用する。"""
        if not checkpoint_segments:
            return None
        if any(
            segment.status != "SUCCEEDED" or not segment.artifact_path
            for segment in checkpoint_segments
        ):
            return None
        paths = {segment.artifact_path for segment in checkpoint_segments if segment.artifact_path}
        if len(paths) != 1:
            return None
        artifact_path = next(iter(paths))
        try:
            data = await self._object_storage.get(artifact_path)
            extraction = StructuredExtraction.model_validate_json(data)
        except Exception as exc:
            logger.info(
                "full_extraction_artifact_load_skipped",
                extra={"error_type": type(exc).__name__},
            )
            return None
        document_id = checkpoint_segments[0].document_id
        if not _cached_full_artifact_matches(extraction, document_id):
            logger.info(
                "full_extraction_artifact_identity_mismatch",
                extra={"document_id": document_id},
            )
            return None
        parser_artifacts = {
            **extraction.parser_artifacts,
            "extraction_artifact_reused": True,
            "extraction_artifact_path": artifact_path,
        }
        return extraction.model_copy(update={"parser_artifacts": parser_artifacts})

    async def _extract_local_office_segments(
        self,
        *,
        document_id: str,
        trace_id: str,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        checkpoint_segments: Sequence[IngestionSegment],
        cancel_checker: Callable[[], Awaitable[bool]] | None,
    ) -> StructuredExtraction | None:
        """OpenXML Office を slide/sheet checkpoint 単位で抽出・cache する。"""
        if not _has_office_checkpoint_segments(checkpoint_segments):
            return None
        parse_result = await asyncio.to_thread(
            parse_openxml_office_segment_extractions,
            source_bytes,
            source_profile=source_profile,
        )
        office_segments = parse_result.segments
        failures = parse_result.failures
        if not office_segments:
            if failures:
                await self._mark_office_segment_failures(
                    failures,
                    checkpoint_segments=checkpoint_segments,
                )
                raise IngestionUserError("Office の一部 segment を解析できませんでした。")
            return None
        retry_targets = _failed_retry_segments(checkpoint_segments)
        available_ranges = {
            (_office_segment_page(segment).page_start, _office_segment_page(segment).page_end)
            for segment in office_segments
        }
        reusable_ranges = _successful_checkpoint_ranges(
            checkpoint_segments,
            available_ranges=available_ranges,
        )
        if retry_targets:
            target_segments = _office_segments_for_retry(office_segments, retry_targets)
        elif reusable_ranges:
            target_segments = [
                segment
                for segment in office_segments
                if (segment.number, segment.number) not in reusable_ranges
            ]
        else:
            target_segments = list(office_segments)
        page_like_targets = [_office_segment_page(segment) for segment in target_segments]
        cached_segments = await self._load_cached_segment_extractions(
            checkpoint_segments,
            target_segments=page_like_targets,
            available_ranges=available_ranges,
        )
        segment_extractions = list(cached_segments.cached)
        target_segments = _append_office_segments_for_ranges(
            target_segments,
            office_segments,
            cached_segments.missing_ranges,
        )
        if retry_targets:
            retry_ranges = {
                (target.page_start, target.page_end)
                for target in retry_targets
                if target.page_start is not None and target.page_end is not None
            }
            target_failures = _office_failures_for_ranges(failures, retry_ranges)
        elif reusable_ranges:
            target_failure_ranges = {
                (failure.number, failure.number)
                for failure in failures
                if (failure.number, failure.number) not in reusable_ranges
            } | cached_segments.missing_ranges
            target_failures = _office_failures_for_ranges(failures, target_failure_ranges)
        else:
            target_failures = list(failures)
        for office_segment in target_segments:
            await _raise_if_cancelled(cancel_checker)
            page_segment = _office_segment_page(office_segment)
            checkpoint = _checkpoint_for_pdf_segment(checkpoint_segments, page_segment)
            await self._mark_segment_running(checkpoint)
            segment_artifact_path = await self._cache_segment_extraction_artifact(
                document_id=document_id,
                trace_id=trace_id,
                segment=checkpoint,
                extraction=office_segment.extraction,
            )
            await self._mark_segment_succeeded(
                checkpoint,
                artifact_path=segment_artifact_path,
            )
            segment_extractions.append(
                _SegmentExtraction(
                    segment=page_segment,
                    extraction=office_segment.extraction,
                )
            )
        if target_failures:
            await self._mark_office_segment_failures(
                target_failures,
                checkpoint_segments=checkpoint_segments,
            )
            raise IngestionUserError("Office の一部 segment を解析できませんでした。")
        merged = _merge_segment_extractions(
            segment_extractions,
            warning_code="office_segmented_extraction",
        )
        return _extraction_with_segment_cache_miss_context(
            merged,
            missing_ranges=cached_segments.missing_ranges,
        )

    async def _mark_office_segment_failures(
        self,
        failures: Sequence[OfficeSegmentFailure],
        *,
        checkpoint_segments: Sequence[IngestionSegment],
    ) -> None:
        for failure in failures:
            checkpoint = _checkpoint_for_office_failure(checkpoint_segments, failure)
            await self._mark_segment_running(checkpoint)
            await self._mark_segment_failed(
                checkpoint,
                status="FAILED",
                error_code=failure.error_code,
                error_message=f"{failure.segment_kind} {failure.number} を解析できませんでした。",
            )

    async def _mark_segments_succeeded(
        self,
        segments: Sequence[IngestionSegment],
        *,
        artifact_path: str | None,
    ) -> list[IngestionSegment]:
        updated_segments: list[IngestionSegment] = []
        latest_segments = await self._segments_with_latest_status(segments)
        latest_by_id = {segment.segment_id: segment for segment in latest_segments}
        for segment in segments:
            latest = latest_by_id.get(segment.segment_id, segment)
            checkpoint_artifact_path = (
                latest.artifact_path or segment.artifact_path or artifact_path
            )
            await self._mark_segment_succeeded(latest, artifact_path=checkpoint_artifact_path)
            updated_segments.append(
                latest.model_copy(
                    update={"status": "SUCCEEDED", "artifact_path": checkpoint_artifact_path}
                )
            )
        return updated_segments

    async def _mark_segments_cancelled(
        self,
        segments: Sequence[IngestionSegment],
    ) -> None:
        for segment in await self._segments_with_latest_status(segments):
            if segment.status == "SUCCEEDED":
                continue
            await self._safe_update_ingestion_segment(segment.segment_id, status="CANCELLED")

    async def _mark_segments_failed(
        self,
        segments: Sequence[IngestionSegment],
        *,
        error: Exception,
    ) -> None:
        for segment in await self._segments_with_latest_status(segments):
            if segment.status in {"SUCCEEDED", "FAILED"}:
                continue
            await self._mark_segment_failed(segment, error=error)

    async def _segments_with_latest_status(
        self,
        segments: Sequence[IngestionSegment],
    ) -> list[IngestionSegment]:
        """DB 上の最新 segment 状態を反映し、成功済み checkpoint を保護する。"""
        if not segments:
            return []
        document_id = segments[0].document_id
        latest = await self._safe_list_ingestion_segments(document_id)
        if not latest:
            return list(segments)
        latest_by_id = {segment.segment_id: segment for segment in latest}
        return [latest_by_id.get(segment.segment_id, segment) for segment in segments]

    async def _mark_segment_running(
        self,
        segment: IngestionSegment | None,
    ) -> None:
        if segment is None:
            return
        await self._safe_update_ingestion_segment(
            segment.segment_id,
            status="RUNNING",
            attempt_count=segment.attempt_count + 1,
            error_code="",
            error_message="",
        )

    async def _mark_segment_succeeded(
        self,
        segment: IngestionSegment | None,
        *,
        artifact_path: str | None = None,
    ) -> None:
        if segment is None:
            return
        await self._safe_update_ingestion_segment(
            segment.segment_id,
            status="SUCCEEDED",
            artifact_path=artifact_path,
            error_code="",
            error_message="",
        )

    async def _mark_segment_failed(
        self,
        segment: IngestionSegment | None,
        *,
        error: Exception | None = None,
        status: str = "FAILED",
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if segment is None:
            return
        await self._safe_update_ingestion_segment(
            segment.segment_id,
            status=status,
            error_code=error_code or _segment_error_code(error),
            error_message=(error_message or _safe_segment_error_message(error))[:500],
        )

    async def _safe_list_ingestion_segments(
        self,
        document_id: str,
    ) -> list[IngestionSegment]:
        list_segments = getattr(self._oracle, "list_ingestion_segments", None)
        if not callable(list_segments):
            return []
        try:
            return list(await list_segments(document_id))
        except Exception as exc:
            logger.info(
                "ingestion_segment_checkpoint_list_skipped",
                extra={"document_id": document_id, "error_type": type(exc).__name__},
            )
            return []

    async def _safe_replace_ingestion_segments(
        self,
        document_id: str,
        segments: Sequence[IngestionSegment],
    ) -> None:
        replace_segments = getattr(self._oracle, "replace_ingestion_segments", None)
        if not callable(replace_segments):
            return
        try:
            await replace_segments(document_id, segments)
        except Exception as exc:
            logger.info(
                "ingestion_segment_checkpoint_replace_skipped",
                extra={"document_id": document_id, "error_type": type(exc).__name__},
            )

    async def _safe_update_ingestion_segment(
        self,
        segment_id: str,
        **updates: object,
    ) -> None:
        update_segment = getattr(self._oracle, "update_ingestion_segment", None)
        if not callable(update_segment):
            return
        cleaned_updates = {key: value for key, value in updates.items() if value is not None}
        try:
            await update_segment(segment_id, **cleaned_updates)
        except Exception as exc:
            logger.info(
                "ingestion_segment_checkpoint_update_skipped",
                extra={"segment_id": segment_id, "error_type": type(exc).__name__},
            )

    async def _extract_with_service_backend(
        self,
        service_backend: str,
        *,
        trace_id: str,
        document_id: str,
        source_bytes: bytes,
        prompt: str,
        content_type: str,
        parser_profile: str,
        checkpoint_segments: Sequence[IngestionSegment],
        cancel_checker: Callable[[], Awaitable[bool]] | None,
    ) -> StructuredExtraction | None:
        """明示選択された service backend で抽出する。

        まず再開用の cached 抽出を再利用し、無ければ各 OCI クラウドサービスを直接呼ぶ。
        `oci_document_understanding` が利用不可/失敗のときは None を返し、呼び出し側で
        既存のローカル/VLM フローへ安全に縮退させる。`oci_genai_vision`(旧称
        enterprise_ai_vlm)は明示選択なので常に VLM 抽出まで実行する。
        """
        cached_extraction = await self._load_cached_full_extraction(checkpoint_segments)
        if cached_extraction is not None:
            return cached_extraction
        if service_backend == "oci_document_understanding":
            extracted = await self._extract_with_document_understanding(
                trace_id=trace_id,
                document_id=document_id,
                source_bytes=source_bytes,
                content_type=content_type,
                parser_profile=parser_profile,
                checkpoint_segments=checkpoint_segments,
                cancel_checker=cancel_checker,
            )
            if extracted is None:
                return None
            return _validate_structured_extraction_payload(extracted)
        # oci_genai_vision(旧 enterprise_ai_vlm): fallback ではなく明示選択 → 直接 VLM 抽出。
        extracted = await self._extract_with_vlm(
            trace_id=trace_id,
            document_id=document_id,
            source_bytes=source_bytes,
            prompt=prompt,
            content_type=content_type,
            parser_profile=parser_profile,
            checkpoint_segments=checkpoint_segments,
            cancel_checker=cancel_checker,
        )
        return _validate_structured_extraction_payload(extracted)

    async def _augment_with_raptor(
        self,
        trace_id: str,
        chunks: list[Chunk],
        cancel_checker: Callable[[], Awaitable[bool]] | None,
    ) -> list[Chunk]:
        """RAPTOR 再帰要約索引(opt-in)。leaf に summary node を加えて返す。

        要約は OCI Enterprise AI で行い、失敗/未設定の cluster は skip(最低でも leaf を残す)。
        OFF(既定)のときは leaf chunk をそのまま返す。
        """
        if not getattr(self._settings, "rag_raptor_enabled", False):
            return chunks
        await _raise_if_cancelled(cancel_checker)

        async def _summarize(text: str) -> str | None:
            try:
                return await self._vlm.generate(
                    "次のテキスト群の要点を簡潔な日本語で要約してください。",
                    text,
                    system_prompt="あなたは文書を階層的に要約するアシスタントです。",
                )
            except Exception:  # noqa: BLE001 - 要約失敗は当該 cluster を skip(leaf は残る)
                return None

        augmented = await build_raptor_summaries(
            chunks,
            summarizer=_summarize,
            cluster_size=int(getattr(self._settings, "rag_raptor_cluster_size", 5)),
            max_levels=int(getattr(self._settings, "rag_raptor_max_levels", 2)),
        )
        return augmented

    async def _transcribe_audio(
        self,
        *,
        trace_id: str,
        document_id: str,
        source_bytes: bytes,
        content_type: str,
        source_profile: SourceProfile | None,
        cancel_checker: Callable[[], Awaitable[bool]] | None,
    ) -> StructuredExtraction | None:
        """音声/動画を文字起こしする。OCI AI Speech 優先、未設定/失敗は parser-asr へ縮退。

        いずれも利用不可なら None を返し、呼び出し側で「未対応」として扱う。
        """
        _ = trace_id
        if not getattr(self._settings, "rag_parser_asr_enabled", True):
            return None
        await _raise_if_cancelled(cancel_checker)
        # 1) OCI AI Speech(設定済みのとき優先)。
        payload = await self._speech.transcribe(
            source_bytes, content_type=content_type, document_id=document_id
        )
        if payload is not None:
            try:
                return _validate_structured_extraction_payload(payload)
            except Exception:  # noqa: BLE001 - schema 不一致はローカル経路へ縮退する
                logger.warning("OCI Speech 結果の検証に失敗。ローカル ASR へ縮退します。")
        # 2) ローカル faster-whisper(parser-asr マイクロサービス)。
        await _raise_if_cancelled(cancel_checker)
        result = await asyncio.to_thread(
            self._parser_service.runner, "asr", source_bytes, source_profile, content_type
        )
        if result.extraction is not None:
            return result.extraction
        return None

    async def _extract_with_document_understanding(
        self,
        *,
        trace_id: str,
        document_id: str,
        source_bytes: bytes,
        content_type: str,
        parser_profile: str,
        checkpoint_segments: Sequence[IngestionSegment],
        cancel_checker: Callable[[], Awaitable[bool]] | None,
    ) -> dict[str, object] | None:
        """OCI Document Understanding(非同期 job)で抽出する。失敗/未設定は None。"""
        _ = (trace_id, parser_profile, checkpoint_segments)
        await _raise_if_cancelled(cancel_checker)
        payload = await self._document_understanding.analyze(
            source_bytes,
            content_type=content_type,
            document_id=document_id,
        )
        await _raise_if_cancelled(cancel_checker)
        return payload

    async def _extract_with_vlm(
        self,
        *,
        trace_id: str,
        document_id: str,
        source_bytes: bytes,
        prompt: str,
        content_type: str,
        parser_profile: str,
        checkpoint_segments: Sequence[IngestionSegment] = (),
        cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, object]:
        """必要なら PDF を segment に分けて VLM 抽出する。"""
        segments = await asyncio.to_thread(
            _pdf_segments_for_ingestion,
            source_bytes,
            content_type=content_type,
            max_pages_per_segment=self._settings.rag_pdf_max_pages_per_segment,
            max_segments=self._settings.rag_pdf_max_segments,
            enabled=self._settings.rag_pdf_segmentation_enabled,
        )
        retry_targets = _failed_retry_segments(checkpoint_segments)
        if not segments or (len(segments) == 1 and segments[0].page_count <= 1):
            await _raise_if_cancelled(cancel_checker)
            checkpoint = _checkpoint_for_source(checkpoint_segments)
            await self._mark_segment_running(checkpoint)
            try:
                result = await self._extract_single_vlm_input(
                    trace_id=trace_id,
                    source_bytes=source_bytes,
                    prompt=prompt,
                    content_type=content_type,
                    parser_profile=parser_profile,
                    stage="vlm_extraction",
                    extra_attributes={"pdf_segmented": False},
                )
            except Exception as exc:
                await self._mark_segment_failed(checkpoint, error=exc)
                raise
            await self._mark_segment_succeeded(checkpoint)
            return result

        segment_extractions: list[_SegmentExtraction] = []
        available_ranges = {(segment.page_start, segment.page_end) for segment in segments}
        reusable_ranges = _successful_checkpoint_ranges(
            checkpoint_segments,
            available_ranges=available_ranges,
        )
        if retry_targets:
            target_segments = _pdf_segments_for_retry(segments, retry_targets)
        elif reusable_ranges:
            target_segments = [
                segment
                for segment in segments
                if (segment.page_start, segment.page_end) not in reusable_ranges
            ]
        else:
            target_segments = list(segments)
        cached_segments = await self._load_cached_segment_extractions(
            checkpoint_segments,
            target_segments=target_segments,
            available_ranges=available_ranges,
        )
        segment_extractions.extend(cached_segments.cached)
        target_segments = _append_pdf_segments_for_ranges(
            target_segments,
            segments,
            cached_segments.missing_ranges,
        )
        for segment in target_segments:
            await _raise_if_cancelled(cancel_checker)
            checkpoint = _checkpoint_for_pdf_segment(checkpoint_segments, segment)
            segment_extractions.extend(
                await self._extract_pdf_segment(
                    trace_id=trace_id,
                    document_id=document_id,
                    segment=segment,
                    prompt=prompt,
                    parser_profile=parser_profile,
                    original_source_bytes=len(source_bytes),
                    segment_total=len(segments),
                    checkpoint_segment=checkpoint,
                    cancel_checker=cancel_checker,
                )
            )
        merged = _merge_pdf_segment_extractions(segment_extractions)
        merged = _extraction_with_segment_cache_miss_context(
            merged,
            missing_ranges=cached_segments.missing_ranges,
        )
        return merged.to_document_payload()

    async def _extract_pdf_segment(
        self,
        *,
        trace_id: str,
        document_id: str,
        segment: PdfPageSegment,
        prompt: str,
        parser_profile: str,
        original_source_bytes: int,
        segment_total: int,
        checkpoint_segment: IngestionSegment | None = None,
        cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    ) -> list[_SegmentExtraction]:
        """PDF segment を抽出し、出力上限に当たった場合は単ページへ分割して再試行する。"""
        segment_prompt = _pdf_segment_prompt(prompt, segment)
        try:
            await _raise_if_cancelled(cancel_checker)
            await self._mark_segment_running(checkpoint_segment)
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
            segment_extraction = _validate_structured_extraction_payload(extracted)
            segment_artifact_path = await self._cache_segment_extraction_artifact(
                document_id=document_id,
                trace_id=trace_id,
                segment=checkpoint_segment,
                extraction=segment_extraction,
            )
            await self._mark_segment_succeeded(
                checkpoint_segment,
                artifact_path=segment_artifact_path,
            )
            return [
                _SegmentExtraction(
                    segment=segment,
                    extraction=segment_extraction,
                )
            ]
        except EnterpriseAiIncompleteResponseError:
            if segment.page_count <= 1:
                await self._mark_segment_failed(
                    checkpoint_segment,
                    status="FAILED",
                    error_code="enterprise_ai_incomplete_response",
                    error_message="Enterprise AI の出力が上限に達しました。",
                )
                raise
            page_segments = await asyncio.to_thread(
                split_pdf_page_segments,
                segment.content,
                max_pages_per_segment=1,
                page_number_offset=segment.page_start - 1,
            )
            if len(page_segments) <= 1:
                raise
            results: list[_SegmentExtraction] = []
            for page_segment in page_segments:
                await _raise_if_cancelled(cancel_checker)
                results.extend(
                    await self._extract_pdf_segment(
                        trace_id=trace_id,
                        document_id=document_id,
                        segment=page_segment,
                        prompt=prompt,
                        parser_profile=parser_profile,
                        original_source_bytes=original_source_bytes,
                        segment_total=segment_total,
                        checkpoint_segment=None,
                        cancel_checker=cancel_checker,
                    )
                )
            await self._mark_segment_succeeded(checkpoint_segment)
            return results
        except Exception as exc:
            await self._mark_segment_failed(checkpoint_segment, error=exc)
            raise

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
        trace_id: str,
        document_id: str,
        extraction: StructuredExtraction,
        chunks: list[Chunk],
        vectors: list[list[float]],
        *,
        cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        """抽出本文とチャンクを一貫した indexing stage として保存する。"""
        await _raise_if_cancelled(cancel_checker)
        await self._oracle.save_index(document_id, extraction, chunks, vectors)
        await _raise_if_cancelled(cancel_checker)
        if not resolve_graph_adapter(self._settings).enabled:
            return
        try:
            await _observe_ingestion_stage(
                trace_id,
                "graph_indexing",
                self._save_graph_index(document_id, extraction, chunks),
                attributes={
                    "chunk_count": len(chunks),
                    "element_count": len(extraction.elements),
                },
                result_attributes=_graph_index_result_attributes,
            )
        except Exception as exc:
            logger.info(
                "graph_indexing_skipped",
                extra={"document_id": document_id, "error_type": type(exc).__name__},
            )

    async def _save_graph_index(
        self,
        document_id: str,
        extraction: StructuredExtraction,
        chunks: list[Chunk],
    ) -> GraphIndex:
        """構造化抽出から GraphRAG-lite index を作り Oracle へ保存する。"""
        graph_params = resolve_graph_adapter(self._settings)
        knowledge_bases = await self._oracle.list_document_knowledge_bases(document_id)
        graph_index = await asyncio.to_thread(
            build_graph_index,
            document_id=document_id,
            knowledge_base_ids=[knowledge_base.id for knowledge_base in knowledge_bases],
            extraction=extraction,
            chunks=chunks,
            build_claims=graph_params.build_claims,
            build_community_summaries=graph_params.build_community_summaries,
        )
        await self._oracle.replace_document_graph_index(document_id, graph_index)
        return graph_index


def _safe_persistent_error_message(error: Exception) -> str:
    """document table に保存してよい短いエラーメッセージを返す。"""
    if getattr(error, "safe_for_user", False):
        return str(error)[:200]
    return INGESTION_INTERNAL_ERROR_MESSAGE


def _segment_error_code(error: Exception | None) -> str:
    """segment checkpoint に保存する低 cardinality の error code を返す。"""
    if error is None:
        return "error"
    code = getattr(error, "error_code", None)
    if isinstance(code, str) and code.strip():
        return code.strip()[:80]
    if isinstance(error, ValidationError):
        return "structured_extraction_validation_error"
    return type(error).__name__


def _safe_segment_error_message(error: Exception | None) -> str:
    """segment UI に出してよい原因メッセージへ正規化する。"""
    if error is None:
        return ""
    if isinstance(error, ValidationError):
        return _validation_error_message(error)
    if getattr(error, "safe_for_user", False):
        return str(error)
    return INGESTION_INTERNAL_ERROR_MESSAGE


def _validate_structured_extraction_payload(payload: Mapping[str, object]) -> StructuredExtraction:
    """抽出 payload を検証し、ValidationError を利用者向けに変換する。"""
    try:
        return StructuredExtraction.model_validate(payload)
    except ValidationError as exc:
        raise EnterpriseAiValidationError(_validation_error_message(exc)) from exc


def _validation_error_message(error: ValidationError) -> str:
    """Pydantic validation error から非機密な原因摘要を作る。"""
    details: list[str] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False)[:3]:
        location = ".".join(str(part) for part in item.get("loc", ()) if part != "__root__")
        error_type = str(item.get("type") or "validation_error")
        message = str(item.get("msg") or "schema validation failed")
        details.append(f"{location}: {error_type} ({message})" if location else message)
    if error.error_count() > len(details):
        details.append(f"ほか {error.error_count() - len(details)} 件")
    joined = " / ".join(details) if details else "StructuredExtraction schema validation failed"
    return (
        "Enterprise AI の抽出結果が保存用 schema と一致しません。"
        f"失敗項目: {joined}。"
        "VLM response path / payload template と raw_text・confidence・elements の形式を"
        "確認して再実行してください。"
    )


async def _raise_if_cancelled(
    cancel_checker: Callable[[], Awaitable[bool]] | None,
) -> None:
    """job が cancel 済みなら以降の stage を止める。"""
    if cancel_checker is None:
        return
    if await cancel_checker():
        raise IngestionCancelledError(INGESTION_JOB_CANCELLED_MESSAGE)


def _unsupported_parser_message(result: ParserRegistryResult) -> str:
    """parser registry の未対応理由を利用者向け文言へ寄せる。"""
    if result.unsupported_reason == "audio_transcription_not_configured":
        return (
            "音声ファイルの取込は現在未対応です。"
            "承認済みの音声文字起こし経路を設定してから再試行してください。"
        )
    if result.unsupported_reason == "tiff_image_not_supported":
        return (
            "TIFF 画像の取込は現在未対応です。"
            "PNG/JPEG/WEBP へ変換してから再アップロードしてください。"
        )
    if result.unsupported_reason == "legacy_office_binary_not_supported":
        return (
            "旧形式の Office バイナリ文書は現在未対応です。"
            "DOCX/PPTX/XLSX へ変換してから再アップロードしてください。"
        )
    return "このファイル形式は取込に対応していません。"


def _parser_result_attributes(result: ParserRegistryResult) -> Mapping[str, object]:
    """parser registry 結果から非機密な trace attribute を作る。"""
    extraction = result.extraction
    return {
        "parser_backend": result.parser_backend,
        "parser_version": result.parser_version,
        "parser_fallback_used": result.fallback_used,
        "chunk_template": result.template,
        "parser_warning_count": len(result.warnings),
        "parser_unsupported": result.unsupported_reason is not None,
        "element_count": len(extraction.elements) if extraction is not None else 0,
        "page_count": len(extraction.pages) if extraction is not None else 0,
        "table_count": len(extraction.tables) if extraction is not None else 0,
    }


def _checkpoint_parser_backend(
    parser_result: ParserRegistryResult,
    source_profile: SourceProfile | None,
) -> str:
    """checkpoint には実際に使う予定の parser backend を残す。"""
    if _uses_external_adapter_extraction(parser_result):
        return parser_result.parser_backend
    if source_profile is not None and source_profile.parser_backend == "local_partition":
        return source_profile.parser_backend
    return parser_result.parser_backend


def _uses_external_adapter_extraction(parser_result: ParserRegistryResult) -> bool:
    """任意 external adapter が実際に extraction を返したかを判定する。"""
    return parser_result.extraction is not None and _is_external_adapter_backend(
        parser_result.parser_backend
    )


def _is_external_adapter_backend(parser_backend: str) -> bool:
    return parser_backend in {"docling", "marker", "unstructured"}


def _service_parser_backend(parser_backend: str) -> str | None:
    """parser_result が service backend(OCI クラウド直接呼び出し)の sentinel か判定する。"""
    return parser_backend if parser_backend in SERVICE_ADAPTER_BACKENDS else None


def _extraction_with_parser_context(
    extraction: StructuredExtraction,
    *,
    parser_result: ParserRegistryResult,
    fallback_template: str,
    source_parser: str,
) -> StructuredExtraction:
    """抽出結果へ parser/chunk template lineage を付与する。"""
    chunk_template = (
        parser_result.template
        if parser_result.template != "enterprise_ai_fallback"
        else fallback_template
    )
    effective_source_parser = _source_parser_name(
        extraction,
        parser_result=parser_result,
        fallback=source_parser,
    )
    elements = [
        element.model_copy(
            update={
                "source_parser": element.source_parser or effective_source_parser,
                "metadata": {
                    **element.metadata,
                    "source_parser": element.source_parser or effective_source_parser,
                    "chunk_template": element.metadata.get("chunk_template") or chunk_template,
                },
            }
        )
        for element in extraction.elements
    ]
    parser_artifacts = {
        **extraction.parser_artifacts,
        "parser_backend": parser_result.parser_backend,
        "parser_version": parser_result.parser_version,
        "fallback_used": parser_result.fallback_used,
        "chunk_template": chunk_template,
        "source_parser": effective_source_parser,
    }
    return extraction.model_copy(
        update={
            "warnings": _dedupe_text([*extraction.warnings, *parser_result.warnings]),
            "elements": elements,
            "parser_artifacts": parser_artifacts,
        }
    )


def _passthrough_derivation(
    *,
    profile: str,
    source_sha: str,
    content_type: str,
    converter_name: str = "passthrough",
    warnings: list[str] | None = None,
) -> SourceDerivation:
    """変換しない(passthrough / no-op / 縮退)場合の派生系譜。derived は原本と同一を指す。"""
    return SourceDerivation(
        derivation_id=uuid4().hex,
        preprocess_profile=profile,
        converted=False,
        converter_name=converter_name,
        converter_version="v1",
        source_content_type=content_type,
        source_sha256=source_sha,
        derived_content_type=content_type,
        derived_sha256=source_sha,
        warnings=warnings or [],
    )


def _extraction_with_source_derivation(
    extraction: StructuredExtraction,
    derivation: SourceDerivation,
) -> StructuredExtraction:
    """派生系譜(溯源)を抽出 metadata(parser_artifacts)へ刻む。"""
    parser_artifacts = {
        **extraction.parser_artifacts,
        "source_derivation": derivation.model_dump(mode="json"),
    }
    return extraction.model_copy(update={"parser_artifacts": parser_artifacts})


def _source_derivation_id(extraction: StructuredExtraction) -> str | None:
    """抽出 metadata から派生系譜 ID を取り出す(無ければ None)。"""
    derivation = extraction.parser_artifacts.get("source_derivation")
    if isinstance(derivation, Mapping):
        value = derivation.get("derivation_id")
        if isinstance(value, str) and value:
            return value
    return None


def _chunks_with_source_derivation(chunks: list[Chunk], derivation_id: str | None) -> list[Chunk]:
    """各 chunk metadata へ派生系譜 ID を付与する(chunk → 派生原本 → 原本の追跡)。"""
    if not derivation_id:
        return chunks
    for chunk in chunks:
        chunk.metadata["source_derivation_id"] = derivation_id
    return chunks


def _canonical_artifact_extension(content_type: str) -> str:
    """canonical artifact の保存拡張子を content_type から決める(表示・取り回し用)。"""
    normalized = (content_type or "").split(";", 1)[0].strip().casefold()
    mapping = {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "text/html": ".html",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "application/zip": ".zip",
    }
    return mapping.get(normalized, ".bin")


def _source_parser_name(
    extraction: StructuredExtraction,
    *,
    parser_result: ParserRegistryResult,
    fallback: str,
) -> str:
    """抽出 payload / parser result から source parser 名を決める。"""
    value = extraction.parser_artifacts.get("source_parser")
    if isinstance(value, str) and value.strip():
        return value.strip()[:80]
    if parser_result.parser_backend == "local_partition":
        return parser_result.template[:80]
    return fallback[:80]


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


def _checkpoint_segments_for_source(
    *,
    document_id: str,
    source_bytes: bytes,
    content_type: str,
    source_profile: SourceProfile | None,
    parser_backend: str,
    parser_profile: str,
    max_pages_per_segment: int,
    max_segments: int,
    segmentation_enabled: bool,
) -> list[IngestionSegment]:
    """source bytes から永続 checkpoint の初期 segment を作る。"""
    pdf_segments = _pdf_segments_for_ingestion(
        source_bytes,
        content_type=content_type,
        max_pages_per_segment=max_pages_per_segment,
        max_segments=max_segments,
        enabled=segmentation_enabled,
    )
    if pdf_segments and not (len(pdf_segments) == 1 and pdf_segments[0].page_count <= 1):
        return [
            IngestionSegment(
                segment_id=_pdf_checkpoint_segment_id(document_id, segment),
                document_id=document_id,
                status="QUEUED",
                parser_backend=parser_backend,
                parser_profile=parser_profile,
                page_start=segment.page_start,
                page_end=segment.page_end,
            )
            for segment in pdf_segments
        ]
    office_segments = _office_checkpoint_segments_for_source(
        document_id=document_id,
        source_bytes=source_bytes,
        content_type=content_type,
        source_profile=source_profile,
        parser_backend=parser_backend,
        parser_profile=parser_profile,
        max_segments=max_segments,
    )
    if office_segments:
        return office_segments
    return [
        IngestionSegment(
            segment_id=f"{document_id}:source",
            document_id=document_id,
            status="QUEUED",
            parser_backend=parser_backend,
            parser_profile=parser_profile,
            page_start=(
                1 if _observability_content_type(content_type).startswith("image/") else None
            ),
            page_end=1 if _observability_content_type(content_type).startswith("image/") else None,
        )
    ]


def _office_checkpoint_segments_for_source(
    *,
    document_id: str,
    source_bytes: bytes,
    content_type: str,
    source_profile: SourceProfile | None,
    parser_backend: str,
    parser_profile: str,
    max_segments: int,
) -> list[IngestionSegment]:
    """OpenXML Office を slide/sheet 単位の checkpoint にする。"""
    office_kind = _office_checkpoint_kind(
        content_type=content_type,
        source_profile=source_profile,
    )
    if office_kind is None:
        return []
    if office_kind == "slide":
        numbers = _openxml_member_numbers(source_bytes, r"ppt/slides/slide(\d+)\.xml")
    else:
        numbers = _openxml_member_numbers(source_bytes, r"xl/worksheets/sheet(\d+)\.xml")
    if not numbers:
        return []
    if len(numbers) > max_segments:
        raise IngestionUserError(
            "Office の論理 segment 数が上限を超えています。"
            f"max_segments={max_segments}, actual={len(numbers)}"
        )
    return [
        IngestionSegment(
            segment_id=f"{document_id}:{office_kind}{number}",
            document_id=document_id,
            status="QUEUED",
            parser_backend=parser_backend,
            parser_profile=parser_profile,
            page_start=number,
            page_end=number,
        )
        for number in numbers
    ]


def _office_checkpoint_kind(
    *,
    content_type: str,
    source_profile: SourceProfile | None,
) -> str | None:
    extension = (source_profile.extension if source_profile is not None else None) or ""
    normalized = _observability_content_type(content_type)
    if extension == ".pptx" or normalized.endswith("officedocument.presentationml.presentation"):
        return "slide"
    if extension == ".xlsx" or normalized.endswith("officedocument.spreadsheetml.sheet"):
        return "sheet"
    return None


def _openxml_member_numbers(source_bytes: bytes, pattern: str) -> list[int]:
    regex = re.compile(pattern)
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as archive:
            numbers = []
            for name in archive.namelist():
                match = regex.fullmatch(name)
                if match:
                    numbers.append(int(match.group(1)))
    except (zipfile.BadZipFile, ValueError):
        return []
    return sorted(set(numbers))


def _failed_retry_segments(
    checkpoint_segments: Sequence[IngestionSegment],
) -> list[IngestionSegment]:
    """今回 failed segment retry として絞り込む checkpoint を返す。"""
    return [
        segment
        for segment in checkpoint_segments
        if segment.page_start is not None
        and segment.page_end is not None
        and (segment.status == "FAILED" or segment.error_code == "retry_failed_segment")
    ]


def _failed_checkpoint_count(checkpoint_segments: Sequence[IngestionSegment]) -> int:
    return sum(
        1
        for segment in checkpoint_segments
        if segment.status == "FAILED" or segment.error_code == "retry_failed_segment"
    )


def _successful_checkpoint_ranges(
    checkpoint_segments: Sequence[IngestionSegment],
    *,
    available_ranges: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    """現行 segmentation と一致し、artifact を再利用できる checkpoint 範囲。"""
    ranges: set[tuple[int, int]] = set()
    for segment in checkpoint_segments:
        if (
            segment.status != "SUCCEEDED"
            or not segment.artifact_path
            or segment.page_start is None
            or segment.page_end is None
        ):
            continue
        segment_range = (segment.page_start, segment.page_end)
        if segment_range in available_ranges:
            ranges.add(segment_range)
    return ranges


def _pdf_segments_for_retry(
    segments: Sequence[PdfPageSegment],
    retry_targets: Sequence[IngestionSegment],
) -> list[PdfPageSegment]:
    """failed checkpoint があれば該当 page range の PDF segment だけ返す。"""
    if not retry_targets:
        return list(segments)
    ranges = {
        (target.page_start, target.page_end)
        for target in retry_targets
        if target.page_start is not None and target.page_end is not None
    }
    selected = [segment for segment in segments if (segment.page_start, segment.page_end) in ranges]
    return selected or list(segments)


def _append_pdf_segments_for_ranges(
    selected_segments: Sequence[PdfPageSegment],
    all_segments: Sequence[PdfPageSegment],
    ranges: set[tuple[int, int]],
) -> list[PdfPageSegment]:
    """cache miss した PDF segment を再処理対象へ追加する。"""
    result = list(selected_segments)
    seen = {(segment.page_start, segment.page_end) for segment in result}
    for segment in all_segments:
        segment_range = (segment.page_start, segment.page_end)
        if segment_range not in ranges or segment_range in seen:
            continue
        result.append(segment)
        seen.add(segment_range)
    return result


def _office_segments_for_retry(
    segments: Sequence[OfficeSegmentExtraction],
    retry_targets: Sequence[IngestionSegment],
) -> list[OfficeSegmentExtraction]:
    """failed checkpoint があれば該当 slide/sheet だけ返す。"""
    if not retry_targets:
        return list(segments)
    numbers = {
        target.page_start
        for target in retry_targets
        if target.page_start is not None and target.page_start == target.page_end
    }
    return [segment for segment in segments if segment.number in numbers]


def _append_office_segments_for_ranges(
    selected_segments: Sequence[OfficeSegmentExtraction],
    all_segments: Sequence[OfficeSegmentExtraction],
    ranges: set[tuple[int, int]],
) -> list[OfficeSegmentExtraction]:
    """cache miss した Office segment を再処理対象へ追加する。"""
    result = list(selected_segments)
    seen = {(segment.number, segment.number) for segment in result}
    for segment in all_segments:
        segment_range = (segment.number, segment.number)
        if segment_range not in ranges or segment_range in seen:
            continue
        result.append(segment)
        seen.add(segment_range)
    return result


def _office_failures_for_ranges(
    failures: Sequence[OfficeSegmentFailure],
    target_ranges: set[tuple[int, int]],
) -> list[OfficeSegmentFailure]:
    """本回で実処理する Office segment の failure だけ返す。"""
    return [failure for failure in failures if (failure.number, failure.number) in target_ranges]


def _has_office_checkpoint_segments(
    checkpoint_segments: Sequence[IngestionSegment],
) -> bool:
    return any(
        segment.segment_id.rsplit(":", 1)[-1].startswith(("slide", "sheet"))
        for segment in checkpoint_segments
    )


def _office_segment_page(segment: OfficeSegmentExtraction) -> PdfPageSegment:
    return PdfPageSegment(
        index=segment.number - 1,
        page_start=segment.number,
        page_end=segment.number,
        content=b"",
    )


def _extraction_with_artifact_cache_metadata(
    extraction: StructuredExtraction,
    *,
    artifact_kind: str,
    document_id: str,
    trace_id: str,
    segment: IngestionSegment | None = None,
) -> StructuredExtraction:
    """Object Storage cache payload を後方互換な parser_artifacts で自描述にする。"""
    parser_artifacts: dict[str, ExtractionArtifactValue] = {
        **extraction.parser_artifacts,
        "extraction_artifact_schema_version": EXTRACTION_ARTIFACT_SCHEMA_VERSION,
        "extraction_artifact_kind": artifact_kind,
        "extraction_artifact_document_id": document_id,
        "extraction_artifact_trace_id": trace_id,
    }
    if segment is not None:
        parser_artifacts["extraction_artifact_segment_id"] = segment.segment_id
        if segment.page_start is not None:
            parser_artifacts["extraction_artifact_page_start"] = segment.page_start
        if segment.page_end is not None:
            parser_artifacts["extraction_artifact_page_end"] = segment.page_end
    return extraction.model_copy(update={"parser_artifacts": parser_artifacts})


def _cached_segment_artifact_matches(
    extraction: StructuredExtraction,
    segment: IngestionSegment,
) -> bool:
    """Object Storage の segment artifact が現在の checkpoint と一致するか確認する。"""
    artifacts = extraction.parser_artifacts
    return (
        _artifact_int(artifacts.get("extraction_artifact_schema_version"))
        == EXTRACTION_ARTIFACT_SCHEMA_VERSION
        and _artifact_string(artifacts.get("extraction_artifact_kind")) == "segment"
        and _artifact_string(artifacts.get("extraction_artifact_document_id"))
        == segment.document_id
        and _artifact_string(artifacts.get("extraction_artifact_segment_id")) == segment.segment_id
        and _artifact_int(artifacts.get("extraction_artifact_page_start")) == segment.page_start
        and _artifact_int(artifacts.get("extraction_artifact_page_end")) == segment.page_end
    )


def _cached_full_artifact_matches(
    extraction: StructuredExtraction,
    document_id: str,
) -> bool:
    """Object Storage の full extraction artifact が現在文書のものか確認する。"""
    artifacts = extraction.parser_artifacts
    return (
        _artifact_int(artifacts.get("extraction_artifact_schema_version"))
        == EXTRACTION_ARTIFACT_SCHEMA_VERSION
        and _artifact_string(artifacts.get("extraction_artifact_kind")) == "full"
        and _artifact_string(artifacts.get("extraction_artifact_document_id")) == document_id
    )


def _artifact_string(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _artifact_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _checkpoint_for_source(
    checkpoint_segments: Sequence[IngestionSegment],
) -> IngestionSegment | None:
    return next((segment for segment in checkpoint_segments if segment.page_start is None), None)


def _checkpoint_for_pdf_segment(
    checkpoint_segments: Sequence[IngestionSegment],
    pdf_segment: PdfPageSegment,
) -> IngestionSegment | None:
    return next(
        (
            segment
            for segment in checkpoint_segments
            if segment.page_start == pdf_segment.page_start
            and segment.page_end == pdf_segment.page_end
        ),
        None,
    )


def _checkpoint_for_office_failure(
    checkpoint_segments: Sequence[IngestionSegment],
    failure: OfficeSegmentFailure,
) -> IngestionSegment | None:
    return next(
        (
            segment
            for segment in checkpoint_segments
            if segment.page_start == failure.number and segment.page_end == failure.number
        ),
        None,
    )


def _pdf_checkpoint_segment_id(document_id: str, segment: PdfPageSegment) -> str:
    return f"{document_id}:p{segment.page_start}-{segment.page_end}"


def _safe_artifact_key_part(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)
    if cleaned in {".", ".."}:
        return "segment"
    return cleaned[:160] or "segment"


def _safe_artifact_prefix(value: object) -> str:
    """Object Storage artifact prefix を監査しやすい key path へ正規化する。"""
    raw = str(value or "").replace("\\", "/")
    parts = [
        _safe_artifact_key_part(part)
        for part in raw.strip().strip("/").split("/")
        if part.strip() not in {"", ".", ".."}
    ]
    return "/".join(parts) or "artifacts/extractions"


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
    return _merge_segment_extractions(
        segment_extractions,
        warning_code="pdf_segmented_extraction",
    )


def _merge_segment_extractions(
    segment_extractions: Sequence[_SegmentExtraction],
    *,
    warning_code: str,
) -> StructuredExtraction:
    """複数 segment の抽出結果を 1 つの StructuredExtraction へ統合する。"""
    if not segment_extractions:
        return StructuredExtraction()

    raw_parts: list[str] = []
    elements: list[DocumentElement] = []
    pages: list[ExtractionPage] = []
    tables: list[ExtractionTable] = []
    assets: list[ExtractionAsset] = []
    warnings: list[str] = [warning_code]
    confidence_values: list[float] = []
    document_type = "ドキュメント"
    next_order = 0

    ordered_segment_extractions = sorted(
        segment_extractions,
        key=lambda item: (item.segment.page_start, item.segment.page_end, item.segment.index),
    )

    for segment_extraction in ordered_segment_extractions:
        segment = segment_extraction.segment
        extraction = segment_extraction.extraction
        if extraction.document_type and extraction.document_type != "ドキュメント":
            document_type = extraction.document_type
        confidence_values.append(extraction.confidence)
        warnings.extend(extraction.warnings)
        text = extraction.raw_text.strip()
        if text:
            raw_parts.append(f"{_page_marker(segment)}\n{text}")
        element_id_map = _segment_element_id_map(
            extraction.elements,
            segment=segment,
            starting_order=next_order,
        )
        for element in extraction.elements:
            adjusted = _element_with_absolute_page(
                element,
                segment,
                order=next_order,
                element_id_map=element_id_map,
            )
            elements.append(adjusted)
            next_order += 1
        pages.extend(_pages_with_absolute_page(extraction.pages, segment, element_id_map))
        tables.extend(_tables_with_absolute_page(extraction.tables, segment, element_id_map))
        assets.extend(_assets_with_absolute_page(extraction.assets, segment))

    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    return StructuredExtraction(
        raw_text="\n\n".join(raw_parts),
        document_type=document_type,
        confidence=confidence,
        warnings=_dedupe_text(warnings),
        elements=elements,
        pages=_merge_extraction_pages(pages),
        tables=tables,
        assets=assets,
        parser_artifacts=_merged_segment_parser_artifacts(
            ordered_segment_extractions,
            warning_code=warning_code,
        ),
    )


def _extraction_with_segment_cache_miss_context(
    extraction: StructuredExtraction,
    *,
    missing_ranges: set[tuple[int, int]],
) -> StructuredExtraction:
    """成功 checkpoint の artifact 欠落を非機密 warning として残す。"""
    if not missing_ranges:
        return extraction
    parser_artifacts = {
        **extraction.parser_artifacts,
        "segment_extraction_artifact_cache_miss_count": len(missing_ranges),
    }
    warnings = _dedupe_text([*extraction.warnings, "segment_extraction_artifact_cache_miss"])
    return extraction.model_copy(
        update={"parser_artifacts": parser_artifacts, "warnings": warnings}
    )


def _page_marker(segment: PdfPageSegment) -> str:
    """raw_text に入れる page marker。infer_document_elements が解釈できる形にする。"""
    return f"--- page {segment.page_start} ---"


def _element_with_absolute_page(
    element: DocumentElement,
    segment: PdfPageSegment,
    *,
    order: int,
    element_id_map: Mapping[str, str] | None = None,
) -> DocumentElement:
    """segment 内 page_number と element lineage を元文書スコープへ寄せる。"""
    source_element_id = _element_source_id(element)
    scoped_element_id = (
        element_id_map.get(source_element_id, source_element_id)
        if element_id_map is not None
        else source_element_id
    )
    parent_id = (
        element_id_map.get(element.parent_id, element.parent_id)
        if element.parent_id is not None and element_id_map is not None
        else element.parent_id
    )
    metadata = dict(element.metadata)
    if scoped_element_id != source_element_id:
        metadata.setdefault("source_element_id", source_element_id)
    return element.model_copy(
        update={
            "order": order,
            "element_id": scoped_element_id,
            "parent_id": parent_id,
            "page_number": _absolute_page_number(element.page_number, segment),
            "metadata": metadata,
        }
    )


def _segment_element_id_map(
    elements: Sequence[DocumentElement],
    *,
    segment: PdfPageSegment,
    starting_order: int,
) -> dict[str, str]:
    """segment-local element id を文書全体で一意な id に写像する。"""
    prefix = _segment_lineage_prefix(segment)
    result: dict[str, str] = {}
    used: set[str] = set()
    for offset, element in enumerate(elements):
        source_id = _element_source_id(element)
        scoped_id = _segment_scoped_identifier(
            prefix,
            source_id,
            fallback_order=starting_order + offset,
        )
        if scoped_id in used:
            scoped_id = _segment_scoped_identifier(
                prefix,
                f"{source_id}-{offset}",
                fallback_order=starting_order + offset,
            )
        result[source_id] = scoped_id
        used.add(scoped_id)
    return result


def _pages_with_absolute_page(
    pages: Sequence[ExtractionPage],
    segment: PdfPageSegment,
    element_id_map: Mapping[str, str],
) -> list[ExtractionPage]:
    """segment page metadata を元文書の page metadata に変換する。"""
    result: list[ExtractionPage] = []
    for page in pages:
        page_number = _absolute_page_number(page.page_number, segment)
        result.append(
            page.model_copy(
                update={
                    "page_number": page_number,
                    "label": _absolute_page_label(page.label, page_number),
                    "element_ids": _mapped_element_ids(page.element_ids, element_id_map),
                }
            )
        )
    return result


def _tables_with_absolute_page(
    tables: Sequence[ExtractionTable],
    segment: PdfPageSegment,
    element_id_map: Mapping[str, str],
) -> list[ExtractionTable]:
    """segment table metadata の page/element/table id を文書スコープへ変換する。"""
    prefix = _segment_lineage_prefix(segment)
    result: list[ExtractionTable] = []
    for index, table in enumerate(tables):
        page_number = _absolute_optional_page_number(table.page_number, segment)
        result.append(
            table.model_copy(
                update={
                    "table_id": _segment_scoped_identifier(
                        prefix,
                        table.table_id,
                        fallback_order=index,
                    ),
                    "element_id": _mapped_optional_element_id(
                        table.element_id,
                        element_id_map,
                    ),
                    "page_number": page_number,
                }
            )
        )
    return result


def _assets_with_absolute_page(
    assets: Sequence[ExtractionAsset],
    segment: PdfPageSegment,
) -> list[ExtractionAsset]:
    """segment asset metadata の page/asset id を文書スコープへ変換する。"""
    prefix = _segment_lineage_prefix(segment)
    result: list[ExtractionAsset] = []
    for index, asset in enumerate(assets):
        result.append(
            asset.model_copy(
                update={
                    "asset_id": _segment_scoped_identifier(
                        prefix,
                        asset.asset_id,
                        fallback_order=index,
                    ),
                    "page_number": _absolute_optional_page_number(asset.page_number, segment),
                }
            )
        )
    return result


def _merge_extraction_pages(pages: Sequence[ExtractionPage]) -> list[ExtractionPage]:
    """同じ絶対 page の metadata を element_ids 中心に統合する。"""
    by_page: dict[int, ExtractionPage] = {}
    for page in pages:
        existing = by_page.get(page.page_number)
        if existing is None:
            by_page[page.page_number] = page.model_copy(
                update={"element_ids": _dedupe_text(page.element_ids)}
            )
            continue
        by_page[page.page_number] = existing.model_copy(
            update={
                "label": existing.label or page.label,
                "width": existing.width or page.width,
                "height": existing.height or page.height,
                "rotation": existing.rotation if existing.rotation is not None else page.rotation,
                "element_ids": _dedupe_text([*existing.element_ids, *page.element_ids]),
                "metadata": {**page.metadata, **existing.metadata},
            }
        )
    return [by_page[page_number] for page_number in sorted(by_page)]


def _merged_segment_parser_artifacts(
    segment_extractions: Sequence[_SegmentExtraction],
    *,
    warning_code: str,
) -> dict[str, ExtractionMetadataValue]:
    """segment merge の構造情報を非機密 metadata として残す。"""
    page_starts = [item.segment.page_start for item in segment_extractions]
    page_ends = [item.segment.page_end for item in segment_extractions]
    artifacts: dict[str, ExtractionMetadataValue] = {
        "segment_merge": True,
        "segment_merge_warning": warning_code,
        "segment_count": len(segment_extractions),
        "segment_page_start": min(page_starts) if page_starts else None,
        "segment_page_end": max(page_ends) if page_ends else None,
    }
    source_parsers = _dedupe_text(
        [
            str(value)
            for item in segment_extractions
            if (
                value := item.extraction.parser_artifacts.get("source_parser")
                or item.extraction.parser_artifacts.get("parser_backend")
            )
            is not None
        ]
    )
    if source_parsers:
        artifacts["segment_source_parsers"] = ",".join(source_parsers)[:200]
    return artifacts


def _element_source_id(element: DocumentElement) -> str:
    """segment 内で parser が付けた元 element id を読む。"""
    if element.element_id:
        return element.element_id
    for key in ("element_id", "id"):
        value = element.metadata.get(key)
        if isinstance(value, str | int):
            cleaned = str(value).strip()
            if cleaned:
                return cleaned[:128]
    return f"el-{element.order:04d}"


def _mapped_element_ids(
    element_ids: Sequence[str],
    element_id_map: Mapping[str, str],
) -> list[str]:
    """page/table metadata 内の element ids を merge 後の id にそろえる。"""
    return _dedupe_text([element_id_map.get(element_id, element_id) for element_id in element_ids])


def _mapped_optional_element_id(
    element_id: str | None,
    element_id_map: Mapping[str, str],
) -> str | None:
    if element_id is None:
        return None
    return element_id_map.get(element_id, element_id)


def _segment_lineage_prefix(segment: PdfPageSegment) -> str:
    """segment lineage id の page-range prefix を作る。"""
    return f"p{segment.page_start}-{segment.page_end}"


def _segment_scoped_identifier(prefix: str, value: str, *, fallback_order: int) -> str:
    """segment-local id を JSON/metadata に扱いやすい短い id へ変換する。"""
    cleaned = _safe_artifact_key_part(value)
    if cleaned == "segment":
        cleaned = f"el-{fallback_order:04d}"
    return f"{prefix}-{cleaned}"[:128]


def _absolute_optional_page_number(
    page_number: int | None,
    segment: PdfPageSegment,
) -> int:
    return _absolute_page_number(page_number, segment)


def _absolute_page_label(label: str | None, page_number: int) -> str:
    if label is None or re.fullmatch(r"(?i)page\s+\d+", label.strip()):
        return f"page {page_number}"
    return label


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


async def _observe_cpu_ingestion_stage[T](
    trace_id: str,
    stage: str,
    operation: Callable[[], T],
    *,
    attributes: Mapping[str, object] | None = None,
    result_attributes: Callable[[T], Mapping[str, object]] | None = None,
) -> T:
    """CPU バウンドな同期 stage を thread へ退避し、event loop を塞がずに記録する。"""
    return await _observe_ingestion_stage(
        trace_id,
        stage,
        asyncio.to_thread(operation),
        attributes=attributes,
        result_attributes=result_attributes,
    )


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


def _graph_index_result_attributes(graph_index: GraphIndex) -> Mapping[str, object]:
    """GraphRAG-lite index 結果から件数だけを trace attribute にする。"""
    return {
        "graph_entity_count": len(graph_index.entities),
        "graph_relationship_count": len(graph_index.relationships),
        "graph_claim_count": len(graph_index.claims),
        "graph_community_summary_count": len(graph_index.community_summaries),
        "graph_entity_chunk_link_count": len(graph_index.entity_chunk_links),
    }


def _extraction_structure_attributes(extraction: StructuredExtraction) -> Mapping[str, object]:
    """構造化抽出から非機密な件数だけを trace attribute にする。"""
    quality = extraction.quality_report or build_ingestion_quality_report(extraction)
    return {
        "element_count": len(extraction.elements),
        "table_count": quality.table_count,
        "figure_count": quality.figure_count,
        "formula_count": quality.formula_count,
        "page_count": quality.page_count,
        "asset_count": max(
            len(extraction.assets),
            _raw_artifact_count(extraction.parser_artifacts, STRUCTURE_ASSET_COUNT_KEYS),
        ),
        "low_confidence_count": quality.low_confidence_count,
        "failed_segment_count": quality.failed_segment_count,
    }


def _raw_extraction_structure_attributes(extracted: Mapping[str, object]) -> Mapping[str, object]:
    """VLM 生 payload から本文を見ずに構造件数だけを読む。"""
    elements = _raw_mapping_items(extracted.get("elements"))
    pages_payload = _raw_mapping_items(extracted.get("pages"))
    tables = _raw_mapping_items(extracted.get("tables"))
    assets = _raw_mapping_items(extracted.get("assets"))
    artifacts = _raw_mapping(extracted.get("parser_artifacts"))
    pages: set[int] = set()
    table_count = sum(1 for item in elements if _raw_mapping_label(item.get("kind")) == "table")
    figure_count = sum(
        1
        for item in elements
        if _raw_mapping_label(item.get("kind")) in {"figure", "figure_caption"}
    )
    formula_count = sum(
        1
        for item in elements
        if _raw_mapping_label(item.get("kind")) in {"formula", "equation"}
        or _raw_mapping_label(item.get("content_kind")) == "equation"
    )
    for item in elements:
        if page_number := _raw_page_number(item.get("page_number")):
            pages.add(page_number)
    for page_payload in pages_payload:
        if _raw_string_items(page_payload.get("element_ids")):
            page_number = _raw_page_number(page_payload.get("page_number"))
            if page_number is not None:
                pages.add(page_number)
    for container in (*tables, *assets):
        page_number = _raw_page_number(container.get("page_number"))
        if page_number is not None:
            pages.add(page_number)
    declared_page_count = max(
        len(pages_payload),
        _raw_artifact_count(artifacts, STRUCTURE_PAGE_COUNT_KEYS),
    )
    return {
        "element_count": len(elements),
        "table_count": max(
            table_count,
            len(tables),
            _raw_artifact_count(artifacts, STRUCTURE_TABLE_COUNT_KEYS),
        ),
        "figure_count": max(
            figure_count,
            sum(1 for asset in assets if _raw_is_figure_asset_kind(asset.get("kind"))),
            _raw_artifact_count(artifacts, STRUCTURE_FIGURE_COUNT_KEYS),
        ),
        "formula_count": max(
            formula_count,
            _raw_artifact_count(artifacts, STRUCTURE_FORMULA_COUNT_KEYS),
        ),
        "page_count": max(declared_page_count, len(pages)),
        "asset_count": max(
            len(assets),
            _raw_artifact_count(artifacts, STRUCTURE_ASSET_COUNT_KEYS),
        ),
    }


def _raw_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _raw_mapping_items(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    items: list[Mapping[str, object]] = []
    for item in value:
        if isinstance(item, Mapping):
            items.append(item)
    return items


def _raw_string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _raw_mapping_label(value: object) -> str:
    return str(value).strip().casefold() if value is not None else ""


def _raw_page_number(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


def _raw_artifact_count(artifacts: Mapping[str, object], keys: Sequence[str]) -> int:
    return max((_raw_positive_int(artifacts.get(key)) for key in keys), default=0)


def _raw_positive_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, float):
        return int(value) if value.is_integer() and value > 0 else 0
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _raw_is_figure_asset_kind(kind: object) -> bool:
    return _raw_mapping_label(kind) in FIGURE_ASSET_KINDS


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
