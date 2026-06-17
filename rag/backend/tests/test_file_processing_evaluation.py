"""ファイル処理 golden set 用メトリクスのテスト。"""

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import cast

from pytest import CaptureFixture

from app.rag import file_processing_evaluation as evaluation_module
from app.rag import file_processing_golden_cli
from app.rag.chunking import Chunk, ChunkMetadata
from app.rag.file_processing_evaluation import (
    REQUIRED_FILE_PROCESSING_METRICS,
    REQUIRED_FILE_PROCESSING_SCENARIOS,
    PageHitCase,
    TableQaResult,
    bbox_citation_coverage,
    build_file_processing_staging_plan,
    citation_traceability_coverage,
    element_lineage_coverage,
    extraction_page_coverage,
    failed_segment_rate,
    low_confidence_document_rate,
    page_hit_accuracy,
    parser_fallback_rate,
    run_file_processing_contract_checks,
    table_qa_accuracy,
    validate_file_processing_fixture_assets,
    validate_file_processing_manifest,
)
from app.schemas.extraction import StructuredExtraction
from app.schemas.search import RetrievedChunk

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_parser_fallback_rate_reads_quality_report_and_artifacts() -> None:
    """fallback_used は quality_report / parser_artifacts の双方から読める。"""
    extractions = [
        {"quality_report": {"fallback_used": True}},
        {"parser_artifacts": {"fallback_used": False}},
        {"parser_artifacts": {"fallback_used": True}},
        {},
    ]

    assert parser_fallback_rate(extractions) == 0.5


def test_quality_rates_read_quality_report_without_raw_text() -> None:
    """抽取品質 metric は quality_report の非機密 metadata から集計する。"""
    extractions: list[Mapping[str, object]] = [
        {
            "quality_report": {
                "page_coverage": 1.0,
                "low_confidence_count": 0,
                "failed_segment_count": 0,
                "quality_warnings": [],
            }
        },
        {
            "quality_report": {
                "page_coverage": 0.5,
                "low_confidence_count": 2,
                "failed_segment_count": 1,
                "quality_warnings": ["low_confidence_elements", "failed_segments"],
            }
        },
        {"quality_report": {"page_coverage": "0.75"}},
        {"parser_artifacts": {"failed_segment_count": 1}},
    ]

    assert extraction_page_coverage(extractions) == 0.75
    assert low_confidence_document_rate(extractions) == 0.25
    assert failed_segment_rate(extractions) == 0.5


def test_table_qa_accuracy_matches_normalized_expected_answer() -> None:
    """表 QA は空白や大小差を正規化して期待値の包含を評価する。"""
    results = [
        TableQaResult(
            case_id="ok",
            expected_answer="1000 円",
            actual_answer="交通費は1000円です。",
        ),
        TableQaResult(case_id="miss", expected_answer="部門長", actual_answer="承認者は経理です。"),
    ]

    assert table_qa_accuracy(results) == 0.5


def test_page_hit_accuracy_uses_citation_page_range() -> None:
    """citation metadata の page range が期待 page と重なれば hit とする。"""
    cases = [
        PageHitCase(case_id="hit", expected_document_id="doc-1", expected_pages=(2,)),
        PageHitCase(case_id="miss", expected_document_id="doc-2", expected_pages=(4,)),
        PageHitCase(case_id="api-shape", expected_document_id="doc-3", expected_pages=(5,)),
    ]
    retrieved: Mapping[str, Sequence[RetrievedChunk | Mapping[str, object]]] = {
        "hit": [
            RetrievedChunk(
                document_id="doc-1",
                chunk_id="doc-1:0",
                text="根拠",
                score=1.0,
                metadata={"page_start": 1, "page_end": 2},
            )
        ],
        "miss": [
            {"document_id": "doc-2", "metadata": {"page_start": 2, "page_end": 3}},
        ],
        "api-shape": [
            {
                "document_id": "doc-3",
                "chunk_id": "doc-3:0",
                "page_start": 5,
                "page_end": 5,
                "metadata": {},
            },
        ],
    }

    assert page_hit_accuracy(cases, retrieved) == 2 / 3


