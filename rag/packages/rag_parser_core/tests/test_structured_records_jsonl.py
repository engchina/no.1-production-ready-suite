"""解析エンジン「構造化レコード JSONL」の検索用テキストと記録の境界の検証（#2457）。

データはすべて合成。
"""

from __future__ import annotations

import pytest

from rag_parser_core.capabilities import ADAPTER_CAPABILITIES, adapter_supports_source
from rag_parser_core.registry import run_external_adapter
from rag_parser_core.sheet_records import (
    SheetBlock,
    SheetColumn,
    SheetPreambleRow,
    SheetRecords,
    SheetRecordsDocument,
)
from rag_parser_core.structured_records import (
    JSONL_RECORD_BOUNDARY,
    STRUCTURED_RECORD_ID_KEY,
    STRUCTURED_RECORDS_BOUNDARY_ARTIFACT_KEY,
    STRUCTURED_RECORDS_BOUNDARY_COUNT_ARTIFACT_KEY,
    STRUCTURED_RECORDS_CONTENT_TYPE,
    DocumentLine,
    RecordBoundaryError,
    RecordField,
    RecordLocation,
    RowLine,
    dump_structured_records,
    escape_record_boundary,
    has_record_boundaries,
    join_record_boundary_text,
    record_boundary_plain_text,
    sheet_records_to_lines,
    split_record_boundary_text,
    unescape_record_boundary,
)

BACKEND = "structured_records_jsonl"


def _csv(*values: str) -> bytes:
    lines: list[DocumentLine | RowLine] = [
        DocumentLine(
            record_id="document",
            source_type="csv",
            converter="csv_to_json",
            converter_version="v2",
            header_row=1,
        )
    ]
    for index, value in enumerate(values, start=1):
        lines.append(
            RowLine(
                record_id=f"row-{index:06d}",
                source_type="csv",
                location=RecordLocation(row_start=index + 1, row_end=index + 1),
                fields=[
                    RecordField(name="項目", position=1, value=f"値 {index}"),
                    RecordField(name="備考", position=2, value=value),
                ],
            )
        )
    return dump_structured_records(lines)


def _sheet() -> bytes:
    document = SheetRecordsDocument(
        source_format="xlsx",
        sheets=[
            SheetRecords(
                name="表 1",
                header_row=2,
                columns=[
                    SheetColumn(name="名前", column="A"),
                    SheetColumn(name="既定値", column="C", role="default", role_method="detected"),
                ],
                preamble=[SheetPreambleRow(row_number=1, cell_range="A1:A1", text="説明")],
                blocks=[
                    SheetBlock(
                        kind="row",
                        row_start=3,
                        row_end=3,
                        cell_range="A3:C3",
                        values={"名前": "設定 A", "既定値": "30"},
                    ),
                    SheetBlock(
                        kind="row",
                        row_start=4,
                        row_end=4,
                        cell_range="A4:C4",
                        values={"名前": "設定 B", "既定値": "60"},
                    ),
                ],
            )
        ],
    )
    return dump_structured_records(
        sheet_records_to_lines(document, converter="excel_to_json", converter_version="v4")
    )


@pytest.mark.parametrize(
    "text",
    [
        "本文",
        JSONL_RECORD_BOUNDARY,
        "<<<\\JSONL_RECORD_BOUNDARY>>>",
        "<<<\\\\\\JSONL_RECORD_BOUNDARY>>>",
        f"前 {JSONL_RECORD_BOUNDARY} 後\n{JSONL_RECORD_BOUNDARY}\n<<<{JSONL_RECORD_BOUNDARY}>>>",
    ],
)
def test_escape_is_reversible_and_hides_the_marker(text: str) -> None:
    escaped = escape_record_boundary(text)
    assert JSONL_RECORD_BOUNDARY not in escaped
    assert unescape_record_boundary(escaped) == text


def test_escape_adds_one_backslash_per_level() -> None:
    assert escape_record_boundary(JSONL_RECORD_BOUNDARY) == "<<<\\JSONL_RECORD_BOUNDARY>>>"
    assert (
        escape_record_boundary("<<<\\JSONL_RECORD_BOUNDARY>>>") == "<<<\\\\JSONL_RECORD_BOUNDARY>>>"
    )


def test_split_keeps_original_text_equal_to_the_marker() -> None:
    texts = ["一件目", f"原文の {JSONL_RECORD_BOUNDARY} を含む", JSONL_RECORD_BOUNDARY, "四件目"]
    raw = join_record_boundary_text(texts)
    assert raw.count(f"\n{JSONL_RECORD_BOUNDARY}\n") == 3
    assert not raw.startswith(JSONL_RECORD_BOUNDARY) and not raw.endswith(JSONL_RECORD_BOUNDARY)
    parts = split_record_boundary_text(raw)
    assert [part.text for part in parts] == texts
    for part in parts:
        assert raw[part.start_offset : part.end_offset] == escape_record_boundary(part.text)


