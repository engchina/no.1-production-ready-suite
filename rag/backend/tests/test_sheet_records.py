"""Excel の行の記録（前処理 excel_to_json v2）を要素・chunk にし、場所を残す（#1221）。

前処理のサービス（openpyxl）は別の venv で動くため、ここでは前処理が作る JSON
（`SheetRecordsDocument`）を直接作り、parser の側（rag_parser_core）と chunking
（rag_pipeline_core）と前処理の契約を確かめる。
データはすべて合成。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from rag_parser_core.capabilities import adapter_supports_source
from rag_parser_core.preprocess import ConvertHealth, ConvertOutcome, ConvertResponse
from rag_parser_core.preprocess_service import create_preprocess_app
from rag_parser_core.registry import run_external_adapter
from rag_parser_core.sheet_records import (
    SHEET_RECORDS_CONTENT_TYPE,
    SheetBlock,
    SheetPreambleRow,
    SheetRecords,
    SheetRecordsDocument,
)
from rag_parser_core.source import SourceProfile

from app.rag.chunking import chunk_extraction_with_strategy
from app.rag.source_profile import build_source_profile


def _xlsx_profile() -> SourceProfile:
    return build_source_profile(
        original_file_name="経費コード.xlsx",
        sanitized_file_name="経費コード.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_size_bytes=16,
        content_sha256="a" * 64,
    )


def _document() -> SheetRecordsDocument:
    rows = [
        SheetBlock(
            kind="row",
            row_start=row,
            row_end=row,
            cell_range=f"A{row}:C{row}",
            values={"コード": f"A0{row}", "名称": f"費目{row}", "区分": "旅費"},
        )
        for row in (3, 4, 6)
    ]
    return SheetRecordsDocument(
        source_format="xlsx",
        sheets=[
            SheetRecords(
                name="コード表",
                header_row=2,
                mode="table",
                preamble=[
                    SheetPreambleRow(row_number=1, cell_range="A1:A1", text="費目のコードの一覧")
                ],
                blocks=rows,
            ),
            SheetRecords(
                name="手順",
                header_row=1,
                mode="procedure",
                blocks=[
                    SheetBlock(
                        kind="procedure_step",
                        row_start=3,
                        row_end=4,
                        cell_range="A3:D4",
                        section_path=["事前準備", "アカウントの登録"],
                        lines=[
                            "セクション: 事前準備",
                            "作業項目: アカウントの登録",
                            "作業内容: 権限",
                        ],
                    )
                ],
            ),
        ],
    )


def test_every_adapter_accepts_sheet_records_without_external_parser() -> None:
    """Docling（PDF と画像だけ）でも、前処理の行の記録は rag_parser_core が要素にする。"""
    profile = _xlsx_profile()
    assert adapter_supports_source("docling", source_profile=profile, content_type="") is False
    # 行の記録を扱う parser のサービスは Docling と Unstructured だけ。
    assert not adapter_supports_source(
        "mineru", source_profile=profile, content_type=SHEET_RECORDS_CONTENT_TYPE
    )
    for backend in ("docling", "unstructured"):
        assert adapter_supports_source(
            backend, source_profile=profile, content_type=f"{SHEET_RECORDS_CONTENT_TYPE}; x=1"
        )

    result = run_external_adapter(
        "docling", _document().to_json_bytes(), profile, SHEET_RECORDS_CONTENT_TYPE
    )

    assert result.extraction is not None
    assert result.parser_version == "sheet_records_v1"
    elements = result.extraction.elements
    # シート名だけの見出しの要素は作らない（節と文脈の見出しに入る）。
    assert all(element.kind == "text" for element in elements)
    assert all(element.metadata.get("sheet_name") for element in elements)
    record = next(element for element in elements if element.metadata.get("row_start") == 3)
    assert record.text == "コード: A03 / 名称: 費目3 / 区分: 旅費"
    assert record.section_path == ["コード表"]
    assert record.content_kind == "record"
    assert record.metadata["cell_range"] == "A3:C3"
    assert (record.metadata["cell_column_start"], record.metadata["cell_column_end"]) == ("A", "C")
    step = next(
        element
        for element in elements
        if element.section_path[:1] == ["手順"] and element.kind == "text"
    )
    assert step.section_path == ["手順", "事前準備", "アカウントの登録"]
    assert step.text.splitlines()[0] == "セクション: 事前準備"
    assert (step.metadata["row_start"], step.metadata["row_end"]) == (3, 4)

    broken = run_external_adapter("docling", b"{}", profile, SHEET_RECORDS_CONTENT_TYPE)
    assert broken.extraction is None
    assert "sheet_records_invalid" in broken.warnings


def test_chunks_carry_sheet_rows_and_cell_ranges() -> None:
    result = run_external_adapter(
        "docling", _document().to_json_bytes(), _xlsx_profile(), SHEET_RECORDS_CONTENT_TYPE
    )
    assert result.extraction is not None

    chunks = chunk_extraction_with_strategy(
        result.extraction, strategy="structure_aware", chunk_size=800, overlap=0
    )

    by_text = {chunk.text.splitlines()[0]: chunk.metadata for chunk in chunks}
    rows = next(meta for text, meta in by_text.items() if text.startswith("コード: A03"))
    # 同じシートの行は 1 chunk にまとまり、行の範囲とセル範囲が残る（空行の 5 行目も範囲に含む）。
    assert rows["sheet_name"] == "コード表"
    assert (rows["row_start"], rows["row_end"], rows["cell_range"]) == (3, 6, "A3:C6")
    # 表頭より上の説明は本文の chunk になる。シート名だけの chunk は作らない。
    preamble = next(chunk.metadata for chunk in chunks if "費目のコードの一覧" in chunk.text)
    assert all(chunk.metadata.get("sheet_name") for chunk in chunks)
    assert (preamble["row_start"], preamble["cell_range"]) == (1, "A1:A1")
    step = next(meta for text, meta in by_text.items() if text == "セクション: 事前準備")
    assert step["sheet_name"] == "手順"
    assert (step["row_start"], step["row_end"], step["cell_range"]) == (3, 4, "A3:D4")
    assert step["section_path"] == "手順 > 事前準備 > アカウントの登録"


def test_preprocess_service_passes_options_only_to_converters_that_take_them() -> None:
    received: dict[str, Any] = {}

    def with_options(
        source_bytes: bytes,
        content_type: str,
        preprocess_profile: str,
        source_profile: SourceProfile | None,
        *,
        options: dict[str, Any] | None = None,
    ) -> ConvertOutcome:
        received["options"] = options
        return ConvertOutcome(
            converted=True,
            converter_name="stub",
            converter_version="v1",
            derived_bytes=b"{}",
            derived_content_type=SHEET_RECORDS_CONTENT_TYPE,
        )

    def without_options(
        source_bytes: bytes,
        content_type: str,
        preprocess_profile: str,
        source_profile: SourceProfile | None,
    ) -> ConvertOutcome:
        return ConvertOutcome.passthrough(reason="stub")

    def health() -> ConvertHealth:
        return ConvertHealth(status="ok", supported_profiles=["excel_to_json"])

    payload = {
        "content_type": "application/vnd.ms-excel",
        "preprocess_profile": "excel_to_json",
        "options": json.dumps({"header_row": 2, "mode": "table"}),
    }
    files = {"file": ("a.xlsx", b"PK", "application/octet-stream")}
    client = TestClient(create_preprocess_app(converter=with_options, health_probe=health))
    response = client.post("/convert", files=files, data=payload)
    assert response.status_code == 200
    assert ConvertResponse.model_validate(response.json()).converted is True
    assert received["options"] == {"header_row": 2, "mode": "table"}

    # 選択肢の無い前処理（office_to_pdf など）は options を受け取らない。
    other = TestClient(create_preprocess_app(converter=without_options, health_probe=health))
    assert other.post("/convert", files=files, data=payload).status_code == 200


def test_recipe_excel_options_reach_the_preprocess_service(monkeypatch: Any) -> None:
    """文書レシピの excel_options が設定に解決され、前処理のサービスへ JSON で渡る。"""
    import httpx

    from app.clients.preprocess_service import PreprocessServiceClient
    from app.config import Settings
    from app.rag.kb_adapter_config import KnowledgeBaseAdapterConfig
    from app.rag.preprocess_strategy import preprocess_options
    from app.rag.variant_keys import compute_extraction_recipe_id
    from app.schemas.document import DocumentProcessingConfig

    recipe = DocumentProcessingConfig.model_validate(
        {"preprocess_profile": "excel_to_json", "excel_options": {"mode": "table", "header_row": 2}}
    )
    config = KnowledgeBaseAdapterConfig(ingestion=recipe)
    base = Settings(_env_file=None, rag_preprocess_enabled=True)
    settings = base.model_copy(update=config.settings_overrides("ingestion"))
    options = preprocess_options(settings, "excel_to_json")
    assert options is not None
    assert (options["mode"], options["header_row"]) == ("table", 2)
    assert preprocess_options(settings, "office_to_pdf") is None

    # excel_to_json のときだけ、選択肢が抽出の ID を変える。
    other = base.model_copy(
        update={"rag_preprocess_profile": "excel_to_json", "rag_preprocess_excel_options": {}}
    )
    assert compute_extraction_recipe_id("s" * 64, settings) != compute_extraction_recipe_id(
        "s" * 64, other
    )
    pdf = base.model_copy(update={"rag_preprocess_profile": "office_to_pdf"})
    assert compute_extraction_recipe_id("s" * 64, pdf) == compute_extraction_recipe_id(
        "s" * 64, pdf.model_copy(update={"rag_preprocess_excel_options": {"mode": "table"}})
    )

    sent: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8", errors="replace")
        sent["has_options"] = 'name="options"' in body and '"header_row": 2' in body
        return httpx.Response(
            200,
            json={
                "converted": True,
                "converter_name": "excel_to_json",
                "converter_version": "v2",
                "derived_content_base64": "e30=",
                "derived_content_type": SHEET_RECORDS_CONTENT_TYPE,
            },
        )

    transport = httpx.MockTransport(handler)
    original = httpx.Client.__init__

    def patched(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = transport
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", patched)
    PreprocessServiceClient(settings).convert(
        b"PK", content_type="application/octet-stream", profile="excel_to_json", options=options
    )
    assert sent["has_options"] is True

    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DocumentProcessingConfig.model_validate({"excel_options": {"header_row": 0}})
    with pytest.raises(ValidationError):
        DocumentProcessingConfig.model_validate({"excel_options": {"unknown": 1}})


def test_answer_records_carry_sheet_location_for_citations() -> None:
    """回答フローへ渡す chunk に表計算の場所を入れ、親はシートの範囲をまとめる（#1224）。"""
    from app.rag.answer_engine import _merged_sheet_location, _sheet_location

    assert _sheet_location(
        {"sheet_name": "費目コード", "row_start": 3, "row_end": 6, "cell_range": "A3:D6"}
    ) == {"sheet_name": "費目コード", "row_start": 3, "row_end": 6, "cell_range": "A3:D6"}
    assert _sheet_location({"page_start": 2}) is None
    merged = _merged_sheet_location(
        [
            {"sheet_name": "費目コード", "cell_range": "B3:D4"},
            {"sheet_name": "費目コード", "cell_range": "A6:C9"},
        ]
    )
    assert merged == {
        "sheet_name": "費目コード",
        "row_start": 3,
        "row_end": 9,
        "cell_range": "A3:D9",
    }
    # 別のシートが混ざる親は場所をまとめない（頁と同じく、確かな場所だけを出す）。
    assert _merged_sheet_location([{"sheet_name": "a"}, {"sheet_name": "b"}]) is None