def test_citation_traceability_coverage_requires_page_and_lineage() -> None:
    """traceable citation は page と element/bbox/section のいずれかを持つ。"""
    citations: list[RetrievedChunk | Mapping[str, object]] = [
        RetrievedChunk(
            document_id="doc-1",
            chunk_id="doc-1:0",
            text="根拠",
            score=1.0,
            metadata={
                "page_start": 2,
                "page_end": 2,
                "element_ids": "el-1,el-2",
                "bbox": "[0.1, 0.2, 0.4, 0.5]",
            },
        ),
        {
            "document_id": "doc-2",
            "chunk_id": "doc-2:0",
            "page_start": 1,
            "page_end": 1,
            "section_path": "第1章/概要",
            "metadata": {},
        },
        {
            "document_id": "doc-3",
            "chunk_id": "doc-3:0",
            "metadata": {"element_ids": "el-3"},
        },
        {
            "document_id": "doc-4",
            "chunk_id": "doc-4:0",
            "metadata": {"page_start": 4},
        },
    ]

    assert citation_traceability_coverage(citations) == 0.5


def test_local_required_checks_require_resolvable_element_lineage() -> None:
    """local contract でも orphan element_ids を traceable と見なさない。"""
    extraction = StructuredExtraction.model_validate(
        {
            "raw_text": "本文",
            "elements": [
                {
                    "kind": "text",
                    "text": "本文",
                    "element_id": "el-1",
                    "page_number": 1,
                }
            ],
        }
    )
    orphan_chunk = Chunk(
        text="本文",
        index=0,
        start_offset=0,
        end_offset=2,
        metadata={
            "element_ids": "missing-el",
            "page_start": 1,
            "chunk_group_id": "g1",
        },
    )
    page_lineage_extraction = StructuredExtraction.model_validate(
        {
            "raw_text": "本文",
            "elements": [{"kind": "text", "text": "本文", "page_number": 1}],
            "pages": [{"page_number": 1, "element_ids": ["page-el"]}],
        }
    )
    page_chunk = Chunk(
        text="本文",
        index=1,
        start_offset=0,
        end_offset=2,
        metadata={
            "element_ids": "page-el",
            "page_start": 1,
            "chunk_group_id": "g2",
        },
    )
    json_lineage_chunk = Chunk(
        text="本文",
        index=2,
        start_offset=0,
        end_offset=2,
        metadata={
            "element_ids": '["page-el"]',
            "page_start": 1,
            "chunk_group_id": "g3",
        },
    )

    assert evaluation_module._check_element_lineage(extraction, [orphan_chunk]) == (
        "failure",
        "element_ids_unresolved",
    )
    assert evaluation_module._check_chunk_traceability(extraction, [orphan_chunk]) == (
        "failure",
        "lineage_metadata_missing",
    )
    assert evaluation_module._check_element_lineage(
        page_lineage_extraction,
        [page_chunk],
    ) == ("passed", "ok")
    assert evaluation_module._check_chunk_traceability(
        page_lineage_extraction,
        [page_chunk],
    ) == ("passed", "ok")
    assert evaluation_module._check_element_lineage(
        page_lineage_extraction,
        [json_lineage_chunk],
    ) == ("passed", "ok")


