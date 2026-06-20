"""parser adapter compatibility matrix のテスト。"""

import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

from pytest import MonkeyPatch

from app.config import Settings
from app.rag import parser_adapter_contract as parser_adapter_contract_module
from app.rag import parser_adapter_contract_cli, parser_adapter_readiness
from app.rag.parser_adapter_contract import (
    ParserAdapterCompatibilityCase,
    ParserAdapterCompatibilityMatrix,
    ParserAdapterFixtureSpec,
    parser_adapter_contract_artifact_payload,
    parser_adapter_contract_summary,
    parser_adapter_fixture_root_from_manifest,
    parser_adapter_fixture_specs_from_manifest,
    run_parser_adapter_compatibility_matrix,
)
from app.rag.parsers import ParserRegistryResult
from app.schemas.extraction import DocumentElement, ExtractionPage, StructuredExtraction


def test_compatibility_matrix_does_not_block_disabled_adapters() -> None:
    """local 設定では外部 adapter が disabled でも compatibility は失敗にしない。"""
    matrix = run_parser_adapter_compatibility_matrix(
        Settings(
            rag_parser_adapter_backend="local",
            rag_parser_docling_enabled=False,
        ),
        source_kinds=["pdf"],
        backends=["docling"],
    )

    assert matrix.passed is True
    case = matrix.cases[0]
    assert case.backend == "docling"
    assert case.source_kind == "pdf"
    assert case.status in {"available", "disabled"}
    assert case.blocking is False


def test_compatibility_matrix_blocks_enabled_missing_adapter(
    monkeypatch: MonkeyPatch,
) -> None:
    """有効化された adapter package がない場合は matrix を失敗にする。"""
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda *_args: (False, None, None),
    )

    matrix = run_parser_adapter_compatibility_matrix(
        Settings(
            rag_parser_adapter_backend="docling",
            rag_parser_docling_enabled=True,
        ),
        source_kinds=["pdf"],
        backends=["docling"],
    )

    assert matrix.passed is False
    assert matrix.blocking_failure_count == 1
    case = matrix.cases[0]
    assert case.status == "missing"
    assert case.blocking is True
    assert case.warning_codes == ("adapter_package_missing",)


def test_compatibility_matrix_does_not_block_unrouted_backend_by_default(
    monkeypatch: MonkeyPatch,
) -> None:
    """coverage matrix では非対象 backend/source pair を unsupported として記録するだけにする。"""
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda import_name, _distribution_names: (
            import_name == "docling",
            "2.103.0" if import_name == "docling" else None,
            import_name if import_name == "docling" else None,
        ),
    )

    matrix = run_parser_adapter_compatibility_matrix(
        Settings(
            rag_parser_adapter_backend="docling",
            rag_parser_docling_enabled=True,
        ),
        source_kinds=["email"],
        backends=["docling"],
    )

    assert matrix.passed is True
    assert matrix.blocking_failure_count == 0
    case = matrix.cases[0]
    assert case.status == "unsupported"
    assert case.blocking is False
    assert case.reason_codes == ("adapter_not_routed_for_source",)


def test_strict_compatibility_matrix_requires_backend_schema_remap_evidence(
    monkeypatch: MonkeyPatch,
) -> None:
    """strict smoke は active adapter に少なくとも1件の passed remap 証跡を要求する。"""
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda import_name, _distribution_names: (
            import_name == "docling",
            "2.103.0" if import_name == "docling" else None,
            import_name if import_name == "docling" else None,
        ),
    )

    matrix = run_parser_adapter_compatibility_matrix(
        Settings(
            rag_parser_adapter_backend="docling",
            rag_parser_docling_enabled=True,
        ),
        source_kinds=["email"],
        backends=["docling"],
        require_backend_evidence=True,
    )

    assert matrix.passed is False
    assert matrix.blocking_failure_count == 1
    assert matrix.case_count == 2
    unsupported_case = matrix.cases[0]
    evidence_case = matrix.cases[1]
    assert unsupported_case.status == "unsupported"
    assert unsupported_case.blocking is False
    assert evidence_case.status == "failed"
    assert evidence_case.blocking is True
    assert evidence_case.source_kind == "unknown"
    assert evidence_case.fixture_name == "manifest-fixture-set"
    assert evidence_case.reason_codes == ("adapter_schema_remap_evidence_missing",)


