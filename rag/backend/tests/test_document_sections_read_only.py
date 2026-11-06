"""章節GETは旧状態からレシピを補建せず、保存済みの版だけを読む。"""

from typing import Any

import pytest
from rag_parser_core.extraction import DocumentNavigationNode, ExtractionPage, StructuredExtraction

from app.api.routes import documents as routes
from app.clients.oracle import default_document_recipe_id
from app.main import app
from tests.support import AsgiTestClient

DOCUMENT = "synthetic-doc"
DEFAULT = default_document_recipe_id(DOCUMENT)
URL = f"/api/documents/{DOCUMENT}/sections"


class SectionsOracle:
    def __init__(self, *, missing: bool = False, visible: bool = True) -> None:
        self.visible = visible
        self.recipes: dict[str, dict[str, object]] = {
            "synthetic-slot2": {
                "recipe_id": "synthetic-slot2",
                "slot_no": 2,
                "active_extraction_recipe_id": "second",
            },
        }
        if not missing:
            self.recipes[DEFAULT] = {
                "recipe_id": DEFAULT,
                "slot_no": 1,
                "active_extraction_recipe_id": "current",
            }
        self.writes: list[str] = []
        self.recipe_reads: list[str] = []
        self.chunk_set_reads: list[str] = []
        self.artifact_reads: list[str] = []
        self.stored: dict[str, Any] | None = None

    async def document_exists(self, document_id: str) -> bool:
        return self.visible and document_id == DOCUMENT

    async def ensure_default_document_recipe(self, document_id: str) -> dict[str, object]:
        self.writes.append("ensure")
        existing = next((row for row in self.recipes.values() if row.get("slot_no") == 1), None)
        if existing is not None:
            return existing
        self.writes.append("MERGE")
        self.recipes[DEFAULT] = {
            "recipe_id": DEFAULT,
            "slot_no": 1,
            "active_extraction_recipe_id": None,
        }
        return self.recipes[DEFAULT]

    async def list_document_recipes(self, document_id: str) -> list[dict[str, object]]:
        self.recipe_reads.extend(
            str(row["recipe_id"]) for row in self.recipes.values() if row.get("slot_no") == 1
        )
        return list(self.recipes.values()) if self.visible and document_id == DOCUMENT else []

    async def get_document_recipe(
        self, document_id: str, recipe_id: str
    ) -> dict[str, object] | None:
        self.recipe_reads.append(recipe_id)
        return self.recipes.get(recipe_id) if self.visible and document_id == DOCUMENT else None

    async def get_latest_recipe_chunk_set(self, recipe_id: str, **_: object) -> dict[str, object]:
        self.chunk_set_reads.append(recipe_id)
        return {"extraction_recipe_id": "served"}

    async def get_document_extraction_artifact(
        self, *, document_id: str, extraction_recipe_id: str
    ) -> dict[str, object]:
        assert document_id == DOCUMENT
        self.artifact_reads.append(extraction_recipe_id)
        extraction = StructuredExtraction(
            raw_text="合成の技術説明",
            pages=[ExtractionPage(page_number=1)],
            navigation=[
                DocumentNavigationNode(
                    section_id="section-a",
                    title=f"合成 {extraction_recipe_id}",
                    depth=1,
                    page_start=1,
                    page_end=1,
                )
            ],
        )
        return {"extraction_json": extraction.model_dump(mode="json")}

    async def get_document_sections(self, document_id: str) -> dict[str, Any] | None:
        return self.stored


@pytest.fixture
def oracle(monkeypatch: pytest.MonkeyPatch) -> SectionsOracle:
    fake = SectionsOracle()
    monkeypatch.setattr(routes, "OracleClient", lambda: fake)
    return fake


def test_implicit_get_reads_served_extraction_without_ensure(oracle: SectionsOracle) -> None:
    response = AsgiTestClient(app).get(URL)
    assert response.status_code == 200
    assert oracle.writes == []
    assert oracle.recipe_reads == [DEFAULT]
    assert oracle.artifact_reads == ["served"]
    assert response.json()["data"]["sections"][0]["title"] == "合成 served"


def test_explicit_get_preserves_current_extraction_without_ensure(oracle: SectionsOracle) -> None:
    response = AsgiTestClient(app).get(URL, params={"recipe_id": DEFAULT})
    assert response.status_code == 200
    assert oracle.writes == []
    assert oracle.artifact_reads == ["current"]
    assert oracle.chunk_set_reads == []


