"""保存しない章節候補の確認。合成の抽出結果を使い、書き込みを認めない。"""

from pathlib import Path
from typing import Any

import pytest
from rag_parser_core.extraction import DocumentNavigationNode, ExtractionPage, StructuredExtraction

from app.api.routes import documents as documents_route
from app.main import app
from app.schemas.extraction import DocumentElement
from tests.security_support import enable_production_auth, login
from tests.support import AsgiTestClient

client = AsgiTestClient(app)
URL = "/api/documents/doc-1/sections/preview"


class PreviewOracle:
    def __init__(self) -> None:
        self.visible = True
        self.available = True
        self.headings = True
        self.manual = False
        self.mode: str | None = None

    async def document_exists(self, document_id: str) -> bool:
        return self.visible and document_id == "doc-1"

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, Any] | None:
        if document_id != "doc-1" or recipe_id != "recipe-1":
            return None
        return {
            "active_extraction_recipe_id": "ext-1" if self.available else None,
            "processing_config": {"section_rules_mode": self.mode},
        }

    async def get_document_extraction_artifact(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"document_id": "doc-1", "extraction_recipe_id": "ext-1"}
        navigation = (
            [
                DocumentNavigationNode(
                    section_id="nav-a", title="概要", depth=1, page_start=1, page_end=3
                ),
                DocumentNavigationNode(
                    section_id="nav-b", title="設定", depth=2, page_start=2, page_end=2
                ),
            ]
            if self.headings
            else []
        )
        extraction = StructuredExtraction(
            raw_text="合成の本文",
            navigation=navigation,
            pages=[ExtractionPage(page_number=i) for i in range(1, 4)],
            elements=[
                DocumentElement(order=i, page_number=i + 1, text=text, kind="text")
                for i, text in enumerate(["1. 概要", "1.1 設定", "第1章 補足"])
            ],
        )
        return {"extraction_json": extraction.model_dump(mode="json")}

    async def get_document_sections(self, document_id: str) -> dict[str, Any] | None:
        return {"sections": [{"title": "人が編集した章節"}]} if self.manual else None

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"プレビューは保存や既定レシピの作成を行わない: {name}")


@pytest.fixture
def preview_oracle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PreviewOracle:
    monkeypatch.setenv("RAG_SECTION_RULES_FILE", str(tmp_path / "rules.json"))
    oracle = PreviewOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: oracle)
    return oracle


@pytest.mark.parametrize(
    "mode,count", [("parser", 2), ("numbered", 2), ("official", 2), ("legal", 1), ("custom", 1)]
)
def test_preview_uses_unsaved_rules_without_saving(
    mode: str, count: int, preview_oracle: PreviewOracle, tmp_path: Path
) -> None:
    preview_oracle.manual = True
    preview_oracle.mode = "parser"
    response = client.post(
        URL,
        json={
            "recipe_id": "recipe-1",
            "mode": mode,
            "rules": [{"name": "階層1", "pattern": r"^1\.\s", "level": 1}],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["sections"]) == count
    assert data["extraction_available"] is True and data["manual_edits_present"] is True
    assert data["recipe_overrides_mode"] is True and data["effective_mode"] == "parser"
    assert data["mode"] == mode and data["page_count"] == 3
    assert data["sections"][0]["page_start"] == (3 if mode == "legal" else 1)
    assert all(s["title"] != "人が編集した章節" for s in data["sections"])
    assert not (tmp_path / "rules.json").exists()
    if mode == "parser":
        assert [(s["title"], s["level"]) for s in data["sections"]] == [("概要", 1), ("設定", 2)]
    if mode == "numbered":
        assert [(s["title"], s["level"]) for s in data["sections"]] == [
            ("1. 概要", 1),
            ("1.1 設定", 2),
        ]


def test_preview_distinguishes_no_headings_from_no_extraction(
    preview_oracle: PreviewOracle,
) -> None:
    preview_oracle.headings = False
    body = {"recipe_id": "recipe-1", "mode": "parser"}
    data = client.post(URL, json=body).json()["data"]
    assert data["sections"] == [] and data["extraction_available"] is True
    assert data["recipe_overrides_mode"] is False
    assert len(client.post(URL, json={**body, "mode": "numbered"}).json()["data"]["sections"]) == 2
    preview_oracle.available = False
    data = client.post(URL, json=body).json()["data"]
    assert data["sections"] == [] and data["extraction_available"] is False


def test_preview_keeps_document_and_recipe_access_boundaries(preview_oracle: PreviewOracle) -> None:
    body = {"recipe_id": "recipe-other", "mode": "parser"}
    assert client.post(URL, json=body).status_code == 404
    preview_oracle.visible = False
    assert client.post(URL, json={**body, "recipe_id": "recipe-1"}).status_code == 404
    assert client.post(URL, json={"mode": "parser"}).status_code == 422


def test_preview_requires_document_workspace_permission(
    preview_oracle: PreviewOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("settings-only", ["menu.settings_parser_adapters"])
    denied = login(client, "settings-only")
    assert (
        client.post(
            URL, json={"recipe_id": "recipe-1", "mode": "parser"}, headers=denied
        ).status_code
        == 403
    )
    auth.user_with_permissions("document-reader", ["menu.file_list"])
    allowed = login(client, "document-reader")
    result = client.post(URL, json={"recipe_id": "recipe-1", "mode": "parser"}, headers=allowed)
    assert result.status_code == 200, result.text
