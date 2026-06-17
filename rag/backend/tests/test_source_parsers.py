"""source parser registry のルーティングテスト。"""

import sys
from email.message import EmailMessage
from io import BytesIO
from types import ModuleType, SimpleNamespace
from zipfile import ZipFile

import pytest

from app.rag.ingestion_quality import build_ingestion_quality_report
from app.rag.parsers import parse_openxml_office_segment_extractions, parse_with_registry
from app.rag.source_profile import build_source_profile
from app.schemas.document import SourceModality


def test_parser_registry_partitions_text_html_email_sources() -> None:
    """標準テキスト系は Enterprise AI に送らず local partition する。"""
    cases = [
        ("plain.txt", "# 見出し\n本文".encode(), "text/plain", "text"),
        ("memo.md", b"# Title\nBody", "text/markdown", "text"),
        ("table.csv", b"name,value\nalpha,1", "text/csv", "text"),
        ("payload.json", b'{"name":"alpha"}', "application/json", "text"),
        (
            "events.jsonl",
            b'{"event":"created"}\n{"event":"done"}',
            "application/octet-stream",
            "text",
        ),
        (
            "events.ndjson",
            b'{"event":"created"}\n{"event":"done"}',
            "application/x-ndjson",
            "text",
        ),
        ("page.html", b"<html><body><h1>Title</h1><p>Body</p></body></html>", "text/html", "html"),
        (
            "mail.eml",
            b"Subject: Hello\nFrom: a@example.com\nTo: b@example.com\n\nBody",
            "message/rfc822",
            "email",
        ),
    ]

    for file_name, data, content_type, modality in cases:
        profile = build_source_profile(
            original_file_name=file_name,
            sanitized_file_name=file_name,
            content_type=content_type,
            file_size_bytes=len(data),
            content_sha256="a" * 64,
            data=data,
        )

        result = parse_with_registry(data, source_profile=profile, content_type=content_type)

        assert profile.modality == SourceModality(modality)
        assert result.parser_backend == "local_partition"
        assert result.extraction is not None
        assert result.extraction.raw_text


def test_parser_registry_preserves_markdown_code_and_equation_blocks() -> None:
    """Markdown code/formula block は local parser の element lineage を保持する。"""
    data = b"""# Runbook
```sql
select 1 from dual;
```

\\[
a^2 + b^2 = c^2
\\]
"""
    content_type = "text/markdown"
    profile = build_source_profile(
        original_file_name="runbook.md",
        sanitized_file_name="runbook.md",
        content_type=content_type,
        file_size_bytes=len(data),
        content_sha256="b" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type=content_type)

    assert result.parser_backend == "local_partition"
    assert result.extraction is not None
    code_element = next(element for element in result.extraction.elements if element.kind == "code")
    equation_element = next(
        element for element in result.extraction.elements if element.kind == "equation"
    )
    assert code_element.content_kind == "code"
    assert code_element.source_parser == "local_text_structure"
    assert code_element.metadata["code_language"] == "sql"
    assert code_element.metadata["chunk_template"] == "markdown_by_heading"
    assert equation_element.content_kind == "equation"
    assert equation_element.source_parser == "local_text_structure"
    assert equation_element.metadata["equation_delimiter"] == "\\[\\]"


def test_parser_registry_preserves_csv_and_tsv_table_cells() -> None:
    """CSV/TSV は quoting を尊重し、tables/cells と table chunk lineage を持つ。"""
    cases = [
        (
            "table.csv",
            b'name,amount\n"alpha, beta",1200\n',
            "text/csv",
            [
                (0, 0, "name"),
                (0, 1, "amount"),
                (1, 0, "alpha, beta"),
                (1, 1, "1200"),
            ],
            "csv",
        ),
        (
            "table.tsv",
            b"name\tamount\nalpha\t1200\n",
            "text/tab-separated-values",
            [
                (0, 0, "name"),
                (0, 1, "amount"),
                (1, 0, "alpha"),
                (1, 1, "1200"),
            ],
            "tsv",
        ),
    ]

    for file_name, data, content_type, expected_cells, table_format in cases:
        profile = build_source_profile(
            original_file_name=file_name,
            sanitized_file_name=file_name,
            content_type=content_type,
            file_size_bytes=len(data),
            content_sha256="0" * 64,
            data=data,
        )

        result = parse_with_registry(data, source_profile=profile, content_type=content_type)

        assert result.template == "table_preserve_rows"
        assert result.extraction is not None
        assert result.extraction.parser_artifacts["table_format"] == table_format
        assert result.extraction.elements[0].kind == "table"
        assert result.extraction.elements[0].content_kind == "table"
        assert result.extraction.elements[0].metadata["chunk_template"] == "table_preserve_rows"
        table = result.extraction.tables[0]
        assert table.metadata["row_count"] == 2
        assert table.metadata["column_count"] == 2
        assert [(cell.row, cell.col, cell.text) for cell in table.cells] == expected_cells


