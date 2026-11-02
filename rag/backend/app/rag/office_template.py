"""Office のテンプレート形式の定義と、アップロードの原本の確認(#4054)。

テンプレート(.dotx / .potx / .xltx)とマクロ付きのテンプレート(.dotm / .potm / .xltm)は、拡張子と
MIME type が利用者の申告にすぎないので、原本を保存する前に OOXML の package の本体の種類と照らす。
読むのは package の目録と ``[Content_Types].xml`` だけで、マクロの部品は開かず、実行もしない。
"""

from __future__ import annotations

import io
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any
from xml.parsers import expat

OOXML_ZIP_MAGIC = b"PK\x03\x04"
# パスワードで保護した OOXML は、OLE2 の複合文書の中に暗号化して入っている。
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_CONTENT_TYPES_PART = "[Content_Types].xml"
_CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
# 目録の大きさの上限(要素の数の上限が 1 件あたり数百バイトでも収まる大きさ)。
_MAX_CONTENT_TYPES_BYTES = 16 * 1024 * 1024
_VBA_PART_NAMES = frozenset({"vbaproject.bin", "vbadata.xml"})
_OCTET_STREAM = "application/octet-stream"


@dataclass(frozen=True)
class OfficeTemplateFormat:
    """テンプレートの 1 形式(拡張子・MIME type・package の本体の content type)。"""

    extension: str
    media_type: str
    main_part_type: str
    macro_enabled: bool


OFFICE_TEMPLATE_FORMATS = (
    OfficeTemplateFormat(
        ".dotx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
        macro_enabled=False,
    ),
    OfficeTemplateFormat(
        ".dotm",
        "application/vnd.ms-word.template.macroenabled.12",
        "application/vnd.ms-word.template.macroenabledtemplate.main+xml",
        macro_enabled=True,
    ),
    OfficeTemplateFormat(
        ".potx",
        "application/vnd.openxmlformats-officedocument.presentationml.template",
        "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
        macro_enabled=False,
    ),
    OfficeTemplateFormat(
        ".potm",
        "application/vnd.ms-powerpoint.template.macroenabled.12",
        "application/vnd.ms-powerpoint.template.macroenabled.main+xml",
        macro_enabled=True,
    ),
    OfficeTemplateFormat(
        ".xltx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml",
        macro_enabled=False,
    ),
    OfficeTemplateFormat(
        ".xltm",
        "application/vnd.ms-excel.template.macroenabled.12",
        "application/vnd.ms-excel.template.macroenabled.main+xml",
        macro_enabled=True,
    ),
)
_FORMAT_BY_EXTENSION = {item.extension: item for item in OFFICE_TEMPLATE_FORMATS}
_FORMAT_BY_MEDIA_TYPE = {item.media_type: item for item in OFFICE_TEMPLATE_FORMATS}
# テンプレート以外の OOXML の本体(.docx をテンプレートの拡張子に変えた原本などを見分ける)。
_DOCUMENT_MAIN_PART_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "application/vnd.ms-word.document.macroenabled.main+xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml",
        "application/vnd.ms-powerpoint.presentation.macroenabled.main+xml",
        "application/vnd.ms-powerpoint.slideshow.macroenabled.main+xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        "application/vnd.ms-excel.sheet.macroenabled.main+xml",
        "application/vnd.ms-excel.sheet.binary.macroenabled.main",
    }
)
_ALL_MAIN_PART_TYPES = _DOCUMENT_MAIN_PART_TYPES | {
    item.main_part_type for item in OFFICE_TEMPLATE_FORMATS
}

OFFICE_TEMPLATE_EXTENSIONS = frozenset(_FORMAT_BY_EXTENSION)
OFFICE_TEMPLATE_MEDIA_TYPES = frozenset(_FORMAT_BY_MEDIA_TYPE)
MACRO_TEMPLATE_EXTENSIONS = frozenset(
    item.extension for item in OFFICE_TEMPLATE_FORMATS if item.macro_enabled
)
MACRO_TEMPLATE_MEDIA_TYPES = frozenset(
    item.media_type for item in OFFICE_TEMPLATE_FORMATS if item.macro_enabled
)

DECLARED_TYPE_MISMATCH_MESSAGE = (
    "ファイルの拡張子と MIME type が Office テンプレートの形式として一致しないため、"
    "受け付けませんでした。正しい拡張子のファイルをアップロードし直してください。"
)
ENCRYPTED_TEMPLATE_MESSAGE = (
    "パスワードで保護(暗号化)された Office テンプレートは読めないため、受け付けませんでした。"
    "保護を外したファイルをアップロードし直してください。"
)
PACKAGE_TOO_LARGE_MESSAGE = (
    "Office テンプレートの展開後の大きさか要素の数が上限を超えているため、受け付けませんでした。"
)


