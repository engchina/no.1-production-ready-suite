"""ファイル種別ごとの軽量 parser registry。

Docling / Marker / Unstructured 系の「ファイルタイプ別 partition -> 共通 schema」
という考え方を、本プロジェクトの `StructuredExtraction` へ再マップする。
外部 parser は任意依存として扱い、feature flag 有効時だけ呼び出す。
得られた出力は必ず本プロジェクトの `StructuredExtraction` へ再マップする。
"""

from __future__ import annotations

import csv
import html
import importlib
import importlib.util
import re
import sys
import tempfile
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from io import BytesIO, StringIO
from pathlib import Path, PurePath
from typing import Any, cast
from xml.etree import ElementTree

from charset_normalizer import from_bytes

from app.schemas.document import SourceModality, SourceProfile
from app.schemas.extraction import (
    DocumentElement,
    ExtractionAsset,
    ExtractionMetadataValue,
    ExtractionTable,
    ExtractionTableCell,
    StructuredExtraction,
)

LOCAL_PARSER_VERSION = "local_partition_v1"
TABLE_PRESERVE_ROWS_TEMPLATE = "table_preserve_rows"
EXTERNAL_ADAPTER_PACKAGES = {
    "docling": "docling",
    "marker": "marker",
    "unstructured": "unstructured",
}


@dataclass(frozen=True)
class ParserRegistryResult:
    """parser registry の結果。None extraction は Enterprise AI fallback を意味する。"""

    extraction: StructuredExtraction | None
    parser_backend: str
    parser_version: str = LOCAL_PARSER_VERSION
    fallback_used: bool = False
    template: str = "enterprise_ai_fallback"
    warnings: tuple[str, ...] = ()
    unsupported_reason: str | None = None


@dataclass(frozen=True)
class OfficeSegmentExtraction:
    """OpenXML Office の slide/sheet 単位の抽出結果。"""

    segment_kind: str
    number: int
    source_path: str
    extraction: StructuredExtraction


@dataclass(frozen=True)
class OfficeSegmentFailure:
    """OpenXML Office の slide/sheet 単位 parser 失敗。"""

    segment_kind: str
    number: int
    source_path: str
    error_code: str = "office_segment_parse_failed"


@dataclass(frozen=True)
class OfficeSegmentParseResult:
    """OpenXML Office segment parser の成功/失敗一覧。"""

    segments: tuple[OfficeSegmentExtraction, ...] = ()
    failures: tuple[OfficeSegmentFailure, ...] = ()


@dataclass(frozen=True)
class _OfficeTableElementMatch:
    """Office table element と tables[] の lineage metadata 対応。"""

    element_id: str | None
    page_number: int | None
    metadata: dict[str, ExtractionMetadataValue]


@dataclass(frozen=True)
class _HTMLBlock:
    """HTML block parser が抽出した読み順 block。"""

    tag: str
    text: str


@dataclass(frozen=True)
class _HTMLTable:
    """HTML table parser が抽出した行列。"""

    rows: tuple[tuple[str, ...], ...]


class _TextHTMLParser(HTMLParser):
    """HTML から読み順に近い text block を抽出する。"""

    BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "p",
        "section",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._blocks: list[_HTMLBlock] = []
        self._current_tag: str | None = None
        self._buffer: list[str] = []
        self._tables: list[_HTMLTable] = []
        self._table_depth = 0
        self._table_rows: list[list[str]] | None = None
        self._table_row: list[str] | None = None
        self._table_cell_buffer: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        normalized = tag.lower()
        if normalized == "table":
            self._flush_block()
            self._table_depth += 1
            if self._table_depth == 1:
                self._table_rows = []
            return
        if self._table_depth > 0:
            if normalized == "tr":
                self._flush_table_cell()
                self._flush_table_row()
                self._table_row = []
            elif normalized in {"td", "th"}:
                self._flush_table_cell()
                self._table_cell_buffer = []
            return
        if normalized in self.BLOCK_TAGS:
            self._flush_block()
            self._current_tag = normalized
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if self._table_depth > 0:
            if normalized in {"td", "th"}:
                self._flush_table_cell()
            elif normalized == "tr":
                self._flush_table_cell()
                self._flush_table_row()
            elif normalized == "table":
                self._flush_table_cell()
                self._flush_table_row()
                if self._table_depth == 1:
                    self._flush_table()
                self._table_depth = max(0, self._table_depth - 1)
            return
        if normalized in self.BLOCK_TAGS:
            self._flush_block()
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        cleaned = re.sub(r"\s+", " ", html.unescape(data)).strip()
        if cleaned:
            if self._table_depth > 0 and self._table_cell_buffer is not None:
                self._table_cell_buffer.append(cleaned)
                return
            self._parts.append(cleaned)
            if self._current_tag is None:
                self._current_tag = "text"
            self._buffer.append(cleaned)

    def text(self) -> str:
        return _clean_text("\n".join(self._parts))

    def blocks(self) -> tuple[_HTMLBlock, ...]:
        self._flush_block()
        return tuple(self._blocks)

    def tables(self) -> tuple[_HTMLTable, ...]:
        self._flush_table_cell()
        self._flush_table_row()
        self._flush_table()
        return tuple(self._tables)

    def _flush_block(self) -> None:
        if not self._buffer:
            self._current_tag = None
            return
        text = _clean_text(" ".join(self._buffer))
        if text:
            self._blocks.append(_HTMLBlock(tag=self._current_tag or "text", text=text))
        self._buffer = []
        self._current_tag = None

    def _flush_table_cell(self) -> None:
        if self._table_cell_buffer is None:
            return
        text = _clean_table_cell(" ".join(self._table_cell_buffer))
        if text and self._table_row is not None:
            self._table_row.append(text)
        self._table_cell_buffer = None

    def _flush_table_row(self) -> None:
        if self._table_row is None:
            return
        if self._table_rows is not None and any(cell.strip() for cell in self._table_row):
            self._table_rows.append(self._table_row)
        self._table_row = None

    def _flush_table(self) -> None:
        if not self._table_rows:
            self._table_rows = None
            return
        if max((len(row) for row in self._table_rows), default=0) > 1:
            rows = tuple(tuple(cell for cell in row) for row in self._table_rows)
            self._tables.append(_HTMLTable(rows=rows))
            self._blocks.append(
                _HTMLBlock(tag="table", text="\n".join(_xlsx_markdown_row(row) for row in rows))
            )
        self._table_rows = None


def parse_with_registry(
    source_bytes: bytes,
    *,
    source_profile: SourceProfile | None,
    content_type: str,
    adapter_backend: str = "local",
    docling_enabled: bool = False,
    marker_enabled: bool = False,
    unstructured_enabled: bool = False,
) -> ParserRegistryResult:
    """source profile に基づき、ローカル parser で処理できる場合は抽出する。"""
    modality = source_profile.modality if source_profile is not None else SourceModality.UNKNOWN
    if modality == SourceModality.AUDIO:
        return ParserRegistryResult(
            extraction=None,
            parser_backend="unsupported",
            template="unsupported_audio",
            warnings=("unsupported_audio",),
            unsupported_reason="audio_transcription_not_configured",
        )
    if _is_unsupported_outlook_msg(source_profile):
        return ParserRegistryResult(
            extraction=None,
            parser_backend="unsupported",
            template="unsupported_outlook_msg",
            warnings=("unsupported_outlook_msg",),
            unsupported_reason="outlook_msg_not_supported",
        )
    if _is_unsupported_tiff_image(source_profile):
        return ParserRegistryResult(
            extraction=None,
            parser_backend="unsupported",
            template="unsupported_tiff_image",
            warnings=("unsupported_tiff_image",),
            unsupported_reason="tiff_image_not_supported",
        )
    if _is_unsupported_legacy_office_binary(source_profile):
        return ParserRegistryResult(
            extraction=None,
            parser_backend="unsupported",
            template="unsupported_legacy_office_binary",
            warnings=("unsupported_legacy_office_binary",),
            unsupported_reason="legacy_office_binary_not_supported",
        )

    adapter_warnings = _external_adapter_disabled_warnings(
        adapter_backend=adapter_backend,
        docling_enabled=docling_enabled,
        marker_enabled=marker_enabled,
        unstructured_enabled=unstructured_enabled,
    )
    adapter_fallback_used = bool(adapter_warnings)
    for backend in _requested_external_adapters(
        adapter_backend=adapter_backend,
        docling_enabled=docling_enabled,
        marker_enabled=marker_enabled,
        unstructured_enabled=unstructured_enabled,
    ):
        adapter_result = _external_adapter_result(
            backend,
            source_bytes=source_bytes,
            source_profile=source_profile,
            content_type=content_type,
        )
        if adapter_result.extraction is not None:
            return _with_adapter_fallback_context(
                adapter_result,
                adapter_warnings=adapter_warnings,
                adapter_fallback_used=adapter_fallback_used,
            )
        adapter_fallback_used = adapter_fallback_used or adapter_result.fallback_used
        adapter_warnings = tuple(dict.fromkeys([*adapter_warnings, *adapter_result.warnings]))

    if modality == SourceModality.TEXT:
        return _with_adapter_fallback_context(
            _text_result(
                source_bytes,
                parser_profile="local_text_structure",
                source_profile=source_profile,
                content_type=content_type,
            ),
            adapter_warnings=adapter_warnings,
            adapter_fallback_used=adapter_fallback_used,
        )
    if modality == SourceModality.HTML:
        return _with_adapter_fallback_context(
            _html_result(source_bytes),
            adapter_warnings=adapter_warnings,
            adapter_fallback_used=adapter_fallback_used,
        )
    if modality == SourceModality.EMAIL:
        return _with_adapter_fallback_context(
            _email_result(source_bytes),
            adapter_warnings=adapter_warnings,
            adapter_fallback_used=adapter_fallback_used,
        )
    if modality == SourceModality.OFFICE:
        result = _office_result(source_bytes, source_profile=source_profile)
        if result.extraction is not None:
            return _with_adapter_fallback_context(
                result,
                adapter_warnings=adapter_warnings,
                adapter_fallback_used=adapter_fallback_used,
            )
        return _with_adapter_fallback_context(
            ParserRegistryResult(
                extraction=None,
                parser_backend="enterprise_ai",
                fallback_used=True,
                template="office_enterprise_ai_fallback",
                warnings=result.warnings or ("office_local_parse_failed",),
            ),
            adapter_warnings=adapter_warnings,
            adapter_fallback_used=adapter_fallback_used,
        )
    _ = content_type
    return _with_adapter_fallback_context(
        ParserRegistryResult(extraction=None, parser_backend="enterprise_ai"),
        adapter_warnings=adapter_warnings,
        adapter_fallback_used=adapter_fallback_used,
    )