def test_source_profile_classifies_tsv_extension_as_text_when_mime_is_generic() -> None:
    """TSV は application/octet-stream でも拡張子から local table parser へ進める。"""
    data = b"name\tamount\nalpha\t1200\n"

    profile = build_source_profile(
        original_file_name="metrics.tsv",
        sanitized_file_name="metrics.tsv",
        content_type="application/octet-stream",
        file_size_bytes=len(data),
        content_sha256="d" * 64,
        data=data,
    )

    assert profile.modality == SourceModality.TEXT
    assert profile.parser_backend == "local_partition"
    assert profile.parser_profile == "local_text_structure"
    assert profile.preview_kind == "text"
    assert profile.unsupported_reason is None


def test_parser_registry_preserves_html_table_cells_in_reading_order() -> None:
    """HTML table は普通 text block に分解せず、table/cell 構造として保持する。"""
    data = (
        "<html><body><h1>料金</h1>"
        "<table><tr><th>項目</th><th>金額</th></tr>"
        "<tr><td>交通費</td><td>1200</td></tr></table>"
        "<p>注記</p></body></html>"
    ).encode()
    profile = build_source_profile(
        original_file_name="page.html",
        sanitized_file_name="page.html",
        content_type="text/html",
        file_size_bytes=len(data),
        content_sha256="3" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type="text/html")

    assert result.extraction is not None
    assert [element.kind for element in result.extraction.elements] == ["title", "table", "text"]
    table_element = result.extraction.elements[1]
    assert table_element.content_kind == "table"
    assert table_element.section_path == ["料金"]
    assert table_element.metadata["chunk_template"] == "html_semantic"
    table = result.extraction.tables[0]
    assert table.table_id == "html-table-0000"
    assert table.metadata["row_count"] == 2
    assert table.metadata["column_count"] == 2
    assert [(cell.row, cell.col, cell.text) for cell in table.cells] == [
        (0, 0, "項目"),
        (0, 1, "金額"),
        (1, 0, "交通費"),
        (1, 1, "1200"),
    ]


def test_parser_registry_preserves_email_parts_and_attachment_metadata() -> None:
    """email は headers/body を要素化し、添付は原文なし metadata asset として保持する。"""
    message = EmailMessage()
    message["Subject"] = "契約更新"
    message["From"] = "alice@example.com"
    message["To"] = "bob@example.com"
    message["Date"] = "Wed, 17 Jun 2026 10:00:00 +0900"
    message.set_content("本文です。添付を確認してください。")
    message.add_attachment(
        b"secret attachment bytes",
        maintype="application",
        subtype="pdf",
        filename="contract.pdf",
    )
    data = message.as_bytes()
    profile = build_source_profile(
        original_file_name="mail.eml",
        sanitized_file_name="mail.eml",
        content_type="message/rfc822",
        file_size_bytes=len(data),
        content_sha256="4" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type="message/rfc822")

    assert result.extraction is not None
    assert result.extraction.parser_artifacts["attachment_count"] == 1
    assert [element.element_id for element in result.extraction.elements] == [
        "email-headers",
        "email-body",
    ]
    headers, body = result.extraction.elements
    assert headers.content_kind == "email"
    assert headers.metadata["email_part"] == "headers"
    assert headers.metadata["subject_chars"] == 4
    assert "Subject: 契約更新" in headers.text
    assert body.metadata["email_part"] == "body"
    assert "本文です" in body.text
    asset = result.extraction.assets[0]
    assert asset.asset_id == "email-attachment-0000"
    assert asset.kind == "email_attachment"
    assert asset.alt_text == "contract.pdf"
    assert asset.metadata["file_name"] == "contract.pdf"
    assert asset.metadata["content_type"] == "application/pdf"
    assert asset.metadata["size_bytes"] == len(b"secret attachment bytes")
    assert "secret attachment bytes" not in str(result.extraction.to_document_payload())


def test_parser_registry_marks_outlook_msg_as_unsupported_until_parser_exists() -> None:
    """Outlook MSG は RFC822 parser へ誤投入せず、明示的な未対応として返す。"""
    data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1outlook msg"
    profile = build_source_profile(
        original_file_name="approval.msg",
        sanitized_file_name="approval.msg",
        content_type="application/octet-stream",
        file_size_bytes=len(data),
        content_sha256="9" * 64,
        data=data,
    )

    result = parse_with_registry(
        data,
        source_profile=profile,
        content_type=profile.content_type,
    )

    assert profile.modality == SourceModality.EMAIL
    assert profile.parser_backend == "unsupported"
    assert profile.parser_profile == "unsupported_outlook_msg"
    assert profile.preview_kind == "unsupported"
    assert profile.unsupported_reason == "outlook_msg_not_supported"
    assert "unsupported_outlook_msg" in profile.quality_warnings
    assert result.extraction is None
    assert result.parser_backend == "unsupported"
    assert result.unsupported_reason == "outlook_msg_not_supported"
    assert "unsupported_outlook_msg" in result.warnings


