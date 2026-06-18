"""ファイル種別ごとの軽量 parser registry(re-export shim)。

正本は共有 package `rag_parser_core.registry`。backend は本モジュール経由で
従来の import パス(`app.rag.parsers`)を維持する。外部 adapter(docling/marker/
unstructured/mineru/dots_ocr)の実行は backend では parser マイクロサービスへ委譲する
(`app.clients.parser_service` の HTTP runner を `parse_with_registry` に注入)。
"""

from rag_parser_core.registry import (
    EXTERNAL_ADAPTER_PACKAGES,
    LOCAL_PARSER_VERSION,
    ExternalAdapterRunner,
    OfficeSegmentExtraction,
    OfficeSegmentFailure,
    OfficeSegmentParseResult,
    ParserRegistryResult,
    parse_openxml_office_segment_extractions,
    parse_with_registry,
    template_for_source_profile,
)

__all__ = [
    "EXTERNAL_ADAPTER_PACKAGES",
    "LOCAL_PARSER_VERSION",
    "ExternalAdapterRunner",
    "OfficeSegmentExtraction",
    "OfficeSegmentFailure",
    "OfficeSegmentParseResult",
    "ParserRegistryResult",
    "parse_openxml_office_segment_extractions",
    "parse_with_registry",
    "template_for_source_profile",
]