def test_code_and_equation_contract_requires_block_metadata() -> None:
    """code/formula block は content_kind だけでなく block 固有 metadata も要求する。"""
    extraction = StructuredExtraction.model_validate(
        {
            "elements": [
                {
                    "kind": "code",
                    "text": "select 1 from dual;",
                    "content_kind": "code",
                    "metadata": {"code_language": "sql"},
                },
                {
                    "kind": "equation",
                    "text": "E = mc^2",
                    "content_kind": "equation",
                    "metadata": {"equation_delimiter": "$$"},
                },
            ]
        }
    )
    code_without_language = Chunk(
        text="select 1 from dual;",
        index=0,
        start_offset=0,
        end_offset=19,
        metadata={"content_kind": "code"},
    )
    code_with_language = Chunk(
        text="select 1 from dual;",
        index=0,
        start_offset=0,
        end_offset=19,
        metadata={"content_kind": "code", "code_language": "sql"},
    )
    equation_without_delimiter = Chunk(
        text="E = mc^2",
        index=1,
        start_offset=20,
        end_offset=28,
        metadata={"content_kind": "equation"},
    )
    equation_with_delimiter = Chunk(
        text="E = mc^2",
        index=1,
        start_offset=20,
        end_offset=28,
        metadata={"content_kind": "equation", "equation_delimiter": "$$"},
    )

    assert evaluation_module._check_content_kind_present(
        extraction,
        [code_without_language],
        expected_kind="code",
        required_metadata_key="code_language",
    ) == ("failure", "code_code_language_missing")
    assert evaluation_module._check_content_kind_present(
        extraction,
        [code_with_language],
        expected_kind="code",
        required_metadata_key="code_language",
    ) == ("passed", "ok")
    assert evaluation_module._check_content_kind_present(
        extraction,
        [equation_without_delimiter],
        expected_kind="equation",
        required_metadata_key="equation_delimiter",
    ) == ("failure", "equation_equation_delimiter_missing")
    assert evaluation_module._check_content_kind_present(
        extraction,
        [equation_with_delimiter],
        expected_kind="equation",
        required_metadata_key="equation_delimiter",
    ) == ("passed", "ok")


def test_table_preserve_rows_requires_header_repeat_for_split_chunks() -> None:
    """長表 row-group chunk は各 part に表頭と行範囲 metadata を要求する。"""
    table_shape: ChunkMetadata = {
        "table_id": "tbl-expenses",
        "table_row_count": 3,
        "table_column_count": 2,
    }
    chunks = [
        Chunk(
            text="|項目|金額|\n|---|---|\n|交通費|1000円|",
            index=0,
            start_offset=0,
            end_offset=30,
            metadata={
                "content_kind": "table",
                "chunk_template": "table_preserve_rows",
                **table_shape,
                "chunk_group_id": "grp-table",
                "chunk_part_index": 1,
                "chunk_part_count": 2,
                "table_data_row_start": 1,
                "table_data_row_end": 1,
                "table_header_repeated": False,
            },
        ),
        Chunk(
            text="|項目|金額|\n|---|---|\n|宿泊費|2000円|",
            index=1,
            start_offset=31,
            end_offset=61,
            metadata={
                "content_kind": "table",
                "chunk_template": "table_preserve_rows",
                **table_shape,
                "chunk_group_id": "grp-table",
                "chunk_part_index": 2,
                "chunk_part_count": 2,
                "table_data_row_start": 2,
                "table_data_row_end": 2,
                "table_header_repeated": True,
            },
        ),
    ]

    assert evaluation_module._check_table_preserve_rows(chunks) == ("passed", "ok")


def test_table_preserve_rows_requires_table_lineage_and_shape_metadata() -> None:
    """表 chunk は citation で使う table id と表サイズ metadata を持つ。"""
    missing_table_id = [
        Chunk(
            text="|項目|金額|\n|---|---|\n|交通費|1000円|",
            index=0,
            start_offset=0,
            end_offset=30,
            metadata={
                "content_kind": "table",
                "chunk_template": "table_preserve_rows",
                "table_row_count": 2,
                "table_column_count": 2,
            },
        )
    ]
    missing_shape = [
        Chunk(
            text="|項目|金額|\n|---|---|\n|交通費|1000円|",
            index=0,
            start_offset=0,
            end_offset=30,
            metadata={
                "content_kind": "table",
                "chunk_template": "table_preserve_rows",
                "table_id": "tbl-expenses",
                "table_row_count": 2,
            },
        )
    ]

    assert evaluation_module._check_table_preserve_rows(missing_table_id) == (
        "failure",
        "table_lineage_metadata_missing",
    )
    assert evaluation_module._check_table_preserve_rows(missing_shape) == (
        "failure",
        "table_shape_metadata_missing",
    )