def test_source_profile_accepts_webp_as_enterprise_ai_image() -> None:
    """WEBP は upload whitelist / SourceProfile / Enterprise AI image payload を揃える。"""
    data = b"RIFF....WEBP"
    profile = build_source_profile(
        original_file_name="receipt.webp",
        sanitized_file_name="receipt.webp",
        content_type="image/webp",
        file_size_bytes=len(data),
        content_sha256="8" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type=profile.content_type)

    assert profile.modality == SourceModality.IMAGE
    assert profile.parser_profile == "enterprise_ai_image_ocr"
    assert profile.parser_backend == "enterprise_ai"
    assert profile.preview_kind == "image"
    assert profile.unsupported_reason is None
    assert result.extraction is None
    assert result.parser_backend == "enterprise_ai"


def test_parser_registry_marks_tiff_image_as_unsupported_until_conversion_exists() -> None:
    """TIFF はブラウザ preview / Enterprise AI image payload 未整合のため明示的に止める。"""
    data = b"II*\x00tiff"
    profile = build_source_profile(
        original_file_name="scan.tiff",
        sanitized_file_name="scan.tiff",
        content_type="application/octet-stream",
        file_size_bytes=len(data),
        content_sha256="7" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type=profile.content_type)

    assert profile.modality == SourceModality.IMAGE
    assert profile.parser_profile == "unsupported_tiff_image"
    assert profile.parser_backend == "unsupported"
    assert profile.preview_kind == "unsupported"
    assert profile.unsupported_reason == "tiff_image_not_supported"
    assert "unsupported_tiff_image" in profile.quality_warnings
    assert result.extraction is None
    assert result.parser_backend == "unsupported"
    assert result.unsupported_reason == "tiff_image_not_supported"
    assert "unsupported_tiff_image" in result.warnings


def test_parser_registry_partitions_openxml_office_sources() -> None:
    """docx/pptx/xlsx は標準ライブラリ adapter で最低限の text block にする。"""
    cases = [
        (
            "doc.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx_bytes("Word 本文"),
            "office_document",
        ),
        (
            "slides.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            _pptx_bytes("Slide 本文"),
            "office_slide",
        ),
        (
            "book.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _xlsx_bytes("Sheet 本文"),
            "office_sheet",
        ),
    ]

    for file_name, content_type, data, template in cases:
        profile = build_source_profile(
            original_file_name=file_name,
            sanitized_file_name=file_name,
            content_type=content_type,
            file_size_bytes=len(data),
            content_sha256="b" * 64,
            data=data,
        )

        result = parse_with_registry(data, source_profile=profile, content_type=content_type)

        assert profile.modality == SourceModality.OFFICE
        assert profile.parser_profile == "local_office_structure"
        assert profile.parser_backend == "local_partition"
        assert result.parser_backend == "local_partition"
        assert result.template == template
        assert result.extraction is not None
        assert "本文" in result.extraction.raw_text


def test_source_profile_marks_legacy_office_as_unsupported() -> None:
    """旧バイナリ Office は local parser 対応済みのように表示しない。"""
    data = b"legacy office"
    profile = build_source_profile(
        original_file_name="legacy.doc",
        sanitized_file_name="legacy.doc",
        content_type="application/msword",
        file_size_bytes=len(data),
        content_sha256="9" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type=profile.content_type)

    assert profile.modality == SourceModality.OFFICE
    assert profile.parser_profile == "unsupported_legacy_office_binary"
    assert profile.parser_backend == "unsupported"
    assert profile.preview_kind == "unsupported"
    assert profile.unsupported_reason == "legacy_office_binary_not_supported"
    assert "unsupported_legacy_office_binary" in profile.quality_warnings
    assert result.extraction is None
    assert result.parser_backend == "unsupported"
    assert result.unsupported_reason == "legacy_office_binary_not_supported"
    assert "unsupported_legacy_office_binary" in result.warnings


def test_parser_registry_uses_openxml_content_type_when_extension_is_missing() -> None:
    """拡張子がなくても OpenXML MIME が正しければ local Office parser に合わせる。"""
    data = _docx_bytes("MIME 判定本文")
    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    profile = build_source_profile(
        original_file_name="upload",
        sanitized_file_name="upload",
        content_type=content_type,
        file_size_bytes=len(data),
        content_sha256="8" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type=content_type)

    assert profile.extension is None
    assert profile.parser_profile == "local_office_structure"
    assert profile.parser_backend == "local_partition"
    assert result.parser_backend == "local_partition"
    assert result.extraction is not None
    assert "MIME 判定本文" in result.extraction.raw_text


