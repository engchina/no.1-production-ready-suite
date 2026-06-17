"""file-processing golden set を staging 環境で実行する runner。"""

from __future__ import annotations

import hashlib
import math
import mimetypes
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from app.clients.object_storage import ObjectStorageClient
from app.clients.oracle import OracleClient, close_oracle_pool
from app.config import Settings, get_settings
from app.rag.file_processing_evaluation import (
    FileProcessingContractReport,
    FileProcessingMetricThresholdResult,
    FileProcessingStagingRequirement,
    build_file_processing_staging_plan,
    evaluate_file_processing_metric_thresholds,
    run_file_processing_contract_checks,
)
from app.rag.guardrails import GroundednessEvaluation, evaluate_groundedness
from app.rag.ingestion import IngestionPipeline
from app.rag.pipeline import RagPipeline
from app.rag.source_profile import build_source_profile
from app.schemas.document import (
    DocumentChunkView,
    DocumentDetail,
    FileStatus,
    IngestionSegment,
    SourceProfile,
)
from app.schemas.knowledge_base import KnowledgeBaseDetail
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest, SearchResponse

FILE_PROCESSING_STAGING_PROMPT = (
    "文書をページ、表、見出し、bbox、要素IDを保持して日本語で構造化抽出してください。"
)
ARTIFACT_CACHE_PROBE_PAYLOAD = (
    b'{"probe":"file_processing_staging_artifact_cache","contains_document_text":false}\n'
)
ARTIFACT_CACHE_PROBE_CONTENT_TYPE = "application/json"


class StagingOracleProtocol(Protocol):
    """file-processing staging runner が使う Oracle client の最小契約。"""

    async def create_knowledge_base(
        self,
        *,
        name: str,
        description: str | None = None,
        default_search_mode: SearchMode = SearchMode.HYBRID,
        retrieval_config: Mapping[str, object] | None = None,
    ) -> KnowledgeBaseDetail:
        """一時的な staging KB を作成する。"""

    async def archive_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDetail:
        """一時的な staging KB をアーカイブする。"""

    async def assign_documents_to_knowledge_base(
        self,
        knowledge_base_id: str,
        document_ids: Sequence[str],
    ) -> KnowledgeBaseDetail:
        """既存文書を KB へ追加する。"""

    async def create_document(
        self,
        file_name: str,
        object_storage_path: str,
        content_type: str | None,
        file_size_bytes: int | None = None,
        content_sha256: str | None = None,
        duplicate_of_document_id: str | None = None,
        knowledge_base_ids: Sequence[str] | None = None,
    ) -> DocumentDetail:
        """document row を作成する。"""

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        """document detail を返す。"""

    async def delete_document(self, document_id: str) -> bool:
        """document/chunk/index を削除する。"""

    async def count_document_chunks(self, document_id: str) -> int:
        """document の chunk 数を返す。"""

    async def list_document_chunks(self, document_id: str) -> list[DocumentChunkView]:
        """document の chunk metadata を返す。"""

    async def list_ingestion_segments(self, document_id: str) -> list[IngestionSegment]:
        """document の segment checkpoint を返す。"""


class StagingObjectStorageProtocol(Protocol):
    """file-processing staging runner が使う Object Storage の最小契約。"""

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        """object を保存し URI を返す。"""

    async def get(self, key: str) -> bytes:
        """object を取得する。"""

    async def delete(self, key: str) -> bool:
        """object を削除する。"""


class StagingIngestionProtocol(Protocol):
    """file-processing staging runner が使う ingestion pipeline 契約。"""

    async def ingest(
        self,
        document_id: str,
        image_bytes: bytes,
        prompt: str,
        *,
        content_type: str = "application/octet-stream",
        source_profile: SourceProfile | None = None,
    ) -> DocumentDetail:
        """document を取込む。"""


class StagingSearchProtocol(Protocol):
    """file-processing staging runner が使う search pipeline 契約。"""

    async def run(self, request: SearchRequest) -> SearchResponse:
        """検索を実行する。"""


@dataclass(frozen=True)
class FileProcessingStagingGateResult:
    """staging で 1 pending check を閉じた結果。"""

    case_id: str
    scenario: str
    check: str
    suggested_gate: str
    passed: bool
    failure_code: str | None = None
    evidence: Mapping[str, object] | None = None


@dataclass(frozen=True)
class FileProcessingStagingRuntimeCheckResult:
    """staging case 実行前に外部契約を検証した結果。"""

    check: str
    status: str
    failure_code: str | None = None
    evidence: Mapping[str, object] | None = None

    @property
    def passed(self) -> bool:
        return self.status in {"ok", "skipped"}


@dataclass(frozen=True)
class FileProcessingStagingCaseResult:
    """staging で 1 manifest case を実行した結果。"""

    case_id: str
    scenario: str
    fixture: str
    document_id: str | None
    status: str
    chunk_count: int
    segment_count: int
    gate_results: tuple[FileProcessingStagingGateResult, ...]
    cleanup: Mapping[str, str]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.gate_results)


@dataclass(frozen=True)
class FileProcessingStagingReport:
    """file-processing staging runner の非機密結果。"""

    run_id: str
    knowledge_base_id: str | None
    case_results: tuple[FileProcessingStagingCaseResult, ...]
    runtime_checks: tuple[FileProcessingStagingRuntimeCheckResult, ...] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)
    threshold_results: tuple[FileProcessingMetricThresholdResult, ...] = ()
    local_manifest_errors: tuple[str, ...] = ()
    cleanup: Mapping[str, str] | None = None

    @property
    def passed(self) -> bool:
        return (
            not self.local_manifest_errors
            and all(check.passed for check in self.runtime_checks)
            and all(result.passed for result in self.case_results)
            and all(result.passed for result in self.threshold_results)
        )

    @property
    def case_count(self) -> int:
        return len(self.case_results)

    @property
    def gate_count(self) -> int:
        return sum(len(result.gate_results) for result in self.case_results)

    @property
    def failure_count(self) -> int:
        gate_failures = sum(
            1
            for case_result in self.case_results
            for gate_result in case_result.gate_results
            if not gate_result.passed
        )
        threshold_failures = sum(1 for result in self.threshold_results if not result.passed)
        runtime_failures = sum(1 for check in self.runtime_checks if not check.passed)
        return (
            len(self.local_manifest_errors)
            + runtime_failures
            + gate_failures
            + threshold_failures
        )


