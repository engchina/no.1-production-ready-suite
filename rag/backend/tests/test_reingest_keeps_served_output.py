"""再取込の間も旧版で答え続けるための、候補・抽出・章節の境界（#4115。実 DB なし）。"""

from typing import cast

from app.api.routes import documents as documents_route
from app.clients.oracle import OracleClient
from app.rag.document_sections_service import sections_source
from app.rag.variant_keys import compute_isolated_extraction_id
from app.schemas.document import FileStatus


class _SectionsOracle:
    async def ensure_default_document_recipe(self, document_id: str) -> dict[str, object]:
        return {"recipe_id": "recipe-1"}

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, object]:
        return {"processing_config": {}, "active_extraction_recipe_id": "extraction-new"}

    async def get_latest_recipe_chunk_set(
        self, recipe_id: str, *, status: str | None = None, active: bool | None = None
    ) -> dict[str, object] | None:
        assert (status, active) == ("INDEXED", True)
        return {"extraction_recipe_id": "extraction-served"}

    async def get_document_extraction_artifact(
        self, *, document_id: str, extraction_recipe_id: str
    ) -> dict[str, object] | None:
        return None


async def test_search_sections_follow_the_served_chunk_set_during_rebuild() -> None:
    oracle = cast(OracleClient, _SectionsOracle())
    served = await sections_source(oracle, "doc-1", None)
    assert served.extraction_recipe_id == "extraction-served"


async def test_explicit_recipe_sections_show_the_recipe_current_extraction() -> None:
    oracle = cast(OracleClient, _SectionsOracle())
    current = await sections_source(oracle, "doc-1", "recipe-1")
    assert current.extraction_recipe_id == "extraction-new"


class _RecipeStatusOracle:
    def __init__(self, status: FileStatus) -> None:
        self.status = status

    async def ensure_default_document_recipe(self, document_id: str) -> dict[str, object]:
        return {"recipe_id": "recipe-1", "status": self.status.value}


async def test_failed_recipe_on_indexed_document_is_not_skipped_as_already_indexed() -> None:
    failed = cast(OracleClient, _RecipeStatusOracle(FileStatus.ERROR))
    indexed = cast(OracleClient, _RecipeStatusOracle(FileStatus.INDEXED))
    assert await documents_route._default_recipe_failed(failed, "doc-1") is True
    assert await documents_route._default_recipe_failed(indexed, "doc-1") is False


def test_isolated_extraction_id_depends_on_the_build() -> None:
    first = compute_isolated_extraction_id("extraction-1", "job-1:1")
    assert first == compute_isolated_extraction_id("extraction-1", "job-1:1")
    assert first != compute_isolated_extraction_id("extraction-1", "job-1:2")
    assert first != "extraction-1"