def test_table_preserve_rows_rejects_split_chunk_without_header_context() -> None:
    """表 chunk が存在するだけでは長表 row-group の contract を満たさない。"""
    table_shape: ChunkMetadata = {
        "table_id": "tbl-expenses",
        "table_row_count": 3,
        "table_column_count": 2,
    }
    first_chunk = Chunk(
        text="|項目|金額|\n|---|---|\n|交通費|1000円|",
        index=0,
        start_offset=0,
        end_offset=30,
        metadata={
            "content_kind": "table",
            "chunk_template": "table_preserve_rows",
            **table_shape,
            "chunk_part_index": 1,
            "chunk_part_count": 2,
            "table_data_row_start": 1,
            "table_data_row_end": 1,
            "table_header_repeated": False,
        },
    )
    missing_header = [
        first_chunk,
        Chunk(
            text="|宿泊費|2000円|",
            index=1,
            start_offset=31,
            end_offset=45,
            metadata={
                "content_kind": "table",
                "chunk_template": "table_preserve_rows",
                **table_shape,
                "chunk_part_index": 2,
                "chunk_part_count": 2,
                "table_data_row_start": 2,
                "table_data_row_end": 2,
                "table_header_repeated": True,
            },
        ),
    ]
    missing_row_range = [
        first_chunk,
        Chunk(
            text="|項目|金額|\n|---|---|\n|宿泊費|2000円|",
            index=1,
            start_offset=31,
            end_offset=61,
            metadata={
                "content_kind": "table",
                "chunk_template": "table_preserve_rows",
                **table_shape,
                "chunk_part_index": 2,
                "chunk_part_count": 2,
                "table_header_repeated": True,
            },
        ),
    ]

    assert evaluation_module._check_table_preserve_rows(missing_header) == (
        "failure",
        "table_header_not_repeated",
    )
    assert evaluation_module._check_table_preserve_rows(missing_row_range) == (
        "failure",
        "table_row_group_metadata_missing",
    )


def test_bbox_and_element_lineage_coverage_accept_api_and_metadata_shapes() -> None:
    """bbox / element lineage は RetrievedChunk と API chunk view の両方から読む。"""
    citations: list[RetrievedChunk | Mapping[str, object]] = [
        RetrievedChunk(
            document_id="doc-1",
            chunk_id="doc-1:0",
            text="根拠",
            score=1.0,
            metadata={"bbox": "[0, 0, 50, 10]", "element_ids": '["el-1"]'},
        ),
        {
            "document_id": "doc-2",
            "chunk_id": "doc-2:0",
            "bbox": [0.1, 0.1, 0.3, 0.2],
            "element_ids": ["el-2", "el-3"],
            "metadata": {},
        },
        {
            "document_id": "doc-3",
            "chunk_id": "doc-3:0",
            "bbox": [0, 0, 0, 0],
            "element_ids": [],
            "metadata": {},
        },
        {
            "document_id": "doc-4",
            "chunk_id": "doc-4:0",
            "metadata": {"bbox": "not-json", "element_ids": ""},
        },
    ]

    assert bbox_citation_coverage(citations) == 0.5
    assert element_lineage_coverage(citations) == 0.5