async def run_file_processing_staging_checks(
    manifest: Mapping[str, object],
    *,
    manifest_path: Path,
    oracle: StagingOracleProtocol,
    storage: StagingObjectStorageProtocol,
    ingestion: StagingIngestionProtocol,
    search: StagingSearchProtocol,
    cleanup: bool = False,
    run_id: str | None = None,
    artifact_cache_enabled: bool = True,
    artifact_cache_prefix: str = "artifacts/extractions",
) -> FileProcessingStagingReport:
    """manifest の staging pending checks を実環境で検証する。"""
    resolved_run_id = run_id or uuid4().hex
    local_report = run_file_processing_contract_checks(manifest, manifest_path=manifest_path)
    if not local_report.passed:
        return FileProcessingStagingReport(
            run_id=resolved_run_id,
            knowledge_base_id=None,
            case_results=(),
            local_manifest_errors=(
                *local_report.manifest_errors,
                *(failure for result in local_report.case_results for failure in result.failures),
            ),
            threshold_results=evaluate_file_processing_metric_thresholds(
                {},
                _mapping(manifest.get("thresholds")),
            ),
        )

    requirements = build_file_processing_staging_plan(manifest, local_report)
    if not requirements:
        metrics: Mapping[str, float] = {}
        return FileProcessingStagingReport(
            run_id=resolved_run_id,
            knowledge_base_id=None,
            case_results=(),
            metrics=metrics,
            threshold_results=evaluate_file_processing_metric_thresholds(
                metrics,
                _mapping(manifest.get("thresholds")),
            ),
        )

    fixture_root = _fixture_root(manifest, manifest_path)
    case_by_id = _case_manifest_by_id(manifest)
    requirements_by_case = _requirements_by_case(requirements)
    runtime_checks = (
        await _verify_extraction_artifact_cache_roundtrip(
            storage=storage,
            run_id=resolved_run_id,
            enabled=artifact_cache_enabled,
            artifact_cache_prefix=artifact_cache_prefix,
        ),
    )
    if any(not check.passed for check in runtime_checks):
        metrics = _staging_metrics(())
        return FileProcessingStagingReport(
            run_id=resolved_run_id,
            knowledge_base_id=None,
            case_results=(),
            runtime_checks=runtime_checks,
            metrics=metrics,
            threshold_results=(),
        )
    kb = await oracle.create_knowledge_base(
        name=f"file-processing-golden-{resolved_run_id}",
        description="file-processing golden staging run",
        default_search_mode=SearchMode.HYBRID,
    )
    case_results: list[FileProcessingStagingCaseResult] = []
    created_documents: list[str] = []
    created_objects: list[str] = []
    cleanup_status: dict[str, str] = {"knowledge_base": "pending" if cleanup else "skipped"}
    try:
        for case_id, case_requirements in requirements_by_case.items():
            case = case_by_id[case_id]
            result, document_ids, object_uris = await _run_staging_case(
                case,
                case_requirements=case_requirements,
                fixture_root=fixture_root,
                run_id=resolved_run_id,
                knowledge_base_id=kb.id,
                oracle=oracle,
                storage=storage,
                ingestion=ingestion,
                search=search,
            )
            case_results.append(result)
            created_documents.extend(document_ids)
            created_objects.extend(object_uris)
    finally:
        if cleanup:
            cleanup_status = await _cleanup_staging_resources(
                oracle=oracle,
                storage=storage,
                knowledge_base_id=kb.id,
                document_ids=created_documents,
                object_uris=created_objects,
            )
    metrics = _merge_local_contract_metrics(_staging_metrics(case_results), local_report)
    return FileProcessingStagingReport(
        run_id=resolved_run_id,
        knowledge_base_id=kb.id,
        case_results=tuple(case_results),
        runtime_checks=runtime_checks,
        metrics=metrics,
        threshold_results=evaluate_file_processing_metric_thresholds(
            metrics,
            _mapping(manifest.get("thresholds")),
        ),
        cleanup=cleanup_status,
    )


async def run_file_processing_staging_checks_with_real_clients(
    manifest: Mapping[str, object],
    *,
    manifest_path: Path,
    cleanup: bool = False,
    settings: Settings | None = None,
) -> FileProcessingStagingReport:
    """実 OCI / Oracle client を使って staging checks を実行する。"""
    resolved_settings = settings or get_settings()
    oracle = OracleClient(settings=resolved_settings)
    storage = ObjectStorageClient(settings=resolved_settings)
    try:
        return await run_file_processing_staging_checks(
            manifest,
            manifest_path=manifest_path,
            oracle=oracle,
            storage=storage,
            ingestion=IngestionPipeline(
                oracle=oracle,
                object_storage=storage,
                settings=resolved_settings,
            ),
            search=RagPipeline(oracle=oracle, settings=resolved_settings),
            cleanup=cleanup,
            artifact_cache_enabled=resolved_settings.rag_extraction_artifact_cache_enabled,
            artifact_cache_prefix=resolved_settings.rag_extraction_artifact_prefix,
        )
    finally:
        close_oracle_pool()


async def _verify_extraction_artifact_cache_roundtrip(
    *,
    storage: StagingObjectStorageProtocol,
    run_id: str,
    enabled: bool,
    artifact_cache_prefix: str,
) -> FileProcessingStagingRuntimeCheckResult:
    """extraction artifact cache の put/get/delete 契約を非機密 payload で検証する。"""
    check = "extraction_artifact_cache_roundtrip"
    if not enabled:
        return FileProcessingStagingRuntimeCheckResult(
            check=check,
            status="skipped",
            evidence={"artifact_cache_enabled": False},
        )

    prefix = _artifact_cache_probe_prefix(artifact_cache_prefix)
    key = f"{prefix}/staging-preflight/{run_id}/{uuid4().hex}.json"
    object_uri: str | None = None
    try:
        object_uri = await storage.put(
            key,
            ARTIFACT_CACHE_PROBE_PAYLOAD,
            ARTIFACT_CACHE_PROBE_CONTENT_TYPE,
        )
        fetched = await storage.get(object_uri)
        if fetched != ARTIFACT_CACHE_PROBE_PAYLOAD:
            cleanup_status = await _delete_artifact_cache_probe(storage, object_uri)
            return FileProcessingStagingRuntimeCheckResult(
                check=check,
                status="failed",
                failure_code="artifact_cache_probe_readback_mismatch",
                evidence={
                    "object_ref_hash": _hash_label(object_uri),
                    "payload_bytes": len(ARTIFACT_CACHE_PROBE_PAYLOAD),
                    "cleanup": cleanup_status,
                },
            )
        deleted = await storage.delete(object_uri)
    except Exception as exc:
        cleanup_status = (
            await _delete_artifact_cache_probe(storage, object_uri)
            if object_uri is not None
            else "not_created"
        )
        evidence: dict[str, object] = {
            "error_type": type(exc).__name__,
            "payload_bytes": len(ARTIFACT_CACHE_PROBE_PAYLOAD),
            "cleanup": cleanup_status,
        }
        if object_uri is not None:
            evidence["object_ref_hash"] = _hash_label(object_uri)
        return FileProcessingStagingRuntimeCheckResult(
            check=check,
            status="failed",
            failure_code="artifact_cache_roundtrip_failed",
            evidence=evidence,
        )

    cleanup_status = "deleted" if deleted else "missing"
    if not deleted:
        return FileProcessingStagingRuntimeCheckResult(
            check=check,
            status="failed",
            failure_code="artifact_cache_probe_cleanup_missing",
            evidence={
                "object_ref_hash": _hash_label(object_uri),
                "payload_bytes": len(ARTIFACT_CACHE_PROBE_PAYLOAD),
                "cleanup": cleanup_status,
            },
        )
    return FileProcessingStagingRuntimeCheckResult(
        check=check,
        status="ok",
        evidence={
            "object_ref_hash": _hash_label(object_uri),
            "payload_bytes": len(ARTIFACT_CACHE_PROBE_PAYLOAD),
            "cleanup": cleanup_status,
        },
    )