def test_parser_registry_preserves_xlsx_table_cells() -> None:
    """XLSX は markdown text だけでなく tables/cells も保持する。"""
    data = _xlsx_table_bytes([["項目", "金額"], ["交通費", "1200"]])
    content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    profile = build_source_profile(
        original_file_name="book.xlsx",
        sanitized_file_name="book.xlsx",
        content_type=content_type,
        file_size_bytes=len(data),
        content_sha256="6" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type=content_type)
    segments = parse_openxml_office_segment_extractions(data, source_profile=profile)

    assert result.extraction is not None
    assert result.extraction.tables
    table = result.extraction.tables[0]
    assert table.table_id == "xlsx-sheet-1"
    assert table.page_number == 1
    assert table.metadata["row_count"] == 2
    assert table.metadata["column_count"] == 2
    assert [(cell.row, cell.col, cell.text) for cell in table.cells] == [
        (0, 0, "項目"),
        (0, 1, "金額"),
        (1, 0, "交通費"),
        (1, 1, "1200"),
    ]
    assert len(segments.segments) == 1
    assert segments.segments[0].extraction.tables[0].cells[3].text == "1200"


def test_parser_registry_preserves_docx_table_cells() -> None:
    """DOCX table は paragraph 順を保ち、tables/cells を保持する。"""
    data = _docx_table_bytes(
        before="契約条件",
        rows=[["項目", "値"], ["期間", "1年"]],
        after="以上",
    )
    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    profile = build_source_profile(
        original_file_name="contract.docx",
        sanitized_file_name="contract.docx",
        content_type=content_type,
        file_size_bytes=len(data),
        content_sha256="5" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type=content_type)

    assert result.extraction is not None
    assert result.extraction.raw_text.splitlines() == [
        "契約条件",
        "| 項目 | 値 |",
        "| 期間 | 1年 |",
        "以上",
    ]
    table = result.extraction.tables[0]
    assert table.table_id == "docx-table-0000"
    assert table.metadata["row_count"] == 2
    assert table.metadata["column_count"] == 2
    assert [(cell.row, cell.col, cell.text) for cell in table.cells] == [
        (0, 0, "項目"),
        (0, 1, "値"),
        (1, 0, "期間"),
        (1, 1, "1年"),
    ]


def test_parser_registry_preserves_pptx_table_cells() -> None:
    """PPTX slide table は slide/page lineage 付きで cells を保持する。"""
    data = _pptx_table_bytes([["項目", "値"], ["進捗", "90%"]])
    content_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    profile = build_source_profile(
        original_file_name="deck.pptx",
        sanitized_file_name="deck.pptx",
        content_type=content_type,
        file_size_bytes=len(data),
        content_sha256="7" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type=content_type)
    segments = parse_openxml_office_segment_extractions(data, source_profile=profile)

    assert result.extraction is not None
    table = result.extraction.tables[0]
    assert table.table_id == "pptx-slide-1-table-0000"
    assert table.page_number == 1
    assert table.metadata["office_segment_number"] == 1
    assert table.metadata["row_count"] == 2
    assert table.metadata["column_count"] == 2
    assert [(cell.row, cell.col, cell.text) for cell in table.cells] == [
        (0, 0, "項目"),
        (0, 1, "値"),
        (1, 0, "進捗"),
        (1, 1, "90%"),
    ]
    assert len(segments.segments) == 1
    assert segments.segments[0].extraction.tables[0].cells[3].text == "90%"


def test_parser_registry_records_safe_fallback_for_corrupted_office() -> None:
    """壊れた Office は fallback_used と warning を残して Enterprise AI 側へ渡す。"""
    data = b"not a zip"
    profile = build_source_profile(
        original_file_name="broken.docx",
        sanitized_file_name="broken.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_size_bytes=len(data),
        content_sha256="c" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type=profile.content_type)

    assert result.extraction is None
    assert result.parser_backend == "enterprise_ai"
    assert result.fallback_used is True
    assert "office_local_parse_failed" in result.warnings


