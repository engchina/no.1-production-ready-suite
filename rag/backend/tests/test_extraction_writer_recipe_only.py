"""抽出保存は正式recipeへ書き、保存失敗を索引成功に変えない。"""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.config import Settings
from app.rag.ingestion import IngestionPipeline, IngestionUserError
from app.schemas.extraction import StructuredExtraction


class ExtractionOracle:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def upsert_document_extraction_artifact(self, **kwargs: Any) -> None:
        self.calls.append(("artifact", kwargs))
        if self.fail:
            raise RuntimeError("synthetic-save-error")

    async def update_document_recipe_status(self, **kwargs: Any) -> None:
        self.calls.append(("pointer", kwargs))


def pipeline(
    oracle: ExtractionOracle, recipe: str | None, revision: int | None = 1
) -> IngestionPipeline:
    value = IngestionPipeline.__new__(IngestionPipeline)
    value._oracle = cast(Any, oracle)
    value._recipe_id = recipe
    value._recipe_revision = revision
    value._build_id = None
    value._settings = Settings.model_construct()
    return value


@pytest.mark.parametrize(
    ("recipe", "has_hash", "fail", "revision"),
    [
        (None, False, False, 1),
        (None, False, True, 1),
        (None, True, False, 1),
        (None, True, True, 1),
        ("synthetic-recipe", False, False, 1),
        ("synthetic-recipe", False, True, 1),
        ("synthetic-recipe", True, False, 1),
        ("synthetic-recipe", True, True, 1),
        ("synthetic-recipe", True, False, None),
    ],
)
async def test_extraction_writer_requires_recipe_and_preserves_formal_save(
    recipe: str | None, has_hash: bool, fail: bool, revision: int | None
) -> None:
    oracle = ExtractionOracle(fail=fail)
    helper = pipeline(oracle, recipe, revision)
    source = cast(Any, SimpleNamespace(content_sha256="a" * 64)) if has_hash else None
    extraction = StructuredExtraction(raw_text="合成本文")
    if recipe is None or not has_hash or revision is None:
        with pytest.raises(IngestionUserError):
            await helper._persist_extraction_layer("synthetic-doc", source, extraction)
        assert oracle.calls == []
    elif fail:
        with pytest.raises(RuntimeError, match="synthetic-save-error"):
            await helper._persist_extraction_layer("synthetic-doc", source, extraction)
        assert [name for name, _ in oracle.calls] == ["artifact"]
    else:
        artifact_id = await helper._persist_extraction_layer("synthetic-doc", source, extraction)
        assert artifact_id
        assert [name for name, _ in oracle.calls] == ["artifact", "pointer"]
        assert oracle.calls[0][1]["document_id"] == "synthetic-doc"
        assert oracle.calls[0][1]["source_sha256"] == "a" * 64
        assert oracle.calls[0][1]["extraction"] == extraction.to_document_payload()
        assert oracle.calls[1][1]["recipe_id"] == recipe
        assert oracle.calls[1][1]["active_extraction_recipe_id"] == artifact_id


@pytest.mark.parametrize("served", [False, True])
async def test_formal_build_isolates_served_extraction_without_changing_source(
    served: bool,
) -> None:
    from app.rag.variant_keys import (
        compute_document_recipe_extraction_id,
        compute_extraction_recipe_id,
        compute_isolated_extraction_id,
    )

    oracle = ExtractionOracle()
    helper = pipeline(oracle, "synthetic-recipe", 2)
    extraction = StructuredExtraction(raw_text="合成本文")
    source = cast(Any, SimpleNamespace(content_sha256="a" * 64))
    base = compute_document_recipe_extraction_id(
        compute_extraction_recipe_id("a" * 64, helper._settings), "synthetic-recipe", 2
    )
    reads = []

    async def served_ids(document_id: str) -> set[str]:
        reads.append(document_id)
        return {base} if served else set()

    cast(Any, oracle).served_extraction_ids = served_ids
    helper._build_id = "synthetic-build"
    result = await helper._persist_extraction_layer("synthetic-doc", source, extraction)
    expected = compute_isolated_extraction_id(base, "synthetic-build") if served else base
    assert result == expected
    assert reads == ["synthetic-doc"]
    assert [name for name, _ in oracle.calls] == ["artifact", "pointer"]
    assert oracle.calls[0][1]["extraction_recipe_id"] == expected
    assert oracle.calls[1][1]["active_extraction_recipe_id"] == expected
    assert oracle.calls[0][1]["source_sha256"] == "a" * 64


async def test_recipe_pointer_failure_is_not_a_successful_save() -> None:
    class FailingPointer(ExtractionOracle):
        async def update_document_recipe_status(self, **kwargs: Any) -> None:
            await super().update_document_recipe_status(**kwargs)
            raise RuntimeError("synthetic-pointer-error")

    oracle = FailingPointer()
    helper = pipeline(oracle, "synthetic-recipe")
    with pytest.raises(RuntimeError, match="synthetic-pointer-error"):
        await helper._persist_extraction_layer(
            "synthetic-doc",
            cast(Any, SimpleNamespace(content_sha256="a" * 64)),
            StructuredExtraction(raw_text="合成本文"),
        )
    assert [name for name, _ in oracle.calls] == ["artifact", "pointer"]