def office_template_format(
    extension: str | None, content_type: str | None
) -> OfficeTemplateFormat | None:
    """拡張子(無ければ MIME type)が示すテンプレートの形式。テンプレートでなければ None。"""
    if extension:
        return _FORMAT_BY_EXTENSION.get(extension.casefold())
    return _FORMAT_BY_MEDIA_TYPE.get((content_type or "").casefold())


def office_template_upload_problem(
    *,
    file_name: str,
    content_type: str,
    data: bytes,
    max_members: int,
    max_uncompressed_bytes: int,
) -> str | None:
    """テンプレートの原本として受け付けられない理由(利用者向け)。問題が無ければ None。

    テンプレートでない形式は判定しない(None)。``content_type`` は正規化(小文字)済みの値を渡す。
    """
    extension = PurePath(file_name).suffix.casefold()
    media_type = content_type.casefold()
    declared = _FORMAT_BY_MEDIA_TYPE.get(media_type)
    template = office_template_format(extension, media_type)
    if template is None:
        return DECLARED_TYPE_MISMATCH_MESSAGE if declared is not None and extension else None
    if declared is not template and media_type != _OCTET_STREAM:
        return DECLARED_TYPE_MISMATCH_MESSAGE
    return _package_problem(template, data, max_members, max_uncompressed_bytes)


def _package_problem(
    template: OfficeTemplateFormat,
    data: bytes,
    max_members: int,
    max_uncompressed_bytes: int,
) -> str | None:
    if data.startswith(_OLE2_MAGIC):
        return ENCRYPTED_TEMPLATE_MESSAGE
    if not data.startswith(OOXML_ZIP_MAGIC):
        return _not_template_message(template)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            infos = package.infolist()
            if len(infos) > max_members or (
                sum(info.file_size for info in infos) > max_uncompressed_bytes
            ):
                return PACKAGE_TOO_LARGE_MESSAGE
            part_names = {info.filename.casefold() for info in infos}
            content_types = package.getinfo(_CONTENT_TYPES_PART)
            if content_types.file_size > _MAX_CONTENT_TYPES_BYTES:
                return _not_template_message(template)
            overrides = _content_type_overrides(package.read(content_types))
            if package.testzip() is not None:
                return _not_template_message(template)
    except (
        # 目録が無い(KeyError)・暗号化や未対応の圧縮(RuntimeError・NotImplementedError)・
        # 壊れた ZIP(BadZipFile・EOFError・zlib.error)・XML でない目録(ExpatError・ValueError)。
        zipfile.BadZipFile,
        KeyError,
        RuntimeError,
        NotImplementedError,
        EOFError,
        OSError,
        ValueError,
        expat.ExpatError,
        zlib.error,
    ):
        return _not_template_message(template)
    main_parts = {
        part_name for part_name, part_type in overrides.items() if part_type in _ALL_MAIN_PART_TYPES
    }
    if len(main_parts) != 1:
        return _not_template_message(template)
    main_part = next(iter(main_parts))
    if overrides[main_part] != template.main_part_type or main_part not in part_names:
        return _not_template_message(template)
    if not template.macro_enabled and any(_is_vba_part(name) for name in part_names):
        return (
            f"{template.extension} はマクロを含まない形式ですが、この原本にはマクロ(VBA)の部品が"
            "含まれているため、受け付けませんでした。"
        )
    return None


def _not_template_message(template: OfficeTemplateFormat) -> str:
    return (
        f"内容が {template.extension} の Office テンプレートではないか、ファイルが壊れているため、"
        "受け付けませんでした。"
        "Office で開けることと拡張子を確かめてからアップロードし直してください。"
    )


def _is_vba_part(part_name: str) -> bool:
    return part_name.rsplit("/", 1)[-1] in _VBA_PART_NAMES


def _content_type_overrides(payload: bytes) -> dict[str, str]:
    """``[Content_Types].xml`` の Override(部品の名前 → content type。どちらも小文字)。

    OPC は DTD を許さないので、DOCTYPE があれば読まずに止める(実体の展開を防ぐ)。
    """
    overrides: dict[str, str] = {}
    override_tag = f"{_CONTENT_TYPES_NAMESPACE} Override"

    def reject_doctype(*_args: Any) -> None:
        raise ValueError("doctype_not_allowed")

    def start_element(name: str, attributes: dict[str, str]) -> None:
        part_name = attributes.get("PartName")
        part_type = attributes.get("ContentType")
        if name == override_tag and part_name and part_type:
            overrides[part_name.lstrip("/").casefold()] = part_type.casefold()

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartDoctypeDeclHandler = reject_doctype
    parser.StartElementHandler = start_element
    parser.Parse(payload, True)
    return overrides
