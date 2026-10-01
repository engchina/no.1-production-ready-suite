"""文書の章節(章節ナビゲーション)の API(#713)。"""

from typing import Any

import pytest
from rag_parser_core.extraction import (
    DocumentNavigationNode,
    ExtractionPage,
    StructuredExtraction,
)

from app.api.routes import documents as documents_route
from app.clients.oracle import DocumentSectionsConflictError
from app.main import app
from app.rag.document_sections import section_errors, sections_from_navigation
from app.schemas.document import DocumentSection
from tests.support import AsgiTestClient

client = AsgiTestClient(app)
URL = "/api/documents/doc-1/sections"


def _navigation(*, second_pages: tuple[int, int] = (3, 4)) -> list[DocumentNavigationNode]:
    return [
        DocumentNavigationNode(
            section_id="nav-1", title="第1章 総則", depth=1, page_start=1, page_end=4
        ),
        DocumentNavigationNode(
            section_id="nav-2",
            title="第1条 目的",
            depth=2,
            page_start=second_pages[0],
            page_end=second_pages[1],
        ),
        DocumentNavigationNode(
            section_id="nav-3", title="第2章 申請", depth=1, page_start=5, page_end=6
        ),
    ]


class FakeSectionsOracle:
    def __init__(self) -> None:
        self.navigation = _navigation()
        self.stored: dict[str, Any] | None = None

    async def document_exists(self, document_id: str) -> bool:
        return document_id == "doc-1"

    async def ensure_default_document_recipe(self, document_id: str) -> dict[str, Any]:
        return {"recipe_id": "recipe-1"}

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, Any]:
        return {"recipe_id": recipe_id, "active_extraction_recipe_id": "ext-1"}

    async def get_document_extraction_artifact(self, **_: Any) -> dict[str, Any]:
        extraction = StructuredExtraction(
            raw_text="本文",
            pages=[ExtractionPage(page_number=page) for page in range(1, 7)],
            navigation=self.navigation,
        )
        return {"extraction_json": extraction.model_dump(mode="json")}

    async def get_document_sections(self, document_id: str) -> dict[str, Any] | None:
        return self.stored

    async def save_document_sections(
        self, document_id: str, sections: list[dict[str, Any]], *, base_revision: int | None
    ) -> int:
        current = self.stored["revision"] if self.stored else None
        if current != base_revision:
            raise DocumentSectionsConflictError(document_id)
        revision = (current or 0) + 1
        self.stored = {"sections": sections, "revision": revision, "updated_at": None}
        return revision

    async def delete_document_sections(self, document_id: str) -> None:
        self.stored = None


@pytest.fixture
def oracle(monkeypatch: pytest.MonkeyPatch) -> FakeSectionsOracle:
    fake = FakeSectionsOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)
    return fake


def test_sections_come_from_extraction_until_edited(oracle: FakeSectionsOracle) -> None:
    data = client.get(URL).json()["data"]

    assert data["source"] == "extraction"
    assert data["page_count"] == 6
    assert data["revision"] is None
    assert [(s["title"], s["level"], s["page_start"], s["page_end"]) for s in data["sections"]] == [
        ("第1章 総則", 1, 1, 4),
        ("第1条 目的", 2, 3, 4),
        ("第2章 申請", 1, 5, 6),
    ]
    assert {s["origin"] for s in data["sections"]} == {"extraction"}


def test_saved_sections_are_shared_and_refresh_unedited_pages(
    oracle: FakeSectionsOracle,
) -> None:
    sections = client.get(URL).json()["data"]["sections"]
    sections[0] = {**sections[0], "title": "第1章 総則と目的", "edited": True}
    sections.append({"id": "manual-1", "title": "附則", "level": 1, "page_start": 6, "page_end": 6})

    saved = client.put(URL, json={"sections": sections, "base_revision": None})
    assert saved.status_code == 200
    data = saved.json()["data"]
    assert data["source"] == "manual"
    assert data["revision"] == 1
    assert [s["title"] for s in data["sections"]][-1] == "附則"

    # 抽出をやり直してページが変わっても、人が変えていない章節だけ新しい抽出に合わせる。
    oracle.navigation = _navigation(second_pages=(2, 3))
    oracle.navigation[0].page_end = 3
    refreshed = client.get(URL).json()["data"]["sections"]
    assert (refreshed[0]["page_start"], refreshed[0]["page_end"]) == (1, 4)
    assert (refreshed[1]["page_start"], refreshed[1]["page_end"]) == (2, 3)
    assert refreshed[3]["origin"] == "manual"


def test_save_rejects_conflicts_and_invalid_sections(oracle: FakeSectionsOracle) -> None:
    sections = client.get(URL).json()["data"]["sections"]
    assert client.put(URL, json={"sections": sections, "base_revision": None}).status_code == 200

    # 読み込んだ後にほかの操作で保存されていたら 409。
    stale = client.put(URL, json={"sections": sections, "base_revision": None})
    assert stale.status_code == 409

    bad = [{**sections[0], "page_start": 5, "page_end": 2}]
    response = client.put(URL, json={"sections": bad, "base_revision": 1})
    assert response.status_code == 422
    assert "開始ページが終了ページより後ろ" in response.text

    blank = [{**sections[0], "title": "  "}]
    assert client.put(URL, json={"sections": blank, "base_revision": 1}).status_code == 422


def test_reset_returns_to_extraction(oracle: FakeSectionsOracle) -> None:
    sections = client.get(URL).json()["data"]["sections"]
    client.put(URL, json={"sections": sections[:1], "base_revision": None})

    data = client.delete(URL).json()["data"]

    assert data["source"] == "extraction"
    assert len(data["sections"]) == 3
    assert client.get("/api/documents/missing/sections").status_code == 404


def test_section_errors_check_levels_ids_and_page_count() -> None:
    def section(section_id: str, level: int, start: int | None = None) -> DocumentSection:
        return DocumentSection(id=section_id, title=section_id, level=level, page_start=start)

    assert section_errors([section("a", 1), section("b", 2), section("c", 1)], 6) == []
    errors = section_errors([section("a", 1), section("a", 3, start=9)], 6)
    assert any("重複" in error for error in errors)
    assert any("2 段以上深く" in error for error in errors)
    assert any("ページ数(6)" in error for error in errors)


def test_sections_from_navigation_keeps_levels_continuous() -> None:
    """抽出の章節の深さが飛んでいても、階層は 1 段ずつにそろえる。"""
    nodes = [
        DocumentNavigationNode(section_id="a", title="A", depth=1),
        DocumentNavigationNode(section_id="b", title="B", depth=3),
        DocumentNavigationNode(section_id="c", title=" ", depth=1),
    ]
    assert [(s.id, s.level) for s in sections_from_navigation(nodes)] == [("a", 1), ("b", 2)]
