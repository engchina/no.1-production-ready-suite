"""Office テンプレートの原本の確認(#4054)。package はすべて合成で作る。"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.rag.office_template import (
    DECLARED_TYPE_MISMATCH_MESSAGE,
    ENCRYPTED_TEMPLATE_MESSAGE,
    OFFICE_TEMPLATE_FORMATS,
    PACKAGE_TOO_LARGE_MESSAGE,
    OfficeTemplateFormat,
    office_template_upload_problem,
)

_DOCX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_FORMATS = {item.extension: item for item in OFFICE_TEMPLATE_FORMATS}
# Office が書く大文字小文字のままの本体の content type(照合は大文字小文字を区別しない)。
_OFFICE_MAIN_TYPES = {
    ".dotm": "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
    ".potm": "application/vnd.ms-powerpoint.template.macroEnabled.main+xml",
    ".xltm": "application/vnd.ms-excel.template.macroEnabled.main+xml",
}


def _package(
    main_type: str,
    *,
    extra_parts: dict[str, bytes] | None = None,
    content_types: bytes | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            content_types
            or (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="xml" ContentType="application/xml"/>'
                f'<Override PartName="/main/part.xml" ContentType="{main_type}"/>'
                "</Types>"
            ).encode(),
        )
        package.writestr("main/part.xml", b"<root/>")
        for name, payload in (extra_parts or {}).items():
            package.writestr(name, payload)
    return buffer.getvalue()


def _problem(
    file_name: str,
    content_type: str,
    data: bytes,
    *,
    max_members: int = 1000,
    max_uncompressed_bytes: int = 1024 * 1024,
) -> str | None:
    return office_template_upload_problem(
        file_name=file_name,
        content_type=content_type,
        data=data,
        max_members=max_members,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )


def _main_type(template: OfficeTemplateFormat) -> str:
    return _OFFICE_MAIN_TYPES.get(template.extension, template.main_part_type)


@pytest.mark.parametrize("template", OFFICE_TEMPLATE_FORMATS, ids=lambda item: item.extension)
def test_valid_template_is_accepted_with_declared_or_missing_mime(
    template: OfficeTemplateFormat,
) -> None:
    data = _package(_main_type(template))
    file_name = f"synthetic{template.extension}"
    assert _problem(file_name, template.media_type, data) is None
    assert _problem(file_name, "application/octet-stream", data) is None
    assert _problem(file_name.upper(), template.media_type, data) is None
    # 拡張子の無い原本は MIME type で形式を決める。
    assert _problem("synthetic", template.media_type, data) is None


def test_macro_template_may_contain_vba_part_and_may_omit_it() -> None:
    template = _FORMATS[".dotm"]
    vba = {"word/vbaProject.bin": b"synthetic-macro"}
    assert _problem("a.dotm", template.media_type, _package(_main_type(template))) is None
    data = _package(_main_type(template), extra_parts=vba)
    assert _problem("a.dotm", template.media_type, data) is None


@pytest.mark.parametrize(
    ("extension", "main_type"),
    [
        # .docx の名前だけを変えた原本。
        (".dotx", _DOCX_MAIN),
        # 別のテンプレートの種類。
        (".dotx", _FORMATS[".xltx"].main_part_type),
        (".xltx", _FORMATS[".potx"].main_part_type),
        # マクロ付きの本体を、マクロなしの拡張子で送る。
        (".dotx", _OFFICE_MAIN_TYPES[".dotm"]),
        (".xltx", _OFFICE_MAIN_TYPES[".xltm"]),
        # マクロなしの本体を、マクロ付きの拡張子で送る。
        (".potm", _FORMATS[".potx"].main_part_type),
    ],
)
def test_extension_and_package_kind_must_match(extension: str, main_type: str) -> None:
    template = _FORMATS[extension]
    problem = _problem(f"a{extension}", template.media_type, _package(main_type))
    assert problem is not None
    assert extension in problem
    assert "受け付けませんでした" in problem


@pytest.mark.parametrize("extension", [".dotx", ".potx", ".xltx"])
@pytest.mark.parametrize("part", ["vbaProject.bin", "VBAPROJECT.BIN", "vbaData.xml"])
def test_plain_template_with_vba_part_is_rejected(extension: str, part: str) -> None:
    template = _FORMATS[extension]
    data = _package(template.main_part_type, extra_parts={f"sub/{part}": b"synthetic-macro"})
    problem = _problem(f"a{extension}", template.media_type, data)
    assert problem is not None
    assert "マクロ(VBA)の部品" in problem


@pytest.mark.parametrize(
    "data",
    [
        b"not a zip",
        b"%PDF-1.7 synthetic",
        b"PK\x03\x04broken",
    ],
)
def test_non_package_bytes_are_rejected(data: bytes) -> None:
    template = _FORMATS[".dotx"]
    problem = _problem("a.dotx", template.media_type, data)
    assert problem is not None
    assert ".dotx" in problem


def test_package_with_flipped_bytes_is_rejected() -> None:
    template = _FORMATS[".dotx"]
    data = bytearray(_package(template.main_part_type))
    # 本体の部品の圧縮データを壊す(CRC か展開で止まる)。
    position = data.index(b"main/part.xml") + len(b"main/part.xml")
    data[position] ^= 0xFF
    assert _problem("a.dotx", template.media_type, bytes(data)) is not None


def test_password_protected_template_gets_specific_message() -> None:
    data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16
    assert _problem("a.dotx", _FORMATS[".dotx"].media_type, data) == ENCRYPTED_TEMPLATE_MESSAGE


def test_package_without_content_types_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("main/part.xml", b"<root/>")
    assert _problem("a.xltx", _FORMATS[".xltx"].media_type, buffer.getvalue()) is not None


@pytest.mark.parametrize(
    "content_types",
    [
        b"not xml",
        b"<Types/>",
        # OPC は DTD を許さない(実体の展開を防ぐ)。
        (
            b'<?xml version="1.0"?><!DOCTYPE Types [<!ENTITY a "b">]>'
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
        ),
    ],
)
def test_invalid_content_types_are_rejected(content_types: bytes) -> None:
    template = _FORMATS[".potx"]
    data = _package(template.main_part_type, content_types=content_types)
    assert _problem("a.potx", template.media_type, data) is not None


def test_main_part_declared_but_missing_is_rejected() -> None:
    template = _FORMATS[".dotx"]
    types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Override PartName="/absent.xml" ContentType="{template.main_part_type}"/></Types>'
    ).encode()
    data = _package(template.main_part_type, content_types=types)
    assert _problem("a.dotx", template.media_type, data) is not None


def test_limits_stop_oversized_packages() -> None:
    template = _FORMATS[".dotx"]
    data = _package(template.main_part_type, extra_parts={"big.xml": b"0" * 4096})
    media_type = template.media_type
    assert _problem("a.dotx", media_type, data, max_members=2) == PACKAGE_TOO_LARGE_MESSAGE
    assert (
        _problem("a.dotx", media_type, data, max_uncompressed_bytes=1024)
        == PACKAGE_TOO_LARGE_MESSAGE
    )


@pytest.mark.parametrize(
    ("file_name", "content_type"),
    [
        # テンプレートの拡張子に、別の形式の MIME type。
        ("a.dotx", "application/pdf"),
        ("a.dotx", "text/plain"),
        ("a.dotx", _FORMATS[".xltx"].media_type),
        ("a.dotx", _FORMATS[".dotm"].media_type),
        # テンプレートの MIME type に、別の形式の拡張子。
        ("a.docx", _FORMATS[".dotx"].media_type),
        ("a.pdf", _FORMATS[".dotx"].media_type),
        ("a.bin", _FORMATS[".xltm"].media_type),
    ],
)
def test_declared_extension_and_mime_must_agree(file_name: str, content_type: str) -> None:
    data = _package(_FORMATS[".dotx"].main_part_type)
    assert _problem(file_name, content_type, data) == DECLARED_TYPE_MISMATCH_MESSAGE


@pytest.mark.parametrize(
    ("file_name", "content_type"),
    [
        ("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("a.docm", "application/vnd.ms-word.document.macroEnabled.12".casefold()),
        ("a.pdf", "application/pdf"),
        ("a.txt", "application/octet-stream"),
    ],
)
def test_other_formats_are_not_inspected(file_name: str, content_type: str) -> None:
    assert _problem(file_name, content_type, b"synthetic") is None