def test_file_processing_golden_manifest_tracks_traceability_metrics() -> None:
    """file-processing golden set は機械検証できる契約を明示する。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert validate_file_processing_manifest(manifest) == ()
    assert validate_file_processing_fixture_assets(manifest, manifest_path=manifest_path) == ()
    assert set(manifest["metrics"]) >= REQUIRED_FILE_PROCESSING_METRICS
    assert set(manifest["thresholds"]) >= REQUIRED_FILE_PROCESSING_METRICS
    assert manifest["thresholds"]["table_qa_accuracy"] == {"min": 1.0}
    assert manifest["thresholds"]["parser_fallback_rate"] == {"max": 0.2}
    assert manifest["staging_policy"]["required_runtime_checks"] == [
        "extraction_artifact_cache_roundtrip"
    ]
    assert {case["scenario"] for case in manifest["cases"]} >= REQUIRED_FILE_PROCESSING_SCENARIOS
    fixture_root = (manifest_path.parent / manifest["fixture_root"]).resolve()
    duplicate_case = next(
        case for case in manifest["cases"] if case["id"] == "duplicate-file-canonical-kb"
    )
    assert (
        sha256((fixture_root / duplicate_case["fixture"]).read_bytes()).digest()
        == sha256((fixture_root / duplicate_case["duplicate_fixture"]).read_bytes()).digest()
    )
    assert any(case["id"] == "long-table-row-groups" for case in manifest["cases"])
    assert any(case["id"] == "long-table-tsv-row-groups" for case in manifest["cases"])
    assert any(case["id"] == "two-column-pdf-reading-order" for case in manifest["cases"])
    assert any(case["id"] == "image-ocr-bbox" for case in manifest["cases"])
    assert any(case["id"] == "tiff-image-unsupported" for case in manifest["cases"])
    assert any(case["id"] == "audio-unsupported" for case in manifest["cases"])
    assert any(case["id"] == "markdown-code-formula-blocks" for case in manifest["cases"])
    assert any(case["id"] == "corrupted-file-partial-failure" for case in manifest["cases"])
    assert any(case["id"] == "legacy-office-unsupported" for case in manifest["cases"])


def test_file_processing_golden_manifest_requires_markdown_code_formula_case() -> None:
    """Markdown の code/formula block 保持は必須 golden scenario として扱う。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cases"] = [
        case
        for case in manifest["cases"]
        if case.get("scenario") != "markdown_code_formula_blocks"
    ]

    errors = validate_file_processing_manifest(manifest)

    assert "missing_scenarios:markdown_code_formula_blocks" in errors


def test_file_processing_manifest_requires_scenario_specific_checks() -> None:
    """scenario 名だけでなく、各 scenario の重要 check も必須にする。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    markdown_case = next(
        case
        for case in manifest["cases"]
        if case["scenario"] == "markdown_code_formula_blocks"
    )
    markdown_case["required_checks"] = ["heading_structure", "element_lineage"]

    errors = validate_file_processing_manifest(manifest)

    assert (
        "case[markdown-code-formula-blocks]:missing_required_checks:"
        "code_block,equation_block"
    ) in errors


def test_file_processing_contract_runner_executes_local_parser_checks() -> None:
    """同梱 fixture は local parser/chunker contract と staging pending を分離する。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    report = run_file_processing_contract_checks(manifest, manifest_path=manifest_path)

    assert report.passed is True
    assert report.case_count == 16
    assert report.failure_count == 0
    assert report.pending_staging_check_count > 0

    by_id = {result.case_id: result for result in report.case_results}
    assert {
        "table_preserve_rows",
        "table_qa_accuracy",
        "element_lineage",
    } <= set(by_id["long-table-row-groups"].passed_checks)
    assert {
        "table_preserve_rows",
        "table_qa_accuracy",
        "element_lineage",
    } <= set(by_id["long-table-tsv-row-groups"].passed_checks)
    assert {
        "heading_structure",
        "section_path",
        "citation_traceability",
    } <= set(by_id["html-semantic-blocks"].passed_checks)
    assert {
        "code_block",
        "equation_block",
        "element_lineage",
    } <= set(by_id["markdown-code-formula-blocks"].passed_checks)
    assert {
        "email_headers",
        "thread_body",
        "attachment_metadata",
    } <= set(by_id["email-thread-headers"].passed_checks)
    assert "canonical_alias" in by_id["duplicate-file-canonical-kb"].passed_checks
    assert any(
        check.startswith("searchable_canonical:")
        for check in by_id["duplicate-file-canonical-kb"].pending_checks
    )
    assert {
        "failed_segment_status",
        "safe_error",
    } <= set(by_id["corrupted-file-partial-failure"].passed_checks)
    assert {
        "expected_unsupported_reason",
        "expected_warning",
        "safe_error",
        "unsupported_reason",
    } <= set(by_id["legacy-office-unsupported"].passed_checks)
    assert {
        "expected_unsupported_reason",
        "expected_warning",
        "safe_error",
        "unsupported_reason",
    } <= set(by_id["tiff-image-unsupported"].passed_checks)
    assert {
        "expected_unsupported_reason",
        "expected_warning",
        "safe_error",
        "unsupported_reason",
    } <= set(by_id["audio-unsupported"].passed_checks)
    assert any(
        check.startswith("ocr_text:") for check in by_id["scanned-pdf-ocr-ja"].pending_checks
    )
    staging_plan = build_file_processing_staging_plan(manifest, report)
    assert len(staging_plan) == report.pending_staging_check_count
    gates = {requirement.suggested_gate for requirement in staging_plan}
    assert {
        "enterprise_ai_file_extraction_gate",
        "duplicate_kb_membership_gate",
        "preview_bbox_citation_gate",
        "segment_artifact_reuse_gate",
    } <= gates