def test_compatibility_matrix_runs_installed_adapter_remap(
    monkeypatch: MonkeyPatch,
) -> None:
    """runtime に package があれば parse_with_registry 経由で schema remap を検証する。"""
    unstructured_module = ModuleType("unstructured")
    unstructured_module.__dict__["__version__"] = "7.8.9"
    partition_package = ModuleType("unstructured.partition")
    auto_module = ModuleType("unstructured.partition.auto")

    class FakeElement:
        id = "compatibility-element-1"
        category = "NarrativeText"
        text = "非機密 artifact には入れない本文"
        metadata = SimpleNamespace(page_number=1)

    def partition(*, filename: str, content_type: str) -> list[object]:
        assert filename.endswith(".pdf")
        assert content_type == "application/pdf"
        return [FakeElement()]

    auto_module.__dict__["partition"] = partition
    monkeypatch.setitem(sys.modules, "unstructured", unstructured_module)
    monkeypatch.setitem(sys.modules, "unstructured.partition", partition_package)
    monkeypatch.setitem(sys.modules, "unstructured.partition.auto", auto_module)
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda import_name, _distribution_names: (
            import_name == "unstructured",
            "7.8.9" if import_name == "unstructured" else None,
            import_name if import_name == "unstructured" else None,
        ),
    )

    matrix = run_parser_adapter_compatibility_matrix(
        Settings(
            rag_parser_adapter_backend="unstructured",
            rag_parser_unstructured_enabled=True,
        ),
        source_kinds=["pdf"],
        backends=["unstructured"],
    )

    assert matrix.passed is True
    case = matrix.cases[0]
    assert case.status == "passed"
    assert case.parser_backend == "unstructured"
    assert case.adapter_import_name == "unstructured"
    assert case.adapter_distribution_name == "unstructured"
    assert case.adapter_package_version == "7.8.9"
    assert case.element_count == 1
    assert "schema_remap_contract_ok" in case.reason_codes
    assert "非機密 artifact" not in json.dumps(asdict(matrix), ensure_ascii=False)