def _requested_external_adapters(
    *,
    adapter_backend: str,
    docling_enabled: bool,
    marker_enabled: bool,
    unstructured_enabled: bool,
) -> tuple[str, ...]:
    normalized = adapter_backend.strip().casefold()
    if normalized in EXTERNAL_ADAPTER_PACKAGES:
        if _external_adapter_flag_enabled(
            normalized,
            docling_enabled=docling_enabled,
            marker_enabled=marker_enabled,
            unstructured_enabled=unstructured_enabled,
        ):
            return (normalized,)
        return ()
    if normalized != "auto":
        return ()
    requested: list[str] = []
    if docling_enabled:
        requested.append("docling")
    if marker_enabled:
        requested.append("marker")
    if unstructured_enabled:
        requested.append("unstructured")
    return tuple(requested)


def _external_adapter_disabled_warnings(
    *,
    adapter_backend: str,
    docling_enabled: bool,
    marker_enabled: bool,
    unstructured_enabled: bool,
) -> tuple[str, ...]:
    normalized = adapter_backend.strip().casefold()
    if normalized not in EXTERNAL_ADAPTER_PACKAGES:
        return ()
    if _external_adapter_flag_enabled(
        normalized,
        docling_enabled=docling_enabled,
        marker_enabled=marker_enabled,
        unstructured_enabled=unstructured_enabled,
    ):
        return ()
    return (f"{normalized}_adapter_feature_flag_disabled",)


def _external_adapter_flag_enabled(
    backend: str,
    *,
    docling_enabled: bool,
    marker_enabled: bool,
    unstructured_enabled: bool,
) -> bool:
    return {
        "docling": docling_enabled,
        "marker": marker_enabled,
        "unstructured": unstructured_enabled,
    }.get(backend, False)


def _external_adapter_result(
    backend: str,
    *,
    source_bytes: bytes,
    source_profile: SourceProfile | None,
    content_type: str,
) -> ParserRegistryResult:
    """任意 parser adapter を呼び出し、失敗時は fallback 用 warning だけ返す。"""
    package = EXTERNAL_ADAPTER_PACKAGES[backend]
    if not _module_available(package):
        return _adapter_fallback_result(backend, f"{backend}_adapter_package_missing")
    try:
        if backend == "docling":
            return _docling_adapter_result(
                source_bytes,
                source_profile=source_profile,
                content_type=content_type,
            )
        if backend == "marker":
            return _marker_adapter_result(
                source_bytes,
                source_profile=source_profile,
                content_type=content_type,
            )
        if backend == "unstructured":
            return _unstructured_adapter_result(
                source_bytes,
                source_profile=source_profile,
                content_type=content_type,
            )
    except Exception:
        return _adapter_fallback_result(backend, f"{backend}_adapter_failed")
    return _adapter_fallback_result(backend, f"{backend}_adapter_unsupported")


def _module_available(name: str) -> bool:
    """テスト用 fake module と通常インストールの両方を安全に検出する。"""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _adapter_fallback_result(backend: str, warning: str) -> ParserRegistryResult:
    return ParserRegistryResult(
        extraction=None,
        parser_backend=backend,
        parser_version=_adapter_version(backend),
        fallback_used=True,
        template=f"{backend}_fallback",
        warnings=(warning,),
    )


def _docling_adapter_result(
    source_bytes: bytes,
    *,
    source_profile: SourceProfile | None,
    content_type: str,
) -> ParserRegistryResult:
    """Docling の document export を共通抽出 schema へ再マップする。"""
    converter_module = importlib.import_module("docling.document_converter")
    converter_type = converter_module.DocumentConverter
    with _temporary_source_file(source_bytes, source_profile, content_type) as path:
        converted = converter_type().convert(str(path))
    document = getattr(converted, "document", converted)
    structured_elements = _adapter_child_elements(document)
    text, export_kind = _export_adapter_text(
        document,
        preferred_methods=("export_to_markdown", "export_to_text"),
    )
    if not structured_elements and not text.strip():
        return _adapter_fallback_result("docling", "docling_adapter_empty")
    version = _adapter_version("docling")
    artifacts = {
        "adapter_export": "structured_elements" if structured_elements else export_kind,
        "external_adapter": "docling",
        **_docling_artifacts(document),
    }
    if structured_elements:
        extraction = _structured_from_adapter_elements(
            structured_elements,
            document_type=_document_type_for_source(source_profile),
            source_parser="docling_adapter",
            template=template_for_source_profile(source_profile),
            parser_backend="docling",
            parser_version=version,
            extra_artifacts=artifacts,
        )
    else:
        extraction = _structured_from_text(
            text,
            document_type=_document_type_for_source(source_profile),
            source_parser="docling_adapter",
            template=template_for_source_profile(source_profile),
            default_content_kind=_default_content_kind_for_source(source_profile),
            parser_backend="docling",
            parser_version=version,
            extra_artifacts=artifacts,
        )
    return ParserRegistryResult(
        extraction=extraction,
        parser_backend="docling",
        parser_version=version,
        template=template_for_source_profile(source_profile),
    )


def _marker_adapter_result(
    source_bytes: bytes,
    *,
    source_profile: SourceProfile | None,
    content_type: str,
) -> ParserRegistryResult:
    """Marker の markdown/chunk 変換結果を共通抽出 schema へ再マップする。

    Marker の LLM 補正モードは使わない。parser engine としてのローカル変換だけを
    呼び出すことで、非 OCI LLM provider を混ぜない。
    """
    converter_module = importlib.import_module("marker.converters.pdf")
    models_module = importlib.import_module("marker.models")
    output_module = importlib.import_module("marker.output")
    converter_type = converter_module.PdfConverter
    create_model_dict = models_module.create_model_dict
    text_from_rendered = getattr(output_module, "text_from_rendered", None)

    with _temporary_source_file(source_bytes, source_profile, content_type) as path:
        converter = converter_type(artifact_dict=create_model_dict())
        rendered = converter(str(path))
    structured_elements = _adapter_child_elements(rendered)
    if structured_elements:
        text = ""
        export_kind = "structured_elements"
    elif callable(text_from_rendered):
        exported = text_from_rendered(rendered)
        text = exported[0] if isinstance(exported, tuple | list) else exported
        export_kind = "text_from_rendered"
    else:
        text, export_kind = _export_adapter_text(
            rendered,
            preferred_methods=("markdown", "text", "raw_text"),
        )
    if not structured_elements and (not isinstance(text, str) or not text.strip()):
        return _adapter_fallback_result("marker", "marker_adapter_empty")
    version = _adapter_version("marker")
    artifacts: dict[str, ExtractionMetadataValue] = {
        "adapter_export": export_kind,
        "external_adapter": "marker",
        "llm_enabled": False,
    }
    if structured_elements:
        extraction = _structured_from_adapter_elements(
            structured_elements,
            document_type=_document_type_for_source(source_profile),
            source_parser="marker_adapter",
            template=template_for_source_profile(source_profile),
            parser_backend="marker",
            parser_version=version,
            extra_artifacts=artifacts,
        )
    else:
        extraction = _structured_from_text(
            text,
            document_type=_document_type_for_source(source_profile),
            source_parser="marker_adapter",
            template=template_for_source_profile(source_profile),
            default_content_kind=_default_content_kind_for_source(source_profile),
            parser_backend="marker",
            parser_version=version,
            extra_artifacts=artifacts,
        )
    return ParserRegistryResult(
        extraction=extraction,
        parser_backend="marker",
        parser_version=version,
        template=template_for_source_profile(source_profile),
    )