def test_parser_registry_records_external_adapter_unavailable_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docling/Marker/Unstructured flags は未導入環境でも安全に local fallback する。"""

    def unavailable(name: str) -> bool:
        return False

    monkeypatch.setattr("app.rag.parsers._module_available", unavailable)
    data = b"# Title\nBody"
    profile = build_source_profile(
        original_file_name="memo.md",
        sanitized_file_name="memo.md",
        content_type="text/markdown",
        file_size_bytes=len(data),
        content_sha256="e" * 64,
        data=data,
    )

    result = parse_with_registry(
        data,
        source_profile=profile,
        content_type=profile.content_type,
        adapter_backend="docling",
        docling_enabled=True,
    )

    assert result.parser_backend == "local_partition"
    assert result.extraction is not None
    assert result.fallback_used is True
    assert "docling_adapter_package_missing" in result.warnings


def test_parser_registry_requires_adapter_feature_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """adapter backend を明示しても feature flag が false なら外部 parser は呼ばない。"""
    docling_module = ModuleType("docling")
    converter_module = ModuleType("docling.document_converter")

    class FakeDocumentConverter:
        def convert(self, path: str) -> object:
            _ = path
            raise AssertionError("feature flag が false の adapter は呼び出さない")

    converter_module.__dict__["DocumentConverter"] = FakeDocumentConverter
    monkeypatch.setitem(sys.modules, "docling", docling_module)
    monkeypatch.setitem(sys.modules, "docling.document_converter", converter_module)
    data = b"# Title\nBody"
    profile = build_source_profile(
        original_file_name="memo.md",
        sanitized_file_name="memo.md",
        content_type="text/markdown",
        file_size_bytes=len(data),
        content_sha256="8" * 64,
        data=data,
    )

    result = parse_with_registry(
        data,
        source_profile=profile,
        content_type=profile.content_type,
        adapter_backend="docling",
    )

    assert result.parser_backend == "local_partition"
    assert result.extraction is not None
    assert result.fallback_used is True
    assert "docling_adapter_feature_flag_disabled" in result.warnings


def test_parser_registry_adapter_fallback_warning_affects_quality_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """adapter fallback は取込品質にも残し、parser fallback rate で追跡できる。"""

    monkeypatch.setattr("app.rag.parsers._module_available", lambda _name: False)
    data = b"<h1>Title</h1><p>Body</p>"
    profile = build_source_profile(
        original_file_name="page.html",
        sanitized_file_name="page.html",
        content_type="text/html",
        file_size_bytes=len(data),
        content_sha256="7" * 64,
        data=data,
    )

    result = parse_with_registry(
        data,
        source_profile=profile,
        content_type=profile.content_type,
        adapter_backend="docling",
        docling_enabled=True,
    )

    assert result.extraction is not None
    quality = build_ingestion_quality_report(
        result.extraction.model_copy(
            update={"warnings": [*result.extraction.warnings, *result.warnings]}
        ),
        source_profile=profile,
        parser_backend=result.parser_backend,
        parser_version=result.parser_version,
        fallback_used=result.fallback_used,
    )
    assert result.fallback_used is True
    assert quality.fallback_used is True
    assert quality.risk_level == "medium"
    assert "parser_fallback_used" in quality.quality_warnings
    assert "docling_adapter_package_missing" in quality.quality_warnings


def test_parser_registry_auto_continues_after_missing_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto adapter は前段 adapter 不在時も次の有効 adapter へ進み warning を残す。"""
    marker_module = ModuleType("marker")
    marker_module.__dict__["__version__"] = "4.5.6"
    converters_module = ModuleType("marker.converters")
    pdf_module = ModuleType("marker.converters.pdf")
    models_module = ModuleType("marker.models")
    output_module = ModuleType("marker.output")

    class FakePdfConverter:
        def __init__(self, *, artifact_dict: dict[str, object]) -> None:
            assert artifact_dict == {"model": "fake"}

        def __call__(self, path: str) -> object:
            assert path.endswith(".pdf")
            return object()

    def create_model_dict() -> dict[str, object]:
        return {"model": "fake"}

    def text_from_rendered(rendered: object) -> tuple[str, dict[str, object], dict[str, object]]:
        _ = rendered
        return "# Marker Fallback\n本文", {}, {}

    pdf_module.__dict__["PdfConverter"] = FakePdfConverter
    models_module.__dict__["create_model_dict"] = create_model_dict
    output_module.__dict__["text_from_rendered"] = text_from_rendered
    monkeypatch.setitem(sys.modules, "marker", marker_module)
    monkeypatch.setitem(sys.modules, "marker.converters", converters_module)
    monkeypatch.setitem(sys.modules, "marker.converters.pdf", pdf_module)
    monkeypatch.setitem(sys.modules, "marker.models", models_module)
    monkeypatch.setitem(sys.modules, "marker.output", output_module)
    monkeypatch.setattr("app.rag.parsers._module_available", lambda name: name == "marker")

    data = b"%PDF"
    profile = build_source_profile(
        original_file_name="sample.pdf",
        sanitized_file_name="sample.pdf",
        content_type="application/pdf",
        file_size_bytes=len(data),
        content_sha256="9" * 64,
        data=data,
    )

    result = parse_with_registry(
        data,
        source_profile=profile,
        content_type=profile.content_type,
        adapter_backend="auto",
        docling_enabled=True,
        marker_enabled=True,
    )

    assert result.parser_backend == "marker"
    assert result.parser_version == "4.5.6"
    assert result.fallback_used is True
    assert result.warnings == ("docling_adapter_package_missing",)
    assert result.extraction is not None
    assert result.extraction.parser_artifacts["external_adapter"] == "marker"
    assert "Marker Fallback" in result.extraction.raw_text