def test_compatibility_matrix_uses_manifest_fixtures_for_real_remap(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """manifest case fixture ごとに package 経由の schema remap 証跡を作る。"""
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    (fixture_root / "scanned-contract-ja.pdf").write_bytes(b"%PDF-1.7 fixture")
    (fixture_root / "manual.html").write_text("<h1>検索運用</h1>", encoding="utf-8")
    manifest_path = tmp_path / "manifests" / "file-processing-golden-set.json"
    manifest_path.parent.mkdir()
    manifest = {
        "fixture_root": "../fixtures",
        "cases": [
            {
                "id": "scanned-pdf-ocr-ja",
                "fixture": "scanned-contract-ja.pdf",
                "modality": "pdf",
                "scenario": "scanned_pdf_ocr",
                "adapter_schema_remap": True,
            },
            {
                "id": "html-semantic-blocks",
                "fixture": "manual.html",
                "modality": "html",
                "scenario": "html_semantic_blocks",
                "adapter_schema_remap": True,
            },
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda import_name, _distribution_names: (
            import_name == "unstructured",
            "7.8.9" if import_name == "unstructured" else None,
            import_name if import_name == "unstructured" else None,
        ),
    )
    calls: list[tuple[str, str, str]] = []

    def parse_manifest_fixture(
        *_args: Any,
        **kwargs: Any,
    ) -> ParserRegistryResult:
        source_profile = kwargs["source_profile"]
        content_type = kwargs["content_type"]
        adapter_backend = kwargs["adapter_backend"]
        calls.append(
            (
                source_profile.sanitized_file_name,
                content_type,
                adapter_backend,
            )
        )
        is_pdf = source_profile.sanitized_file_name.endswith(".pdf")
        return ParserRegistryResult(
            extraction=StructuredExtraction(
                raw_text="artifact には raw_text を出さない",
                pages=[ExtractionPage(page_number=1, element_ids=["adapter-el-1"])],
                elements=[
                    DocumentElement(
                        kind="title" if not is_pdf else "text",
                        text="非機密 artifact には入れない本文",
                        element_id="adapter-el-1",
                        source_parser="unstructured_adapter",
                        page_number=1,
                        section_path=["検索運用"] if not is_pdf else [],
                        metadata={"link_count": 1} if not is_pdf else {},
                    )
                ],
            ),
            parser_backend="unstructured",
            parser_version="7.8.9",
            template="html_semantic" if not is_pdf else "pdf_layout",
        )

    monkeypatch.setattr(
        parser_adapter_contract_module,
        "parse_with_registry",
        parse_manifest_fixture,
    )

    specs = parser_adapter_fixture_specs_from_manifest(manifest)
    matrix = run_parser_adapter_compatibility_matrix(
        Settings(
            rag_parser_adapter_backend="unstructured",
            rag_parser_unstructured_enabled=True,
        ),
        fixture_root=parser_adapter_fixture_root_from_manifest(
            manifest,
            manifest_path=manifest_path,
        ),
        fixture_specs=specs,
        backends=["unstructured"],
    )

    assert matrix.passed is True
    assert matrix.fixture_root == str(fixture_root)
    assert matrix.case_count == 2
    assert calls == [
        ("scanned-contract-ja.pdf", "application/pdf", "unstructured"),
        ("manual.html", "text/html", "unstructured"),
    ]
    assert [case.case_id for case in matrix.cases] == [
        "scanned-pdf-ocr-ja",
        "html-semantic-blocks",
    ]
    assert [case.scenario for case in matrix.cases] == [
        "scanned_pdf_ocr",
        "html_semantic_blocks",
    ]
    assert {case.status for case in matrix.cases} == {"passed"}
    summary = parser_adapter_contract_summary(matrix)
    assert summary["backend_passed_scenarios"] == {
        "unstructured": ["html_semantic_blocks", "scanned_pdf_ocr"]
    }
    assert "schema_remap_contract_ok" in matrix.cases[0].reason_codes
    assert "非機密 artifact" not in json.dumps(asdict(matrix), ensure_ascii=False)


def test_compatibility_matrix_requires_real_package_version_evidence(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """schema remap が返っても package distribution/version 証跡がなければ失敗にする。"""
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    (fixture_root / "manual.html").write_text("<h1>検索運用</h1>", encoding="utf-8")
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda import_name, _distribution_names: (
            import_name == "docling",
            None,
            None,
        ),
    )

    def parse_html_fixture(*_args: Any, **_kwargs: Any) -> ParserRegistryResult:
        return ParserRegistryResult(
            extraction=StructuredExtraction(
                raw_text="artifact には raw_text を出さない",
                elements=[
                    DocumentElement(
                        kind="title",
                        text="非機密 artifact には入れない本文",
                        element_id="adapter-html-1",
                        source_parser="docling_adapter",
                        section_path=["検索運用"],
                    )
                ],
            ),
            parser_backend="docling",
            parser_version="docling_adapter_v1",
            template="html_semantic",
        )

    monkeypatch.setattr(
        parser_adapter_contract_module,
        "parse_with_registry",
        parse_html_fixture,
    )

    matrix = run_parser_adapter_compatibility_matrix(
        Settings(
            rag_parser_adapter_backend="docling",
            rag_parser_docling_enabled=True,
        ),
        fixture_root=fixture_root,
        fixture_specs=(
            ParserAdapterFixtureSpec(
                source_kind="html",
                file_name="manual.html",
                content_type="text/html",
                case_id="html-semantic-blocks",
                scenario="html_semantic_blocks",
            ),
        ),
        backends=["docling"],
        require_backend_evidence=True,
    )

    case = matrix.cases[0]
    assert matrix.passed is False
    assert matrix.blocking_failure_count == 1
    assert case.status == "failed"
    assert case.adapter_import_name == "docling"
    assert case.adapter_distribution_name is None
    assert case.adapter_package_version is None
    assert case.element_count == 1
    assert case.reason_codes == (
        "adapter_distribution_name_missing",
        "adapter_package_version_missing",
    )


def test_strict_manifest_requires_schema_remap_fixture_for_each_routed_source(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """strict manifest では source kind を正向き fixture 不足で黙って縮小しない。"""
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    (fixture_root / "manual.html").write_text("<h1>検索運用</h1>", encoding="utf-8")
    manifest = {
        "cases": [
            {
                "id": "corrupted-pdf",
                "fixture": "broken.pdf",
                "modality": "pdf",
                "scenario": "corrupted_file",
                "expected_warning": "pdf_segment_parse_failed",
            },
            {
                "id": "html-semantic-blocks",
                "fixture": "manual.html",
                "modality": "html",
                "scenario": "html_semantic_blocks",
                "adapter_schema_remap": True,
            },
        ],
    }
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda import_name, _distribution_names: (
            import_name == "docling",
            "2.103.0" if import_name == "docling" else None,
            import_name if import_name == "docling" else None,
        ),
    )

    def parse_html_fixture(*_args: Any, **_kwargs: Any) -> ParserRegistryResult:
        return ParserRegistryResult(
            extraction=StructuredExtraction(
                raw_text="artifact には raw_text を出さない",
                elements=[
                    DocumentElement(
                        kind="title",
                        text="非機密 artifact には入れない本文",
                        element_id="adapter-html-1",
                        source_parser="docling_adapter",
                        section_path=["検索運用"],
                    )
                ],
            ),
            parser_backend="docling",
            parser_version="2.103.0",
            template="html_semantic",
        )

    monkeypatch.setattr(
        parser_adapter_contract_module,
        "parse_with_registry",
        parse_html_fixture,
    )

    matrix = run_parser_adapter_compatibility_matrix(
        Settings(
            rag_parser_adapter_backend="docling",
            rag_parser_docling_enabled=True,
        ),
        fixture_root=fixture_root,
        source_kinds=["pdf", "html"],
        fixture_specs=parser_adapter_fixture_specs_from_manifest(manifest),
        backends=["docling"],
        require_backend_evidence=True,
    )

    assert matrix.passed is False
    assert matrix.source_kinds == ("pdf", "html")
    assert matrix.blocking_failure_count == 1
    assert matrix.case_count == 2
    assert matrix.cases[0].status == "passed"
    source_gap_case = matrix.cases[1]
    assert source_gap_case.source_kind == "pdf"
    assert source_gap_case.status == "failed"
    assert source_gap_case.blocking is True
    assert source_gap_case.reason_codes == ("adapter_schema_remap_fixture_missing_for_source",)
    summary = parser_adapter_contract_summary(matrix)
    assert summary["missing_source_kinds"] == ["pdf"]
    assert summary["blocking_failure_source_kinds"] == ["pdf"]
    assert summary["blocking_failure_reason_counts"] == {
        "adapter_schema_remap_fixture_missing_for_source": 1
    }


def test_manifest_fixture_specs_skip_negative_staging_cases() -> None:
    """unsupported/corrupted case は safe-error gate 用であり adapter remap smoke から外す。"""
    specs = parser_adapter_fixture_specs_from_manifest(
        {
            "cases": [
                {
                    "id": "image-ocr-bbox",
                    "fixture": "receipt-ja.png",
                    "modality": "image",
                    "scenario": "image_ocr_bbox",
                    "expected_chunk_template": "ocr_page",
                },
                {
                    "id": "tiff-image-unsupported",
                    "fixture": "scan.tiff",
                    "modality": "image",
                    "expected_chunk_template": "unsupported_tiff_image",
                    "expected_unsupported_reason": "tiff_image_not_supported",
                },
                {
                    "id": "corrupted-file-partial-failure",
                    "fixture": "broken.xlsx",
                    "modality": "office",
                    "expected_warning": "office_segment_parse_failed",
                },
            ]
        }
    )

    assert [spec.case_id for spec in specs] == ["image-ocr-bbox"]
    assert [spec.file_name for spec in specs] == ["receipt-ja.png"]


def test_manifest_fixture_specs_strict_requires_declared_schema_remap() -> None:
    """strict smoke は adapter_schema_remap=true の manifest fixture だけを証跡にする。"""
    manifest = {
        "cases": [
            {
                "id": "scanned-pdf-ocr-ja",
                "fixture": "scanned-contract-ja.pdf",
                "modality": "pdf",
                "scenario": "scanned_pdf_ocr",
            },
            {
                "id": "html-semantic-blocks",
                "fixture": "manual.html",
                "modality": "html",
                "scenario": "html_semantic_blocks",
                "adapter_schema_remap": True,
            },
        ]
    }

    legacy_specs = parser_adapter_fixture_specs_from_manifest(manifest)
    strict_specs = parser_adapter_fixture_specs_from_manifest(
        manifest,
        require_declared_schema_remap=True,
    )

    assert [spec.case_id for spec in legacy_specs] == [
        "scanned-pdf-ocr-ja",
        "html-semantic-blocks",
    ]
    assert [spec.case_id for spec in strict_specs] == ["html-semantic-blocks"]


def test_compatibility_matrix_requires_pdf_page_lineage(
    monkeypatch: MonkeyPatch,
) -> None:
    """PDF adapter smoke は element があっても page lineage がなければ失敗にする。"""
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda import_name, _distribution_names: (
            import_name == "docling",
            "2.103.0" if import_name == "docling" else None,
            import_name if import_name == "docling" else None,
        ),
    )

    def parse_without_page_lineage(*_args: object, **_kwargs: object) -> ParserRegistryResult:
        return ParserRegistryResult(
            extraction=StructuredExtraction(
                raw_text="PDF text",
                elements=[
                    DocumentElement(
                        kind="text",
                        text="PDF text",
                        element_id="adapter-el-1",
                        source_parser="docling_adapter",
                    )
                ],
            ),
            parser_backend="docling",
            parser_version="2.103.0",
            template="pdf_layout",
        )

    monkeypatch.setattr(
        parser_adapter_contract_module,
        "parse_with_registry",
        parse_without_page_lineage,
    )

    matrix = run_parser_adapter_compatibility_matrix(
        Settings(
            rag_parser_adapter_backend="docling",
            rag_parser_docling_enabled=True,
        ),
        source_kinds=["pdf"],
        backends=["docling"],
    )

    assert matrix.passed is False
    assert matrix.blocking_failure_count == 1
    case = matrix.cases[0]
    assert case.status == "failed"
    assert case.element_count == 1
    assert case.page_count == 0
    assert case.reason_codes == ("schema_remap_page_lineage_missing",)


def test_compatibility_matrix_accepts_docx_heading_lineage(
    monkeypatch: MonkeyPatch,
) -> None:
    """DOCX は page がなくても heading/section lineage があれば Office contract とする。"""
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda import_name, _distribution_names: (
            import_name == "docling",
            "2.103.0" if import_name == "docling" else None,
            import_name if import_name == "docling" else None,
        ),
    )

    def parse_docx_heading_lineage(*_args: object, **_kwargs: object) -> ParserRegistryResult:
        return ParserRegistryResult(
            extraction=StructuredExtraction(
                raw_text="経費ポリシー\n本文",
                elements=[
                    DocumentElement(
                        kind="title",
                        text="経費ポリシー",
                        element_id="adapter-title-1",
                        source_parser="docling_adapter",
                        metadata={
                            "chunk_template": "office_document",
                            "section_level": 1,
                            "adapter_element_type": "text",
                        },
                    ),
                    DocumentElement(
                        kind="text",
                        text="本文",
                        element_id="adapter-body-1",
                        source_parser="docling_adapter",
                    ),
                ],
            ),
            parser_backend="docling",
            parser_version="2.103.0",
            template="office_document",
        )

    monkeypatch.setattr(
        parser_adapter_contract_module,
        "parse_with_registry",
        parse_docx_heading_lineage,
    )

    matrix = run_parser_adapter_compatibility_matrix(
        Settings(
            rag_parser_adapter_backend="docling",
            rag_parser_docling_enabled=True,
        ),
        source_kinds=["office"],
        backends=["docling"],
    )

    assert matrix.passed is True
    case = matrix.cases[0]
    assert case.status == "passed"
    assert case.reason_codes == ("schema_remap_contract_ok",)