def _unstructured_adapter_result(
    source_bytes: bytes,
    *,
    source_profile: SourceProfile | None,
    content_type: str,
) -> ParserRegistryResult:
    """Unstructured の partition elements を共通抽出 schema へ再マップする。"""
    partition_module = importlib.import_module("unstructured.partition.auto")
    partition = partition_module.partition
    with _temporary_source_file(source_bytes, source_profile, content_type) as path:
        partitioned = partition(filename=str(path), content_type=content_type)
    elements = list(partitioned or [])
    if not elements:
        return _adapter_fallback_result("unstructured", "unstructured_adapter_empty")
    version = _adapter_version("unstructured")
    extraction = _structured_from_adapter_elements(
        elements,
        document_type=_document_type_for_source(source_profile),
        source_parser="unstructured_adapter",
        template=template_for_source_profile(source_profile),
        parser_backend="unstructured",
        parser_version=version,
    )
    return ParserRegistryResult(
        extraction=extraction,
        parser_backend="unstructured",
        parser_version=version,
        template=template_for_source_profile(source_profile),
    )


def _with_adapter_fallback_context(
    result: ParserRegistryResult,
    *,
    adapter_warnings: tuple[str, ...],
    adapter_fallback_used: bool,
) -> ParserRegistryResult:
    fallback_used = result.fallback_used or adapter_fallback_used
    if not adapter_warnings and fallback_used == result.fallback_used:
        return result
    return ParserRegistryResult(
        extraction=result.extraction,
        parser_backend=result.parser_backend,
        parser_version=result.parser_version,
        fallback_used=fallback_used,
        template=result.template,
        warnings=tuple(dict.fromkeys([*result.warnings, *adapter_warnings])),
        unsupported_reason=result.unsupported_reason,
    )


def _text_result(
    source_bytes: bytes,
    *,
    parser_profile: str,
    source_profile: SourceProfile | None = None,
    content_type: str = "",
) -> ParserRegistryResult:
    text = _decode_text_bytes(source_bytes)
    if _is_delimited_table_source(source_profile=source_profile, content_type=content_type):
        delimited = _delimited_table_extraction(
            text,
            source_profile=source_profile,
            content_type=content_type,
            parser_profile=parser_profile,
        )
        if delimited is not None:
            return ParserRegistryResult(
                extraction=delimited,
                parser_backend="local_partition",
                template="table_preserve_rows",
            )
    extraction = _structured_from_text(
        text,
        document_type="テキスト文書",
        source_parser=parser_profile,
        template="markdown_by_heading" if _looks_like_markdown(text) else "text_blocks",
    )
    return ParserRegistryResult(
        extraction=extraction,
        parser_backend="local_partition",
        template="markdown_by_heading" if _looks_like_markdown(text) else "text_blocks",
    )


def _html_result(source_bytes: bytes) -> ParserRegistryResult:
    parser = _TextHTMLParser()
    parser.feed(_decode_text_bytes(source_bytes))
    extraction = _structured_from_html_blocks(parser.blocks(), parser.tables())
    return ParserRegistryResult(
        extraction=extraction,
        parser_backend="local_partition",
        template="html_semantic",
    )


def _structured_from_html_blocks(
    blocks: Sequence[_HTMLBlock],
    tables: Sequence[_HTMLTable] = (),
) -> StructuredExtraction:
    """HTML heading / section lineage を保持する構造化抽出へ変換する。"""
    elements: list[DocumentElement] = []
    extraction_tables: list[ExtractionTable] = []
    raw_parts: list[str] = []
    section_path: list[str] = []
    table_index = 0
    for order, block in enumerate(blocks):
        if block.tag == "table":
            if table_index >= len(tables):
                continue
            table = tables[table_index]
            table_index += 1
            table_id = f"html-table-{table_index - 1:04d}"
            markdown = "\n".join(_xlsx_markdown_row(row) for row in table.rows)
            if not markdown.strip():
                continue
            row_count = len(table.rows)
            column_count = max((len(row) for row in table.rows), default=0)
            metadata: dict[str, ExtractionMetadataValue] = {
                "source_parser": "local_html_semantic",
                "parser_backend": "local_partition",
                "parser_version": LOCAL_PARSER_VERSION,
                "chunk_template": "html_semantic",
                "html_tag": "table",
                "row_count": row_count,
                "column_count": column_count,
            }
            raw_parts.append(markdown)
            elements.append(
                DocumentElement(
                    kind="table",
                    text=markdown,
                    order=len(elements),
                    element_id=table_id,
                    content_kind="table",
                    source_parser="local_html_semantic",
                    page_number=1,
                    section_path=list(section_path),
                    confidence=1.0,
                    metadata=metadata,
                )
            )
            extraction_tables.append(
                ExtractionTable(
                    table_id=table_id,
                    element_id=table_id,
                    page_number=1,
                    cells=_table_cells_from_rows(table.rows),
                    metadata=metadata,
                )
            )
            continue
        heading_level = _html_heading_level(block.tag)
        if heading_level is not None:
            section_path = section_path[: heading_level - 1]
            section_path.append(block.text)
            kind = "title"
            raw_parts.append(f"{'#' * heading_level} {block.text}")
        else:
            kind = "list" if block.tag == "li" else "text"
            raw_parts.append(block.text)
        elements.append(
            DocumentElement(
                kind=kind,
                text=block.text,
                order=order,
                element_id=f"html-{order:04d}",
                content_kind="text",
                source_parser="local_html_semantic",
                page_number=1,
                section_path=list(section_path),
                confidence=1.0,
                metadata={
                    "source_parser": "local_html_semantic",
                    "parser_backend": "local_partition",
                    "chunk_template": "html_semantic",
                    "html_tag": block.tag,
                },
            )
        )
    return StructuredExtraction(
        raw_text=_clean_text("\n\n".join(raw_parts)),
        document_type="HTML",
        confidence=1.0 if elements else 0.0,
        elements=elements,
        tables=extraction_tables,
        parser_artifacts={
            "source_parser": "local_html_semantic",
            "chunk_template": "html_semantic",
            "parser_backend": "local_partition",
            "parser_version": LOCAL_PARSER_VERSION,
            "html_block_count": len(elements),
            "table_count": len(extraction_tables),
        },
    )


def _html_heading_level(tag: str) -> int | None:
    if len(tag) == 2 and tag.startswith("h") and tag[1].isdigit():
        level = int(tag[1])
        if 1 <= level <= 6:
            return level
    return None


def _email_result(source_bytes: bytes) -> ParserRegistryResult:
    message = BytesParser(policy=policy.default).parsebytes(source_bytes)
    header_values = _email_header_values(message)
    headers = [f"{label}: {value}" for label, value in header_values.items()]
    body = _email_body_text(message)
    text = _clean_text("\n".join([*headers, "", body]))
    attachments = list(message.iter_attachments())
    elements = _email_elements(headers=headers, body=body, header_values=header_values)
    assets = _email_attachment_assets(attachments)
    extraction = StructuredExtraction(
        raw_text=text,
        document_type="メール",
        confidence=1.0 if text else 0.0,
        elements=elements,
        assets=assets,
        parser_artifacts={
            "source_parser": "local_email_thread",
            "chunk_template": "email_thread",
            "parser_backend": "local_partition",
            "parser_version": LOCAL_PARSER_VERSION,
            "subject_chars": len(header_values["Subject"]),
            "attachment_count": len(assets),
            "has_body": bool(body.strip()),
        },
    )
    return ParserRegistryResult(
        extraction=extraction,
        parser_backend="local_partition",
        template="email_thread",
    )


def _email_header_values(message: object) -> dict[str, str]:
    return {
        "Subject": _email_header_value(message, "subject"),
        "From": _email_header_value(message, "from"),
        "To": _email_header_value(message, "to"),
        "Date": _email_header_value(message, "date"),
    }


def _email_header_value(message: object, key: str) -> str:
    get_header = getattr(message, "get", None)
    if callable(get_header):
        value = get_header(key, "")
        return str(value or "").strip()
    return ""


def _email_elements(
    *,
    headers: Sequence[str],
    body: str,
    header_values: Mapping[str, str],
) -> list[DocumentElement]:
    metadata = {
        "source_parser": "local_email_thread",
        "parser_backend": "local_partition",
        "chunk_template": "email_thread",
    }
    elements = [
        DocumentElement(
            kind="text",
            text=_clean_text("\n".join(headers)),
            order=0,
            element_id="email-headers",
            content_kind="email",
            source_parser="local_email_thread",
            page_number=1,
            confidence=1.0,
            metadata={
                **metadata,
                "email_part": "headers",
                "subject_chars": len(header_values.get("Subject", "")),
                "from_present": bool(header_values.get("From")),
                "to_present": bool(header_values.get("To")),
                "date_present": bool(header_values.get("Date")),
            },
        )
    ]
    if body.strip():
        elements.append(
            DocumentElement(
                kind="text",
                text=_clean_text(body),
                order=1,
                element_id="email-body",
                content_kind="email",
                source_parser="local_email_thread",
                page_number=1,
                confidence=1.0,
                metadata={
                    **metadata,
                    "email_part": "body",
                },
            )
        )
    return elements