def test_parser_registry_uses_docling_adapter_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docling adapter が使える場合は共通 schema に remap して返す。"""
    docling_module = ModuleType("docling")
    docling_module.__dict__["__version__"] = "1.2.3"
    converter_module = ModuleType("docling.document_converter")

    class FakeDocument:
        pages = {"1": object(), "2": object()}
        tables = [object()]

        def export_to_markdown(self) -> str:
            return "# Docling\n本文"

    class FakeDocumentConverter:
        def convert(self, path: str) -> object:
            assert path.endswith(".pdf")
            return SimpleNamespace(document=FakeDocument())

    converter_module.__dict__["DocumentConverter"] = FakeDocumentConverter
    monkeypatch.setitem(sys.modules, "docling", docling_module)
    monkeypatch.setitem(sys.modules, "docling.document_converter", converter_module)

    data = b"%PDF"
    profile = build_source_profile(
        original_file_name="sample.pdf",
        sanitized_file_name="sample.pdf",
        content_type="application/pdf",
        file_size_bytes=len(data),
        content_sha256="f" * 64,
        data=data,
    )

    result = parse_with_registry(
        data,
        source_profile=profile,
        content_type=profile.content_type,
        adapter_backend="docling",
        docling_enabled=True,
    )

    assert result.parser_backend == "docling"
    assert result.parser_version == "1.2.3"
    assert result.template == "pdf_layout"
    assert result.extraction is not None
    assert "Docling" in result.extraction.raw_text
    assert result.extraction.parser_artifacts["parser_backend"] == "docling"
    assert result.extraction.parser_artifacts["page_count"] == 2
    assert result.extraction.parser_artifacts["table_count"] == 1


def test_docling_adapter_remaps_structured_text_and_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docling の block/table 出力は flat markdown に潰さず共通 schema へ remap する。"""
    docling_module = ModuleType("docling")
    docling_module.__dict__["__version__"] = "2.0.0"
    converter_module = ModuleType("docling.document_converter")

    class FakeDocument:
        pages = {"1": object(), "2": object()}
        texts = [
            SimpleNamespace(
                category="Title",
                text="請求書",
                metadata=SimpleNamespace(page_number=1),
            ),
            SimpleNamespace(category="NarrativeText", text="支払条件です。"),
        ]
        tables = [
            SimpleNamespace(
                kind="table",
                rows=[["項目", "金額"], ["交通費", "1000円"]],
                metadata=SimpleNamespace(page_number=2, bbox=[0, 0, 50, 20]),
            )
        ]

        def export_to_markdown(self) -> str:
            return "# 請求書\n支払条件です。"

    class FakeDocumentConverter:
        def convert(self, path: str) -> object:
            assert path.endswith(".pdf")
            return SimpleNamespace(document=FakeDocument())

    converter_module.__dict__["DocumentConverter"] = FakeDocumentConverter
    monkeypatch.setitem(sys.modules, "docling", docling_module)
    monkeypatch.setitem(sys.modules, "docling.document_converter", converter_module)

    data = b"%PDF"
    profile = build_source_profile(
        original_file_name="invoice.pdf",
        sanitized_file_name="invoice.pdf",
        content_type="application/pdf",
        file_size_bytes=len(data),
        content_sha256="3" * 64,
        data=data,
    )

    result = parse_with_registry(
        data,
        source_profile=profile,
        content_type=profile.content_type,
        adapter_backend="docling",
        docling_enabled=True,
    )

    assert result.parser_backend == "docling"
    assert result.extraction is not None
    assert [element.kind for element in result.extraction.elements] == ["title", "text", "table"]
    table_element = result.extraction.elements[2]
    assert table_element.content_kind == "table"
    assert table_element.page_number == 2
    assert table_element.bbox == [0.0, 0.0, 50.0, 20.0]
    assert table_element.metadata["bbox_unit"] == "percent"
    assert "bbox_coordinate_mode" not in table_element.metadata
    assert result.extraction.parser_artifacts["adapter_export"] == "structured_elements"
    assert result.extraction.parser_artifacts["adapter_element_count"] == 3
    table = result.extraction.tables[0]
    assert table.page_number == 2
    assert [(cell.row, cell.col, cell.text) for cell in table.cells] == [
        (0, 0, "項目"),
        (0, 1, "金額"),
        (1, 0, "交通費"),
        (1, 1, "1000円"),
    ]