async def _delete_artifact_cache_probe(
    storage: StagingObjectStorageProtocol,
    object_uri: str,
) -> str:
    try:
        return "deleted" if await storage.delete(object_uri) else "missing"
    except Exception:
        return "error"


def _artifact_cache_probe_prefix(value: str) -> str:
    parts = [
        part
        for part in value.strip().replace("\\", "/").strip("/").split("/")
        if part not in ("", ".", "..")
    ]
    return "/".join(parts) or "artifacts/extractions"


async def _run_staging_case(
    case: Mapping[str, object],
    *,
    case_requirements: Sequence[FileProcessingStagingRequirement],
    fixture_root: Path,
    run_id: str,
    knowledge_base_id: str,
    oracle: StagingOracleProtocol,
    storage: StagingObjectStorageProtocol,
    ingestion: StagingIngestionProtocol,
    search: StagingSearchProtocol,
) -> tuple[FileProcessingStagingCaseResult, list[str], list[str]]:
    case_id = _case_id(case)
    if case_id == "duplicate-file-canonical-kb":
        return await _run_duplicate_staging_case(
            case,
            case_requirements=case_requirements,
            fixture_root=fixture_root,
            run_id=run_id,
            knowledge_base_id=knowledge_base_id,
            oracle=oracle,
            storage=storage,
            ingestion=ingestion,
            search=search,
        )
    evidence, document_ids, object_uris = await _ingest_fixture_for_staging(
        case,
        fixture_name=str(case["fixture"]),
        fixture_root=fixture_root,
        run_id=run_id,
        knowledge_base_id=knowledge_base_id,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
    )
    if _case_id(case) == "corrupted-file-partial-failure":
        second_pass = await _retry_existing_document(
            case,
            evidence=evidence,
            fixture_root=fixture_root,
            ingestion=ingestion,
            oracle=oracle,
        )
        evidence = second_pass
    elif _should_search_case(case):
        evidence = await _collect_search_evidence(
            case,
            evidence=evidence,
            knowledge_base_id=knowledge_base_id,
            search=search,
            document_filter=True,
        )
    gate_results = tuple(
        _evaluate_staging_requirement(requirement, case=case, evidence=evidence)
        for requirement in case_requirements
    )
    object_uris = _unique_nonempty_strings([*object_uris, *_artifact_paths_from_evidence(evidence)])
    return (
        _case_result_from_evidence(case, evidence, gate_results),
        document_ids,
        object_uris,
    )


async def _run_duplicate_staging_case(
    case: Mapping[str, object],
    *,
    case_requirements: Sequence[FileProcessingStagingRequirement],
    fixture_root: Path,
    run_id: str,
    knowledge_base_id: str,
    oracle: StagingOracleProtocol,
    storage: StagingObjectStorageProtocol,
    ingestion: StagingIngestionProtocol,
    search: StagingSearchProtocol,
) -> tuple[FileProcessingStagingCaseResult, list[str], list[str]]:
    canonical_evidence, document_ids, object_uris = await _ingest_fixture_for_staging(
        case,
        fixture_name=str(case["fixture"]),
        fixture_root=fixture_root,
        run_id=run_id,
        knowledge_base_id=None,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
    )
    duplicate_fixture = str(case["duplicate_fixture"])
    duplicate_data = (fixture_root / duplicate_fixture).read_bytes()
    duplicate_sha = hashlib.sha256(duplicate_data).hexdigest()
    duplicate_uri = await storage.put(
        f"file-processing-golden/{run_id}/{duplicate_fixture}",
        duplicate_data,
        _content_type(duplicate_fixture),
    )
    duplicate_detail = await oracle.create_document(
        file_name=duplicate_fixture,
        object_storage_path=duplicate_uri,
        content_type=_content_type(duplicate_fixture),
        file_size_bytes=len(duplicate_data),
        content_sha256=duplicate_sha,
        duplicate_of_document_id=canonical_evidence.document_id,
        knowledge_base_ids=[knowledge_base_id],
    )
    await oracle.assign_documents_to_knowledge_base(
        knowledge_base_id,
        [canonical_evidence.document_id],
    )
    document_ids.append(duplicate_detail.id)
    object_uris.append(duplicate_uri)
    search_response = await search.run(
        SearchRequest(
            query=_staging_query(case),
            top_k=5,
            rerank_top_n=3,
            mode=SearchMode.HYBRID,
            filters={"knowledge_base_id": knowledge_base_id},
            knowledge_base_ids=[knowledge_base_id],
        )
    )
    groundedness = _search_groundedness(search_response)
    evidence = replace(
        canonical_evidence,
        duplicate_document_id=duplicate_detail.id,
        duplicate_of_document_id=duplicate_detail.duplicate_of_document_id,
        knowledge_base_search_hit=any(
            chunk.document_id == canonical_evidence.document_id
            for chunk in search_response.citations
        ),
        retrieval_hit=any(
            chunk.document_id == canonical_evidence.document_id
            for chunk in search_response.citations
        ),
        search_executed=True,
        search_page_hit=_search_hits_expected_page(case, search_response, canonical_evidence),
        search_citation_count=len(search_response.citations),
        search_elapsed_ms=search_response.elapsed_ms,
        groundedness_passed=groundedness.grounded,
        groundedness_score=groundedness.score,
    )
    gate_results = tuple(
        _evaluate_staging_requirement(requirement, case=case, evidence=evidence)
        for requirement in case_requirements
    )
    object_uris = _unique_nonempty_strings([*object_uris, *_artifact_paths_from_evidence(evidence)])
    return (
        _case_result_from_evidence(case, evidence, gate_results),
        document_ids,
        object_uris,
    )