def _email_attachment_assets(attachments: Sequence[object]) -> list[ExtractionAsset]:
    assets: list[ExtractionAsset] = []
    for index, attachment in enumerate(attachments):
        filename = _email_attachment_filename(attachment, index)
        content_type = _email_attachment_content_type(attachment)
        size_bytes = _email_attachment_size_bytes(attachment)
        metadata: dict[str, ExtractionMetadataValue] = {
            "source_parser": "local_email_thread",
            "parser_backend": "local_partition",
            "chunk_template": "email_thread",
            "attachment_index": index,
            "file_name": filename,
            "content_type": content_type,
        }
        if size_bytes is not None:
            metadata["size_bytes"] = size_bytes
        assets.append(
            ExtractionAsset(
                asset_id=f"email-attachment-{index:04d}",
                kind="email_attachment",
                page_number=1,
                alt_text=filename,
                metadata=metadata,
            )
        )
    return assets


def _email_attachment_filename(attachment: object, index: int) -> str:
    get_filename = getattr(attachment, "get_filename", None)
    if callable(get_filename):
        filename = get_filename()
        if isinstance(filename, str) and filename.strip():
            return filename.strip()[:255]
    return f"attachment-{index + 1}"


def _email_attachment_content_type(attachment: object) -> str:
    get_content_type = getattr(attachment, "get_content_type", None)
    if callable(get_content_type):
        content_type = get_content_type()
        if isinstance(content_type, str) and content_type.strip():
            return content_type.strip()[:128]
    return "application/octet-stream"


def _email_attachment_size_bytes(attachment: object) -> int | None:
    get_payload = getattr(attachment, "get_payload", None)
    if callable(get_payload):
        payload = get_payload(decode=True)
        if isinstance(payload, bytes):
            return len(payload)
    get_content = getattr(attachment, "get_content", None)
    if callable(get_content):
        try:
            content = get_content()
        except Exception:
            return None
        if isinstance(content, bytes):
            return len(content)
        if isinstance(content, str):
            return len(content.encode("utf-8"))
    return None


def _office_result(
    source_bytes: bytes,
    *,
    source_profile: SourceProfile | None,
) -> ParserRegistryResult:
    office_kind = _office_kind(source_profile)
    tables: list[ExtractionTable] = []
    if office_kind is None:
        return ParserRegistryResult(
            extraction=None,
            parser_backend="enterprise_ai",
            fallback_used=True,
            warnings=("unsupported_legacy_office_binary",),
        )
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as archive:
            if office_kind == "docx":
                text, tables = _office_docx_text_and_tables(archive)
                template = "office_document"
                document_type = "Word 文書"
                content_kind = "text"
            elif office_kind == "pptx":
                text, tables = _office_pptx_text_and_tables(archive)
                template = "office_slide"
                document_type = "PowerPoint"
                content_kind = "slide"
            else:
                text, tables = _office_xlsx_text_and_tables(archive)
                template = "office_sheet"
                document_type = "Excel"
                content_kind = "sheet"
    except Exception:
        return ParserRegistryResult(
            extraction=None,
            parser_backend="enterprise_ai",
            fallback_used=True,
            warnings=("office_local_parse_failed",),
        )
    if not text.strip():
        return ParserRegistryResult(
            extraction=None,
            parser_backend="enterprise_ai",
            fallback_used=True,
            warnings=("office_local_parse_empty",),
        )
    extraction = _structured_from_text(
        text,
        document_type=document_type,
        source_parser="local_office_structure",
        template=template,
        default_content_kind=content_kind,
    )
    if tables:
        table_matches = _office_table_element_matches(tables)
        elements: list[DocumentElement] = []
        for element in extraction.elements:
            metadata = dict(element.metadata)
            update: dict[str, object] = {"metadata": metadata}
            if element.content_kind == "table" or element.kind == "table":
                match = table_matches.get(element.text.strip())
                if match is not None:
                    metadata.update(match.metadata)
                    if match.element_id:
                        update["element_id"] = match.element_id
                    if match.page_number is not None:
                        update["page_number"] = match.page_number
            elements.append(element.model_copy(update=update))
        extraction = extraction.model_copy(
            update={
                "elements": elements,
                "tables": tables,
                "parser_artifacts": {
                    **extraction.parser_artifacts,
                    "table_count": len(tables),
                },
            }
        )
    return ParserRegistryResult(
        extraction=extraction,
        parser_backend="local_partition",
        template=template,
    )


def parse_openxml_office_segment_extractions(
    source_bytes: bytes,
    *,
    source_profile: SourceProfile | None,
) -> OfficeSegmentParseResult:
    """OpenXML PPTX/XLSX を slide/sheet 単位の StructuredExtraction にする。"""
    office_kind = _office_kind(source_profile)
    if office_kind not in {"pptx", "xlsx"}:
        return OfficeSegmentParseResult()
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as archive:
            if office_kind == "pptx":
                return _pptx_segment_parse_result(archive)
            return _xlsx_segment_parse_result(archive)
    except Exception:
        return OfficeSegmentParseResult()


def _office_kind(source_profile: SourceProfile | None) -> str | None:
    """拡張子または MIME type から OpenXML Office 種別を返す。"""
    extension = (source_profile.extension if source_profile is not None else "") or ""
    content_type = (source_profile.content_type if source_profile is not None else "") or ""
    if extension == ".docx" or content_type.endswith("officedocument.wordprocessingml.document"):
        return "docx"
    if extension == ".pptx" or content_type.endswith("officedocument.presentationml.presentation"):
        return "pptx"
    if extension == ".xlsx" or content_type.endswith("officedocument.spreadsheetml.sheet"):
        return "xlsx"
    return None


def _pptx_segment_parse_result(
    archive: zipfile.ZipFile,
) -> OfficeSegmentParseResult:
    segments: list[OfficeSegmentExtraction] = []
    failures: list[OfficeSegmentFailure] = []
    for number, name in _openxml_numbered_members(archive, r"ppt/slides/slide(\d+)\.xml"):
        try:
            text, tables = _office_pptx_slide_text_and_tables(
                archive,
                name,
                slide_number=number,
            )
        except Exception:
            failures.append(
                OfficeSegmentFailure(
                    segment_kind="slide",
                    number=number,
                    source_path=name,
                )
            )
            continue
        if not text.strip():
            continue
        segments.append(
            OfficeSegmentExtraction(
                segment_kind="slide",
                number=number,
                source_path=name,
                extraction=_structured_office_segment(
                    text,
                    number=number,
                    source_path=name,
                    document_type="PowerPoint",
                    template="office_slide",
                    content_kind="slide",
                    tables=tables,
                ),
            )
        )
    return OfficeSegmentParseResult(segments=tuple(segments), failures=tuple(failures))


def _xlsx_segment_parse_result(
    archive: zipfile.ZipFile,
) -> OfficeSegmentParseResult:
    try:
        shared_strings = _xlsx_shared_strings(archive)
    except Exception:
        shared_strings = {}
    segments: list[OfficeSegmentExtraction] = []
    failures: list[OfficeSegmentFailure] = []
    for number, name in _openxml_numbered_members(archive, r"xl/worksheets/sheet(\d+)\.xml"):
        try:
            text, table = _office_xlsx_sheet_text_and_table(
                archive,
                name,
                shared_strings=shared_strings,
                sheet_number=number,
            )
        except Exception:
            failures.append(
                OfficeSegmentFailure(
                    segment_kind="sheet",
                    number=number,
                    source_path=name,
                )
            )
            continue
        if not text.strip():
            continue
        segments.append(
            OfficeSegmentExtraction(
                segment_kind="sheet",
                number=number,
                source_path=name,
                extraction=_structured_office_segment(
                    text,
                    number=number,
                    source_path=name,
                    document_type="Excel",
                    template="office_sheet",
                    content_kind="sheet",
                    tables=[table] if table is not None else [],
                ),
            )
        )
    return OfficeSegmentParseResult(segments=tuple(segments), failures=tuple(failures))


def _openxml_numbered_members(
    archive: zipfile.ZipFile,
    pattern: str,
) -> list[tuple[int, str]]:
    regex = re.compile(pattern)
    members: list[tuple[int, str]] = []
    for name in archive.namelist():
        match = regex.fullmatch(name)
        if match:
            members.append((int(match.group(1)), name))
    return sorted(members)


