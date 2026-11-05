"""引用プレビューの種類は引用版から取得する。DB/ファイルは読まない。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.errors import DocumentFileMissingError, SourceVersionStaleError
from app.api.routes import documents
from app.rag.document_crop import VersionSourceUnknownError
from app.schemas.document import DocumentPreprocessArtifact


@pytest.fixture
def oracle(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    value = SimpleNamespace(
        get_document=AsyncMock(return_value=SimpleNamespace(file_name="sample.docx")),
        get_document_recipe=AsyncMock(return_value={"preprocess_artifact": None}),
        recipe_version_exists=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(documents, "OracleClient", lambda: value)
    return value


@pytest.mark.parametrize("current", [None, "jsonl", "pdf"])
@pytest.mark.parametrize("cited_pdf", [True, False])
async def test_source_uses_cited_artifact(
    monkeypatch: pytest.MonkeyPatch, oracle: SimpleNamespace, current: str | None, cited_pdf: bool
) -> None:
    oracle.get_document_recipe.return_value = {"preprocess_artifact": current}
    artifact = DocumentPreprocessArtifact(
        derivation_id="source-a",
        profile="passthrough",
        file_name="sample.pdf" if cited_pdf else "sample.docx",
        content_type="application/pdf" if cited_pdf else "application/octet-stream",
        object_storage_path="private/source-a",
    )
    reader = AsyncMock(return_value=artifact)
    monkeypatch.setattr(documents, "chunk_set_prepared_artifact", reader)
    result = await documents.document_recipe_preview_source("doc-a", "recipe-a", "cs-a")
    assert result.data is not None
    assert result.data.is_pdf is cited_pdf
    assert "object_storage_path" not in result.data.model_dump()
    oracle.recipe_version_exists.assert_awaited_once_with(
        document_id="doc-a", recipe_id="recipe-a", chunk_set_id="cs-a"
    )
    assert reader.await_args is not None
    assert reader.await_args.args[3] == "cs-a"


async def test_wrong_recipe_or_version_is_not_current(
    monkeypatch: pytest.MonkeyPatch, oracle: SimpleNamespace
) -> None:
    oracle.recipe_version_exists.return_value = False
    reader = AsyncMock()
    monkeypatch.setattr(documents, "chunk_set_prepared_artifact", reader)
    with pytest.raises(SourceVersionStaleError):
        await documents.document_recipe_preview_source("doc-a", "recipe-a", "cs-other")
    reader.assert_not_awaited()


async def test_missing_source_is_not_current(
    monkeypatch: pytest.MonkeyPatch, oracle: SimpleNamespace
) -> None:
    reader = AsyncMock(side_effect=VersionSourceUnknownError())
    monkeypatch.setattr(documents, "chunk_set_prepared_artifact", reader)
    with pytest.raises(DocumentFileMissingError):
        await documents.document_recipe_preview_source("doc-a", "recipe-a", "cs-a")


async def test_blank_scope_rejected_before_read(oracle: SimpleNamespace) -> None:
    with pytest.raises(HTTPException) as exc:
        await documents.document_recipe_preview_source("doc-a", "recipe-a", " ")
    assert exc.value.status_code == 422
    oracle.get_document.assert_not_awaited()


async def test_workbench_current_recipe_is_preserved(oracle: SimpleNamespace) -> None:
    current = DocumentPreprocessArtifact(
        derivation_id="current", profile="passthrough", file_name="sample.pdf"
    )
    oracle.get_document_recipe.return_value = {"preprocess_artifact": current.model_dump()}
    _, actual = await documents._recipe_detail_and_artifact("doc-a", "recipe-a")
    assert actual == current
    oracle.recipe_version_exists.assert_not_awaited()