def test_file_processing_contract_runner_reports_local_regression() -> None:
    """期待 template が実装とずれたら case failure として検出する。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cases"][2]["expected_chunk_template"] = "broken_template"

    report = run_file_processing_contract_checks(manifest, manifest_path=manifest_path)

    assert report.passed is False
    assert report.failure_count == 1
    assert any(
        failure.startswith("expected_chunk_template:")
        for failure in report.case_results[2].failures
    )


def test_file_processing_golden_cli_writes_non_sensitive_report(tmp_path: Path) -> None:
    """CLI は local contract report を JSON artifact として保存できる。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    output_path = tmp_path / "file-processing-report.json"

    exit_code = file_processing_golden_cli.main([str(manifest_path), "--output", str(output_path)])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["promotion_ready"] is False
    assert payload["case_count"] == 16
    assert payload["pending_staging_check_count"] > 0
    assert payload["staging_policy"] == {
        "required_for_promotion": True,
        "pending_checks_block_promotion": True,
        "required_runtime_checks": ["extraction_artifact_cache_roundtrip"],
    }
    assert set(payload["metric_summary"]) >= set(manifest_metrics())
    assert payload["metric_summary"]["retrieval_recall"]["status"] == "requires_staging"
    assert payload["metric_summary"]["parser_fallback_rate"]["value"] == 1 / 16
    assert payload["metric_summary"]["extraction_page_coverage"]["status"] == "requires_staging"
    assert payload["metric_summary"]["extraction_page_coverage"]["value"] is None
    assert payload["metric_summary"]["low_confidence_document_rate"]["value"] == 0.0
    assert payload["metric_summary"]["failed_segment_rate"]["value"] == 1 / 16
    assert payload["metric_summary"]["table_qa_accuracy"]["status"] == "measured"
    assert payload["metric_summary"]["table_qa_accuracy"]["value"] == 1.0
    assert payload["metric_summary"]["page_hit_accuracy"]["status"] == "requires_staging"
    assert payload["metric_summary"]["page_hit_accuracy"]["value"] is None
    assert payload["metric_summary"]["citation_traceability_coverage"]["status"] == "partial"
    assert payload["metric_summary"]["preview_addressability_coverage"]["status"] == (
        "requires_staging"
    )
    assert payload["metric_summary"]["element_lineage_coverage"]["value"] == 1.0
    assert payload["metric_summary"]["groundedness"]["status"] == "requires_staging"
    assert payload["metric_summary"]["ingestion_p95_ms"]["status"] == "requires_staging"
    threshold_by_metric = {result["metric"]: result for result in payload["threshold_results"]}
    assert threshold_by_metric["table_qa_accuracy"]["status"] == "passed"
    assert threshold_by_metric["parser_fallback_rate"]["status"] == "passed"
    assert threshold_by_metric["extraction_page_coverage"]["status"] == "pending"
    assert threshold_by_metric["low_confidence_document_rate"]["status"] == "passed"
    assert threshold_by_metric["failed_segment_rate"]["status"] == "passed"
    assert threshold_by_metric["page_hit_accuracy"]["status"] == "pending"
    blocker_by_code = {blocker["code"]: blocker for blocker in payload["promotion_blockers"]}
    assert (
        blocker_by_code["pending_staging_checks"]["count"]
        == payload["pending_staging_check_count"]
    )
    assert any(blocker["code"] == "threshold_pending" for blocker in payload["promotion_blockers"])
    assert len(payload["staging_requirements"]) == payload["pending_staging_check_count"]
    assert {requirement["suggested_gate"] for requirement in payload["staging_requirements"]} >= {
        "enterprise_ai_file_extraction_gate",
        "duplicate_kb_membership_gate",
    }
    assert "raw_text" not in output_path.read_text(encoding="utf-8")