def _structured_office_segment(
    text: str,
    *,
    number: int,
    source_path: str,
    document_type: str,
    template: str,
    content_kind: str,
    tables: Sequence[ExtractionTable] = (),
) -> StructuredExtraction:
    extraction = _structured_from_text(
        text,
        document_type=document_type,
        source_parser="local_office_structure",
        template=template,
        default_content_kind=content_kind,
        extra_artifacts={
            "office_segment_number": number,
            "office_segment_path": source_path,
        },
    )
    table_matches = _office_table_element_matches(tables)
    elements: list[DocumentElement] = []
    for element in extraction.elements:
        metadata = {
            **element.metadata,
            "office_segment_number": number,
            "office_segment_path": source_path,
        }
        update: dict[str, object] = {
            "page_number": number,
            "metadata": metadata,
        }
        if element.content_kind == "table" or element.kind == "table":
            match = table_matches.get(element.text.strip())
            if match is not None:
                metadata.update(match.metadata)
                if match.element_id:
                    update["element_id"] = match.element_id
        elements.append(element.model_copy(update=update))
    payload = extraction.to_document_payload()
    payload["elements"] = [element.to_payload() for element in elements]
    payload["tables"] = [table.model_dump(exclude_none=True) for table in tables]
    payload["pages"] = []
    return StructuredExtraction.model_validate(payload)


def _office_table_element_matches(
    tables: Sequence[ExtractionTable],
) -> dict[str, _OfficeTableElementMatch]:
    """tables[] を inferred table element metadata へ戻すための lookup を作る。"""
    matches: dict[str, _OfficeTableElementMatch] = {}
    for table in tables:
        markdown = _table_markdown_from_cells(table.cells)
        if not markdown:
            continue
        row_count, column_count = _table_shape_from_cells(table.cells)
        metadata = dict(table.metadata)
        metadata["table_id"] = table.table_id
        if table.element_id:
            metadata["element_id"] = table.element_id
        configured_row_count = _int_value(metadata.get("row_count"))
        configured_column_count = _int_value(metadata.get("column_count"))
        metadata["row_count"] = configured_row_count or row_count
        metadata["column_count"] = configured_column_count or column_count
        matches[markdown] = _OfficeTableElementMatch(
            element_id=table.element_id or table.table_id,
            page_number=table.page_number,
            metadata=metadata,
        )
    return matches


def _table_markdown_from_cells(cells: Sequence[ExtractionTableCell]) -> str:
    rows_by_index: dict[int, list[tuple[int, str]]] = {}
    for cell in cells:
        rows_by_index.setdefault(cell.row, []).append((cell.col, cell.text))
    rows: list[list[str]] = []
    for row_index in sorted(rows_by_index):
        ordered_cells = [text for _col, text in sorted(rows_by_index[row_index])]
        if any(value.strip() for value in ordered_cells):
            rows.append(ordered_cells)
    return "\n".join(_xlsx_markdown_row(row) for row in rows)


def _table_shape_from_cells(cells: Sequence[ExtractionTableCell]) -> tuple[int, int]:
    if not cells:
        return 0, 0
    return max(cell.row for cell in cells) + 1, max(cell.col for cell in cells) + 1


def _structured_from_text(
    text: str,
    *,
    document_type: str,
    source_parser: str,
    template: str,
    default_content_kind: str = "text",
    parser_backend: str = "local_partition",
    parser_version: str = LOCAL_PARSER_VERSION,
    extra_artifacts: Mapping[str, ExtractionMetadataValue] | None = None,
) -> StructuredExtraction:
    extraction = StructuredExtraction(
        raw_text=text,
        document_type=document_type,
        confidence=1.0 if text.strip() else 0.0,
        warnings=[],
        parser_artifacts={
            "source_parser": source_parser,
            "chunk_template": template,
            "parser_backend": parser_backend,
            "parser_version": parser_version,
            **dict(extra_artifacts or {}),
        },
    )
    elements = [
        element.model_copy(
            update={
                "source_parser": source_parser,
                "content_kind": (
                    default_content_kind if element.kind == "text" else element.content_kind
                ),
                "metadata": {
                    **element.metadata,
                    "source_parser": source_parser,
                    "parser_backend": parser_backend,
                    "chunk_template": template,
                },
            }
        )
        for element in extraction.elements
    ]
    return extraction.model_copy(update={"elements": elements})


def _structured_from_adapter_elements(
    elements: Sequence[object],
    *,
    document_type: str,
    source_parser: str,
    template: str,
    parser_backend: str,
    parser_version: str,
    extra_artifacts: Mapping[str, ExtractionMetadataValue] | None = None,
) -> StructuredExtraction:
    mapped_elements: list[DocumentElement] = []
    mapped_tables: list[ExtractionTable] = []
    raw_parts: list[str] = []
    for order, item in enumerate(elements):
        text = _adapter_element_text(item)
        if not text:
            continue
        kind = _adapter_element_kind(item)
        content_kind = _content_kind_for_adapter_kind(kind)
        element_id = _adapter_element_id(item, order)
        page_number = _adapter_element_page_number(item)
        bbox = _adapter_element_bbox(item)
        confidence = _adapter_element_confidence(item)
        metadata = _adapter_element_metadata(item)
        for key, value in _adapter_bbox_lineage_metadata(bbox).items():
            metadata.setdefault(key, value)
        metadata.update(
            {
                "source_parser": source_parser,
                "parser_backend": parser_backend,
                "chunk_template": template,
                "adapter_element_type": kind,
            }
        )
        mapped_elements.append(
            DocumentElement(
                kind=kind,
                text=text,
                order=order,
                element_id=element_id,
                content_kind=content_kind,
                source_parser=source_parser,
                page_number=page_number,
                bbox=bbox,
                confidence=confidence,
                metadata=metadata,
            )
        )
        if content_kind == "table":
            mapped_tables.extend(
                _tables_from_adapter_text(
                    text,
                    element_id=element_id,
                    page_number=page_number,
                    source_parser=source_parser,
                    parser_backend=parser_backend,
                    parser_version=parser_version,
                )
            )
        raw_parts.append(text)

    extraction = StructuredExtraction(
        raw_text=_clean_text("\n\n".join(raw_parts)),
        document_type=document_type,
        confidence=1.0 if raw_parts else 0.0,
        warnings=[],
        elements=mapped_elements,
        tables=mapped_tables,
        parser_artifacts={
            "source_parser": source_parser,
            "chunk_template": template,
            "parser_backend": parser_backend,
            "parser_version": parser_version,
            "external_adapter": parser_backend,
            "adapter_element_count": len(mapped_elements),
            "adapter_table_count": len(mapped_tables),
            **dict(extra_artifacts or {}),
        },
    )
    return extraction


def _delimited_table_extraction(
    text: str,
    *,
    source_profile: SourceProfile | None,
    content_type: str,
    parser_profile: str,
) -> StructuredExtraction | None:
    delimiter = _delimiter_for_delimited_source(
        source_profile=source_profile,
        content_type=content_type,
    )
    rows = _delimited_rows(text, delimiter=delimiter)
    if not rows or max((len(row) for row in rows), default=0) <= 1:
        return None
    table_kind = "tsv" if delimiter == "\t" else "csv"
    table_id = f"{table_kind}-table-0000"
    markdown = "\n".join(_xlsx_markdown_row(row) for row in rows)
    column_count = max(len(row) for row in rows)
    cells = _table_cells_from_rows(rows)
    metadata: dict[str, ExtractionMetadataValue] = {
        "source_parser": parser_profile,
        "parser_backend": "local_partition",
        "parser_version": LOCAL_PARSER_VERSION,
        "chunk_template": TABLE_PRESERVE_ROWS_TEMPLATE,
        "table_format": table_kind,
        "row_count": len(rows),
        "column_count": column_count,
    }
    return StructuredExtraction(
        raw_text=markdown,
        document_type=table_kind.upper(),
        confidence=1.0,
        elements=[
            DocumentElement(
                kind="table",
                text=markdown,
                order=0,
                element_id=table_id,
                content_kind="table",
                source_parser=parser_profile,
                page_number=1,
                confidence=1.0,
                metadata=metadata,
            )
        ],
        tables=[
            ExtractionTable(
                table_id=table_id,
                element_id=table_id,
                page_number=1,
                cells=cells,
                metadata=metadata,
            )
        ],
        parser_artifacts={
            "source_parser": parser_profile,
            "chunk_template": TABLE_PRESERVE_ROWS_TEMPLATE,
            "parser_backend": "local_partition",
            "parser_version": LOCAL_PARSER_VERSION,
            "table_count": 1,
            "row_count": len(rows),
            "column_count": column_count,
            "table_format": table_kind,
        },
    )


def _is_delimited_table_source(
    *,
    source_profile: SourceProfile | None,
    content_type: str,
) -> bool:
    extension = (source_profile.extension if source_profile is not None else "") or ""
    normalized_content_type = _normalized_content_type_for_parser(
        content_type or (source_profile.content_type if source_profile is not None else "")
    )
    return extension in {".csv", ".tsv"} or normalized_content_type in {
        "text/csv",
        "application/csv",
        "text/tab-separated-values",
    }


def _delimiter_for_delimited_source(
    *,
    source_profile: SourceProfile | None,
    content_type: str,
) -> str:
    extension = (source_profile.extension if source_profile is not None else "") or ""
    normalized_content_type = _normalized_content_type_for_parser(
        content_type or (source_profile.content_type if source_profile is not None else "")
    )
    if extension == ".tsv" or normalized_content_type == "text/tab-separated-values":
        return "\t"
    return ","