def test_compatibility_matrix_accepts_office_pages_lineage(
    monkeypatch: MonkeyPatch,
) -> None:
    """PPTX などは elements の page_number がなくても pages[] で slide lineage とする。"""
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda import_name, _distribution_names: (
            import_name == "docling",
            "2.103.0" if import_name == "docling" else None,
            import_name if import_name == "docling" else None,
        ),
    )

    def parse_pptx_pages_lineage(*_args: object, **_kwargs: object) -> ParserRegistryResult:
        return ParserRegistryResult(
            extraction=StructuredExtraction(
                raw_text="Slide 1\nSlide 2",
                pages=[
                    ExtractionPage(page_number=1, element_ids=["slide-text-1"]),
                    ExtractionPage(page_number=2, element_ids=["slide-text-2"]),
                ],
                elements=[
                    DocumentElement(
                        kind="text",
                        text="Slide 1",
                        element_id="slide-text-1",
                        source_parser="docling_adapter",
                    ),
                    DocumentElement(
                        kind="text",
                        text="Slide 2",
                        element_id="slide-text-2",
                        source_parser="docling_adapter",
                    ),
                ],
            ),
            parser_backend="docling",
            parser_version="2.103.0",
            template="office_slide",
        )

    monkeypatch.setattr(
        parser_adapter_contract_module,
        "parse_with_registry",
        parse_pptx_pages_lineage,
    )

    matrix = run_parser_adapter_compatibility_matrix(
        Settings(
            rag_parser_adapter_backend="docling",
            rag_parser_docling_enabled=True,
        ),
        source_kinds=["office"],
        backends=["docling"],
    )

    assert matrix.passed is True
    case = matrix.cases[0]
    assert case.status == "passed"
    assert case.page_count == 2
    assert case.reason_codes == ("schema_remap_contract_ok",)