def test_parser_registry_uses_marker_adapter_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marker adapter は LLM 補正を有効化せず markdown を remap する。"""
    marker_module = ModuleType("marker")
    marker_module.__dict__["__version__"] = "4.5.6"
    converters_module = ModuleType("marker.converters")
    pdf_module = ModuleType("marker.converters.pdf")
    models_module = ModuleType("marker.models")
    output_module = ModuleType("marker.output")

    class FakePdfConverter:
        def __init__(self, *, artifact_dict: dict[str, object]) -> None:
            assert artifact_dict == {"model": "fake"}

        def __call__(self, path: str) -> object:
            assert path.endswith(".pdf")
            return object()

    def create_model_dict() -> dict[str, object]:
        return {"model": "fake"}

    def text_from_rendered(rendered: object) -> tuple[str, dict[str, object], dict[str, object]]:
        _ = rendered
        return "# Marker\n本文", {}, {}

    pdf_module.__dict__["PdfConverter"] = FakePdfConverter
    models_module.__dict__["create_model_dict"] = create_model_dict
    output_module.__dict__["text_from_rendered"] = text_from_rendered
    monkeypatch.setitem(sys.modules, "marker", marker_module)
    monkeypatch.setitem(sys.modules, "marker.converters", converters_module)
    monkeypatch.setitem(sys.modules, "marker.converters.pdf", pdf_module)
    monkeypatch.setitem(sys.modules, "marker.models", models_module)
    monkeypatch.setitem(sys.modules, "marker.output", output_module)

    data = b"%PDF"
    profile = build_source_profile(
        original_file_name="sample.pdf",
        sanitized_file_name="sample.pdf",
        content_type="application/pdf",
        file_size_bytes=len(data),
        content_sha256="1" * 64,
        data=data,
    )

    result = parse_with_registry(
        data,
        source_profile=profile,
        content_type=profile.content_type,
        adapter_backend="marker",
        marker_enabled=True,
    )

    assert result.parser_backend == "marker"
    assert result.parser_version == "4.5.6"
    assert result.extraction is not None
    assert "Marker" in result.extraction.raw_text
    assert result.extraction.parser_artifacts["llm_enabled"] is False


def test_marker_adapter_remaps_chunks_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marker chunks は LLM 補正なしで block lineage として remap する。"""
    marker_module = ModuleType("marker")
    marker_module.__dict__["__version__"] = "5.0.0"
    converters_module = ModuleType("marker.converters")
    pdf_module = ModuleType("marker.converters.pdf")
    models_module = ModuleType("marker.models")
    output_module = ModuleType("marker.output")

    class FakeRendered:
        chunks = [
            SimpleNamespace(kind="Title", text="手順書", metadata=SimpleNamespace(page_number=1)),
            SimpleNamespace(kind="Code", text="```sql\nselect 1 from dual;\n```"),
        ]

    class FakePdfConverter:
        def __init__(self, *, artifact_dict: dict[str, object]) -> None:
            assert artifact_dict == {"model": "fake"}

        def __call__(self, path: str) -> object:
            assert path.endswith(".pdf")
            return FakeRendered()

    def create_model_dict() -> dict[str, object]:
        return {"model": "fake"}

    pdf_module.__dict__["PdfConverter"] = FakePdfConverter
    models_module.__dict__["create_model_dict"] = create_model_dict
    monkeypatch.setitem(sys.modules, "marker", marker_module)
    monkeypatch.setitem(sys.modules, "marker.converters", converters_module)
    monkeypatch.setitem(sys.modules, "marker.converters.pdf", pdf_module)
    monkeypatch.setitem(sys.modules, "marker.models", models_module)
    monkeypatch.setitem(sys.modules, "marker.output", output_module)

    data = b"%PDF"
    profile = build_source_profile(
        original_file_name="runbook.pdf",
        sanitized_file_name="runbook.pdf",
        content_type="application/pdf",
        file_size_bytes=len(data),
        content_sha256="4" * 64,
        data=data,
    )

    result = parse_with_registry(
        data,
        source_profile=profile,
        content_type=profile.content_type,
        adapter_backend="marker",
        marker_enabled=True,
    )

    assert result.parser_backend == "marker"
    assert result.extraction is not None
    assert result.extraction.parser_artifacts["llm_enabled"] is False
    assert result.extraction.parser_artifacts["adapter_export"] == "structured_elements"
    assert [element.content_kind for element in result.extraction.elements] == ["text", "code"]
    assert "select 1" in result.extraction.elements[1].text