@dataclass(frozen=True)
class _StagingEvidence:
    document_id: str
    status: str
    chunks: tuple[DocumentChunkView, ...]
    segments: tuple[IngestionSegment, ...]
    extraction: Mapping[str, object]
    ingestion_error_type: str | None = None
    duplicate_document_id: str | None = None
    duplicate_of_document_id: str | None = None
    knowledge_base_search_hit: bool = False
    retrieval_hit: bool = False
    search_executed: bool = False
    search_page_hit: bool = False
    search_citation_count: int = 0
    search_elapsed_ms: float | None = None
    groundedness_passed: bool | None = None
    groundedness_score: float | None = None
    ingestion_elapsed_ms: float | None = None
    retry_segments: tuple[IngestionSegment, ...] = ()


async def _ingest_fixture_for_staging(
    case: Mapping[str, object],
    *,
    fixture_name: str,
    fixture_root: Path,
    run_id: str,
    knowledge_base_id: str | None,
    oracle: StagingOracleProtocol,
    storage: StagingObjectStorageProtocol,
    ingestion: StagingIngestionProtocol,
) -> tuple[_StagingEvidence, list[str], list[str]]:
    data = (fixture_root / fixture_name).read_bytes()
    content_type = _content_type(fixture_name)
    digest = hashlib.sha256(data).hexdigest()
    object_uri = await storage.put(
        f"file-processing-golden/{run_id}/{fixture_name}",
        data,
        content_type,
    )
    detail = await oracle.create_document(
        file_name=fixture_name,
        object_storage_path=object_uri,
        content_type=content_type,
        file_size_bytes=len(data),
        content_sha256=digest,
        knowledge_base_ids=[knowledge_base_id] if knowledge_base_id else None,
    )
    source_profile = build_source_profile(
        original_file_name=fixture_name,
        sanitized_file_name=fixture_name,
        content_type=content_type,
        file_size_bytes=len(data),
        content_sha256=digest,
        data=data,
    )
    error_type: str | None = None
    started = perf_counter()
    try:
        await ingestion.ingest(
            detail.id,
            data,
            FILE_PROCESSING_STAGING_PROMPT,
            content_type=content_type,
            source_profile=source_profile,
        )
    except Exception as exc:
        if _case_id(case) != "corrupted-file-partial-failure":
            raise
        error_type = type(exc).__name__
    ingestion_elapsed_ms = (perf_counter() - started) * 1000
    evidence = await _collect_staging_evidence(
        oracle,
        detail.id,
        ingestion_error_type=error_type,
    )
    evidence = replace(evidence, ingestion_elapsed_ms=ingestion_elapsed_ms)
    return evidence, [detail.id], [object_uri]


async def _retry_existing_document(
    case: Mapping[str, object],
    *,
    evidence: _StagingEvidence,
    fixture_root: Path,
    ingestion: StagingIngestionProtocol,
    oracle: StagingOracleProtocol,
) -> _StagingEvidence:
    fixture_name = str(case["fixture"])
    data = (fixture_root / fixture_name).read_bytes()
    content_type = _content_type(fixture_name)
    source_profile = build_source_profile(
        original_file_name=fixture_name,
        sanitized_file_name=fixture_name,
        content_type=content_type,
        file_size_bytes=len(data),
        content_sha256=hashlib.sha256(data).hexdigest(),
        data=data,
    )
    with suppress(Exception):
        await ingestion.ingest(
            evidence.document_id,
            data,
            FILE_PROCESSING_STAGING_PROMPT,
            content_type=content_type,
            source_profile=source_profile,
        )
    retry_evidence = await _collect_staging_evidence(
        oracle,
        evidence.document_id,
        ingestion_error_type=evidence.ingestion_error_type,
    )
    return replace(
        retry_evidence,
        retry_segments=retry_evidence.segments,
        ingestion_elapsed_ms=evidence.ingestion_elapsed_ms,
    )


async def _collect_search_evidence(
    case: Mapping[str, object],
    *,
    evidence: _StagingEvidence,
    knowledge_base_id: str,
    search: StagingSearchProtocol,
    document_filter: bool,
) -> _StagingEvidence:
    filters = {"knowledge_base_id": knowledge_base_id}
    if document_filter:
        filters["document_id"] = evidence.document_id
    search_response = await search.run(
        SearchRequest(
            query=_staging_query(case),
            top_k=5,
            rerank_top_n=3,
            mode=SearchMode.HYBRID,
            filters=filters,
            knowledge_base_ids=[knowledge_base_id],
        )
    )
    groundedness = _search_groundedness(search_response)
    return replace(
        evidence,
        search_executed=True,
        retrieval_hit=any(
            chunk.document_id == evidence.document_id for chunk in search_response.citations
        ),
        search_page_hit=_search_hits_expected_page(case, search_response, evidence),
        search_citation_count=len(search_response.citations),
        search_elapsed_ms=search_response.elapsed_ms,
        groundedness_passed=groundedness.grounded,
        groundedness_score=groundedness.score,
    )


async def _collect_staging_evidence(
    oracle: StagingOracleProtocol,
    document_id: str,
    *,
    ingestion_error_type: str | None = None,
) -> _StagingEvidence:
    detail = await oracle.get_document(document_id)
    chunks = tuple(await oracle.list_document_chunks(document_id))
    segments = tuple(await oracle.list_ingestion_segments(document_id))
    status = detail.status.value if detail is not None else "UNKNOWN"
    extraction = detail.extraction if detail is not None else {}
    return _StagingEvidence(
        document_id=document_id,
        status=status,
        chunks=chunks,
        segments=segments,
        extraction=extraction,
        ingestion_error_type=ingestion_error_type,
    )