def test_parser_adapter_contract_cli_writes_non_sensitive_artifact(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """CLI は non-blocking disabled matrix を JSON artifact として保存できる。"""
    output_path = tmp_path / "adapter-contract.json"
    monkeypatch.setattr(
        parser_adapter_contract_cli,
        "get_settings",
        lambda: Settings(
            rag_parser_adapter_backend="local",
            rag_parser_docling_enabled=False,
        ),
    )

    exit_code = parser_adapter_contract_cli.main(
        ["--backend", "docling", "--source-kind", "pdf", "--output", str(output_path)]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["cases"][0]["status"] in {"available", "disabled"}
    assert payload["summary"]["passed"] is True
    assert payload["summary"]["case_count"] == payload["case_count"]
    assert payload["summary"]["backend_source_status"]["docling"]["pdf"] in {
        "available",
        "disabled",
    }
    assert "adapter_disabled" in payload["summary"]["reason_code_counts"] or (
        "adapter_available" in payload["summary"]["reason_code_counts"]
    )
    assert "raw_text" not in output_path.read_text(encoding="utf-8")
    assert "policy-ja.pdf" not in output_path.read_text(encoding="utf-8")
    assert "file-processing-fixtures" not in output_path.read_text(encoding="utf-8")


def test_parser_adapter_contract_artifact_redacts_fixture_identifiers() -> None:
    """artifact payload は fixture path/name/case id を hash label にする。"""
    matrix = ParserAdapterCompatibilityMatrix(
        passed=False,
        fixture_root="/private/customer-fixtures/acme",
        source_kinds=("pdf",),
        backends=("docling",),
        case_count=1,
        blocking_failure_count=1,
        cases=(
            ParserAdapterCompatibilityCase(
                backend="docling",
                source_kind="pdf",
                fixture_name="staging/acme-contract-2026.pdf",
                content_type="application/pdf",
                status="fixture_missing",
                blocking=True,
                case_id="acme-contract-case",
                scenario="scanned_pdf_ocr",
                reason_codes=("fixture_missing",),
            ),
        ),
    )

    payload = parser_adapter_contract_artifact_payload(matrix)
    payload_text = json.dumps(payload, ensure_ascii=False)
    cases = cast(list[dict[str, object]], payload["cases"])
    summary = cast(dict[str, object], payload["summary"])
    blocking_failures = cast(
        list[dict[str, object]],
        summary["blocking_failures"],
    )
    blocking_failure_case_refs = cast(list[str], summary["blocking_failure_case_refs"])

    assert payload["fixture_root"] != "/private/customer-fixtures/acme"
    assert str(payload["fixture_root"]).startswith("fixture_root:")
    assert str(cases[0]["fixture_name"]).startswith("pdf_fixture:")
    assert cases[0]["fixture_name_hash"]
    assert cases[0]["case_ref_hash"]
    assert blocking_failures[0]["case_ref_hash"]
    assert blocking_failure_case_refs[0].startswith("case:")
    assert "/private/customer-fixtures/acme" not in payload_text
    assert "acme-contract-2026.pdf" not in payload_text
    assert "acme-contract-case" not in payload_text


def test_parser_adapter_contract_cli_strict_blocks_missing_adapter(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """CLI strict は feature flag を有効化し、未導入 adapter を gate 失敗にする。"""
    output_path = tmp_path / "adapter-contract-strict.json"
    monkeypatch.setattr(
        parser_adapter_contract_cli,
        "get_settings",
        lambda: Settings(
            rag_parser_adapter_backend="local",
            rag_parser_docling_enabled=False,
            rag_parser_marker_enabled=False,
            rag_parser_unstructured_enabled=False,
        ),
    )
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda *_args: (False, None, None),
    )

    exit_code = parser_adapter_contract_cli.main(
        [
            "--strict",
            "--backend",
            "docling",
            "--source-kind",
            "pdf",
            "--output",
            str(output_path),
        ]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["blocking_failure_count"] == 1
    assert payload["cases"][0]["status"] == "missing"
    assert payload["cases"][0]["blocking"] is True
    assert payload["summary"]["blocking_failures"][0] == {
        "backend": "docling",
        "source_kind": "pdf",
        "status": "missing",
        "warning_codes": ["adapter_package_missing"],
        "reason_codes": ["adapter_missing"],
    }


def test_parser_adapter_contract_cli_strict_blocks_explicit_unrouted_backend(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """CLI strict で backend/source を明示した場合、未対応 route を合格にしない。"""
    output_path = tmp_path / "adapter-contract-unrouted.json"
    monkeypatch.setattr(
        parser_adapter_contract_cli,
        "get_settings",
        lambda: Settings(
            rag_parser_adapter_backend="local",
            rag_parser_docling_enabled=False,
            rag_parser_marker_enabled=False,
            rag_parser_unstructured_enabled=False,
        ),
    )
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda import_name, _distribution_names: (
            import_name == "docling",
            "2.103.0" if import_name == "docling" else None,
            import_name if import_name == "docling" else None,
        ),
    )

    exit_code = parser_adapter_contract_cli.main(
        [
            "--strict",
            "--backend",
            "docling",
            "--source-kind",
            "email",
            "--output",
            str(output_path),
        ]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["blocking_failure_count"] == 1
    assert payload["cases"][0]["status"] == "unsupported"
    assert payload["cases"][0]["blocking"] is True
    assert payload["summary"]["blocking_failures"][0] == {
        "backend": "docling",
        "source_kind": "email",
        "status": "unsupported",
        "warning_codes": [],
        "reason_codes": ["adapter_not_routed_for_source"],
    }


def test_parser_adapter_contract_cli_strict_manifest_uses_real_fixtures(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """CLI strict smoke は staging manifest の fixture specs を contract runner へ渡す。"""
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    (fixture_root / "scanned-contract-ja.pdf").write_bytes(b"%PDF-1.7")
    (fixture_root / "manual.html").write_text("<h1>検索運用</h1>", encoding="utf-8")
    manifest_path = tmp_path / "manifests" / "file-processing-golden-set.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps(
            {
                "fixture_root": "../fixtures",
                "cases": [
                    {
                        "id": "scanned-pdf-ocr-ja",
                        "fixture": "scanned-contract-ja.pdf",
                        "modality": "pdf",
                        "scenario": "scanned_pdf_ocr",
                        "adapter_schema_remap": True,
                    },
                    {
                        "id": "html-semantic-blocks",
                        "fixture": "manual.html",
                        "modality": "html",
                        "scenario": "html_semantic_blocks",
                        "adapter_schema_remap": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "adapter-contract-manifest.json"
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        parser_adapter_contract_cli,
        "get_settings",
        lambda: Settings(
            rag_parser_adapter_backend="local",
            rag_parser_docling_enabled=False,
            rag_parser_marker_enabled=False,
            rag_parser_unstructured_enabled=False,
        ),
    )

    def fake_matrix(settings: Settings, **kwargs: object) -> ParserAdapterCompatibilityMatrix:
        captured["backend"] = settings.rag_parser_adapter_backend
        captured["docling_enabled"] = settings.rag_parser_docling_enabled
        captured["marker_enabled"] = settings.rag_parser_marker_enabled
        captured["unstructured_enabled"] = settings.rag_parser_unstructured_enabled
        captured.update(kwargs)
        return ParserAdapterCompatibilityMatrix(
            passed=True,
            fixture_root=str(kwargs["fixture_root"]),
            source_kinds=("pdf", "html"),
            backends=("unstructured",),
            case_count=2,
            blocking_failure_count=0,
            cases=(),
        )

    monkeypatch.setattr(
        parser_adapter_contract_cli,
        "run_parser_adapter_compatibility_matrix",
        fake_matrix,
    )

    exit_code = parser_adapter_contract_cli.main(
        [
            "--strict",
            "--manifest",
            str(manifest_path),
            "--backend",
            "unstructured",
            "--output",
            str(output_path),
        ]
    )

    fixture_specs = cast(
        tuple[ParserAdapterFixtureSpec, ...],
        captured["fixture_specs"],
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert captured["backend"] == "auto"
    assert captured["docling_enabled"] is True
    assert captured["marker_enabled"] is True
    assert captured["unstructured_enabled"] is True
    assert captured["fixture_root"] == fixture_root
    assert captured["require_backend_evidence"] is True
    assert [spec.file_name for spec in fixture_specs] == [
        "scanned-contract-ja.pdf",
        "manual.html",
    ]
    assert [spec.case_id for spec in fixture_specs] == [
        "scanned-pdf-ocr-ja",
        "html-semantic-blocks",
    ]
    payload_text = output_path.read_text(encoding="utf-8")
    assert payload["fixture_root"] != str(fixture_root)
    assert payload["fixture_root"].startswith("fixture_root:")
    assert payload["fixture_root_hash"]
    assert str(fixture_root) not in payload_text
    assert "scanned-contract-ja.pdf" not in payload_text
    assert "manual.html" not in payload_text
    assert payload["summary"]["case_count"] == 2


def test_parser_adapter_contract_summary_reports_blocking_failures() -> None:
    """matrix summary は backend/source/status だけで失敗箇所を示す。"""
    matrix = ParserAdapterCompatibilityMatrix(
        passed=False,
        fixture_root="/tmp/fixtures",
        source_kinds=("pdf",),
        backends=("docling",),
        case_count=1,
        blocking_failure_count=1,
        cases=(
            ParserAdapterCompatibilityCase(
                backend="docling",
                source_kind="pdf",
                fixture_name="policy-ja.pdf",
                content_type="application/pdf",
                status="fallback",
                blocking=True,
                scenario="two_column_pdf_reading_order",
                warning_codes=("docling_adapter_failed",),
                reason_codes=("adapter_fallback_used",),
            ),
        ),
    )

    summary = parser_adapter_contract_summary(matrix)

    assert summary["passed"] is False
    assert summary["passed_source_kinds"] == []
    assert summary["missing_source_kinds"] == ["pdf"]
    assert summary["scenarios"] == ["two_column_pdf_reading_order"]
    assert summary["passed_scenarios"] == []
    assert summary["missing_scenarios"] == ["two_column_pdf_reading_order"]
    assert summary["blocking_failure_scenarios"] == ["two_column_pdf_reading_order"]
    assert summary["blocking_failure_source_kinds"] == ["pdf"]
    assert summary["blocking_failure_backends"] == ["docling"]
    assert summary["backend_status_counts"] == {"docling": {"fallback": 1}}
    assert summary["backend_source_status"] == {"docling": {"pdf": "fallback"}}
    assert summary["source_kind_status_counts"] == {"pdf": {"fallback": 1}}
    assert summary["backend_passed_source_kinds"] == {}
    assert summary["backend_passed_scenarios"] == {}
    assert summary["reason_code_counts"] == {"adapter_fallback_used": 1}
    assert summary["warning_code_counts"] == {"docling_adapter_failed": 1}
    assert summary["blocking_failure_reason_counts"] == {"adapter_fallback_used": 1}
    assert summary["blocking_failures"] == [
        {
            "backend": "docling",
            "source_kind": "pdf",
            "status": "fallback",
            "warning_codes": ["docling_adapter_failed"],
            "reason_codes": ["adapter_fallback_used"],
            "scenario": "two_column_pdf_reading_order",
        }
    ]
    assert "policy-ja.pdf" not in json.dumps(summary, ensure_ascii=False)


def test_parser_adapter_contract_summary_aggregates_backend_source_status() -> None:
    """同一 backend/source の一部失敗を最後の passed case で隠さない。"""
    matrix = ParserAdapterCompatibilityMatrix(
        passed=False,
        fixture_root="/tmp/fixtures",
        source_kinds=("pdf",),
        backends=("docling",),
        case_count=2,
        blocking_failure_count=1,
        cases=(
            ParserAdapterCompatibilityCase(
                backend="docling",
                source_kind="pdf",
                fixture_name="two-column-report-ja.pdf",
                content_type="application/pdf",
                status="fallback",
                blocking=True,
                scenario="two_column_pdf_reading_order",
                reason_codes=("adapter_fallback_used",),
            ),
            ParserAdapterCompatibilityCase(
                backend="docling",
                source_kind="pdf",
                fixture_name="duplicate-policy-ja.pdf",
                content_type="application/pdf",
                status="passed",
                blocking=True,
                scenario="duplicate_file_canonical_kb",
                reason_codes=("schema_remap_contract_ok",),
            ),
        ),
    )

    summary = parser_adapter_contract_summary(matrix)

    assert summary["backend_source_status"] == {"docling": {"pdf": "fallback"}}
    assert summary["backend_source_status_counts"] == {
        "docling": {"pdf": {"fallback": 1, "passed": 1}}
    }
    assert summary["passed_source_kinds"] == ["pdf"]
    assert summary["missing_source_kinds"] == []
    assert summary["blocking_failure_source_kinds"] == ["pdf"]
