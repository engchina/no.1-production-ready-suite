"""DocRAG LayoutRecord → StructuredExtraction 変換と /parse の options 受け渡し。"""

from unittest.mock import patch

from fastapi.testclient import TestClient
from rag_parser_core.extraction import StructuredExtraction

from app.docrag.extraction import DOCRAG_LAYOUT_ARTIFACT, layout_to_extraction
from app.docrag.layout import LayoutRecord, PageImage
from app.main import app


def _record(record_id: str, category: str, text: str, **raw: object) -> LayoutRecord:
    return LayoutRecord(
        id=record_id,
        engine="docling",
        page=1,
        seq_no=int(record_id.rsplit("-", 1)[-1]),
        bbox=[10.0, 20.0, 110.0, 60.0],
        coord_system="image_top_left",
        page_width=1000,
        page_height=1400,
        category=category,
        text=text,
        raw_type=category.lower(),
        raw=dict(raw),
    )


def test_layout_records_map_to_elements_tables_assets_and_layout_artifact() -> None:
    page = PageImage(
        page=1, width=1000, height=1400, pdf_width=600, pdf_height=840, image_path="/x.png"
    )
    records = [
        _record("docling-p1-1", "Title", "申請手順"),
        _record("docling-p1-2", "Section-header", "入力画面"),
        _record("docling-p1-3", "Text", "受付番号を入力します。"),
        _record("docling-p1-4", "Table", "<table><tr><td>A</td></tr></table>"),
        _record(
            "docling-p1-5",
            "Picture",
            "説明",
            vision_status="succeeded",
            vision_description={"retrieval_text": "登録ボタンの画面"},
        ),
    ]

    extraction = layout_to_extraction(records, [page], source_page_count=1)

    kinds = [element.kind for element in extraction.elements]
    assert kinds == ["title", "title", "text", "table", "figure"]
    body = extraction.elements[2]
    assert body.section_path == ["申請手順", "入力画面"]
    assert body.bbox == [10.0, 20.0, 110.0, 60.0]
    assert body.metadata["page_width"] == 1000.0
    assert body.metadata["bbox_unit"] == "absolute"
    assert extraction.pages[0].width == 1000.0
    assert extraction.tables[0].element_id == "docling-p1-4"
    assert extraction.tables[0].metadata["html"].startswith("<table>")
    assert extraction.assets[0].summary == "登録ボタンの画面"
    assert extraction.elements[4].metadata["vision_retrieval_text"] == "登録ボタンの画面"
    layout = extraction.parser_artifacts[DOCRAG_LAYOUT_ARTIFACT]
    assert isinstance(layout, dict)
    assert [record["id"] for record in layout["records"]] == [r.id for r in records]
    assert "image_path" not in layout["pages"][0]


def test_parse_endpoint_passes_vision_option() -> None:
    captured: dict[str, object] = {}

    def fake_analyze(source_bytes: bytes, **kwargs: object) -> StructuredExtraction:
        captured.update(kwargs, size=len(source_bytes))
        return StructuredExtraction(raw_text="ok")

    with patch("app.main.analyze_source", side_effect=fake_analyze):
        response = TestClient(app).post(
            "/parse",
            files={"file": ("manual.pdf", b"%PDF-1.4", "application/pdf")},
            data={"content_type": "application/pdf", "parser_options": '{"vision_enabled": true}'},
        )

    assert response.status_code == 200
    assert response.json()["extraction"]["raw_text"] == "ok"
    assert captured["vision_enabled"] is True
    assert captured["file_name"] == "manual.pdf"
