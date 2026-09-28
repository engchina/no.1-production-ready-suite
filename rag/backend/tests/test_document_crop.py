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


def _install_preview(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    """プレビューのページ画像 API 用(原本の読み出しは documents route の ObjectStorageClient)。"""
    detail = SimpleNamespace(
        object_storage_path="docs/doc-1/source",
        preprocess_artifact=None,
        file_name="source.pdf",
        content_type="application/pdf",
    )

    class FakeOracle:
        async def get_document(self, document_id: str) -> object | None:
            return detail if document_id == "doc-1" else None

    class FakeStorage:
        async def get(self, path: str) -> bytes:
            assert path == "docs/doc-1/source"
            return data

    monkeypatch.setattr(documents_route, "OracleClient", FakeOracle)
    monkeypatch.setattr(documents_route, "ObjectStorageClient", FakeStorage)


def _rotated_two_page_pdf() -> bytes:
    document = fitz.open()
    document.new_page(width=600, height=800)
    rotated = document.new_page(width=600, height=800)
    rotated.set_rotation(90)
    data: bytes = document.tobytes()
    document.close()
    return data


def test_preview_pages_lists_page_sizes_with_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    """PDF のページ画像プレビュー: ページ数と、/Rotate を反映した向きの寸法を返す(#349)。"""
    _install_preview(monkeypatch, _rotated_two_page_pdf())

    response = client.get("/api/documents/doc-1/preview-pages")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["page_count"] == 2
    assert data["pages"] == [
        {"page_number": 1, "width": 600.0, "height": 800.0},
        {"page_number": 2, "width": 800.0, "height": 600.0},
    ]


def test_preview_page_image_renders_whole_page_at_requested_dpi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_preview(monkeypatch, _rotated_two_page_pdf())

    first = client.get("/api/documents/doc-1/preview-pages/1", params={"dpi": 72})
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/png"
    assert first.headers["cache-control"] == "private, max-age=300"
    assert _size(first.content) == (600, 800)

    # 回転したページは回転後の向きで描く(解析の bbox と同じ向き)。倍率は dpi / 72。
    rotated = client.get("/api/documents/doc-1/preview-pages/2", params={"dpi": 144})
    assert rotated.status_code == 200
    assert _size(rotated.content) == (1600, 1200)


def test_preview_page_image_rejects_invalid_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_preview(monkeypatch, _pdf_bytes())

    assert client.get("/api/documents/doc-1/preview-pages/2").status_code == 422
    assert client.get("/api/documents/doc-1/preview-pages/0").status_code == 422
    assert (
        client.get("/api/documents/doc-1/preview-pages/1", params={"dpi": 1000}).status_code == 422
    )
    assert client.get("/api/documents/other/preview-pages").status_code == 404
    assert client.get("/api/documents/other/preview-pages/1").status_code == 404


def test_preview_pages_rejects_files_that_are_not_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_preview(monkeypatch, b"\x00\x01not-a-document")

    response = client.get("/api/documents/doc-1/preview-pages")

    assert response.status_code == 422


def test_render_page_png_caps_large_pages() -> None:
    document = fitz.open()
    document.new_page(width=14400, height=14400)
    data: bytes = document.tobytes()
    document.close()

    width, height = _size(document_crop.render_page_png(data, 1, dpi=288))

    assert width * height <= document_crop.MAX_PAGE_PIXELS
    assert abs(width - 4000) <= 1


def test_recipe_preview_pages_use_recipe_prepared_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """レシピのプレビューは、そのレシピのファイル準備後 artifact(変換済み PDF)を描く。"""
    detail = SimpleNamespace(
        object_storage_path="docs/doc-1/source.docx",
        preprocess_artifact=None,
        file_name="source.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    prepared = _pdf_bytes()

    class FakeOracle:
        async def get_document(self, document_id: str) -> object | None:
            return detail if document_id == "doc-1" else None

        async def get_document_recipe(self, document_id: str, recipe_id: str) -> object | None:
            if recipe_id != "recipe-1":
                return None
            return {
                "preprocess_artifact": {
                    "derivation_id": "d-1",
                    "profile": "office_to_pdf",
                    "converted": True,
                    "object_storage_path": "docs/doc-1/recipe-1/prepared.pdf",
                    "content_type": "application/pdf",
                    "file_name": "prepared.pdf",
                }
            }

    class FakeStorage:
        async def get(self, path: str) -> bytes:
            assert path == "docs/doc-1/recipe-1/prepared.pdf"
            return prepared

    monkeypatch.setattr(documents_route, "OracleClient", FakeOracle)
    monkeypatch.setattr(documents_route, "ObjectStorageClient", FakeStorage)

    pages = client.get(
        "/api/documents/doc-1/recipes/recipe-1/preview-pages", params={"variant": "prepared"}
    )
    assert pages.status_code == 200
    assert pages.json()["data"]["page_count"] == 1
    image = client.get(
        "/api/documents/doc-1/recipes/recipe-1/preview-pages/1",
        params={"variant": "prepared", "dpi": 72},
    )
    assert image.status_code == 200
    assert _size(image.content) == (600, 800)
    missing = client.get("/api/documents/doc-1/recipes/other/preview-pages")
    assert missing.status_code == 404
