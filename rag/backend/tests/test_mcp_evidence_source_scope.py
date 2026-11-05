"""根拠の画像・署名プレビューは正式な版がある場合だけ原本を解決する。"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pr_backend_core.mcp import McpToolError

from app.api.routes import source_previews
from app.mcp import tools
from app.rag.document_crop import VersionSourceUnknownError
from app.schemas.document import DocumentPreprocessArtifact


@pytest.mark.parametrize("scope", [None, "", "   ", 7])
async def test_preview_missing_scope_never_reads_document_or_recipe(scope: Any) -> None:
    oracle = SimpleNamespace(get_document=AsyncMock(), get_document_recipe=AsyncMock())
    with pytest.raises(McpToolError) as caught:
        await tools.preview_document(
            cast(Any, oracle), "document-a", "recipe-a", chunk_set_id=scope
        )
    assert caught.value.code == tools.PREVIEW_FILE_MISSING_CODE
    assert caught.value.status == 404
    oracle.get_document.assert_not_awaited()
    oracle.get_document_recipe.assert_not_awaited()


@pytest.mark.parametrize("scope", [None, "", "   ", 7])
async def test_crop_missing_scope_never_reads_or_crops(
    monkeypatch: pytest.MonkeyPatch, scope: Any
) -> None:
    loader = AsyncMock()
    monkeypatch.setattr(tools, "load_parsed_source", loader)
    region = SimpleNamespace(page=1, bbox=(0, 0, 1, 1), page_size=(1, 1))
    with pytest.raises(McpToolError) as caught:
        await tools.crop_figure(
            cast(Any, object()),
            cast(Any, SimpleNamespace(document_id="document-a")),
            {"recipe_id": "recipe-a", "chunk_set_id": scope},
            cast(Any, region),
        )
    assert caught.value.code == tools.IMAGE_SOURCE_MISSING_CODE
    assert caught.value.status == 404
    loader.assert_not_awaited()


async def test_signed_missing_scope_does_not_pass_none_equals_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = SimpleNamespace(get_document=AsyncMock(), get_document_recipe=AsyncMock())
    monkeypatch.setattr(source_previews, "OracleClient", lambda: oracle)
    readable = AsyncMock(return_value=SimpleNamespace(metadata={}))
    monkeypatch.setattr(tools, "readable_chunk", readable)
    claims = SimpleNamespace(
        document_id="document-a", chunk_id="chunk-a", recipe_id="recipe-a", chunk_set_id=None
    )
    with pytest.raises(HTTPException) as caught:
        await source_previews._readable_target(cast(Any, claims))
    assert caught.value.status_code == 404
    readable.assert_awaited_once()
    oracle.get_document.assert_not_awaited()
    oracle.get_document_recipe.assert_not_awaited()


@pytest.mark.parametrize("scope", ["version-a", "version-b"])
async def test_preview_uses_only_the_requested_version(
    monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    detail = SimpleNamespace(
        object_storage_path="synthetic/original", preprocess_artifact="legacy-do-not-read"
    )
    oracle = SimpleNamespace(
        get_document=AsyncMock(return_value=detail), get_document_recipe=AsyncMock()
    )
    artifact = DocumentPreprocessArtifact(
        derivation_id="synthetic",
        profile="passthrough",
        file_name="synthetic.pdf",
        object_storage_path=f"synthetic/{scope}",
    )
    bound = AsyncMock(return_value=artifact)
    monkeypatch.setattr(tools, "chunk_set_prepared_artifact", bound)
    actual_detail, actual_artifact = await tools.preview_document(
        cast(Any, oracle), "document-a", "recipe-a", chunk_set_id=scope
    )
    assert actual_detail is detail and actual_artifact is artifact
    bound.assert_awaited_once_with(oracle, detail, "document-a", scope)
    oracle.get_document_recipe.assert_not_awaited()


async def test_preview_unknown_scope_never_uses_current_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = SimpleNamespace(
        get_document=AsyncMock(return_value=SimpleNamespace(object_storage_path="original")),
        get_document_recipe=AsyncMock(),
    )
    monkeypatch.setattr(
        tools, "chunk_set_prepared_artifact", AsyncMock(side_effect=VersionSourceUnknownError())
    )
    with pytest.raises(McpToolError) as caught:
        await tools.preview_document(
            cast(Any, oracle), "document-a", "recipe-a", chunk_set_id="unknown-version"
        )
    assert caught.value.code == tools.PREVIEW_FILE_MISSING_CODE
    oracle.get_document_recipe.assert_not_awaited()


async def test_signed_valid_scope_passes_exact_version(monkeypatch: pytest.MonkeyPatch) -> None:
    oracle = SimpleNamespace(
        get_document=AsyncMock(return_value=SimpleNamespace(object_storage_path="original"))
    )
    monkeypatch.setattr(source_previews, "OracleClient", lambda: oracle)
    monkeypatch.setattr(
        tools,
        "readable_chunk",
        AsyncMock(return_value=SimpleNamespace(metadata={"chunk_set_id": "version-a"})),
    )
    artifact = DocumentPreprocessArtifact(
        derivation_id="synthetic",
        profile="passthrough",
        file_name="synthetic.pdf",
        object_storage_path="synthetic/version-a",
    )
    bound = AsyncMock(return_value=artifact)
    monkeypatch.setattr(tools, "chunk_set_prepared_artifact", bound)
    claims = SimpleNamespace(
        document_id="document-a", chunk_id="chunk-a", recipe_id="recipe-a", chunk_set_id="version-a"
    )
    _detail, actual = await source_previews._readable_target(cast(Any, claims))
    assert actual is artifact
    await_args = bound.await_args
    assert await_args is not None and await_args.kwargs == {}
    assert await_args.args[-1] == "version-a"


async def test_signed_unknown_scope_does_not_use_recipe_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = SimpleNamespace(
        get_document=AsyncMock(return_value=SimpleNamespace(object_storage_path="original")),
        get_document_recipe=AsyncMock(),
    )
    monkeypatch.setattr(source_previews, "OracleClient", lambda: oracle)
    monkeypatch.setattr(
        tools,
        "readable_chunk",
        AsyncMock(return_value=SimpleNamespace(metadata={"chunk_set_id": "unknown-version"})),
    )
    monkeypatch.setattr(
        tools, "chunk_set_prepared_artifact", AsyncMock(side_effect=VersionSourceUnknownError())
    )
    claims = SimpleNamespace(
        document_id="document-a",
        chunk_id="chunk-a",
        recipe_id="recipe-a",
        chunk_set_id="unknown-version",
    )
    with pytest.raises(HTTPException) as caught:
        await source_previews._readable_target(cast(Any, claims))
    assert caught.value.status_code == 404
    oracle.get_document_recipe.assert_not_awaited()
