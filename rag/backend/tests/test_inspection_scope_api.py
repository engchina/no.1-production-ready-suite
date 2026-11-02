"""認可したレシピの原本範囲を両方の閲覧APIへ伝える。"""

from typing import Any

import pytest

from app.api.routes import documents as routes


class ScopedOracle:
    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, Any]:
        assert (document_id, recipe_id) == ("doc-a", "recipe-a")
        return {"active_extraction_recipe_id": "extraction-a", "active_chunk_set_id": "chunks-a"}

    async def get_document_extraction_artifact(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"document_id": "doc-a", "extraction_recipe_id": "extraction-a"}
        return {
            "extraction_json": {
                "raw_text": "全体",
                "elements": [
                    {"element_id": "a", "kind": "text", "text": "範囲A", "order": 0},
                    {"element_id": "b", "kind": "text", "text": "範囲B", "order": 1},
                ],
            }
        }

    async def list_chunk_set_chunk_window(self, chunk_set_id: str, **kwargs: Any):
        assert chunk_set_id == "chunks-a"
        assert kwargs["scope_element_ids"] == ["b"]
        return [], 0


async def scope(*args: Any, **kwargs: Any) -> dict[str, Any]:
    assert args[0:2] == ("doc-a", "recipe-a")
    assert kwargs == {"offset": 8192, "revision": "source-b", "variant": "prepared"}
    return {"element_ids": ["b"], "scope_status": "exact"}


async def test_text_view_uses_source_ids_before_returning_text(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes, "OracleClient", ScopedOracle)
    monkeypatch.setattr(routes, "_inspector_source_scope", scope)
    response = await routes.document_recipe_extraction_view(
        "doc-a", "recipe-a", source_offset=8192, source_revision="source-b", variant="prepared"
    )
    assert response.data["scope_status"] == "exact"
    assert response.data["payload"]["raw_text"] == "範囲B"


async def test_chunk_view_propagates_only_current_source_ids(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes, "OracleClient", ScopedOracle)
    monkeypatch.setattr(routes, "_inspector_source_scope", scope)
    response = await routes.document_recipe_chunk_window(
        "doc-a", "recipe-a", source_offset=8192, source_revision="source-b", variant="prepared"
    )
    assert response.data["scope_status"] == "exact"
    assert response.data["items"] == []


@pytest.mark.parametrize("recipe_id", [None, "recipe-a"])
async def test_text_window_uses_same_decoder_with_or_without_recipe(
    monkeypatch: pytest.MonkeyPatch, recipe_id: str | None
):
    from types import SimpleNamespace

    from app.schemas.document import DocumentTextWindowRequest

    text = "合成の本文です。文字コードを検出して表示します。" * 20
    detail = SimpleNamespace(preprocess_artifact=None)

    class TextOracle:
        async def get_document(self, document_id: str):
            assert document_id == "doc-a"
            return detail

        async def get_document_recipe(self, document_id: str, recipe_id: str):
            return None

    async def recipe_detail(*args: Any):
        return detail, None

    async def content(*args: Any, **kwargs: Any):
        return text.encode("cp932"), "text/plain", "synthetic.txt"

    monkeypatch.setattr(routes, "OracleClient", TextOracle)
    monkeypatch.setattr(routes, "_recipe_detail_and_artifact", recipe_detail)
    monkeypatch.setattr(routes, "load_document_content", content)
    response = await routes.document_recipe_text_window(
        "doc-a", DocumentTextWindowRequest(), recipe_id
    )
    assert response.data["text"] == text