def _evaluate_staging_requirement(
    requirement: FileProcessingStagingRequirement,
    *,
    case: Mapping[str, object],
    evidence: _StagingEvidence,
) -> FileProcessingStagingGateResult:
    check = requirement.check
    passed = False
    failure_code: str | None = None
    if requirement.suggested_gate == "enterprise_ai_file_extraction_gate":
        passed, failure_code = _enterprise_ai_file_extraction_passed(case, evidence)
    elif requirement.suggested_gate == "preview_bbox_citation_gate":
        passed, failure_code = _preview_bbox_citation_passed(evidence)
    elif requirement.suggested_gate == "file_processing_page_hit_gate":
        passed, failure_code = _page_hit_passed(case, evidence)
    elif requirement.suggested_gate == "duplicate_kb_membership_gate":
        passed, failure_code = _duplicate_kb_membership_passed(evidence)
    elif requirement.suggested_gate == "segment_artifact_reuse_gate":
        passed, failure_code = _segment_artifact_reuse_passed(evidence)
    else:
        passed, failure_code = _generic_traceability_passed(evidence)
    return FileProcessingStagingGateResult(
        case_id=requirement.case_id,
        scenario=requirement.scenario,
        check=check,
        suggested_gate=requirement.suggested_gate,
        passed=passed,
        failure_code=None if passed else failure_code,
        evidence=_safe_evidence_summary(evidence),
    )


def _enterprise_ai_file_extraction_passed(
    case: Mapping[str, object],
    evidence: _StagingEvidence,
) -> tuple[bool, str | None]:
    if evidence.status != FileStatus.INDEXED.value:
        return False, "document_not_indexed"
    if not evidence.chunks:
        return False, "chunks_missing"
    expected_kind = _optional_str(case.get("expected_content_kind"))
    if expected_kind and not _has_content_kind(evidence.chunks, expected_kind):
        return False, "expected_content_kind_missing"
    expected_pages = _int_set(case.get("expected_pages"))
    if expected_pages and not expected_pages <= _chunk_pages(evidence.chunks):
        return False, "expected_pages_missing"
    if not _has_traceable_chunk(evidence.chunks):
        return False, "traceable_chunk_missing"
    return True, None


def _preview_bbox_citation_passed(evidence: _StagingEvidence) -> tuple[bool, str | None]:
    if any(_chunk_preview_addressable(chunk, evidence) for chunk in evidence.chunks):
        return True, None
    if any(_chunk_has_valid_bbox(chunk) for chunk in evidence.chunks):
        if any(_chunk_has_ambiguous_bbox_without_mode(chunk) for chunk in evidence.chunks):
            return False, "bbox_coordinate_mode_missing"
        return False, "preview_address_metadata_missing"
    return False, "bbox_missing"


def _page_hit_passed(
    case: Mapping[str, object],
    evidence: _StagingEvidence,
) -> tuple[bool, str | None]:
    expected_pages = _int_set(case.get("expected_pages"))
    if not expected_pages:
        return False, "expected_pages_missing"
    if not evidence.search_executed:
        return False, "search_not_executed"
    if evidence.search_page_hit:
        return True, None
    return False, "expected_page_not_retrieved"


def _duplicate_kb_membership_passed(evidence: _StagingEvidence) -> tuple[bool, str | None]:
    if not evidence.duplicate_document_id:
        return False, "duplicate_document_missing"
    if evidence.duplicate_of_document_id != evidence.document_id:
        return False, "duplicate_alias_missing"
    if not evidence.knowledge_base_search_hit:
        return False, "canonical_not_searchable_in_kb"
    return True, None


def _segment_artifact_reuse_passed(evidence: _StagingEvidence) -> tuple[bool, str | None]:
    segments = evidence.retry_segments or evidence.segments
    if not any(segment.status == "FAILED" for segment in segments):
        return False, "failed_segment_missing"
    if not any(segment.status == "SUCCEEDED" and segment.artifact_path for segment in segments):
        return False, "successful_segment_artifact_missing"
    return True, None


def _generic_traceability_passed(evidence: _StagingEvidence) -> tuple[bool, str | None]:
    if _has_traceable_chunk(evidence.chunks):
        return True, None
    return False, "traceable_chunk_missing"


def _case_result_from_evidence(
    case: Mapping[str, object],
    evidence: _StagingEvidence,
    gate_results: Sequence[FileProcessingStagingGateResult],
) -> FileProcessingStagingCaseResult:
    return FileProcessingStagingCaseResult(
        case_id=_case_id(case),
        scenario=str(case.get("scenario", "")),
        fixture=str(case.get("fixture", "")),
        document_id=evidence.document_id,
        status=evidence.status,
        chunk_count=len(evidence.chunks),
        segment_count=len(evidence.segments),
        gate_results=tuple(gate_results),
        cleanup={"document": "pending"},
    )


def _safe_evidence_summary(evidence: _StagingEvidence) -> Mapping[str, object]:
    return {
        "status": evidence.status,
        "chunk_count": len(evidence.chunks),
        "segment_count": len(evidence.segments),
        "bbox_chunk_count": sum(1 for chunk in evidence.chunks if _chunk_has_valid_bbox(chunk)),
        "preview_addressable_chunk_count": sum(
            1 for chunk in evidence.chunks if _chunk_preview_addressable(chunk, evidence)
        ),
        "element_lineage_chunk_count": sum(
            1 for chunk in evidence.chunks if _chunk_has_resolvable_element_lineage(chunk, evidence)
        ),
        "traceable_chunk_count": sum(1 for chunk in evidence.chunks if _chunk_traceable(chunk)),
        "artifact_segment_count": sum(1 for segment in evidence.segments if segment.artifact_path),
        "failed_segment_count": sum(
            1 for segment in evidence.segments if segment.status == "FAILED"
        ),
        "parser_fallback_used": _parser_fallback_used(evidence.extraction),
        "extraction_page_coverage": _extraction_page_coverage(evidence.extraction),
        "low_confidence_count": _extraction_low_confidence_count(evidence.extraction),
        "ingestion_elapsed_ms": _rounded_optional_float(evidence.ingestion_elapsed_ms),
        "retrieval_hit": evidence.retrieval_hit,
        "search_executed": evidence.search_executed,
        "search_page_hit": evidence.search_page_hit,
        "search_citation_count": evidence.search_citation_count,
        "search_elapsed_ms": _rounded_optional_float(evidence.search_elapsed_ms),
        "groundedness_passed": evidence.groundedness_passed,
        "groundedness_score": _rounded_optional_float(evidence.groundedness_score),
        "knowledge_base_search_hit": evidence.knowledge_base_search_hit,
        "ingestion_error_type": evidence.ingestion_error_type,
    }


