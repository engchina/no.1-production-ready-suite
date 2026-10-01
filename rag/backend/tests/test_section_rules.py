"""章節の抽出規則(#715)。"""

from pathlib import Path
from typing import Any

import pytest
from rag_parser_core.extraction import ExtractionPage, StructuredExtraction

from app.api.routes import documents as documents_route
from app.main import app
from app.rag.section_rules import PRESET_RULES, SectionRule, sections_from_rules
from app.schemas.extraction import DocumentElement
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def _element(order: int, text: str, page: int | None = None, kind: str = "text") -> DocumentElement:
    return DocumentElement(order=order, text=text, page_number=page, kind=kind)


LEGAL = [
    _element(0, "経費規程", 1),
    _element(1, "第１章　総則", 1),
    _element(2, "第1条（目的）", 1),
    _element(3, "この規程は経費の扱いを定める。", 2),
    _element(4, "第2章 申請", 3),
    _element(5, "第三条の二 期限", 3),
    _element(6, "第4条に定めるとおり処理する。", 4),
    _element(7, "第3章 雑則", 5),
]


def _outline(elements: list[DocumentElement], rules: list[SectionRule]) -> list[tuple[Any, ...]]:
    return [
        (section.title, section.level, section.page_start, section.page_end)
        for section in sections_from_rules(elements, rules)
    ]


def test_legal_preset_finds_chapters_and_articles_with_page_ranges() -> None:
    assert _outline(LEGAL, PRESET_RULES["legal"]) == [
        ("第１章　総則", 1, 1, 2),
        ("第1条（目的）", 2, 1, 2),
        ("第2章 申請", 1, 3, 4),
        ("第三条の二 期限", 2, 3, 4),
        ("第3章 雑則", 1, 5, 5),
    ]


def test_numbered_preset_and_custom_rules() -> None:
    elements = [
        _element(0, "# サンプル商事 経費精算マニュアル"),
        _element(1, "## 1. 対象"),
        _element(2, "1.1 範囲"),
        _element(3, "## 2. 締め日と支払日"),
    ]
    numbered = _outline(elements, PRESET_RULES["numbered"])
    assert [(title, level) for title, level, *_ in numbered] == [
        ("1. 対象", 1),
        ("1.1 範囲", 2),
        ("2. 締め日と支払日", 1),
    ]
    custom = [
        SectionRule(name="題", pattern="^サンプル商事", level=1),
        SectionRule(name="番号", pattern=r"^[0-9]+\.\s", level=2),
    ]
    assert [(title, level) for title, level, *_ in _outline(elements, custom)] == [
        ("サンプル商事 経費精算マニュアル", 1),
        ("1. 対象", 2),
        ("2. 締め日と支払日", 2),
    ]


def test_tables_and_long_lines_are_not_headings() -> None:
    elements = [
        _element(0, "第1章 表", kind="table"),
        _element(1, "第2章 " + "長" * 100),
        _element(2, "第3章 本則"),
    ]
    assert [title for title, *_ in _outline(elements, PRESET_RULES["legal"])] == ["第3章 本則"]


def test_invalid_pattern_is_rejected() -> None:
    with pytest.raises(ValueError, match="正規表現として読めません"):
        SectionRule(name="壊れた規則", pattern="^第(", level=1)


@pytest.fixture
def rules_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "section-rules.json"
    monkeypatch.setenv("RAG_SECTION_RULES_FILE", str(path))
    return path


def test_settings_save_reset_and_reject_invalid_patterns(rules_file: Path) -> None:
    data = client.get("/api/settings/section-rules").json()["data"]
    assert data["mode"] == "parser"
    assert set(data["presets"]) == {"legal", "official", "numbered"}

    saved = client.patch(
        "/api/settings/section-rules",
        json={"mode": "custom", "rules": [{"name": "章", "pattern": "^第[0-9]+章", "level": 1}]},
    )
    assert saved.status_code == 200
    assert saved.json()["data"]["mode"] == "custom"
    assert rules_file.exists()

    bad = client.patch(
        "/api/settings/section-rules",
        json={"mode": "custom", "rules": [{"name": "壊れた規則", "pattern": "^第(", "level": 1}]},
    )
    assert bad.status_code == 422

    assert client.delete("/api/settings/section-rules").json()["data"]["mode"] == "parser"
    assert not rules_file.exists()


class RulesOracle:
    def __init__(self, processing_config: dict[str, Any]) -> None:
        self.processing_config = processing_config

    async def document_exists(self, document_id: str) -> bool:
        return True

    async def ensure_default_document_recipe(self, document_id: str) -> dict[str, Any]:
        return {"recipe_id": "recipe-1"}

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, Any]:
        return {
            "recipe_id": recipe_id,
            "active_extraction_recipe_id": "ext-1",
            "processing_config": self.processing_config,
        }

    async def get_document_extraction_artifact(self, **_: Any) -> dict[str, Any]:
        extraction = StructuredExtraction(
            raw_text="",
            elements=[element.model_dump() for element in LEGAL],
            pages=[ExtractionPage(page_number=page) for page in range(1, 6)],
        )
        return {"extraction_json": extraction.model_dump(mode="json")}

    async def get_document_sections(self, document_id: str) -> None:
        return None


def test_recipe_mode_overrides_the_global_default_and_preview_does_not_save(
    rules_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents_route, "OracleClient", lambda: RulesOracle({}))
    data = client.get("/api/documents/doc-1/sections").json()["data"]
    assert data["rules_mode"] == "parser"

    # 処理レシピの方式が全体の既定(解析エンジンの見出し)より優先する。
    oracle = RulesOracle({"section_rules_mode": "legal"})
    monkeypatch.setattr(documents_route, "OracleClient", lambda: oracle)
    data = client.get("/api/documents/doc-1/sections").json()["data"]
    assert data["rules_mode"] == "legal"
    assert [s["title"] for s in data["sections"]][0] == "第１章　総則"

    preview = client.post(
        "/api/documents/doc-1/sections/preview",
        json={"mode": "custom", "rules": [{"name": "章", "pattern": "^第[0-9]+章", "level": 1}]},
    ).json()["data"]
    assert [s["title"] for s in preview["sections"]] == ["第１章　総則", "第2章 申請", "第3章 雑則"]
    assert not rules_file.exists()