def test_parser_registry_uses_unstructured_adapter_elements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unstructured elements は page/bbox/content_kind を保持して remap する。"""
    unstructured_module = ModuleType("unstructured")
    unstructured_module.__dict__["__version__"] = "7.8.9"
    partition_package = ModuleType("unstructured.partition")
    auto_module = ModuleType("unstructured.partition.auto")

    class FakeMetadata:
        def __init__(self) -> None:
            self.page_number = 3
            self.coordinates = {"points": [(0.1, 0.2), (0.5, 0.6)]}
            self.detection_class_prob = 0.87

    class FakeNarrativeText:
        id = "narrative-1"
        category = "NarrativeText"
        text = "本文ブロック"
        metadata = FakeMetadata()

    class FakeTable:
        id = "table-1"
        category = "Table"
        text = "| A | B |\n| --- | --- |\n| alpha | 1 |"
        metadata = SimpleNamespace(page_number=4, coordinates=[0, 0, 10, 20])

    def partition(*, filename: str, content_type: str) -> list[object]:
        assert filename.endswith(".pdf")
        assert content_type == "application/pdf"
        return [FakeNarrativeText(), FakeTable()]

    auto_module.__dict__["partition"] = partition
    monkeypatch.setitem(sys.modules, "unstructured", unstructured_module)
    monkeypatch.setitem(sys.modules, "unstructured.partition", partition_package)
    monkeypatch.setitem(sys.modules, "unstructured.partition.auto", auto_module)

    data = b"%PDF"
    profile = build_source_profile(
        original_file_name="sample.pdf",
        sanitized_file_name="sample.pdf",
        content_type="application/pdf",
        file_size_bytes=len(data),
        content_sha256="2" * 64,
        data=data,
    )

    result = parse_with_registry(
        data,
        source_profile=profile,
        content_type=profile.content_type,
        adapter_backend="unstructured",
        unstructured_enabled=True,
    )

    assert result.parser_backend == "unstructured"
    assert result.parser_version == "7.8.9"
    assert result.extraction is not None
    first, second = result.extraction.elements
    assert first.page_number == 3
    assert first.bbox == [0.1, 0.2, 0.5, 0.6]
    assert first.confidence == 0.87
    assert first.content_kind == "text"
    assert first.metadata["bbox_coordinate_mode"] == "xyxy"
    assert first.metadata["bbox_unit"] == "ratio"
    assert second.page_number == 4
    assert second.bbox == [0.0, 0.0, 10.0, 20.0]
    assert second.content_kind == "table"
    assert "bbox_coordinate_mode" not in second.metadata
    assert second.metadata["bbox_unit"] == "percent"
    assert result.extraction.tables
    table = result.extraction.tables[0]
    assert table.table_id == "table-1"
    assert table.element_id == "table-1"
    assert table.page_number == 4
    assert table.metadata["row_count"] == 2
    assert table.metadata["column_count"] == 2
    assert [(cell.row, cell.col, cell.text) for cell in table.cells] == [
        (0, 0, "A"),
        (0, 1, "B"),
        (1, 0, "alpha"),
        (1, 1, "1"),
    ]


def test_parser_registry_marks_audio_as_unsupported_without_transcription() -> None:
    """audio は v1 では明示的な unsupported warning に止める。"""
    data = b"audio"
    profile = build_source_profile(
        original_file_name="voice.mp3",
        sanitized_file_name="voice.mp3",
        content_type="audio/mpeg",
        file_size_bytes=len(data),
        content_sha256="d" * 64,
        data=data,
    )

    result = parse_with_registry(data, source_profile=profile, content_type=profile.content_type)

    assert profile.modality == SourceModality.AUDIO
    assert result.extraction is None
    assert result.parser_backend == "unsupported"
    assert result.unsupported_reason == "audio_transcription_not_configured"
    assert "unsupported_audio" in result.warnings


def _docx_bytes(text: str) -> bytes:
    return _zip_bytes({"word/document.xml": f"<document><t>{text}</t></document>"})


def _docx_table_bytes(*, before: str, rows: list[list[str]], after: str) -> bytes:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    table_rows = []
    for row in rows:
        cells = "".join(f"<w:tc><w:p><w:r><w:t>{value}</w:t></w:r></w:p></w:tc>" for value in row)
        table_rows.append(f"<w:tr>{cells}</w:tr>")
    document = (
        f'<w:document xmlns:w="{namespace}"><w:body>'
        f"<w:p><w:r><w:t>{before}</w:t></w:r></w:p>"
        f"<w:tbl>{''.join(table_rows)}</w:tbl>"
        f"<w:p><w:r><w:t>{after}</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    return _zip_bytes({"word/document.xml": document})


def _pptx_bytes(text: str) -> bytes:
    return _zip_bytes({"ppt/slides/slide1.xml": f"<sld><t>{text}</t></sld>"})


def _pptx_table_bytes(rows: list[list[str]]) -> bytes:
    namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
    table_rows = []
    for row in rows:
        cells = "".join(
            (
                "<a:tc><a:txBody><a:p><a:r>"
                f"<a:t>{value}</a:t>"
                "</a:r></a:p></a:txBody></a:tc>"
            )
            for value in row
        )
        table_rows.append(f"<a:tr>{cells}</a:tr>")
    slide = (
        f'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        f'xmlns:a="{namespace}"><p:cSld><p:spTree><p:graphicFrame>'
        f"<a:tbl>{''.join(table_rows)}</a:tbl>"
        "</p:graphicFrame></p:spTree></p:cSld></p:sld>"
    )
    return _zip_bytes({"ppt/slides/slide1.xml": slide})


def _xlsx_bytes(text: str) -> bytes:
    return _xlsx_table_bytes([[text]])


def _xlsx_table_bytes(rows: list[list[str]]) -> bytes:
    values = [value for row in rows for value in row]
    shared = "<sst>" + "".join(f"<si><t>{value}</t></si>" for value in values) + "</sst>"
    worksheet_rows: list[str] = []
    cursor = 0
    for row in rows:
        cells: list[str] = []
        for _value in row:
            cells.append(f'<c t="s"><v>{cursor}</v></c>')
            cursor += 1
        worksheet_rows.append("<row>" + "".join(cells) + "</row>")
    return _zip_bytes(
        {
            "xl/sharedStrings.xml": shared,
            "xl/worksheets/sheet1.xml": (
                "<worksheet><sheetData>"
                + "".join(worksheet_rows)
                + "</sheetData></worksheet>"
            ),
        }
    )


def _zip_bytes(files: dict[str, str]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return output.getvalue()
