"""合成の記録IDと派生hashで構造化原文を固定する。"""

import hashlib
from typing import Any

import pytest
from rag_parser_core.structured_records import (
    DocumentLine,
    RecordField,
    RecordLocation,
    RowLine,
    StructuredRecordsError,
    dump_structured_records,
    parse_structured_records,
    structured_records_extraction,
)

from app.api.errors import SourceVersionStaleError
from app.services.source_reader import reading_window
from app.services.structured_source_reader import enrich_structured_window


def fixture() -> tuple[bytes, dict[str, Any]]:
    data = dump_structured_records(
        [
            DocumentLine(
                record_id="doc",
                source_type="csv",
                converter="csv_to_jsonl",
                converter_version="v2",
                header_row=1,
            ),
            *[
                RowLine(
                    record_id=f"r{i}",
                    source_type="csv",
                    location=RecordLocation(row_start=i + 1, row_end=i + 1, record=i + 1),
                    fields=[
                        RecordField(name="項目", position=1, value="同じ値😀"),
                        RecordField(name="備考", position=2, value="前\n後"),
                    ],
                )
                for i in (1, 2)
            ],
        ]
    )
    document = parse_structured_records(data)
    assert not isinstance(document, StructuredRecordsError)
    extraction = structured_records_extraction(
        document,
        source_parser="structured_records_jsonl",
        parser_backend="structured_records_jsonl",
        parser_version="v1",
    ).model_dump(mode="json")
    extraction["parser_artifacts"]["source_derivation"] = {
        "derived_sha256": hashlib.sha256(data).hexdigest()
    }
    return data, extraction


def test_selected_record_uses_saved_row_with_duplicate_values_and_multiline_cell() -> None:
    data, payload = fixture()
    window = reading_window(payload, revision="v1", anchor_ids=["r2"], quote="")
    result = enrich_structured_window(window, payload, data)
    selected = next(block for block in result["blocks"] if block["cited"])
    assert selected["id"] == "r2"
    assert selected["record"]["parts"][0]["rows"] == [
        {"label": "3", "cells": ["同じ値😀", "前\n後"]}
    ]
    assert selected["record"]["location"]["row_start"] == 3
    assert result["revision"] == "v1"


def test_changed_derived_file_never_reuses_record_coordinates() -> None:
    data, payload = fixture()
    window = reading_window(payload, revision="v1", anchor_ids=["r2"], quote="")
    with pytest.raises(SourceVersionStaleError):
        enrich_structured_window(window, payload, data + b" ")


def native_fixture(source_type: str) -> tuple[bytes, dict[str, Any]]:
    from rag_parser_core.sheet_records import (
        SheetBlock,
        SheetColumn,
        SheetRecords,
        SheetRecordsDocument,
    )
    from rag_parser_core.structured_records import (
        PassageLine,
        WordLocation,
        WordParagraph,
        WordTable,
        sheet_records_to_lines,
    )

    lines: list[Any]
    if source_type == "xlsx":
        lines = list(
            sheet_records_to_lines(
                SheetRecordsDocument(
                    source_format="xlsx",
                    sheets=[
                        SheetRecords(
                            name="シートA",
                            header_row=1,
                            columns=[SheetColumn(name="項目", column="A")],
                            blocks=[
                                SheetBlock(
                                    kind="row",
                                    row_start=4,
                                    row_end=4,
                                    cell_range="A4:A4",
                                    values={"項目": "同じ値"},
                                )
                            ],
                        )
                    ],
                ),
                converter="excel_to_jsonl",
                converter_version="v2",
            )
        )
    else:
        lines = [
            DocumentLine(
                record_id="doc",
                source_type="docx",
                converter="word_to_jsonl",
                converter_version="v1",
                location_basis="original",
            ),
            PassageLine(
                record_id="r1",
                source_type="docx",
                location=WordLocation(
                    body_start=3, body_end=4, paragraph_start=3, paragraph_end=3, table_indices=[1]
                ),
                blocks=[
                    WordParagraph(text="合成段落", body_index=3, paragraph_index=3),
                    WordTable(
                        body_index=4,
                        table_index=1,
                        header_rows=1,
                        rows=[["項目"], ["値"]],
                        row_start=1,
                        row_end=2,
                    ),
                ],
            ),
        ]
    data = dump_structured_records(lines)
    document = parse_structured_records(data)
    assert not isinstance(document, StructuredRecordsError)
    payload = structured_records_extraction(
        document,
        source_parser="structured_records_jsonl",
        parser_backend="structured_records_jsonl",
        parser_version="v1",
    ).model_dump(mode="json")
    payload["parser_artifacts"]["source_derivation"] = {
        "derived_sha256": hashlib.sha256(data).hexdigest()
    }
    return data, payload


@pytest.mark.parametrize("source_type", ["xlsx", "docx"])
def test_native_excel_and_word_locations_are_preserved(source_type: str) -> None:
    data, payload = native_fixture(source_type)
    window = reading_window(
        payload, revision="v1", anchor_ids=[payload["elements"][-1]["element_id"]], quote=""
    )
    result = enrich_structured_window(window, payload, data)
    selected = next(block["record"] for block in result["blocks"] if block["cited"])
    assert selected["parts"]
    if source_type == "xlsx":
        assert selected["location"]["sheet_name"] == "シートA"
        assert selected["location"]["cell_range"] == "A4:A4"
    else:
        assert selected["location"]["word_paragraph_start"] == 3
        assert selected["parts"][1]["rows"] == [{"label": "2", "cells": ["値"]}]


def test_old_record_without_source_hash_stays_readable_without_native_coordinates() -> None:
    data, payload = fixture()
    payload["parser_artifacts"].pop("source_derivation")
    window = reading_window(payload, revision="v1", anchor_ids=["r2"], quote="")
    assert enrich_structured_window(window, payload, data) == window
    assert all("record" not in block for block in window["blocks"])


def test_reviewed_text_does_not_reuse_derived_cell_mapping() -> None:
    data, payload = fixture()
    payload["elements"][-1]["text"] = "修正した合成本文"
    window = reading_window(payload, revision="v1", anchor_ids=["r2"], quote="")
    result = enrich_structured_window(window, payload, data)
    assert "record" not in next(block for block in result["blocks"] if block["cited"])


def test_long_record_keeps_bounded_text_without_expanding_full_table() -> None:
    data, _ = fixture()
    document = parse_structured_records(data)
    assert not isinstance(document, StructuredRecordsError)
    row = next(line for line in document.lines if isinstance(line, RowLine))
    row.fields[0].value = "合成の長い値" * 3000
    data = dump_structured_records([document.document, *document.lines])
    document = parse_structured_records(data)
    assert not isinstance(document, StructuredRecordsError)
    payload = structured_records_extraction(
        document,
        source_parser="structured_records_jsonl",
        parser_backend="structured_records_jsonl",
        parser_version="v1",
    ).model_dump(mode="json")
    payload["parser_artifacts"]["source_derivation"] = {
        "derived_sha256": hashlib.sha256(data).hexdigest()
    }
    window = reading_window(payload, revision="v1", anchor_ids=[row.record_id], quote="")
    result = enrich_structured_window(window, payload, data)
    selected = next(block for block in result["blocks"] if block["cited"])
    assert selected["record"]["parts"] == []
    assert selected["text"] == next(block["text"] for block in window["blocks"] if block["cited"])
    assert len(selected["text"]) <= 8192
