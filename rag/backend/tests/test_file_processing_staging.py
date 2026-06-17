"""file-processing staging runner のテスト。"""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pytest import MonkeyPatch

from app.config import Settings
from app.rag import file_processing_staging_cli, parser_adapter_readiness
from app.rag.file_processing_staging import run_file_processing_staging_checks
from app.rag.staging_smoke import SmokePreflightResult
from app.schemas.document import (
    DocumentChunkView,
    DocumentDetail,
    FileStatus,
    IngestionSegment,
    SourceProfile,
)
from app.schemas.knowledge_base import KnowledgeBaseDetail, KnowledgeBaseStatus
from app.schemas.search import (
    RetrievedChunk,
    SearchDiagnostics,
    SearchMode,
    SearchRequest,
    SearchResponse,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


async def test_file_processing_staging_runner_closes_pending_gates_with_evidence() -> None:
    """staging runner は pending requirements を実 evidence で gate 判定する。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    oracle = FakeStagingOracle()
    storage = FakeObjectStorage()
    ingestion = FakeIngestion(oracle)
    search = FakeSearch(oracle)

    report = await run_file_processing_staging_checks(
        manifest,
        manifest_path=manifest_path,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
        search=search,
        cleanup=True,
        run_id="run-1",
    )

    assert report.passed is True
    assert report.case_count == 5
    assert report.gate_count == 19
    assert report.failure_count == 0
    assert report.runtime_checks[0].check == "extraction_artifact_cache_roundtrip"
    assert report.runtime_checks[0].status == "ok"
    assert report.runtime_checks[0].evidence is not None
    assert report.runtime_checks[0].evidence["cleanup"] == "deleted"
    assert "contains_document_text" not in str(report.runtime_checks[0].evidence)
    assert report.metrics["gate_pass_rate"] == 1.0
    assert report.metrics["parser_fallback_rate"] == 0.0
    assert report.metrics["extraction_page_coverage"] >= 0.8
    assert report.metrics["low_confidence_document_rate"] == 0.0
    assert report.metrics["failed_segment_rate"] <= 0.25
    assert report.metrics["citation_traceability_coverage"] == 1.0
    assert report.metrics["bbox_citation_coverage"] == 1.0
    assert report.metrics["preview_addressability_coverage"] == 1.0
    assert report.metrics["element_lineage_coverage"] == 1.0
    assert report.metrics["retrieval_recall"] == 1.0
    assert report.metrics["groundedness"] == 1.0
    assert report.metrics["ingestion_p95_ms"] >= 0.0
    assert report.metrics["page_hit_accuracy"] == 1.0
    assert report.metrics["table_qa_accuracy"] == 1.0
    threshold_by_metric = {result.metric: result for result in report.threshold_results}
    assert threshold_by_metric["retrieval_recall"].status == "passed"
    assert threshold_by_metric["table_qa_accuracy"].status == "passed"
    assert threshold_by_metric["page_hit_accuracy"].status == "passed"
    assert threshold_by_metric["parser_fallback_rate"].status == "passed"
    assert threshold_by_metric["extraction_page_coverage"].status == "passed"
    assert threshold_by_metric["low_confidence_document_rate"].status == "passed"
    assert threshold_by_metric["failed_segment_rate"].status == "passed"
    assert report.cleanup is not None
    assert report.cleanup["knowledge_base"] == "archived"
    assert all(result.passed for result in report.case_results)
    assert all(
        "raw_text" not in str(gate.evidence)
        for case_result in report.case_results
        for gate in case_result.gate_results
    )
    payload = file_processing_staging_cli._report_payload(report, manifest=manifest)
    assert payload["promotion_ready"] is True
    assert payload["promotion_blockers"] == []
    assert payload["staging_policy"]["required_runtime_checks"] == [
        "extraction_artifact_cache_roundtrip"
    ]

    by_case = {result.case_id: result for result in report.case_results}
    duplicate = by_case["duplicate-file-canonical-kb"]
    assert any(
        gate.suggested_gate == "duplicate_kb_membership_gate" for gate in duplicate.gate_results
    )
    corrupted = by_case["corrupted-file-partial-failure"]
    assert any(
        gate.suggested_gate == "segment_artifact_reuse_gate" for gate in corrupted.gate_results
    )
    assert any(
        deleted.startswith("oci://namespace/bucket/artifacts/extractions/staging-preflight/run-1/")
        for deleted in storage.deleted
    )
    assert any(
        deleted.startswith("oci://namespace/bucket/artifacts/extractions/doc-")
        and "/full.json" in deleted
        for deleted in storage.deleted
    )
    assert any(
        deleted.startswith("oci://namespace/bucket/artifacts/extractions/doc-")
        and "/segments/" in deleted
        for deleted in storage.deleted
    )


async def test_staging_blocks_promotion_when_required_artifact_cache_is_skipped() -> None:
    """artifact cache を無効にした staging は実行できても production promotion では止める。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    oracle = FakeStagingOracle()
    storage = FakeObjectStorage()
    ingestion = FakeIngestion(oracle)
    search = FakeSearch(oracle)

    report = await run_file_processing_staging_checks(
        manifest,
        manifest_path=manifest_path,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
        search=search,
        run_id="artifact-cache-disabled",
        artifact_cache_enabled=False,
    )

    payload = file_processing_staging_cli._report_payload(report, manifest=manifest)

    assert report.passed is True
    assert report.runtime_checks[0].check == "extraction_artifact_cache_roundtrip"
    assert report.runtime_checks[0].status == "skipped"
    assert payload["promotion_ready"] is False
    assert {
        "code": "required_runtime_check_not_ok",
        "check": "extraction_artifact_cache_roundtrip",
        "status": "skipped",
    } in payload["promotion_blockers"]


async def test_file_processing_staging_runner_fails_on_unreadable_artifact_cache() -> None:
    """artifact cache が読めない staging 環境では golden case 実行前に止める。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    oracle = FakeStagingOracle()
    storage = FakeMismatchedArtifactStorage()
    ingestion = FakeIngestion(oracle)
    search = FakeSearch(oracle)

    report = await run_file_processing_staging_checks(
        manifest,
        manifest_path=manifest_path,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
        search=search,
        run_id="artifact-readback-fail",
    )

    assert report.passed is False
    assert report.case_count == 0
    assert report.gate_count == 0
    assert report.failure_count == 1
    assert report.knowledge_base_id is None
    assert len(report.runtime_checks) == 1
    assert report.runtime_checks[0].status == "failed"
    assert report.runtime_checks[0].failure_code == "artifact_cache_probe_readback_mismatch"
    assert report.runtime_checks[0].evidence is not None
    assert report.runtime_checks[0].evidence["cleanup"] == "deleted"
    assert "POLICY" not in str(report.runtime_checks[0].evidence)


async def test_file_processing_staging_runner_reports_missing_bbox_gate() -> None:
    """bbox が欠けると preview bbox gate は失敗として返る。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    oracle = FakeStagingOracle()
    storage = FakeObjectStorage()
    ingestion = FakeIngestion(oracle, image_has_bbox=False)
    search = FakeSearch(oracle)

    report = await run_file_processing_staging_checks(
        manifest,
        manifest_path=manifest_path,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
        search=search,
        run_id="run-2",
    )

    image_result = next(
        result for result in report.case_results if result.case_id == "image-ocr-bbox"
    )
    failed_gates = [gate for gate in image_result.gate_results if not gate.passed]

    assert report.passed is False
    assert report.metrics["bbox_citation_coverage"] < 1.0
    assert report.metrics["preview_addressability_coverage"] < 1.0
    threshold_by_metric = {result.metric: result for result in report.threshold_results}
    assert threshold_by_metric["bbox_citation_coverage"].status == "failed"
    assert threshold_by_metric["preview_addressability_coverage"].status == "failed"
    assert any(gate.failure_code == "bbox_missing" for gate in failed_gates)


async def test_file_processing_staging_runner_rejects_invalid_bbox_gate() -> None:
    """ゼロ面積 bbox は preview へ位置決めできないため欠落扱いにする。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    oracle = FakeStagingOracle()
    storage = FakeObjectStorage()
    ingestion = FakeIngestion(oracle, image_bbox=[0, 0, 0, 0])
    search = FakeSearch(oracle)

    report = await run_file_processing_staging_checks(
        manifest,
        manifest_path=manifest_path,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
        search=search,
        run_id="run-invalid-bbox",
    )

    image_result = next(
        result for result in report.case_results if result.case_id == "image-ocr-bbox"
    )
    failed_gates = [gate for gate in image_result.gate_results if not gate.passed]

    assert report.passed is False
    assert report.metrics["bbox_citation_coverage"] < 1.0
    assert report.metrics["preview_addressability_coverage"] < 1.0
    assert any(gate.failure_code == "bbox_missing" for gate in failed_gates)


async def test_file_processing_staging_runner_requires_bbox_mode_for_ambiguous_bbox() -> None:
    """非原点 bbox は xyxy / xywh の判別 metadata がないと preview gate を通さない。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    oracle = FakeStagingOracle()
    storage = FakeObjectStorage()
    ingestion = FakeIngestion(
        oracle,
        image_bbox=[25, 10, 50, 40],
        image_bbox_coordinate_mode=None,
    )
    search = FakeSearch(oracle)

    report = await run_file_processing_staging_checks(
        manifest,
        manifest_path=manifest_path,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
        search=search,
        run_id="run-ambiguous-bbox-no-mode",
    )

    image_result = next(
        result for result in report.case_results if result.case_id == "image-ocr-bbox"
    )
    failed_gates = [gate for gate in image_result.gate_results if not gate.passed]

    assert report.passed is False
    assert report.metrics["bbox_citation_coverage"] == 1.0
    assert report.metrics["preview_addressability_coverage"] < 1.0
    assert any(gate.failure_code == "bbox_coordinate_mode_missing" for gate in failed_gates)


async def test_file_processing_staging_runner_requires_preview_address_metadata() -> None:
    """bbox だけでは不十分で、preview jump には page と element lineage も必要。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    oracle = FakeStagingOracle()
    storage = FakeObjectStorage()
    ingestion = FakeIngestion(oracle, image_has_element_ids=False)
    search = FakeSearch(oracle)

    report = await run_file_processing_staging_checks(
        manifest,
        manifest_path=manifest_path,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
        search=search,
        run_id="run-3",
    )

    image_result = next(
        result for result in report.case_results if result.case_id == "image-ocr-bbox"
    )
    failed_gates = [gate for gate in image_result.gate_results if not gate.passed]

    assert report.passed is False
    assert report.metrics["bbox_citation_coverage"] == 1.0
    assert report.metrics["preview_addressability_coverage"] < 1.0
    threshold_by_metric = {result.metric: result for result in report.threshold_results}
    assert threshold_by_metric["bbox_citation_coverage"].status == "passed"
    assert threshold_by_metric["preview_addressability_coverage"].status == "failed"
    assert any(gate.failure_code == "preview_address_metadata_missing" for gate in failed_gates)


async def test_file_processing_staging_runner_rejects_orphan_element_lineage() -> None:
    """chunk の element_ids が extraction tree に存在しない場合は lineage 不足にする。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    oracle = FakeStagingOracle()
    storage = FakeObjectStorage()
    ingestion = FakeIngestion(
        oracle,
        image_chunk_element_ids=["missing-el"],
        image_extraction_element_ids=["el-1"],
    )
    search = FakeSearch(oracle)

    report = await run_file_processing_staging_checks(
        manifest,
        manifest_path=manifest_path,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
        search=search,
        run_id="run-orphan-element-lineage",
    )

    image_result = next(
        result for result in report.case_results if result.case_id == "image-ocr-bbox"
    )
    failed_gates = [gate for gate in image_result.gate_results if not gate.passed]

    assert report.passed is False
    assert report.metrics["element_lineage_coverage"] < 1.0
    assert report.metrics["preview_addressability_coverage"] < 1.0
    assert any(gate.failure_code == "preview_address_metadata_missing" for gate in failed_gates)


async def test_file_processing_staging_runner_requires_page_size_for_absolute_bbox() -> None:
    """absolute bbox は extraction.pages の page size がないと preview へ定位できない。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    oracle = FakeStagingOracle()
    storage = FakeObjectStorage()
    ingestion = FakeIngestion(oracle, image_bbox=[153, 198, 306, 396])
    search = FakeSearch(oracle)

    report = await run_file_processing_staging_checks(
        manifest,
        manifest_path=manifest_path,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
        search=search,
        run_id="run-absolute-bbox-no-page-size",
    )

    image_result = next(
        result for result in report.case_results if result.case_id == "image-ocr-bbox"
    )
    failed_gates = [gate for gate in image_result.gate_results if not gate.passed]

    assert report.passed is False
    assert report.metrics["bbox_citation_coverage"] == 1.0
    assert report.metrics["preview_addressability_coverage"] < 1.0
    assert any(gate.failure_code == "preview_address_metadata_missing" for gate in failed_gates)


async def test_file_processing_staging_runner_accepts_absolute_bbox_with_page_size() -> None:
    """absolute bbox は page size があれば preview addressable として扱える。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    oracle = FakeStagingOracle()
    storage = FakeObjectStorage()
    ingestion = FakeIngestion(
        oracle,
        image_bbox=[153, 198, 306, 396],
        image_page_size={"width": 612, "height": 792},
    )
    search = FakeSearch(oracle)

    report = await run_file_processing_staging_checks(
        manifest,
        manifest_path=manifest_path,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
        search=search,
        run_id="run-absolute-bbox-with-page-size",
    )

    assert report.passed is True
    assert report.metrics["bbox_citation_coverage"] == 1.0
    assert report.metrics["preview_addressability_coverage"] == 1.0


async def test_file_processing_staging_runner_requires_search_for_page_hit() -> None:
    """page hit は抽出ページではなく検索 citation のページ命中で判定する。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    two_column_case = next(
        case for case in manifest["cases"] if case["id"] == "two-column-pdf-reading-order"
    )
    two_column_case.pop("staging_query")
    oracle = FakeStagingOracle()
    storage = FakeObjectStorage()
    ingestion = FakeIngestion(oracle)
    search = FakeSearch(oracle)

    report = await run_file_processing_staging_checks(
        manifest,
        manifest_path=manifest_path,
        oracle=oracle,
        storage=storage,
        ingestion=ingestion,
        search=search,
        run_id="page-hit-no-search",
    )

    two_column_result = next(
        result for result in report.case_results if result.case_id == "two-column-pdf-reading-order"
    )
    failed_gates = [gate for gate in two_column_result.gate_results if not gate.passed]
    threshold_by_metric = {result.metric: result for result in report.threshold_results}

    assert report.passed is False
    assert report.metrics["page_hit_accuracy"] == 0.0
    assert threshold_by_metric["page_hit_accuracy"].status == "failed"
    assert any(gate.failure_code == "search_not_executed" for gate in failed_gates)


def test_file_processing_staging_cli_preflight_only_reports_safe_config_gap(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """preflight-only は外部接続せず安全な設定不足だけを artifact 化する。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    output_path = tmp_path / "file-processing-staging-preflight.json"
    monkeypatch.setattr(
        file_processing_staging_cli,
        "get_settings",
        lambda: Settings(
            oci_compartment_id="",
            oci_enterprise_ai_endpoint="",
            oci_enterprise_ai_project_ocid="",
            oci_enterprise_ai_api_key="",
            oci_enterprise_ai_models=[],
            oci_enterprise_ai_default_model="",
            oracle_user="",
            oracle_dsn="",
            object_storage_namespace="",
            object_storage_bucket="",
            upload_storage_backend="oci",
            oracle_password="super-secret-password",
        ),
    )

    exit_code = file_processing_staging_cli.main(
        [str(manifest_path), "--preflight-only", "--output", str(output_path)]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["preflight"]["ok"] is False
    assert payload["preflight"]["checks"]["oracle"] == "missing"
    assert payload["preflight"]["checks"]["object_storage"] == "missing"
    assert payload["parser_adapters"]["adapter_backend"] == "local"
    assert payload["parser_adapters"]["effective_order"] == []
    assert payload["parser_adapter_preflight"]["ok"] is True
    assert "super-secret-password" not in output_path.read_text(encoding="utf-8")


def test_file_processing_staging_cli_stops_before_clients_when_preflight_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """通常実行でも preflight 失敗時は実 staging client を作らない。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    output_path = tmp_path / "file-processing-staging-preflight.json"
    monkeypatch.setattr(file_processing_staging_cli, "get_settings", Settings)

    async def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real staging runner must not be called")

    monkeypatch.setattr(
        file_processing_staging_cli,
        "run_file_processing_staging_checks_with_real_clients",
        fail_if_called,
    )

    exit_code = file_processing_staging_cli.main([str(manifest_path), "--output", str(output_path)])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["preflight"]["ok"] is False
    assert payload["parser_adapters"]["adapter_backend"] == "local"


def test_file_processing_staging_cli_fails_when_report_is_not_promotion_ready(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """staging CLI は report.passed だけでなく promotion_ready=false も失敗扱いにする。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    output_path = tmp_path / "file-processing-staging-report.json"
    monkeypatch.setattr(
        file_processing_staging_cli,
        "get_settings",
        lambda: _complete_oci_settings(),
    )
    monkeypatch.setattr(
        file_processing_staging_cli,
        "staging_smoke_preflight",
        lambda *, settings: SmokePreflightResult(
            ok=True,
            checks={"oracle": "configured", "object_storage": "configured"},
            message="ok",
        ),
    )

    async def fake_runner(
        manifest: Mapping[str, object],
        *,
        manifest_path: Path,
        cleanup: bool = False,
    ) -> object:
        del cleanup
        oracle = FakeStagingOracle()
        return await run_file_processing_staging_checks(
            manifest,
            manifest_path=manifest_path,
            oracle=oracle,
            storage=FakeObjectStorage(),
            ingestion=FakeIngestion(oracle),
            search=FakeSearch(oracle),
            run_id="cli-promotion-not-ready",
            artifact_cache_enabled=False,
        )

    monkeypatch.setattr(
        file_processing_staging_cli,
        "run_file_processing_staging_checks_with_real_clients",
        fake_runner,
    )

    exit_code = file_processing_staging_cli.main([str(manifest_path), "--output", str(output_path)])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["passed"] is True
    assert payload["promotion_ready"] is False
    assert {
        "code": "required_runtime_check_not_ok",
        "check": "extraction_artifact_cache_roundtrip",
        "status": "skipped",
    } in payload["promotion_blockers"]


def test_file_processing_staging_cli_fails_preflight_when_selected_adapter_missing(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """明示選択された parser adapter が未導入なら実 staging 前に失敗する。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    output_path = tmp_path / "file-processing-staging-preflight.json"
    monkeypatch.setattr(
        file_processing_staging_cli,
        "get_settings",
        lambda: _complete_oci_settings(
            rag_parser_adapter_backend="docling",
            rag_parser_docling_enabled=True,
        ),
    )
    monkeypatch.setattr(parser_adapter_readiness, "_package_info", lambda _package: (False, None))

    async def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real staging runner must not be called")

    monkeypatch.setattr(
        file_processing_staging_cli,
        "run_file_processing_staging_checks_with_real_clients",
        fail_if_called,
    )

    exit_code = file_processing_staging_cli.main([str(manifest_path), "--output", str(output_path)])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["preflight"]["ok"] is True
    assert payload["failure_count"] == 1
    assert payload["parser_adapters"]["adapter_backend"] == "docling"
    assert payload["parser_adapter_preflight"] == {
        "ok": False,
        "message": "selected parser adapter is not ready",
        "failures": [
            {
                "backend": "docling",
                "status": "missing",
                "warning_code": "adapter_package_missing",
            }
        ],
    }


def test_file_processing_staging_cli_fails_preflight_when_selected_adapter_flag_disabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """adapter backend を明示しても feature flag が false なら staging 前に失敗する。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    output_path = tmp_path / "file-processing-staging-preflight.json"
    monkeypatch.setattr(
        file_processing_staging_cli,
        "get_settings",
        lambda: _complete_oci_settings(rag_parser_adapter_backend="docling"),
    )
    monkeypatch.setattr(parser_adapter_readiness, "_package_info", lambda _package: (True, "1.0.0"))

    async def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real staging runner must not be called")

    monkeypatch.setattr(
        file_processing_staging_cli,
        "run_file_processing_staging_checks_with_real_clients",
        fail_if_called,
    )

    exit_code = file_processing_staging_cli.main([str(manifest_path), "--output", str(output_path)])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["preflight"]["ok"] is True
    assert payload["failure_count"] == 1
    assert payload["parser_adapters"]["adapter_backend"] == "docling"
    assert payload["parser_adapters"]["effective_order"] == []
    assert payload["parser_adapter_preflight"] == {
        "ok": False,
        "message": "selected parser adapter feature flag is disabled",
        "failures": [
            {
                "backend": "docling",
                "status": "disabled",
                "warning_code": "adapter_feature_flag_disabled",
            }
        ],
    }


def test_file_processing_staging_cli_allows_available_selected_adapter(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """明示選択 adapter が導入済みなら parser adapter preflight は通す。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    output_path = tmp_path / "file-processing-staging-preflight.json"
    monkeypatch.setattr(
        file_processing_staging_cli,
        "get_settings",
        lambda: _complete_oci_settings(
            rag_parser_adapter_backend="docling",
            rag_parser_docling_enabled=True,
        ),
    )
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda _package: (True, "1.0.0"),
    )

    exit_code = file_processing_staging_cli.main(
        [str(manifest_path), "--preflight-only", "--output", str(output_path)]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["parser_adapters"]["adapters"][0]["status"] == "active"
    assert payload["parser_adapter_preflight"] == {
        "ok": True,
        "message": "parser adapter preflight ok",
        "failures": [],
    }


def _complete_oci_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "oci_region": "ap-osaka-1",
        "oci_compartment_id": "ocid1.compartment.oc1..example",
        "oci_enterprise_ai_endpoint": "https://enterprise-ai.example.com",
        "oci_enterprise_ai_project_ocid": "ocid1.generativeaiproject.oc1..example",
        "oci_enterprise_ai_api_key": "sk-test-secret",
        "oci_enterprise_ai_llm_model": "llm-deployment",
        "oci_enterprise_ai_vlm_model": "vlm-deployment",
        "oracle_user": "rag_user",
        "oracle_password": "oracle-password",
        "oracle_dsn": "adb.example.com/rag",
        "object_storage_region": "ap-osaka-1",
        "object_storage_namespace": "namespace",
        "object_storage_bucket": "bucket",
        "upload_storage_backend": "oci",
    }
    values.update(overrides)
    return Settings(**values)


class FakeObjectStorage:
    """Object Storage fake。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.gets: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        assert content_type
        uri = f"oci://namespace/bucket/{key}"
        self.objects[uri] = data
        return uri

    async def get(self, key: str) -> bytes:
        self.gets.append(key)
        return self.objects[key]

    async def delete(self, key: str) -> bool:
        self.deleted.append(key)
        return self.objects.pop(key, None) is not None


class FakeMismatchedArtifactStorage(FakeObjectStorage):
    """artifact cache probe の readback だけを壊す fake。"""

    async def get(self, key: str) -> bytes:
        self.gets.append(key)
        if "/staging-preflight/" in key:
            return b'{"probe":"mismatch"}\n'
        return self.objects[key]


class FakeStagingOracle:
    """Oracle fake。"""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentDetail] = {}
        self.chunks: dict[str, list[DocumentChunkView]] = {}
        self.segments: dict[str, list[IngestionSegment]] = {}
        self.knowledge_base_documents: dict[str, set[str]] = {}
        self.counter = 0

    async def create_knowledge_base(
        self,
        *,
        name: str,
        description: str | None = None,
        default_search_mode: SearchMode = SearchMode.HYBRID,
        retrieval_config: Mapping[str, object] | None = None,
    ) -> KnowledgeBaseDetail:
        del retrieval_config
        now = datetime.now(UTC)
        return KnowledgeBaseDetail(
            id="kb-1",
            name=name,
            description=description,
            status=KnowledgeBaseStatus.ACTIVE,
            default_search_mode=default_search_mode,
            created_at=now,
            updated_at=now,
        )

    async def archive_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDetail:
        now = datetime.now(UTC)
        return KnowledgeBaseDetail(
            id=knowledge_base_id,
            name="archived",
            status=KnowledgeBaseStatus.ARCHIVED,
            created_at=now,
            updated_at=now,
            archived_at=now,
        )

    async def assign_documents_to_knowledge_base(
        self,
        knowledge_base_id: str,
        document_ids: Sequence[str],
    ) -> KnowledgeBaseDetail:
        self.knowledge_base_documents.setdefault(knowledge_base_id, set()).update(document_ids)
        return await self.archive_knowledge_base(knowledge_base_id)

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
        self.counter += 1
        document = DocumentDetail(
            id=f"doc-{self.counter}",
            file_name=file_name,
            status=FileStatus.UPLOADED,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            content_sha256=content_sha256,
            duplicate_of_document_id=duplicate_of_document_id,
            uploaded_at=datetime.now(UTC),
            object_storage_path=object_storage_path,
        )
        self.documents[document.id] = document
        for knowledge_base_id in knowledge_base_ids or []:
            self.knowledge_base_documents.setdefault(knowledge_base_id, set()).add(document.id)
        return document

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        return self.documents.get(document_id)

    async def delete_document(self, document_id: str) -> bool:
        self.documents.pop(document_id, None)
        self.chunks.pop(document_id, None)
        self.segments.pop(document_id, None)
        return True

    async def count_document_chunks(self, document_id: str) -> int:
        return len(self.chunks.get(document_id, []))

    async def list_document_chunks(self, document_id: str) -> list[DocumentChunkView]:
        return list(self.chunks.get(document_id, []))

    async def list_ingestion_segments(self, document_id: str) -> list[IngestionSegment]:
        return list(self.segments.get(document_id, []))

    def set_indexed(
        self,
        document_id: str,
        *,
        chunks: list[DocumentChunkView],
        segments: list[IngestionSegment] | None = None,
        pages: list[dict[str, object]] | None = None,
        elements: list[dict[str, object]] | None = None,
    ) -> None:
        document = self.documents[document_id]
        self.documents[document_id] = document.model_copy(
            update={
                "status": FileStatus.INDEXED,
                "extraction": {
                    "pages": pages or [],
                    "elements": elements or [],
                    "parser_artifacts": {
                        "staging_fake": True,
                        "extraction_artifact_path": (
                            "oci://namespace/bucket/artifacts/extractions/"
                            f"{document_id}/full.json"
                        ),
                    },
                    "quality_report": {
                        "parser_profile": "enterprise_ai_pdf_layout",
                        "parser_backend": "enterprise_ai",
                        "fallback_used": False,
                        "risk_level": "low",
                        "page_count": 1,
                        "page_coverage": 1.0,
                        "table_count": 0,
                        "figure_count": 0,
                        "formula_count": 0,
                        "element_count": len(elements or []),
                        "low_confidence_count": 0,
                        "failed_segment_count": 0,
                        "long_document": False,
                        "quality_warnings": [],
                    },
                },
            }
        )
        self.chunks[document_id] = chunks
        self.segments[document_id] = segments or []

    def set_error_segments(self, document_id: str) -> None:
        document = self.documents[document_id]
        self.documents[document_id] = document.model_copy(update={"status": FileStatus.ERROR})
        self.segments[document_id] = [
            IngestionSegment(
                segment_id=f"{document_id}:sheet1",
                document_id=document_id,
                status="SUCCEEDED",
                parser_backend="local_partition",
                parser_profile="local_office_structure",
                page_start=1,
                page_end=1,
                attempt_count=1,
                artifact_path=f"oci://namespace/bucket/artifacts/{document_id}/sheet1.json",
            ),
            IngestionSegment(
                segment_id=f"{document_id}:sheet2",
                document_id=document_id,
                status="FAILED",
                parser_backend="local_partition",
                parser_profile="local_office_structure",
                page_start=2,
                page_end=2,
                attempt_count=2,
                error_code="office_segment_parse_failed",
            ),
        ]


class FakeIngestion:
    """Ingestion fake。"""

    def __init__(
        self,
        oracle: FakeStagingOracle,
        *,
        image_has_bbox: bool = True,
        image_has_element_ids: bool = True,
        image_bbox: list[float] | None = None,
        image_bbox_coordinate_mode: str | None = "xyxy",
        image_bbox_unit: str | None = None,
        image_page_size: Mapping[str, object] | None = None,
        image_chunk_element_ids: list[str] | None = None,
        image_extraction_element_ids: list[str] | None = None,
    ) -> None:
        self.oracle = oracle
        self.image_has_bbox = image_has_bbox
        self.image_has_element_ids = image_has_element_ids
        self.image_bbox = image_bbox or [0.1, 0.1, 0.3, 0.2]
        self.image_bbox_coordinate_mode = image_bbox_coordinate_mode
        self.image_bbox_unit = image_bbox_unit
        self.image_page_size = image_page_size
        self.image_chunk_element_ids = image_chunk_element_ids
        self.image_extraction_element_ids = image_extraction_element_ids

    async def ingest(
        self,
        document_id: str,
        image_bytes: bytes,
        prompt: str,
        *,
        content_type: str = "application/octet-stream",
        source_profile: SourceProfile | None = None,
    ) -> DocumentDetail:
        del image_bytes, prompt, source_profile
        document = self.oracle.documents[document_id]
        if document.file_name == "broken.xlsx":
            self.oracle.set_error_segments(document_id)
            raise RuntimeError("safe fake parse failure")
        chunks = [
            _chunk(
                document_id,
                content_kind=_content_kind(document.file_name),
                page_start=_page_start(document.file_name),
                bbox=self._bbox(document.file_name),
                metadata=self._chunk_metadata(document.file_name),
                element_ids=self._chunk_element_ids(document.file_name),
            )
        ]
        self.oracle.set_indexed(
            document_id,
            chunks=chunks,
            segments=[
                IngestionSegment(
                    segment_id=f"{document_id}:source",
                    document_id=document_id,
                    status="SUCCEEDED",
                    artifact_path=(
                        "oci://namespace/bucket/artifacts/extractions/"
                        f"{document_id}/segments/source.json"
                    ),
                )
            ],
            pages=self._pages(document.file_name),
            elements=self._elements(document.file_name),
        )
        return self.oracle.documents[document_id]

    def _has_bbox(self, file_name: str) -> bool:
        if file_name == "receipt-ja.png":
            return self.image_has_bbox
        return file_name.endswith(".pdf")

    def _bbox(self, file_name: str) -> list[float] | None:
        if not self._has_bbox(file_name):
            return None
        if file_name == "receipt-ja.png":
            return self.image_bbox
        return [0.1, 0.1, 0.3, 0.2]

    def _chunk_metadata(self, file_name: str) -> dict[str, str]:
        metadata: dict[str, str] = {}
        if not self._has_bbox(file_name):
            return metadata
        if file_name == "receipt-ja.png":
            if self.image_bbox_coordinate_mode is not None:
                metadata["bbox_coordinate_mode"] = self.image_bbox_coordinate_mode
            metadata["bbox_unit"] = self.image_bbox_unit or _bbox_unit_for_fake(self.image_bbox)
            return metadata
        metadata["bbox_coordinate_mode"] = "xyxy"
        metadata["bbox_unit"] = "ratio"
        return metadata

    def _pages(self, file_name: str) -> list[dict[str, object]]:
        if file_name == "receipt-ja.png" and self.image_page_size is not None:
            return [
                {
                    "page_number": 1,
                    "element_ids": self._extraction_element_ids(file_name),
                    **dict(self.image_page_size),
                }
            ]
        return []

    def _has_element_ids(self, file_name: str) -> bool:
        if file_name == "receipt-ja.png":
            return self.image_has_element_ids
        return True

    def _chunk_element_ids(self, file_name: str) -> list[str]:
        if not self._has_element_ids(file_name):
            return []
        if file_name == "receipt-ja.png" and self.image_chunk_element_ids is not None:
            return self.image_chunk_element_ids
        return ["el-1"]

    def _elements(self, file_name: str) -> list[dict[str, object]]:
        page_number = _page_start(file_name)
        return [
            {
                "element_id": element_id,
                "kind": "text",
                "text": "redacted",
                "order": index,
                "page_number": page_number,
            }
            for index, element_id in enumerate(self._extraction_element_ids(file_name))
        ]

    def _extraction_element_ids(self, file_name: str) -> list[str]:
        if file_name == "receipt-ja.png" and self.image_extraction_element_ids is not None:
            return self.image_extraction_element_ids
        return ["el-1"]


class FakeSearch:
    """Search fake。"""

    def __init__(self, oracle: FakeStagingOracle) -> None:
        self.oracle = oracle

    async def run(self, request: SearchRequest) -> SearchResponse:
        knowledge_base_id = request.knowledge_base_ids[0]
        document_ids = self.oracle.knowledge_base_documents.get(knowledge_base_id, set())
        document_filter = request.filters.get("document_id")
        if document_filter:
            document_ids = {document_filter} & document_ids
        ordered_document_ids = sorted(document_ids)
        if "POLICY" in request.query.upper():
            ordered_document_ids = sorted(
                document_ids,
                key=lambda document_id: (
                    not self.oracle.documents[document_id].file_name.startswith("policy-ja.pdf"),
                    document_id,
                ),
            )
        elif "PAGE 2" in request.query.upper():
            ordered_document_ids = sorted(
                document_ids,
                key=lambda document_id: (
                    "two-column-report-ja.pdf" not in self.oracle.documents[document_id].file_name,
                    document_id,
                ),
            )
        elif "TOTAL" in request.query.upper():
            ordered_document_ids = sorted(
                document_ids,
                key=lambda document_id: (
                    "receipt-ja.png" not in self.oracle.documents[document_id].file_name,
                    document_id,
                ),
            )
        citations = [
            _retrieved_chunk_from_view(self.oracle.chunks[document_id][0])
            for document_id in ordered_document_ids
            if document_id in self.oracle.chunks
        ][:1]
        return SearchResponse(
            answer="redacted",
            citations=citations,
            trace_id="trace-1",
            elapsed_ms=1.0,
            diagnostics=SearchDiagnostics(citation_count=len(citations)),
        )


def _chunk(
    document_id: str,
    *,
    content_kind: str,
    page_start: int,
    bbox: list[float] | None,
    metadata: Mapping[str, str] | None = None,
    element_ids: list[str] | None = None,
) -> DocumentChunkView:
    return DocumentChunkView(
        document_id=document_id,
        chunk_id=f"{document_id}:0",
        chunk_index=0,
        text="redacted",
        page_start=page_start,
        page_end=page_start,
        bbox=bbox,
        section_path="section",
        content_kind=content_kind,
        chunk_group_id=f"{document_id}:group",
        source_parser="staging_fake",
        element_ids=element_ids or [],
        metadata=dict(metadata or {}),
    )


def _bbox_unit_for_fake(bbox: Sequence[float] | None) -> str:
    if not bbox:
        return "ratio"
    max_value = max(abs(float(value)) for value in bbox[:4])
    if max_value <= 1:
        return "ratio"
    if max_value <= 100:
        return "percent"
    return "absolute"


def _retrieved_chunk_from_view(chunk: DocumentChunkView) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        text="redacted",
        score=1.0,
        metadata={
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "bbox": json.dumps(chunk.bbox) if chunk.bbox else None,
            "element_ids": ",".join(chunk.element_ids),
            **chunk.metadata,
        },
    )


def _content_kind(file_name: str) -> str:
    if file_name.endswith(".xlsx"):
        return "sheet"
    if file_name.endswith(".pptx"):
        return "slide"
    return "text"


def _page_start(file_name: str) -> int:
    if file_name == "two-column-report-ja.pdf":
        return 2
    return 1