def _staging_metrics(
    case_results: Sequence[FileProcessingStagingCaseResult],
) -> Mapping[str, float]:
    """staging gate の非機密 aggregate metrics を作る。"""
    gate_results = [gate for case in case_results for gate in case.gate_results]
    total_chunks = _evidence_sum(gate_results, "chunk_count")
    metrics: dict[str, float] = {
        "gate_pass_rate": _safe_ratio(
            sum(1 for gate in gate_results if gate.passed),
            len(gate_results),
        ),
        "parser_fallback_rate": _safe_ratio(
            _evidence_true_count(gate_results, "parser_fallback_used"),
            len(case_results),
        ),
        "low_confidence_document_rate": _safe_ratio(
            _evidence_positive_count(gate_results, "low_confidence_count"),
            len(case_results),
        ),
        "failed_segment_rate": _safe_ratio(
            _evidence_positive_count(gate_results, "failed_segment_count"),
            len(case_results),
        ),
        "citation_traceability_coverage": _safe_ratio(
            _evidence_sum(gate_results, "traceable_chunk_count"),
            total_chunks,
        ),
        "bbox_citation_coverage": _safe_ratio(
            _evidence_sum(gate_results, "bbox_chunk_count"),
            total_chunks,
        ),
        "preview_addressability_coverage": _safe_ratio(
            _evidence_sum(gate_results, "preview_addressable_chunk_count"),
            total_chunks,
        ),
        "element_lineage_coverage": _safe_ratio(
            _evidence_sum(gate_results, "element_lineage_chunk_count"),
            total_chunks,
        ),
        "retrieval_recall": _evidence_bool_ratio(
            gate_results,
            numerator_key="retrieval_hit",
            denominator_key="search_executed",
        ),
        "groundedness": _evidence_bool_ratio(
            gate_results,
            numerator_key="groundedness_passed",
            denominator_key="search_executed",
        ),
    }
    ingestion_elapsed_values = _evidence_float_values(gate_results, "ingestion_elapsed_ms")
    if ingestion_elapsed_values:
        metrics["ingestion_p95_ms"] = _percentile(ingestion_elapsed_values, 0.95)
    page_coverage_values = _evidence_float_values(gate_results, "extraction_page_coverage")
    if page_coverage_values:
        metrics["extraction_page_coverage"] = round(
            sum(page_coverage_values) / len(page_coverage_values),
            4,
        )
    _add_gate_metric(
        metrics,
        key="page_hit_accuracy",
        gate_results=gate_results,
        suggested_gate="file_processing_page_hit_gate",
    )
    _add_check_metric(
        metrics,
        key="table_qa_accuracy",
        gate_results=gate_results,
        check="table_qa_accuracy",
    )
    return metrics


def _merge_local_contract_metrics(
    staging_metrics: Mapping[str, float],
    local_report: FileProcessingContractReport,
) -> Mapping[str, float]:
    """staging gate では発生しない local measured metrics を report に補う。"""
    metrics = dict(staging_metrics)
    for key, value in _local_contract_metrics(local_report).items():
        metrics.setdefault(key, value)
    return metrics


def _local_contract_metrics(report: FileProcessingContractReport) -> dict[str, float]:
    metrics: dict[str, float] = {
        "parser_fallback_rate": _safe_ratio(
            sum(1 for result in report.case_results if result.fallback_used),
            report.case_count,
        ),
        "low_confidence_document_rate": _safe_ratio(
            sum(1 for result in report.case_results if result.low_confidence_count > 0),
            report.case_count,
        ),
        "failed_segment_rate": _safe_ratio(
            sum(1 for result in report.case_results if result.failed_segment_count > 0),
            report.case_count,
        ),
    }
    page_coverages = [
        result.page_coverage
        for result in report.case_results
        if result.page_coverage is not None
    ]
    if page_coverages:
        metrics["extraction_page_coverage"] = round(
            sum(page_coverages) / len(page_coverages),
            4,
        )
    check_metrics = {
        "table_qa_accuracy": "table_qa_accuracy",
        "citation_traceability_coverage": "citation_traceability",
        "bbox_citation_coverage": "bbox_citation",
        "preview_addressability_coverage": "preview_jump",
        "element_lineage_coverage": "element_lineage",
    }
    for metric, check in check_metrics.items():
        value = _local_check_metric_value(report, check)
        if value is not None:
            metrics[metric] = value
    return metrics


def _local_check_metric_value(
    report: FileProcessingContractReport,
    check: str,
) -> float | None:
    passed_count = 0
    failed_count = 0
    for result in report.case_results:
        if check in result.passed_checks:
            passed_count += 1
        if any(failure.startswith(f"{check}:") for failure in result.failures):
            failed_count += 1
    measured_count = passed_count + failed_count
    if measured_count == 0:
        return None
    return _safe_ratio(passed_count, measured_count)


def _add_gate_metric(
    metrics: dict[str, float],
    *,
    key: str,
    gate_results: Sequence[FileProcessingStagingGateResult],
    suggested_gate: str,
) -> None:
    matching = [gate for gate in gate_results if gate.suggested_gate == suggested_gate]
    if matching:
        metrics[key] = _safe_ratio(sum(1 for gate in matching if gate.passed), len(matching))


def _add_check_metric(
    metrics: dict[str, float],
    *,
    key: str,
    gate_results: Sequence[FileProcessingStagingGateResult],
    check: str,
) -> None:
    matching = [gate for gate in gate_results if gate.check == check]
    if matching:
        metrics[key] = _safe_ratio(sum(1 for gate in matching if gate.passed), len(matching))


def _evidence_sum(
    gate_results: Sequence[FileProcessingStagingGateResult],
    key: str,
) -> int:
    values: dict[str, int] = {}
    for gate in gate_results:
        evidence = _mapping(gate.evidence)
        value = evidence.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        values[gate.case_id] = max(values.get(gate.case_id, 0), value)
    return sum(values.values())


def _evidence_true_count(
    gate_results: Sequence[FileProcessingStagingGateResult],
    key: str,
) -> int:
    values: dict[str, bool] = {}
    for gate in gate_results:
        evidence = _mapping(gate.evidence)
        value = evidence.get(key)
        if isinstance(value, bool):
            values[gate.case_id] = values.get(gate.case_id, False) or value
    return sum(1 for value in values.values() if value)


def _evidence_positive_count(
    gate_results: Sequence[FileProcessingStagingGateResult],
    key: str,
) -> int:
    values: dict[str, int] = {}
    for gate in gate_results:
        evidence = _mapping(gate.evidence)
        value = evidence.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        values[gate.case_id] = max(values.get(gate.case_id, 0), int(value))
    return sum(1 for value in values.values() if value > 0)


