"""原本と正式レシピの入口を維持し、文書単位の処理後ファイルを拒否する。"""

from types import SimpleNamespace
from typing import Any, Literal

import pytest
from fastapi import HTTPException

from app.api.routes import documents
from app.schemas.document import DocumentPreprocessArtifact, DocumentTextWindowRequest

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("route", ["content", "pages", "page", "window"])
@pytest.mark.parametrize("variant", ["prepared", "original"])
async def test_document_level_prepared_requires_formal_scope(
    monkeypatch: pytest.MonkeyPatch, route: str, variant: Literal["original", "prepared"]
) -> None:
    artifact = DocumentPreprocessArtifact(
        derivation_id="legacy",
        profile="passthrough",
        file_name="synthetic.txt",
        object_storage_path="local://legacy",
        content_type="text/plain",
    )
    detail = SimpleNamespace(
        id="d1", object_storage_path="local://original", preprocess_artifact=artifact
    )
    called: list[str | None] = []

    class Oracle:
        async def get_document(self, doc_id: str) -> Any:
            return detail

    async def served(*args: Any, **kwargs: Any) -> Any:
        called.append(
            kwargs["preprocess_artifact"].object_storage_path
            if kwargs["preprocess_artifact"]
            else None
        )
        return "served"

    async def load(*args: Any, **kwargs: Any) -> Any:
        called.append(
            kwargs["preprocess_artifact"].object_storage_path
            if kwargs["preprocess_artifact"]
            else None
        )
        return b"original text", "synthetic.txt", "text/plain"

    monkeypatch.setattr(documents, "OracleClient", Oracle)
    monkeypatch.setattr(documents, "document_content_response", served)
    monkeypatch.setattr(documents, "_document_preview_pages_response", served)
    monkeypatch.setattr(documents, "_document_preview_page_image_response", served)
    monkeypatch.setattr(documents, "load_document_content", load)

    async def run() -> Any:
        if route == "content":
            return await documents.document_content("d1", variant=variant, disposition="inline")
        if route == "pages":
            return await documents.document_preview_pages("d1", variant=variant)
        if route == "page":
            return await documents.document_preview_page_image("d1", 1, variant=variant, dpi=144)
        return await documents.document_recipe_text_window(
            "d1", DocumentTextWindowRequest(variant=variant, offset=0, limit=100)
        )

    if variant == "prepared":
        with pytest.raises(HTTPException) as caught:
            await run()
        assert caught.value.status_code == 400
        assert called == []  # cache/ファイル読込を含む下流helperへ入らない。
    else:
        await run()
        assert len(called) == 1


@pytest.mark.parametrize("version", [None, "cs-current"])
async def test_formal_recipe_prepared_keeps_explicit_artifact(
    monkeypatch: pytest.MonkeyPatch, version: str | None
) -> None:
    artifact = DocumentPreprocessArtifact(
        derivation_id="current",
        profile="passthrough",
        file_name="synthetic.txt",
        object_storage_path="local://formal",
        content_type="text/plain",
    )

    async def scope(doc_id: str, recipe_id: str, chunk_set_id: Any = None) -> Any:
        assert (doc_id, recipe_id, chunk_set_id) == ("d1", "r1", version)
        return SimpleNamespace(preprocess_artifact=None), artifact

    async def served(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["preprocess_artifact"] is artifact
        return "served-formal"

    monkeypatch.setattr(documents, "_recipe_detail_and_artifact", scope)
    monkeypatch.setattr(documents, "document_content_response", served)
    result: Any = await documents.document_recipe_content(
        "d1", "r1", variant="prepared", disposition="inline", chunk_set_id=version
    )
    assert result == "served-formal"


@pytest.mark.parametrize("route", ["content", "pages", "page", "window"])
async def test_prepared_hidden_or_missing_document_remains_not_found(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """閲覧不可も同じ get_document の None で返り、旧入口の診断より先に404になる。"""

    class Oracle:
        async def get_document(self, document_id: str) -> None:
            return None

    async def must_not_read(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("閲覧できない文書のキャッシュやファイルを読んではならない")

    monkeypatch.setattr(documents, "OracleClient", Oracle)
    for helper in (
        "document_content_response",
        "_document_preview_pages_response",
        "_document_preview_page_image_response",
        "load_document_content",
    ):
        monkeypatch.setattr(documents, helper, must_not_read)
    with pytest.raises(HTTPException) as caught:
        if route == "content":
            await documents.document_content("hidden", variant="prepared", disposition="inline")
        elif route == "pages":
            await documents.document_preview_pages("hidden", variant="prepared")
        elif route == "page":
            await documents.document_preview_page_image("hidden", 1, variant="prepared", dpi=144)
        else:
            await documents.document_recipe_text_window(
                "hidden", DocumentTextWindowRequest(variant="prepared", offset=0, limit=100)
            )
    assert caught.value.status_code == 404


@pytest.mark.parametrize("route", ["pages", "page", "window"])
async def test_formal_recipe_preview_routes_keep_explicit_artifact(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    artifact = DocumentPreprocessArtifact(
        derivation_id="formal",
        profile="passthrough",
        file_name="synthetic.txt",
        object_storage_path="local://formal",
        content_type="text/plain",
    )
    calls: list[str] = []

    async def scope(document_id: str, recipe_id: str, chunk_set_id: str | None = None) -> Any:
        assert (document_id, recipe_id) == ("d1", "r1")
        # text-window は作業中のレシピ、ページ画像は引用版を指定する。
        assert chunk_set_id == (None if route == "window" else "cs1")
        return SimpleNamespace(preprocess_artifact=None), artifact

    async def served(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["preprocess_artifact"] is artifact
        calls.append("served")
        return "served-formal"

    async def load(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["preprocess_artifact"] is artifact
        calls.append("loaded")
        return b"synthetic text", "synthetic.txt", "text/plain"

    class Oracle:
        async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, Any]:
            return {}

    monkeypatch.setattr(documents, "OracleClient", Oracle)
    monkeypatch.setattr(documents, "_recipe_detail_and_artifact", scope)
    monkeypatch.setattr(documents, "_document_preview_pages_response", served)
    monkeypatch.setattr(documents, "_document_preview_page_image_response", served)
    monkeypatch.setattr(documents, "load_document_content", load)
    if route == "pages":
        await documents.document_recipe_preview_pages(
            "d1", "r1", variant="prepared", chunk_set_id="cs1"
        )
    elif route == "page":
        await documents.document_recipe_preview_page_image(
            "d1", "r1", 1, variant="prepared", dpi=144, chunk_set_id="cs1"
        )
    else:
        await documents.document_recipe_text_window(
            "d1", DocumentTextWindowRequest(variant="prepared", offset=0, limit=100), recipe_id="r1"
        )
    assert len(calls) == 1