def test_missing_default_does_not_recreate_or_select_slot2(oracle: SectionsOracle) -> None:
    del oracle.recipes[DEFAULT]
    response = AsgiTestClient(app).get(URL)
    assert response.status_code == 200
    assert response.json()["data"]["sections"] == []
    assert oracle.writes == []
    assert DEFAULT not in oracle.recipes
    assert oracle.recipe_reads == []
    assert oracle.artifact_reads == []
    assert oracle.chunk_set_reads == []


def test_no_recipes_are_not_initialized_by_get(oracle: SectionsOracle) -> None:
    oracle.recipes.clear()
    response = AsgiTestClient(app).get(URL)
    assert response.status_code == 200
    assert response.json()["data"]["sections"] == []
    assert oracle.writes == [] and oracle.recipes == {}
    assert oracle.artifact_reads == [] and oracle.chunk_set_reads == []


@pytest.mark.parametrize("recipe_id", [None, DEFAULT, "foreign-recipe"])
def test_invisible_document_stays_404_without_recipe_access(
    oracle: SectionsOracle, recipe_id: str | None
) -> None:
    oracle.visible = False
    response = AsgiTestClient(app).get(URL, params={"recipe_id": recipe_id} if recipe_id else {})
    assert response.status_code == 404
    assert oracle.writes == [] and oracle.recipe_reads == [] and oracle.artifact_reads == []


def test_explicit_missing_recipe_does_not_read_another_source(oracle: SectionsOracle) -> None:
    response = AsgiTestClient(app).get(URL, params={"recipe_id": "foreign-recipe"})
    assert response.status_code == 200
    assert response.json()["data"]["sections"] == []
    assert oracle.writes == [] and oracle.artifact_reads == [] and oracle.chunk_set_reads == []


def test_manual_sections_survive_missing_default_without_recreation(oracle: SectionsOracle) -> None:
    del oracle.recipes[DEFAULT]
    oracle.stored = {
        "revision": 3,
        "sections": {
            "sections": [
                {
                    "id": "manual-a",
                    "title": "合成の手動修正",
                    "level": 1,
                    "page_start": 1,
                    "page_end": 1,
                    "origin": "manual",
                }
            ],
            "removed_source_ids": [],
        },
    }
    response = AsgiTestClient(app).get(URL)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["source"] == "manual" and data["revision"] == 3
    assert data["sections"][0]["title"] == "合成の手動修正"
    assert oracle.writes == [] and oracle.artifact_reads == []


def test_existing_default_uses_current_if_no_served_output(
    oracle: SectionsOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def none(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(oracle, "get_latest_recipe_chunk_set", none)
    response = AsgiTestClient(app).get(URL)
    assert response.status_code == 200 and oracle.artifact_reads == ["current"]
    assert oracle.writes == []


@pytest.mark.parametrize("has_default", [True, False])
def test_real_get_and_oracle_reader_issue_select_only(
    monkeypatch: pytest.MonkeyPatch, has_default: bool
) -> None:
    from app.clients.oracle import OracleClient
    from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline
    from tests.test_oracle_db_paths import _recipe_row

    document: dict[str, object] = {"document_id": "doc-1"}
    results: list[list[dict[str, object]]]
    if has_default:
        results = [[document], [_recipe_row()], [], []]
    else:
        results = [[document], [_recipe_row(recipe_id="synthetic-slot2", slot_no=2)], []]
    pool = FakeOraclePool(execute_results=results)
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    monkeypatch.setattr(routes, "OracleClient", lambda: client)
    response = AsgiTestClient(app).get("/api/documents/doc-1/sections")
    assert response.status_code == 200
    assert response.json()["data"]["sections"] == []
    assert pool.connection.calls
    assert all(call.statement.lstrip().startswith("SELECT") for call in pool.connection.calls)
    assert pool.connection.commits == 0
    served_reads = [
        call
        for call in pool.connection.calls
        if "FROM rag_chunk_sets" in call.statement
        and "recipe_id = :recipe_id" in call.statement
        and "r.document_id" not in call.statement
    ]
    assert len(served_reads) == (1 if has_default else 0)
    if has_default:
        assert served_reads[0].parameters["recipe_id"] == default_document_recipe_id("doc-1")


def test_implicit_get_uses_actual_slot1_id(oracle: SectionsOracle) -> None:
    row = oracle.recipes.pop(DEFAULT)
    row["recipe_id"] = "synthetic-slot1-actual"
    oracle.recipes["synthetic-slot1-actual"] = row
    response = AsgiTestClient(app).get(URL)
    assert response.status_code == 200
    assert oracle.chunk_set_reads == ["synthetic-slot1-actual"]
    assert oracle.artifact_reads == ["served"] and oracle.writes == []