def _evidence_bool_ratio(
    gate_results: Sequence[FileProcessingStagingGateResult],
    *,
    numerator_key: str,
    denominator_key: str,
) -> float:
    numerator_values: dict[str, bool] = {}
    denominator_values: dict[str, bool] = {}
    for gate in gate_results:
        evidence = _mapping(gate.evidence)
        denominator = evidence.get(denominator_key)
        if isinstance(denominator, bool):
            denominator_values[gate.case_id] = (
                denominator_values.get(gate.case_id, False) or denominator
            )
        numerator = evidence.get(numerator_key)
        if isinstance(numerator, bool):
            numerator_values[gate.case_id] = numerator_values.get(gate.case_id, False) or numerator
    denominator_count = sum(1 for value in denominator_values.values() if value)
    numerator_count = sum(
        1
        for case_id, value in numerator_values.items()
        if value and denominator_values.get(case_id)
    )
    return _safe_ratio(numerator_count, denominator_count)


def _evidence_float_values(
    gate_results: Sequence[FileProcessingStagingGateResult],
    key: str,
) -> tuple[float, ...]:
    values_by_case: dict[str, float] = {}
    for gate in gate_results:
        evidence = _mapping(gate.evidence)
        value = evidence.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        values_by_case[gate.case_id] = float(value)
    return tuple(values_by_case.values())


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _rounded_optional_float(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _parser_fallback_used(extraction: Mapping[str, object]) -> bool:
    quality = _mapping(extraction.get("quality_report"))
    artifacts = _mapping(extraction.get("parser_artifacts"))
    return bool(quality.get("fallback_used")) or bool(artifacts.get("fallback_used"))


def _extraction_page_coverage(extraction: Mapping[str, object]) -> float | None:
    quality = _mapping(extraction.get("quality_report"))
    value = _optional_float(quality.get("page_coverage"))
    return round(value, 4) if value is not None else None


def _extraction_low_confidence_count(extraction: Mapping[str, object]) -> int:
    quality = _mapping(extraction.get("quality_report"))
    return _positive_int(quality.get("low_confidence_count"))


def _should_search_case(case: Mapping[str, object]) -> bool:
    return any(
        isinstance(case.get(key), str) and str(case.get(key)).strip()
        for key in ("staging_query", "expected_answer")
    )


def _search_groundedness(search_response: SearchResponse) -> GroundednessEvaluation:
    context = "\n".join(chunk.text for chunk in search_response.citations)
    return evaluate_groundedness(search_response.answer, context)


def _search_hits_expected_page(
    case: Mapping[str, object],
    search_response: SearchResponse,
    evidence: _StagingEvidence,
) -> bool:
    expected_pages = _int_set(case.get("expected_pages"))
    if not expected_pages:
        return any(chunk.document_id == evidence.document_id for chunk in search_response.citations)
    for chunk in search_response.citations:
        if chunk.document_id != evidence.document_id:
            continue
        page_start = _chunk_page_int(chunk, "page_start")
        page_end = _chunk_page_int(chunk, "page_end") or page_start
        if page_start is None or page_end is None:
            continue
        if expected_pages & set(range(page_start, page_end + 1)):
            return True
    return False


def _metadata_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _metadata_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _chunk_page_int(chunk: RetrievedChunk, key: str) -> int | None:
    """Search citation の page 値を metadata/top-level 両対応で読む。"""
    value = getattr(chunk, key, None)
    if value is None:
        value = chunk.metadata.get(key)
    return _metadata_int(value)


async def _cleanup_staging_resources(
    *,
    oracle: StagingOracleProtocol,
    storage: StagingObjectStorageProtocol,
    knowledge_base_id: str,
    document_ids: Sequence[str],
    object_uris: Sequence[str],
) -> dict[str, str]:
    status: dict[str, str] = {}
    for document_id in _unique_nonempty_strings(document_ids):
        try:
            status[f"document:{document_id}"] = (
                "deleted" if await oracle.delete_document(document_id) else "missing"
            )
        except Exception:
            status[f"document:{document_id}"] = "error"
    for object_uri in _unique_nonempty_strings(object_uris):
        try:
            status[f"object:{_hash_label(object_uri)}"] = (
                "deleted" if await storage.delete(object_uri) else "missing"
            )
        except Exception:
            status[f"object:{_hash_label(object_uri)}"] = "error"
    try:
        await oracle.archive_knowledge_base(knowledge_base_id)
    except Exception:
        status["knowledge_base"] = "error"
    else:
        status["knowledge_base"] = "archived"
    return status


def _artifact_paths_from_evidence(evidence: _StagingEvidence) -> tuple[str, ...]:
    """staging ingestion が生成した full/segment artifact path を cleanup 対象にする。"""
    paths: list[str] = []
    parser_artifacts = _mapping(evidence.extraction.get("parser_artifacts"))
    artifact_path = parser_artifacts.get("extraction_artifact_path")
    if isinstance(artifact_path, str):
        paths.append(artifact_path)
    for segment in (*evidence.segments, *evidence.retry_segments):
        if segment.artifact_path:
            paths.append(segment.artifact_path)
    return tuple(_unique_nonempty_strings(paths))


def _unique_nonempty_strings(values: Sequence[str]) -> list[str]:
    """順序を保って空文字と重複を除く。"""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        stripped = value.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        result.append(stripped)
    return result


def _requirements_by_case(
    requirements: Sequence[FileProcessingStagingRequirement],
) -> dict[str, list[FileProcessingStagingRequirement]]:
    grouped: dict[str, list[FileProcessingStagingRequirement]] = defaultdict(list)
    for requirement in requirements:
        grouped[requirement.case_id].append(requirement)
    return dict(grouped)


def _case_manifest_by_id(manifest: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, str | bytes | bytearray):
        return {}
    return {
        _case_id(case): case for case in raw_cases if isinstance(case, Mapping) and _case_id(case)
    }


def _fixture_root(manifest: Mapping[str, object], manifest_path: Path) -> Path:
    root = str(manifest.get("fixture_root") or ".")
    return (manifest_path.parent / root).resolve()


def _content_type(file_name: str) -> str:
    return mimetypes.guess_type(file_name)[0] or "application/octet-stream"


def _case_id(case: Mapping[str, object]) -> str:
    return str(case.get("id") or "")


def _staging_query(case: Mapping[str, object]) -> str:
    for key in ("staging_query", "expected_answer", "notes"):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(case.get("id") or "file-processing golden")


def _has_content_kind(chunks: Sequence[DocumentChunkView], expected_kind: str) -> bool:
    if expected_kind == "text":
        return bool(chunks)
    return any(chunk.content_kind == expected_kind for chunk in chunks)


def _chunk_pages(chunks: Sequence[DocumentChunkView]) -> set[int]:
    pages: set[int] = set()
    for chunk in chunks:
        if chunk.page_start is None:
            continue
        page_end = chunk.page_end or chunk.page_start
        pages.update(range(chunk.page_start, page_end + 1))
    return pages


def _has_traceable_chunk(chunks: Sequence[DocumentChunkView]) -> bool:
    return any(_chunk_traceable(chunk) for chunk in chunks)


def _chunk_traceable(chunk: DocumentChunkView) -> bool:
    return bool(
        chunk.chunk_id
        and chunk.document_id
        and (chunk.element_ids or _chunk_has_valid_bbox(chunk) or chunk.section_path)
        and chunk.page_start is not None
    )


def _chunk_preview_addressable(chunk: DocumentChunkView, evidence: _StagingEvidence) -> bool:
    return bool(
        chunk.chunk_id
        and chunk.document_id
        and chunk.page_start is not None
        and _chunk_has_valid_bbox(chunk)
        and not _chunk_has_ambiguous_bbox_without_mode(chunk)
        and _chunk_bbox_has_preview_scale(chunk, evidence)
        and _chunk_has_resolvable_element_lineage(chunk, evidence)
    )


def _chunk_has_valid_bbox(chunk: DocumentChunkView) -> bool:
    bbox = chunk.bbox
    if not bbox or len(bbox) < 4:
        return False
    x, y, right_or_width, bottom_or_height = bbox[:4]
    if not all(math.isfinite(value) for value in (x, y, right_or_width, bottom_or_height)):
        return False
    if x < 0 or y < 0:
        return False
    return not (right_or_width <= 0 or bottom_or_height <= 0)


def _chunk_bbox_has_preview_scale(chunk: DocumentChunkView, evidence: _StagingEvidence) -> bool:
    """frontend preview と同じく absolute bbox は page size が必要。"""
    if not chunk.bbox:
        return False
    unit = _chunk_bbox_unit(chunk)
    if unit in {"ratio", "percent"}:
        return True
    if unit is None and max(chunk.bbox[:4]) <= 100:
        return True
    if chunk.page_start is None:
        return False
    return _extraction_page_has_size(evidence.extraction, chunk.page_start)


def _chunk_has_ambiguous_bbox_without_mode(chunk: DocumentChunkView) -> bool:
    """非原点 bbox は xyxy / xywh の解釈差が出るため mode metadata を要求する。"""
    if not chunk.bbox or not _chunk_has_valid_bbox(chunk):
        return False
    if _chunk_bbox_coordinate_mode(chunk) is not None:
        return False
    x, y, right_or_width, bottom_or_height = chunk.bbox[:4]
    return bool((x > 0 or y > 0) and right_or_width > x and bottom_or_height > y)


def _chunk_bbox_coordinate_mode(chunk: DocumentChunkView) -> str | None:
    value = chunk.metadata.get("bbox_coordinate_mode")
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold().replace("-", "_").replace(",", "_")
    normalized = "_".join(part for part in normalized.split("_") if part)
    if normalized in {"xyxy", "x1_y1_x2_y2"}:
        return "xyxy"
    if normalized in {"xywh", "x_y_width_height", "left_top_width_height"}:
        return "xywh"
    return None


def _chunk_bbox_unit(chunk: DocumentChunkView) -> str | None:
    value = chunk.metadata.get("bbox_unit")
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold().replace("-", "_")
    if normalized in {"ratio", "normalized", "relative"}:
        return "ratio"
    if normalized in {"percent", "percentage"}:
        return "percent"
    if normalized in {"absolute", "pixel", "pixels", "point", "points"}:
        return "absolute"
    return None


def _extraction_page_has_size(extraction: Mapping[str, object], page_number: int) -> bool:
    pages = extraction.get("pages")
    if not isinstance(pages, Sequence) or isinstance(pages, str | bytes | bytearray):
        return False
    for page in pages:
        if not isinstance(page, Mapping):
            continue
        if _metadata_int(page.get("page_number")) != page_number:
            continue
        width = _metadata_float(page.get("width"))
        height = _metadata_float(page.get("height"))
        return width is not None and height is not None and width > 0 and height > 0
    return False


def _chunk_has_resolvable_element_lineage(
    chunk: DocumentChunkView,
    evidence: _StagingEvidence,
) -> bool:
    chunk_element_ids = _chunk_element_ids(chunk)
    if not chunk_element_ids:
        return False
    extraction_element_ids = _extraction_element_ids(evidence.extraction)
    return bool(extraction_element_ids and (set(chunk_element_ids) & extraction_element_ids))


def _chunk_element_ids(chunk: DocumentChunkView) -> tuple[str, ...]:
    return tuple(item.strip() for item in chunk.element_ids if item.strip())


def _extraction_element_ids(extraction: Mapping[str, object]) -> set[str]:
    element_ids: set[str] = set()
    elements = extraction.get("elements")
    if isinstance(elements, Sequence) and not isinstance(elements, str | bytes | bytearray):
        for index, element in enumerate(elements):
            if not isinstance(element, Mapping):
                continue
            element_id = _optional_str(element.get("element_id"))
            if not element_id:
                element_id = _optional_str(_mapping(element.get("metadata")).get("element_id"))
            if not element_id:
                order = _metadata_int(element.get("order"))
                element_id = f"el-{order:04d}" if order is not None else f"el-{index:04d}"
            element_ids.add(element_id)
    pages = extraction.get("pages")
    if isinstance(pages, Sequence) and not isinstance(pages, str | bytes | bytearray):
        for page in pages:
            if not isinstance(page, Mapping):
                continue
            for element_id in _string_sequence(page.get("element_ids")):
                element_ids.add(element_id)
    return element_ids


def _string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if not isinstance(value, Sequence) or isinstance(value, bytes | bytearray):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _int_set(value: object) -> set[int]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return set()
    result: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            result.add(item)
        elif isinstance(item, str) and item.strip().isdigit():
            result.add(int(item.strip()))
    return result


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, float) and value.is_integer():
        parsed = int(value)
        return parsed if parsed > 0 else 0
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _hash_label(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
