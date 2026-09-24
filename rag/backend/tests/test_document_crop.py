"""解析結果プレビュー用の crop API(PDF / 画像の bbox 切り出し)。"""

from types import SimpleNamespace

import fitz  # type: ignore[import-untyped]
import pytest

from app.api.routes import documents as documents_route
from app.main import app
from app.rag import document_crop
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def _pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(100, 100, 300, 200), color=(1, 0, 0), fill=(1, 0, 0))
    data: bytes = document.tobytes()
    document.close()
    return data


def _png_bytes() -> bytes:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100), 0)
    pixmap.clear_with(255)
    return bytes(pixmap.tobytes("png"))


def _install(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    detail = SimpleNamespace(object_storage_path="docs/doc-1/source", preprocess_artifact=None)

    class FakeOracle:
        async def get_document(self, document_id: str) -> object | None:
            return detail if document_id == "doc-1" else None

    class FakeStorage:
        async def get(self, path: str) -> bytes:
            assert path == "docs/doc-1/source"
            return data

    monkeypatch.setattr(documents_route, "OracleClient", FakeOracle)
    monkeypatch.setattr(document_crop, "ObjectStorageClient", FakeStorage)


def _size(png: bytes) -> tuple[int, int]:
    pixmap = fitz.Pixmap(png)
    return pixmap.width, pixmap.height


def test_crop_pdf_region_from_page_image_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _pdf_bytes())

    # 1200x1600 px のページ画像座標(2 倍)で指定した bbox は PDF の 100..300 x 100..200 pt。
    response = client.get(
        "/api/documents/doc-1/crop",
        params={
            "page": 1,
            "x0": 200,
            "y0": 200,
            "x1": 600,
            "y1": 400,
            "page_width": 1200,
            "page_height": 1600,
            "dpi": 72,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert _size(response.content) == (200, 100)


def test_crop_image_source_and_invalid_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _png_bytes())
    ok = client.get(
        "/api/documents/doc-1/crop",
        params={
            "page": 1,
            "x0": 0,
            "y0": 0,
            "x1": 100,
            "y1": 50,
            "page_width": 200,
            "page_height": 100,
        },
    )
    assert ok.status_code == 200

    reversed_box = client.get(
        "/api/documents/doc-1/crop",
        params={
            "page": 1,
            "x0": 50,
            "y0": 0,
            "x1": 10,
            "y1": 50,
            "page_width": 200,
            "page_height": 100,
        },
    )
    assert reversed_box.status_code == 422
    beyond_page = client.get(
        "/api/documents/doc-1/crop",
        params={
            "page": 3,
            "x0": 0,
            "y0": 0,
            "x1": 10,
            "y1": 10,
            "page_width": 200,
            "page_height": 100,
        },
    )
    assert beyond_page.status_code == 422
    missing = client.get(
        "/api/documents/other/crop",
        params={
            "page": 1,
            "x0": 0,
            "y0": 0,
            "x1": 10,
            "y1": 10,
            "page_width": 200,
            "page_height": 100,
        },
    )
    assert missing.status_code == 404