def _delimited_rows(text: str, *, delimiter: str) -> list[list[str]]:
    try:
        reader = csv.reader(StringIO(text), delimiter=delimiter, skipinitialspace=True)
        return [
            [_clean_table_cell(value) for value in row]
            for row in reader
            if any(value.strip() for value in row)
        ]
    except csv.Error:
        return []


def _adapter_version(backend: str) -> str:
    package = EXTERNAL_ADAPTER_PACKAGES[backend]
    try:
        module = importlib.import_module(package)
    except Exception:
        return f"{backend}_adapter_v1"
    version = getattr(module, "__version__", None)
    if isinstance(version, str) and version.strip():
        return version.strip()[:80]
    return f"{backend}_adapter_v1"


def _tables_from_adapter_text(
    text: str,
    *,
    element_id: str | None,
    page_number: int | None,
    source_parser: str,
    parser_backend: str,
    parser_version: str,
) -> list[ExtractionTable]:
    cells = _table_cells_from_adapter_text(text)
    if not cells:
        return []
    row_count = max(cell.row for cell in cells) + 1
    column_count = max(cell.col for cell in cells) + 1
    table_id = element_id or "adapter-table-0000"
    return [
        ExtractionTable(
            table_id=table_id,
            element_id=element_id,
            page_number=page_number,
            cells=cells,
            metadata={
                "source_parser": source_parser,
                "parser_backend": parser_backend,
                "parser_version": parser_version,
                "row_count": row_count,
                "column_count": column_count,
            },
        )
    ]


def _table_cells_from_adapter_text(text: str) -> list[ExtractionTableCell]:
    rows = _markdown_table_rows(text)
    if not rows:
        rows = _tabular_text_rows(text)
    return _table_cells_from_rows(rows)


def _table_cells_from_rows(rows: Sequence[Sequence[str]]) -> list[ExtractionTableCell]:
    return [
        ExtractionTableCell(row=row_index, col=col_index, text=str(value).strip())
        for row_index, row in enumerate(rows)
        for col_index, value in enumerate(row)
    ]


def _markdown_table_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if "|" not in stripped:
            continue
        values = _markdown_table_values(stripped)
        if len(values) <= 1 or _is_markdown_separator_row(values):
            continue
        rows.append(values)
    return rows


def _markdown_table_values(line: str) -> list[str]:
    body = line.strip().strip("|")
    values = [_clean_table_cell(value.replace("\\|", "|")) for value in body.split("|")]
    return values