def test_file_processing_golden_cli_can_fail_on_pending(tmp_path: Path) -> None:
    """staging gate では pending を明示的に失敗扱いへ切り替えられる。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    output_path = tmp_path / "file-processing-report.json"

    exit_code = file_processing_golden_cli.main(
        [str(manifest_path), "--output", str(output_path), "--fail-on-pending"]
    )

    assert exit_code == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["promotion_ready"] is False


def test_file_processing_golden_cli_honors_non_blocking_staging_policy(
    tmp_path: Path,
) -> None:
    """manifest が明示的に許可した場合だけ staging pending は promotion を止めない。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixture_root"] = str((manifest_path.parent / manifest["fixture_root"]).resolve())
    manifest["staging_policy"] = {
        "required_for_promotion": False,
        "pending_checks_block_promotion": False,
        "required_runtime_checks": [],
    }
    custom_manifest_path = tmp_path / "manifest.json"
    custom_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output_path = tmp_path / "file-processing-report.json"

    exit_code = file_processing_golden_cli.main(
        [str(custom_manifest_path), "--output", str(output_path)]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["promotion_ready"] is True
    assert payload["pending_staging_check_count"] > 0
    assert payload["staging_policy"] == manifest["staging_policy"]
    assert not any(
        blocker["code"] in {"pending_staging_checks", "threshold_pending"}
        for blocker in payload["promotion_blockers"]
    )
    assert any(result["status"] == "pending" for result in payload["threshold_results"])


def test_file_processing_golden_cli_emits_safe_github_annotation(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """CI annotation は promotion status だけを非機密に出す。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    output_path = tmp_path / "file-processing-report.json"

    exit_code = file_processing_golden_cli.main(
        [str(manifest_path), "--output", str(output_path), "--github-annotations"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "::warning::file-processing golden promotion_not_ready" in captured.out
    assert "promotion_ready=false" in captured.out
    assert "pending_staging_check_count=19" in captured.out
    assert "raw_text" not in captured.out


def test_file_processing_golden_cli_fails_when_local_threshold_regresses(
    tmp_path: Path,
) -> None:
    """local measured metric が manifest threshold を下回ったら gate を失敗させる。"""
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixture_root"] = str((manifest_path.parent / manifest["fixture_root"]).resolve())
    manifest["thresholds"]["parser_fallback_rate"] = {"max": 0.0}
    custom_manifest_path = tmp_path / "manifest.json"
    custom_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output_path = tmp_path / "file-processing-report.json"

    exit_code = file_processing_golden_cli.main(
        [str(custom_manifest_path), "--output", str(output_path)]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    threshold_by_metric = {result["metric"]: result for result in payload["threshold_results"]}
    assert exit_code == 1
    assert payload["promotion_ready"] is False
    assert any(
        blocker["code"] == "threshold_failed" and blocker["metric"] == "parser_fallback_rate"
        for blocker in payload["promotion_blockers"]
    )
    assert threshold_by_metric["parser_fallback_rate"]["status"] == "failed"
    assert threshold_by_metric["parser_fallback_rate"]["actual"] == 1 / 16


def test_nightly_workflow_runs_file_processing_gate_before_api_skip() -> None:
    """nightly workflow は API base URL がなくても file-processing artifact を作る。"""
    workflow = (REPO_ROOT / ".github/workflows/rag-evaluation-nightly.yml").read_text(
        encoding="utf-8"
    )

    assert "file_processing_manifest_path" in workflow
    assert "run_file_processing_staging" in workflow
    assert "app.rag.file_processing_golden_cli" in workflow
    assert "app.rag.file_processing_staging_cli" in workflow
    assert "file-processing-report.json" in workflow
    assert "file-processing-staging-report.json" in workflow
    assert "--github-annotations" in workflow
    assert workflow.index("app.rag.file_processing_golden_cli") < workflow.index(
        'if [ -z "$api_base_url" ]; then'
    )


def test_file_processing_golden_manifest_reports_missing_contract_fields() -> None:
    """manifest の重要契約が欠けたら stable error code を返す。"""
    invalid_manifest: Mapping[str, object] = {
        "metrics": ["table_qa_accuracy"],
        "cases": [
            {
                "id": "broken-case",
                "fixture": "broken.pdf",
                "modality": "pdf",
                "scenario": "scanned_pdf_ocr",
                "expected_parser_profile": "enterprise_ai_pdf_layout",
            }
        ],
    }

    errors = validate_file_processing_manifest(invalid_manifest)

    assert any(error.startswith("missing_metrics:") for error in errors)
    assert any(error.startswith("missing_scenarios:") for error in errors)
    assert "thresholds:missing" in errors
    assert "case[broken-case]:missing_fields:expected_chunk_template,required_checks" in errors
    assert "case[broken-case]:required_checks_empty" in errors
    assert "case[broken-case]:assertion_missing" in errors


def test_file_processing_fixture_asset_validator_reports_missing_assets(
    tmp_path: Path,
) -> None:
    """fixture 参照が存在しない場合は manifest case 単位で検出する。"""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    manifest: Mapping[str, object] = {
        "fixture_root": "fixtures",
        "cases": [
            {
                "id": "missing-pdf",
                "fixture": "missing.pdf",
                "modality": "pdf",
            },
            {
                "id": "bad-extension",
                "fixture": "manual.txt",
                "modality": "html",
            },
            {
                "id": "unsafe",
                "fixture": "../secret.pdf",
                "modality": "pdf",
            },
        ],
    }

    errors = validate_file_processing_fixture_assets(manifest, manifest_path=manifest_path)

    assert any(error.startswith("fixture_root:not_found:") for error in errors)
    assert "case[missing-pdf]:fixture_not_found:missing.pdf" in errors
    assert "case[bad-extension]:fixture_extension_mismatch:html:.txt" in errors
    assert "case[unsafe]:fixture_unsafe_path:../secret.pdf" in errors


def test_file_processing_fixture_asset_validator_accepts_tsv_assets(
    tmp_path: Path,
) -> None:
    """golden set は TSV の表 fixture も table QA 対象として表現できる。"""
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    (fixture_root / "long-table.tsv").write_text("name\tamount\nalpha\t1200\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    manifest: Mapping[str, object] = {
        "fixture_root": "fixtures",
        "cases": [
            {
                "id": "long-table-tsv",
                "fixture": "long-table.tsv",
                "modality": "tsv",
            }
        ],
    }

    errors = validate_file_processing_fixture_assets(manifest, manifest_path=manifest_path)

    assert errors == ()


def manifest_metrics() -> Sequence[str]:
    manifest_path = REPO_ROOT / "docs/evaluation/file-processing-golden-set.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return cast(Sequence[str], manifest["metrics"])