def test_split_without_boundary_is_one_record() -> None:
    assert [part.text for part in split_record_boundary_text("一件だけ")] == ["一件だけ"]


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (f"一件目 {JSONL_RECORD_BOUNDARY}\n二件目", "boundary_not_on_own_line"),
        (f"一件目\n{JSONL_RECORD_BOUNDARY}\n\n{JSONL_RECORD_BOUNDARY}\n三件目", "empty_record"),
    ],
)
def test_split_rejects_broken_boundaries(raw: str, code: str) -> None:
    with pytest.raises(RecordBoundaryError) as error:
        split_record_boundary_text(raw)
    assert error.value.code == code


def test_csv_extraction_separates_records_with_boundaries() -> None:
    result = run_external_adapter(BACKEND, _csv("一行目\n二行目", JSONL_RECORD_BOUNDARY), None, "")
    extraction = result.extraction
    assert extraction is not None
    assert result.parser_backend == BACKEND
    assert result.parser_version == "structured_records_jsonl_v1"
    assert has_record_boundaries(extraction)
    assert extraction.parser_artifacts[STRUCTURED_RECORDS_BOUNDARY_ARTIFACT_KEY] == (
        JSONL_RECORD_BOUNDARY
    )
    assert extraction.parser_artifacts[STRUCTURED_RECORDS_BOUNDARY_COUNT_ARTIFACT_KEY] == 1
    assert extraction.raw_text == (
        "項目: 値 1 / 備考: 一行目\n二行目\n"
        f"{JSONL_RECORD_BOUNDARY}\n"
        "項目: 値 2 / 備考: <<<\\JSONL_RECORD_BOUNDARY>>>"
    )
    # 要素の本文は原文のまま（エスケープしない）。
    assert extraction.elements[1].text == f"項目: 値 2 / 備考: {JSONL_RECORD_BOUNDARY}"
    assert extraction.elements[0].metadata[STRUCTURED_RECORD_ID_KEY] == "row-000001"
    assert (
        extraction.elements[0].metadata["row_start"],
        extraction.elements[0].metadata["row_end"],
    ) == (2, 2)
    assert record_boundary_plain_text(extraction) == "\n".join(
        element.text for element in extraction.elements
    )


def test_single_record_has_no_boundary() -> None:
    extraction = run_external_adapter(BACKEND, _csv("一件"), None, "").extraction
    assert extraction is not None
    assert JSONL_RECORD_BOUNDARY not in extraction.raw_text
    assert extraction.parser_artifacts[STRUCTURED_RECORDS_BOUNDARY_COUNT_ARTIFACT_KEY] == 0


def test_excel_extraction_keeps_record_metadata() -> None:
    extraction = run_external_adapter(BACKEND, _sheet(), None, "").extraction
    assert extraction is not None
    assert [element.element_id for element in extraction.elements] == [
        "sheet-000-r1",
        "sheet-000-r3",
        "sheet-000-r4",
    ]
    assert extraction.raw_text.count(f"\n{JSONL_RECORD_BOUNDARY}\n") == 2
    record = extraction.elements[1].metadata
    assert record["sheet_name"] == "表 1"
    assert record["cell_range"] == "A3:C3"
    assert record["sheet_column_roles"] == "既定値=default"
    assert record[STRUCTURED_RECORD_ID_KEY] == "sheet-000-r3"


def test_oversized_record_is_rejected_without_truncation() -> None:
    result = run_external_adapter(BACKEND, _csv("短い", "長" * 9000), None, "")
    assert result.extraction is None
    assert result.warnings == ("structured_records_invalid:record_too_large@line3",)


def test_invalid_jsonl_is_rejected() -> None:
    result = run_external_adapter(BACKEND, b'{"items": [1]}\n', None, "application/json")
    assert result.extraction is None
    assert result.warnings == ("structured_records_invalid:unknown_schema@line1",)


def test_capability_accepts_preprocessed_jsonl_and_is_ordered_before_xml_tree() -> None:
    assert adapter_supports_source(
        BACKEND, source_profile=None, content_type=STRUCTURED_RECORDS_CONTENT_TYPE
    )
    assert not adapter_supports_source(BACKEND, source_profile=None, content_type="application/pdf")
    order = list(ADAPTER_CAPABILITIES)
    assert order.index("unstructured") < order.index(BACKEND) < order.index("xml_tree_json")
