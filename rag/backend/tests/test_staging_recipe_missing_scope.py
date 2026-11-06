"""同レシピでも旧版を今回の検証結果にしない。"""

from types import SimpleNamespace
from typing import Any

from app.rag.staging_recipes import read_staging_recipe


async def test_failed_recipe_does_not_use_old_active_extraction() -> None:
    class Oracle:
        async def list_document_recipes(self, document_id: str) -> Any:
            return [{"recipe_id": "r1", "slot_no": 1}]

        async def get_document_recipe(self, *args: Any) -> Any:
            return {
                "recipe_id": "r1",
                "status": "ERROR",
                "active_chunk_set_id": "old-cs",
                "active_extraction_recipe_id": "old-e",
            }

        async def get_document_extraction_artifact(self, **kwargs: Any) -> Any:
            return {"extraction_json": {"old": "must-not-use"}}

        async def list_ingestion_segments(self, document_id: str) -> Any:
            return [SimpleNamespace(recipe_id="r1", segment_id="old-segment")]

    state = await read_staging_recipe(Oracle(), "d1")
    assert state.extraction == {}
    assert state.segments == ()


async def test_same_recipe_unbound_segments_are_not_version_evidence() -> None:
    class Oracle:
        async def list_document_recipes(self, document_id: str) -> Any:
            return [{"recipe_id": "r1", "slot_no": 1}]

        async def get_document_recipe(self, *args: Any) -> Any:
            return {"recipe_id": "r1", "status": "INDEXED", "active_chunk_set_id": "new-cs"}

        async def list_chunk_set_chunks(self, chunk_set_id: str) -> Any:
            return []

        async def get_chunk_set(self, chunk_set_id: str) -> Any:
            return {
                "document_id": "d1",
                "recipe_id": "r1",
                "status": "INDEXED",
                "extraction_recipe_id": "new-e",
            }

        async def get_document_extraction_artifact(self, **kwargs: Any) -> Any:
            return {"extraction_json": {"new": "body"}}

        async def list_ingestion_segments(self, document_id: str) -> Any:
            return [
                SimpleNamespace(recipe_id="r1", segment_id="old"),
                SimpleNamespace(recipe_id="r1", segment_id="new"),
            ]

    state = await read_staging_recipe(Oracle(), "d1")
    assert state.extraction == {"new": "body"}
    assert state.segments == ()