def _is_markdown_separator_row(values: Sequence[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", value.replace(" ", "")) for value in values)


def _tabular_text_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if "\t" not in stripped:
            continue
        values = [_clean_table_cell(value) for value in stripped.split("\t")]
        if len(values) > 1:
            rows.append(values)
    return rows


def _clean_table_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


@contextmanager
def _temporary_source_file(
    source_bytes: bytes,
    source_profile: SourceProfile | None,
    content_type: str,
) -> Iterator[Path]:
    suffix = _source_suffix(source_profile, content_type)
    with tempfile.NamedTemporaryFile(prefix="rag-parser-", suffix=suffix) as handle:
        handle.write(source_bytes)
        handle.flush()
        yield Path(handle.name)


def _source_suffix(source_profile: SourceProfile | None, content_type: str) -> str:
    if source_profile is not None and source_profile.extension:
        return source_profile.extension
    by_content_type = {
        "application/pdf": ".pdf",
        "text/html": ".html",
        "message/rfc822": ".eml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    }
    if content_type.startswith("image/"):
        return f".{content_type.split('/', 1)[1].split(';', 1)[0] or 'img'}"
    if content_type.startswith("text/"):
        return ".txt"
    return by_content_type.get(content_type.split(";", 1)[0].strip().casefold(), ".bin")


def _normalized_content_type_for_parser(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().casefold()


def _export_adapter_text(
    value: object,
    *,
    preferred_methods: Sequence[str],
) -> tuple[str, str]:
    if isinstance(value, str):
        return _clean_text(value), "str"
    for method_name in preferred_methods:
        exported = getattr(value, method_name, None)
        if callable(exported):
            exported = exported()
        text = _adapter_text_value(exported)
        if text:
            return _clean_text(text), method_name
    text = _adapter_text_value(value)
    return (_clean_text(text), "str") if text else ("", "unknown")


def _adapter_child_elements(value: object) -> tuple[object, ...]:
    """adapter 出力に含まれる block/chunk/table sequence を抽出する。"""
    candidates: list[object] = []
    for attr in ("elements", "chunks", "blocks", "children", "texts", "tables"):
        child = _object_member(value, attr)
        if child is None:
            continue
        candidates.extend(_adapter_sequence_items(child))
    return tuple(item for item in candidates if _adapter_element_text(item))


def _adapter_sequence_items(value: object) -> list[object]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return list(value)
    values = getattr(value, "values", None)
    if callable(values):
        try:
            raw_values = values()
        except Exception:
            return []
        if isinstance(raw_values, Sequence) and not isinstance(
            raw_values, bytes | bytearray | str
        ):
            return list(raw_values)
        return list(raw_values) if isinstance(raw_values, Iterator) else []
    return []


def _object_member(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _adapter_text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("markdown", "text", "raw_text", "content"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
        return ""
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        for item in value:
            text = _adapter_text_value(item)
            if text:
                return text
        return ""
    text = str(value)
    return "" if text.startswith("<") and text.endswith(">") else text


def _docling_artifacts(document: object) -> dict[str, ExtractionMetadataValue]:
    artifacts: dict[str, ExtractionMetadataValue] = {}
    for attr, key in (
        ("pages", "page_count"),
        ("tables", "table_count"),
        ("pictures", "asset_count"),
    ):
        value = getattr(document, attr, None)
        count = _safe_len(value)
        if count is not None:
            artifacts[key] = count
    return artifacts


def _safe_len(value: object) -> int | None:
    try:
        return len(value)  # type: ignore[arg-type]
    except TypeError:
        return None


def _adapter_element_text(item: object) -> str:
    for attr in ("text", "raw_text", "content"):
        value = getattr(item, attr, None)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)
    for method_name in ("export_to_markdown", "to_markdown", "export_to_text", "to_text"):
        exported = getattr(item, method_name, None)
        if callable(exported):
            exported = exported()
        text = _adapter_text_value(exported)
        if text:
            return _clean_text(text)
    rows = _adapter_table_rows(item)
    if rows:
        return "\n".join(_xlsx_markdown_row(row) for row in rows)
    if isinstance(item, Mapping):
        text = _adapter_text_value(item)
        if text:
            return _clean_text(text)
    text = str(item)
    return "" if text.startswith("<") and text.endswith(">") else _clean_text(text)


def _adapter_element_kind(item: object) -> str:
    for attr in ("category", "type", "kind"):
        value = getattr(item, attr, None)
        if isinstance(value, str) and value.strip():
            return _canonical_adapter_kind(value)
    return _canonical_adapter_kind(item.__class__.__name__)


def _adapter_table_rows(item: object) -> list[list[str]]:
    for attr in ("rows", "data"):
        rows = _rows_from_table_value(_object_member(item, attr))
        if rows:
            return rows
    rows = _rows_from_table_value(item if isinstance(item, Sequence) else None)
    return rows


def _rows_from_table_value(value: object) -> list[list[str]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    rows: list[list[str]] = []
    for row in value:
        if not isinstance(row, Sequence) or isinstance(row, str | bytes | bytearray):
            return []
        cells = [_clean_table_cell(str(cell)) for cell in row]
        if any(cells):
            rows.append(cells)
    if rows and max((len(row) for row in rows), default=0) > 1:
        return rows
    return []


def _canonical_adapter_kind(value: str) -> str:
    normalized = re.sub(r"[\s-]+", "_", value.strip().casefold())
    compact = normalized.replace("_", "")
    if "title" in compact or "heading" in compact:
        return "title"
    if "table" in compact:
        return "table"
    if "formula" in compact or "equation" in compact:
        return "equation"
    if "code" in compact:
        return "code"
    if "list" in compact or "bullet" in compact:
        return "list"
    if "image" in compact or "picture" in compact or "figure" in compact:
        return "figure"
    if "caption" in compact:
        return "figure_caption"
    if "email" in compact:
        return "text"
    if "slide" in compact:
        return "text"
    if "sheet" in compact:
        return "table"
    if "text" in compact or "narrative" in compact or "paragraph" in compact:
        return "text"
    return "other"


def _content_kind_for_adapter_kind(kind: str) -> str:
    normalized = kind.strip().casefold()
    if normalized in {"table"}:
        return "table"
    if normalized in {"figure", "figure_caption"}:
        return "figure"
    if normalized == "equation":
        return "equation"
    if normalized == "code":
        return "code"
    if normalized == "list":
        return "list"
    return "text"


def _adapter_element_id(item: object, order: int) -> str | None:
    for value in (
        getattr(item, "id", None),
        _metadata_get(getattr(item, "metadata", None), "element_id"),
    ):
        if isinstance(value, str | int) and str(value).strip():
            return str(value).strip()[:128]
    return f"adapter-el-{order:04d}"


def _adapter_element_page_number(item: object) -> int | None:
    metadata = getattr(item, "metadata", None)
    for value in (
        _metadata_get(metadata, "page_number"),
        _metadata_get(metadata, "page"),
        getattr(item, "page_number", None),
    ):
        number = _int_value(value)
        if number is not None and number >= 1:
            return number
    return None


def _adapter_element_bbox(item: object) -> Any:
    metadata = getattr(item, "metadata", None)
    for value in (
        _metadata_get(metadata, "coordinates"),
        _metadata_get(metadata, "bbox"),
        _metadata_get(metadata, "bounding_box"),
        getattr(item, "bbox", None),
    ):
        bbox = _adapter_bbox_value(value)
        if bbox is not None:
            return bbox
    return None


def _adapter_bbox_value(value: object) -> object | None:
    if value is None:
        return None
    if isinstance(value, Mapping | Sequence) and not isinstance(value, str | bytes | bytearray):
        return value
    for attr in ("points", "coordinates", "bbox", "bounding_box"):
        nested: object | None = getattr(value, attr, None)
        if nested is not None:
            return nested
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        dumped: object = to_dict()
        return dumped
    return None


def _adapter_bbox_lineage_metadata(
    bbox: object,
) -> dict[str, ExtractionMetadataValue]:
    metadata: dict[str, ExtractionMetadataValue] = {}
    mode = _adapter_bbox_coordinate_mode(bbox)
    if mode is not None:
        metadata["bbox_coordinate_mode"] = mode
    unit = _adapter_bbox_unit(bbox)
    if unit is not None:
        metadata["bbox_unit"] = unit
    return metadata


def _adapter_bbox_coordinate_mode(bbox: object) -> str | None:
    if isinstance(bbox, Mapping):
        lowered = {str(key).strip().casefold(): value for key, value in bbox.items()}
        if all(key in lowered for key in ("x", "y", "width", "height")):
            return "xyxy"
        if all(key in lowered for key in ("x1", "y1", "x2", "y2")):
            return "xyxy"
        for key in ("points", "vertices", "polygon", "coordinates"):
            if key in lowered:
                return _adapter_bbox_coordinate_mode(lowered[key]) or "xyxy"
        for key in ("bbox", "bounding_box", "boundingbox"):
            if key in lowered:
                return _adapter_bbox_coordinate_mode(lowered[key])
    if _is_adapter_point_sequence(bbox):
        return "xyxy"
    return None


def _adapter_bbox_unit(bbox: object) -> str | None:
    coords = _adapter_bbox_numeric_values(bbox)
    if not coords:
        return None
    max_value = max(abs(item) for item in coords)
    if max_value <= 1:
        return "ratio"
    if max_value <= 100:
        return "percent"
    return "absolute"


def _adapter_bbox_numeric_values(value: object) -> list[float]:
    if isinstance(value, Mapping):
        mapped_values: list[float] = []
        for item in value.values():
            mapped_values.extend(_adapter_bbox_numeric_values(item))
        return mapped_values
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        sequence_values: list[float] = []
        for item in value:
            sequence_values.extend(_adapter_bbox_numeric_values(item))
        return sequence_values
    number = _float_value(value)
    return [number] if number is not None else []


def _is_adapter_point_sequence(value: object) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return False
    items = list(value)
    if len(items) < 2:
        return False
    return all(_adapter_point_value(item) is not None for item in items)


def _adapter_point_value(value: object) -> tuple[float, float] | None:
    if isinstance(value, Mapping):
        lowered = {str(key).strip().casefold(): item for key, item in value.items()}
        x = _float_value(lowered.get("x"))
        y = _float_value(lowered.get("y"))
        if x is not None and y is not None:
            return (x, y)
        return None
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        items = list(value)
        if len(items) < 2:
            return None
        x = _float_value(items[0])
        y = _float_value(items[1])
        if x is not None and y is not None:
            return (x, y)
    return None


def _adapter_element_confidence(item: object) -> float | None:
    metadata = getattr(item, "metadata", None)
    for value in (
        getattr(item, "confidence", None),
        _metadata_get(metadata, "confidence"),
        _metadata_get(metadata, "detection_class_prob"),
        _metadata_get(metadata, "probability"),
    ):
        confidence = _float_value(value)
        if confidence is not None and 0.0 <= confidence <= 1.0:
            return confidence
    return None


def _adapter_element_metadata(item: object) -> dict[str, ExtractionMetadataValue]:
    metadata: dict[str, ExtractionMetadataValue] = {}
    source = _mapping_from_object(getattr(item, "metadata", None))
    for key, value in source.items():
        normalized_key = str(key).strip()[:80]
        scalar = _metadata_scalar(value)
        if normalized_key and scalar is not _NON_SCALAR:
            metadata[normalized_key] = cast(ExtractionMetadataValue, scalar)
    return metadata


_NON_SCALAR = object()


def _mapping_from_object(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return {str(key): item for key, item in dumped.items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        dumped = to_dict()
        if isinstance(dumped, Mapping):
            return {str(key): item for key, item in dumped.items()}
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, Mapping):
        return {str(key): item for key, item in attrs.items() if not str(key).startswith("_")}
    return {}


def _metadata_get(metadata: object, key: str) -> object | None:
    if isinstance(metadata, Mapping):
        return metadata.get(key)
    return getattr(metadata, key, None)


def _metadata_scalar(value: object) -> ExtractionMetadataValue | object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return _NON_SCALAR


def _int_value(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _float_value(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _document_type_for_source(source_profile: SourceProfile | None) -> str:
    if source_profile is None:
        return "ドキュメント"
    return {
        SourceModality.PDF: "PDF",
        SourceModality.IMAGE: "画像",
        SourceModality.TEXT: "テキスト文書",
        SourceModality.HTML: "HTML",
        SourceModality.EMAIL: "メール",
        SourceModality.OFFICE: "Office 文書",
        SourceModality.AUDIO: "音声",
        SourceModality.UNKNOWN: "ドキュメント",
    }[source_profile.modality]


def _is_unsupported_outlook_msg(source_profile: SourceProfile | None) -> bool:
    if source_profile is None:
        return False
    return (
        source_profile.modality == SourceModality.EMAIL
        and source_profile.unsupported_reason == "outlook_msg_not_supported"
    )


def _is_unsupported_tiff_image(source_profile: SourceProfile | None) -> bool:
    if source_profile is None:
        return False
    return (
        source_profile.modality == SourceModality.IMAGE
        and source_profile.unsupported_reason == "tiff_image_not_supported"
    )


def _is_unsupported_legacy_office_binary(source_profile: SourceProfile | None) -> bool:
    if source_profile is None:
        return False
    return (
        source_profile.modality == SourceModality.OFFICE
        and source_profile.unsupported_reason == "legacy_office_binary_not_supported"
    )


def _default_content_kind_for_source(source_profile: SourceProfile | None) -> str:
    if source_profile is None:
        return "text"
    if source_profile.modality == SourceModality.EMAIL:
        return "email"
    if source_profile.extension == ".pptx":
        return "slide"
    if source_profile.extension == ".xlsx":
        return "sheet"
    return "text"


def _decode_text_bytes(data: bytes) -> str:
    if not data:
        return ""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    match = from_bytes(data).best()
    if match is not None:
        return str(match)
    return data.decode("utf-8", errors="replace")


def _clean_text(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    collapsed: list[str] = []
    blank_seen = False
    for line in lines:
        if not line:
            if not blank_seen:
                collapsed.append("")
            blank_seen = True
            continue
        collapsed.append(line)
        blank_seen = False
    return "\n".join(collapsed).strip()


def _looks_like_markdown(text: str) -> bool:
    return any(line.lstrip().startswith("#") for line in text.splitlines())


def _email_body_text(message: object) -> str:
    get_body = getattr(message, "get_body", None)
    if callable(get_body):
        body = get_body(preferencelist=("plain", "html"))
        if body is not None:
            content = body.get_content()
            if isinstance(content, str):
                if body.get_content_type() == "text/html":
                    parser = _TextHTMLParser()
                    parser.feed(content)
                    return parser.text()
                return content
    walk = getattr(message, "walk", None)
    if callable(walk):
        parts: list[str] = []
        for part in walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_type() in {"text/plain", "text/html"}:
                content = part.get_content()
                if isinstance(content, str):
                    parts.append(content)
        return "\n\n".join(parts)
    return ""


def _office_xml_text(archive: zipfile.ZipFile, name: str) -> str:
    with archive.open(name) as handle:
        root = ElementTree.fromstring(handle.read())
    return _xml_text(root)


def _office_many_xml_text(archive: zipfile.ZipFile, pattern: str) -> str:
    regex = re.compile(pattern)
    names = sorted(name for name in archive.namelist() if regex.fullmatch(name))
    return "\n\n".join(_office_xml_text(archive, name) for name in names)


def _office_docx_text_and_tables(
    archive: zipfile.ZipFile,
) -> tuple[str, list[ExtractionTable]]:
    with archive.open("word/document.xml") as handle:
        root = ElementTree.fromstring(handle.read())
    body = root.find(".//{*}body")
    if body is None:
        body = root
    parts: list[str] = []
    tables: list[ExtractionTable] = []
    table_index = 0
    for child in list(body):
        local_name = _xml_local_name(child.tag)
        if local_name == "p":
            text = _xml_text(child)
            if text:
                parts.append(text)
        elif local_name == "tbl":
            rows = _docx_table_rows(child)
            if not rows:
                continue
            parts.extend(_xlsx_markdown_row(row) for row in rows)
            table = _docx_table_from_rows(rows, table_index=table_index)
            if table is not None:
                tables.append(table)
                table_index += 1
    if not parts:
        return _xml_text(root), tables
    return "\n".join(parts), tables


def _docx_table_rows(table_node: ElementTree.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table_node.findall(".//{*}tr"):
        cells = [_xml_text(cell) for cell in row.findall("./{*}tc")]
        cleaned = [_clean_table_cell(cell) for cell in cells if cell.strip()]
        if cleaned:
            rows.append(cleaned)
    return rows


def _docx_table_from_rows(
    rows: Sequence[Sequence[str]],
    *,
    table_index: int,
) -> ExtractionTable | None:
    if not rows:
        return None
    column_count = max((len(row) for row in rows), default=0)
    table_id = f"docx-table-{table_index:04d}"
    return ExtractionTable(
        table_id=table_id,
        element_id=table_id,
        page_number=1,
        cells=_table_cells_from_rows(rows),
        metadata={
            "source_parser": "local_office_structure",
            "parser_backend": "local_partition",
            "parser_version": LOCAL_PARSER_VERSION,
            "chunk_template": "office_document",
            "row_count": len(rows),
            "column_count": column_count,
        },
    )


def _office_pptx_text_and_tables(
    archive: zipfile.ZipFile,
) -> tuple[str, list[ExtractionTable]]:
    parts: list[str] = []
    tables: list[ExtractionTable] = []
    table_index = 0
    for slide_number, name in _openxml_numbered_members(archive, r"ppt/slides/slide(\d+)\.xml"):
        text, slide_tables = _office_pptx_slide_text_and_tables(
            archive,
            name,
            slide_number=slide_number,
            table_start_index=table_index,
        )
        if text:
            parts.append(text)
        tables.extend(slide_tables)
        table_index += len(slide_tables)
    return "\n\n".join(parts), tables


def _office_pptx_slide_text_and_tables(
    archive: zipfile.ZipFile,
    name: str,
    *,
    slide_number: int,
    table_start_index: int = 0,
) -> tuple[str, list[ExtractionTable]]:
    with archive.open(name) as handle:
        root = ElementTree.fromstring(handle.read())
    tables: list[ExtractionTable] = []
    for offset, table_node in enumerate(root.findall(".//{*}tbl")):
        rows = _pptx_table_rows(table_node)
        if not rows:
            continue
        table = _pptx_table_from_rows(
            rows,
            slide_number=slide_number,
            table_index=table_start_index + offset,
            source_path=name,
        )
        if table is not None:
            tables.append(table)
    return _xml_text(root), tables


def _pptx_table_rows(table_node: ElementTree.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table_node.findall("./{*}tr"):
        cells = [_xml_text(cell) for cell in row.findall("./{*}tc")]
        cleaned = [_clean_table_cell(cell) for cell in cells if cell.strip()]
        if cleaned:
            rows.append(cleaned)
    return rows


def _pptx_table_from_rows(
    rows: Sequence[Sequence[str]],
    *,
    slide_number: int,
    table_index: int,
    source_path: str,
) -> ExtractionTable | None:
    if not rows:
        return None
    column_count = max((len(row) for row in rows), default=0)
    table_id = f"pptx-slide-{slide_number}-table-{table_index:04d}"
    return ExtractionTable(
        table_id=table_id,
        element_id=table_id,
        page_number=slide_number,
        cells=_table_cells_from_rows(rows),
        metadata={
            "source_parser": "local_office_structure",
            "parser_backend": "local_partition",
            "parser_version": LOCAL_PARSER_VERSION,
            "chunk_template": "office_slide",
            "office_segment_number": slide_number,
            "office_segment_path": source_path,
            "row_count": len(rows),
            "column_count": column_count,
        },
    )


def _office_xlsx_text(archive: zipfile.ZipFile) -> str:
    text, _tables = _office_xlsx_text_and_tables(archive)
    return text


def _office_xlsx_text_and_tables(
    archive: zipfile.ZipFile,
) -> tuple[str, list[ExtractionTable]]:
    shared_strings = _xlsx_shared_strings(archive)
    sheet_names = sorted(
        name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
    )
    rows: list[str] = []
    tables: list[ExtractionTable] = []
    for sheet_index, name in enumerate(sheet_names, start=1):
        sheet_text, table = _office_xlsx_sheet_text_and_table(
            archive,
            name,
            shared_strings=shared_strings,
            sheet_number=sheet_index,
        )
        if sheet_text:
            rows.append(sheet_text)
        if table is not None:
            tables.append(table)
    return "\n\n".join(rows), tables


def _office_xlsx_sheet_text(
    archive: zipfile.ZipFile,
    name: str,
    *,
    shared_strings: Mapping[int, str],
    sheet_number: int,
) -> str:
    text, _table = _office_xlsx_sheet_text_and_table(
        archive,
        name,
        shared_strings=shared_strings,
        sheet_number=sheet_number,
    )
    return text


def _office_xlsx_sheet_text_and_table(
    archive: zipfile.ZipFile,
    name: str,
    *,
    shared_strings: Mapping[int, str],
    sheet_number: int,
) -> tuple[str, ExtractionTable | None]:
    rows = [f"# sheet {sheet_number}"]
    table_rows: list[list[str]] = []
    with archive.open(name) as handle:
        root = ElementTree.fromstring(handle.read())
    for row in root.findall(".//{*}row"):
        cells = []
        for cell in row.findall("{*}c"):
            value = cell.find("{*}v")
            if value is None or value.text is None:
                continue
            text = value.text
            if cell.attrib.get("t") == "s" and text.isdigit():
                text = shared_strings.get(int(text), text)
            cells.append(text)
        if cells:
            table_rows.append(cells)
            rows.append(_xlsx_markdown_row(cells))
    return "\n".join(rows), _xlsx_table_from_rows(
        table_rows,
        sheet_number=sheet_number,
        source_path=name,
    )


def _xlsx_table_from_rows(
    rows: Sequence[Sequence[str]],
    *,
    sheet_number: int,
    source_path: str,
) -> ExtractionTable | None:
    if not rows:
        return None
    cells = [
        ExtractionTableCell(row=row_index, col=col_index, text=str(value).strip())
        for row_index, row in enumerate(rows)
        for col_index, value in enumerate(row)
    ]
    column_count = max((len(row) for row in rows), default=0)
    table_id = f"xlsx-sheet-{sheet_number}"
    return ExtractionTable(
        table_id=table_id,
        element_id=table_id,
        page_number=sheet_number,
        cells=cells,
        metadata={
            "source_parser": "local_office_structure",
            "parser_backend": "local_partition",
            "parser_version": LOCAL_PARSER_VERSION,
            "office_segment_number": sheet_number,
            "office_segment_path": source_path,
            "row_count": len(rows),
            "column_count": column_count,
        },
    )


def _xlsx_markdown_row(cells: Sequence[str]) -> str:
    """XLSX 行を table chunking が認識できる Markdown table row にする。"""
    escaped = [cell.replace("|", "\\|").strip() for cell in cells]
    return "| " + " | ".join(escaped) + " |"


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> dict[int, str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return {}
    with archive.open("xl/sharedStrings.xml") as handle:
        root = ElementTree.fromstring(handle.read())
    values: dict[int, str] = {}
    for index, item in enumerate(root.findall(".//{*}si")):
        values[index] = _xml_text(item)
    return values


def _xml_text(root: ElementTree.Element) -> str:
    texts = [text.strip() for text in root.itertext() if text and text.strip()]
    return _clean_text("\n".join(texts))


def _xml_local_name(tag: object) -> str:
    text = str(tag)
    if "}" in text:
        return text.rsplit("}", 1)[1]
    return text


def template_for_source_profile(source_profile: SourceProfile | None) -> str:
    """chunk metadata 用の既定 template 名。"""
    if source_profile is None:
        return "enterprise_ai_fallback"
    extension = source_profile.extension or ""
    if source_profile.modality == SourceModality.PDF:
        return "pdf_layout"
    if source_profile.modality == SourceModality.IMAGE:
        return "ocr_page"
    if source_profile.modality == SourceModality.HTML:
        return "html_semantic"
    if source_profile.modality == SourceModality.EMAIL:
        return "email_thread"
    if extension == ".pptx":
        return "office_slide"
    if extension == ".xlsx":
        return "office_sheet"
    if source_profile.modality == SourceModality.TEXT:
        name = PurePath(source_profile.sanitized_file_name).suffix.lower()
        return "markdown_by_heading" if name in {".md", ".markdown"} else "text_blocks"
    return "enterprise_ai_fallback"
