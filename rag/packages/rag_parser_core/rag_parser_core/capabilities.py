"""parser backend × 対応ファイル形式の宣言(単一正本)。

`registry._external_adapter_supports_source` の if-chain を宣言的データへ抽出したもの。
判定は従来どおり「modality OR content_type OR 拡張子」の OR セマンティクスで、
音声はどの backend も非対応として先行除外する。

`result.py` と同じ軽量モジュールとして置き、backend / parser microservice / API 公開
(設定画面の対応形式表示)が重い `registry` を import せずに参照できるようにする。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath

from rag_parser_core.sheet_records import SHEET_RECORDS_CONTENT_TYPE
from rag_parser_core.source import SourceModality, SourceProfile

AUDIO_EXTENSIONS = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"})

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
_OPENXML_CONTENT_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)
_OPENXML_EXTENSIONS = frozenset({".docx", ".pptx", ".xlsx"})


@dataclass(frozen=True)
class ParserBackendCapability:
    """backend が処理できる原本の宣言。いずれかに一致すれば対応とみなす(OR)。"""

    modalities: frozenset[SourceModality]
    extensions: frozenset[str] = frozenset()
    content_types: frozenset[str] = frozenset()
    content_type_prefixes: frozenset[str] = frozenset()


_PDF_AND_IMAGE = ParserBackendCapability(
    modalities=frozenset({SourceModality.PDF, SourceModality.IMAGE}),
    extensions=frozenset({".pdf"}) | _IMAGE_EXTENSIONS,
    content_types=frozenset({"application/pdf"}),
    content_type_prefixes=frozenset({"image/"}),
)

_IMAGE_ONLY = ParserBackendCapability(
    modalities=frozenset({SourceModality.IMAGE}),
    extensions=_IMAGE_EXTENSIONS,
    content_type_prefixes=frozenset({"image/"}),
)

# OCI service backend の宣言。oci_genai_vision(= enterprise_ai_vlm)は Files API 経由で
# PDF+画像、inline_image は画像のみ(oci_enterprise_ai 参照)。oci_document_understanding は
# PDF/PNG/JPEG(TIFF はアップロード時に unsupported_tiff_image で遮断済み)。
# いずれも実質 PDF+画像として宣言する。
ADAPTER_CAPABILITIES: Mapping[str, ParserBackendCapability] = {
    # Docling の parser サービス(services/parsers/docling。Docling のレイアウト解析)は PDF と画像だけを
    # 解析する(rag_engine.parsing.rendering.SUPPORTED_SOURCE_FILE_TYPES)。Office / HTML / テキスト / メール は
    # 受けないため、ファイル準備で PDF に変換する(office_to_pdf)か、文書解析で Unstructured を選ぶ(#286)。
    "docling": ParserBackendCapability(
        modalities=frozenset({SourceModality.PDF, SourceModality.IMAGE}),
        extensions=frozenset({".pdf", ".gif", ".jfif"}) | _IMAGE_EXTENSIONS,
        content_types=frozenset({"application/pdf"}),
        content_type_prefixes=frozenset({"image/"}),
    ),
    "unstructured": ParserBackendCapability(
        # 汎用 partition。UNKNOWN も受ける(従来挙動)。判定は modality だけで決まるため、
        # extensions は UI の拡張子表示用の代表列挙(unstructured[all-docs] の対応形式)。
        modalities=frozenset(
            {
                SourceModality.PDF,
                SourceModality.IMAGE,
                SourceModality.TEXT,
                SourceModality.HTML,
                SourceModality.EMAIL,
                SourceModality.OFFICE,
                SourceModality.UNKNOWN,
            }
        ),
        extensions=(
            frozenset(
                {
                    ".pdf",
                    ".txt",
                    ".md",
                    ".markdown",
                    ".csv",
                    ".tsv",
                    ".json",
                    ".html",
                    ".htm",
                    ".xml",
                    ".eml",
                }
            )
            | _IMAGE_EXTENSIONS
            | _OPENXML_EXTENSIONS
        ),
    ),
    "mineru": ParserBackendCapability(
        modalities=frozenset({SourceModality.PDF, SourceModality.IMAGE, SourceModality.OFFICE}),
        extensions=frozenset({".pdf"}) | _IMAGE_EXTENSIONS | _OPENXML_EXTENSIONS,
        content_types=frozenset({"application/pdf"}) | _OPENXML_CONTENT_TYPES,
        content_type_prefixes=frozenset({"image/"}),
    ),
    # 外部 API は画像入力。backend が PDF をページ画像へ変換して順序を維持する。
    "dots_ocr": _PDF_AND_IMAGE,
    "oci_genai_vision": _PDF_AND_IMAGE,
    "enterprise_ai_vlm": _PDF_AND_IMAGE,  # oci_genai_vision の後方互換エイリアス
    "oci_document_understanding": _PDF_AND_IMAGE,
}

# 表示用の modality 並び(SourceModality の定義順。AUDIO/UNKNOWN は表示しない)
_DISPLAY_MODALITY_ORDER: tuple[SourceModality, ...] = (
    SourceModality.PDF,
    SourceModality.IMAGE,
    SourceModality.TEXT,
    SourceModality.HTML,
    SourceModality.EMAIL,
    SourceModality.OFFICE,
)


def _source_extension(source_profile: SourceProfile | None) -> str:
    if source_profile is None:
        return ""
    return (source_profile.extension or PurePath(source_profile.sanitized_file_name).suffix).lower()


def _normalized_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().casefold()


def is_audio_source(source_profile: SourceProfile | None, content_type: str) -> bool:
    modality = source_profile.modality if source_profile is not None else SourceModality.UNKNOWN
    normalized = _normalized_content_type(
        content_type or (source_profile.content_type if source_profile is not None else "")
    )
    return (
        modality == SourceModality.AUDIO
        or normalized.startswith("audio/")
        or _source_extension(source_profile) in AUDIO_EXTENSIONS
    )


# 前処理の行の記録（excel_to_json）を受け付ける parser のサービス（#1221）。
SHEET_RECORDS_BACKENDS = frozenset({"docling", "unstructured"})


def adapter_supports_source(
    backend: str,
    *,
    source_profile: SourceProfile | None,
    content_type: str,
) -> bool:
    """backend が原本を処理できるかの宣言判定(音声は全 backend 非対応)。"""
    capability = ADAPTER_CAPABILITIES.get(backend)
    if capability is None:
        return False
    normalized = _normalized_content_type(
        content_type or (source_profile.content_type if source_profile is not None else "")
    )
    if is_audio_source(source_profile, normalized):
        return False
    if normalized == SHEET_RECORDS_CONTENT_TYPE:
        # 前処理（excel_to_json）の行の記録は、Docling / Unstructured の parser のサービスが
        # rag_parser_core で要素にする（外部の parser に渡さない。#1221）。
        return backend in SHEET_RECORDS_BACKENDS
    modality = source_profile.modality if source_profile is not None else SourceModality.UNKNOWN
    return (
        modality in capability.modalities
        or normalized in capability.content_types
        or any(normalized.startswith(prefix) for prefix in capability.content_type_prefixes)
        or _source_extension(source_profile) in capability.extensions
    )


def supported_modalities(backend: str) -> tuple[SourceModality, ...]:
    """表示用の対応 modality 一覧(定義順・UNKNOWN/AUDIO 除外)。"""
    capability = ADAPTER_CAPABILITIES.get(backend)
    if capability is None:
        return ()
    return tuple(
        modality for modality in _DISPLAY_MODALITY_ORDER if modality in capability.modalities
    )
