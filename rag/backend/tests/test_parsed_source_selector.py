"""解析ファイルの選択を明示し、旧文書 artifact で版を代用しない。"""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.rag import document_crop


def _derivation(path: str | None) -> dict[str, object]:
    return {
        "derivation_id": "synthetic-version",
        "converted": path is not None,
        "derived_object_path": path,
        "source_content_type": "application/pdf",
        "derived_content_type": "application/pdf" if path else None,
    }


class _Oracle:
    def __init__(self, *, denied: bool, legacy: bool) -> None:
        self.events: list[tuple[str, ...]] = []
        self.denied = denied
        self.detail = SimpleNamespace(
            id="doc-a",
            object_storage_path="synthetic/original",
            preprocess_artifact=(
                SimpleNamespace(object_storage_path="synthetic/legacy") if legacy else None
            ),
            file_name="synthetic.pdf",
            content_type="application/pdf",
        )

    async def get_document(self, document_id: str) -> Any:
        self.events.append(("document", document_id))
        return None if self.denied else self.detail

    async def get_document_recipe(
        self, document_id: str, recipe_id: str
    ) -> dict[str, object] | None:
        self.events.append(("recipe", document_id, recipe_id))
        if recipe_id != "recipe-a":
            return None
        return {
            "preprocess_artifact": {
                "derivation_id": "synthetic-recipe",
                "profile": "passthrough",
                "converted": False,
                "object_storage_path": "synthetic/current-recipe",
                "content_type": "application/pdf",
                "file_name": "synthetic.pdf",
            }
        }

    async def chunk_set_source_derivation(self, *, document_id: str, chunk_set_id: str) -> object:
        self.events.append(("chunk_set", document_id, chunk_set_id))
        return {
            "cs-old": _derivation("synthetic/version-old"),
            "cs-plain": _derivation(None),
        }.get(chunk_set_id)

    async def extraction_source_derivation(
        self, *, document_id: str, extraction_recipe_id: str
    ) -> object:
        self.events.append(("revision", document_id, extraction_recipe_id))
        return {"rev-old": _derivation("synthetic/extraction-old")}.get(extraction_recipe_id)


@pytest.mark.parametrize(
    "recipe_id,selectors,denied,legacy,expected,error",
    [
        (None, {}, False, True, None, document_crop.VersionSourceUnknownError),
        (None, {}, False, False, None, document_crop.VersionSourceUnknownError),
        ("recipe-a", {}, False, True, "synthetic/current-recipe", None),
        ("unknown", {}, False, True, None, document_crop.DocumentSourceNotFoundError),
        ("recipe-a", {"chunk_set_id": "cs-old"}, False, True, "synthetic/version-old", None),
        ("recipe-a", {"chunk_set_id": "cs-plain"}, False, True, "synthetic/original", None),
        (
            "recipe-a",
            {"chunk_set_id": "unknown"},
            False,
            True,
            None,
            document_crop.VersionSourceUnknownError,
        ),
        (
            "recipe-a",
            {"extraction_recipe_id": "rev-old"},
            False,
            True,
            "synthetic/extraction-old",
            None,
        ),
        (
            "recipe-a",
            {"extraction_recipe_id": "unknown"},
            False,
            True,
            None,
            document_crop.VersionSourceUnknownError,
        ),
        (
            None,
            {"extraction_recipe_id": "rev-old"},
            False,
            True,
            None,
            document_crop.DocumentSourceNotFoundError,
        ),
        (
            "recipe-a",
            {"chunk_set_id": "cs-old", "extraction_recipe_id": "rev-old"},
            False,
            True,
            None,
            document_crop.VersionSourceUnknownError,
        ),
        (
            "recipe-a",
            {"chunk_set_id": "cs-old"},
            True,
            True,
            None,
            document_crop.DocumentSourceNotFoundError,
        ),
    ],
    ids=[
        "no-selector-legacy",
        "no-selector-original",
        "recipe-current",
        "recipe-unknown",
        "cs-fixed",
        "cs-original",
        "cs-unknown",
        "revision-fixed",
        "revision-unknown",
        "revision-without-recipe",
        "both-versions",
        "acl-denied",
    ],
)
async def test_source_selector_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    recipe_id: str | None,
    selectors: dict[str, str],
    denied: bool,
    legacy: bool,
    expected: str | None,
    error: type[Exception] | None,
) -> None:
    oracle = _Oracle(denied=denied, legacy=legacy)
    reads: list[str] = []

    class Storage:
        async def get(self, path: str) -> bytes:
            reads.append(path)
            return path.encode()

    monkeypatch.setattr(document_crop, "ObjectStorageClient", Storage)
    if error is not None:
        with pytest.raises(error):
            await document_crop.load_parsed_source(
                cast(Any, oracle), "doc-a", recipe_id, **selectors
            )
        assert reads == []
    else:
        actual = await document_crop.load_parsed_source(
            cast(Any, oracle), "doc-a", recipe_id, **selectors
        )
        assert expected is not None and actual == expected.encode()
        assert reads == [expected]

    assert oracle.events[0] == ("document", "doc-a")
    if denied or not (recipe_id or selectors) or len(selectors) == 2:
        assert oracle.events == [("document", "doc-a")]
    elif "chunk_set_id" in selectors:
        assert oracle.events == [
            ("document", "doc-a"),
            ("chunk_set", "doc-a", selectors["chunk_set_id"]),
        ]
