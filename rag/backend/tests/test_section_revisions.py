"""人が修正した章節の履歴（#4171）の差分と API の判定（実 Oracle は別のファイル）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from rag_parser_core.extraction import (
    DocumentNavigationNode,
    ExtractionPage,
    StructuredExtraction,
)

from app.api.routes import documents as documents_route
from app.clients.oracle import DocumentSectionsConflictError
from app.clients.section_revision_store import max_section_revisions
from app.main import app
from app.rag.section_revisions import (
    SectionExtractionBase,
    base_change_flags,
    extraction_base_of,
    section_changes,
    section_outline_fingerprint,
    section_revision_payload,
)
from app.schemas.document import DocumentSection
from tests.support import AsgiTestClient

client = AsgiTestClient(app)
URL = "/api/documents/doc-1/sections"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _section(
    section_id: str,
    *,
    title: str | None = None,
    level: int = 1,
    start: int | None = 1,
    end: int | None = 1,
) -> DocumentSection:
    return DocumentSection(
        id=section_id,
        title=title or f"章節 {section_id}",
        level=level,
        page_start=start,
        page_end=end,
    )


def test_changes_cover_added_changed_moved_and_removed() -> None:
    before = [_section("a"), _section("b"), _section("c"), _section("d")]
    after = [
        _section("b", title="章節 B"),
        _section("c"),
        _section("a"),
        _section("e"),
    ]

    changes = section_changes(before, after)

    assert [(change.section_id, change.change) for change in changes] == [
        ("b", "changed"),
        ("a", "moved"),
        ("e", "added"),
        ("d", "removed"),
    ]
    assert changes[0].before is not None and changes[0].before.title == "章節 b"
    assert changes[0].after is not None and changes[0].after.title == "章節 B"
    assert changes[2].before is None
    assert changes[3].after is None


def test_changes_do_not_mark_flags_or_unmoved_sections() -> None:
    """人が変えたかの印の違いは差分にせず、1 つ動かしても残りを「移動」にしない。"""
    before = [_section("a"), _section("b"), _section("c")]
    after = [before[0].model_copy(update={"edited": True}), before[2], before[1]]

    changes = section_changes(before, after)
    assert [change.change for change in changes] == ["moved"]
    assert section_changes(before, before) == []


def test_level_and_pages_are_changes() -> None:
    before = [_section("a"), _section("b")]
    after = [_section("a"), _section("b", level=2, start=2, end=3)]

    assert [change.change for change in section_changes(before, after)] == ["changed"]


def test_outline_fingerprint_follows_the_extracted_outline() -> None:
    base = [_section("a"), _section("b")]

    assert section_outline_fingerprint(base) == section_outline_fingerprint(list(base))
    assert section_outline_fingerprint(base) != section_outline_fingerprint(base[::-1])
    assert section_outline_fingerprint(base) != section_outline_fingerprint(
        [_section("a", start=2, end=2), _section("b")]
    )


def _current(extraction_id: str | None = "er_a", *ids: str) -> SectionExtractionBase:
    extracted = [_section(section_id) for section_id in (ids or ("a", "b"))]
    return extraction_base_of(extraction_id, extracted, has_extraction=extraction_id is not None)


def test_base_flags_separate_extraction_and_outline_changes() -> None:
    recorded = _current()

    def flags(current: SectionExtractionBase) -> tuple[bool, bool]:
        return base_change_flags(
            recorded_extraction_recipe_id=recorded.extraction_recipe_id,
            recorded_outline_sha256=recorded.outline_sha256,
            current=current,
        )

    assert flags(_current()) == (False, False)
    # 抽出の文の修正: ID は変わるが章節は同じ。
    assert flags(_current("er_b")) == (True, False)
    # 同じ原本・設定の再解析・章節の抽出規則の変更: ID は同じで章節が変わる。
    assert flags(_current("er_a", "a", "c")) == (True, True)
    assert flags(_current("er_b", "a", "c")) == (True, True)
    # 今の抽出が無いと比べられない。
    assert flags(_current(None)) == (False, False)


def test_edit_without_recorded_base_is_not_reported_as_changed() -> None:
    assert base_change_flags(
        recorded_extraction_recipe_id=None,
        recorded_outline_sha256=None,
        current=_current(),
    ) == (False, False)


class FakeOracle:
    """API の判定だけを確かめる偽物（版の書き込みは test_section_revisions_oracle.py）。"""

    def __init__(self) -> None:
        self.navigation = [
            DocumentNavigationNode(
                section_id="nav-1", title="合成の章 1", depth=1, page_start=1, page_end=2
            ),
            DocumentNavigationNode(
                section_id="nav-2", title="合成の章 2", depth=1, page_start=3, page_end=4
            ),
        ]
        self.active_extraction = "er_a"
        self.stored: dict[str, Any] | None = None
        self.revisions: dict[int, dict[str, Any]] = {}
        self.restore_calls: list[dict[str, Any]] = []
        self.restore_error: Exception | None = None
        self.saved_bases: list[SectionExtractionBase] = []

    async def document_exists(self, document_id: str) -> bool:
        return document_id == "doc-1"

    async def ensure_default_document_recipe(self, document_id: str) -> dict[str, Any]:
        return {"recipe_id": "recipe-1"}

    async def list_document_recipes(self, document_id: str) -> list[dict[str, Any]]:
        row = await self.get_document_recipe(document_id, "recipe-1")
        return [{**row, "slot_no": 1, "recipe_id": "recipe-1"}]

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, Any]:
        return {"recipe_id": recipe_id, "active_extraction_recipe_id": self.active_extraction}

    async def get_document_extraction_artifact(self, **_: Any) -> dict[str, Any]:
        extraction = StructuredExtraction(
            raw_text="本文",
            pages=[ExtractionPage(page_number=page) for page in range(1, 5)],
            navigation=self.navigation,
        )
        return {"extraction_json": extraction.model_dump(mode="json")}

    async def get_document_sections(self, document_id: str) -> dict[str, Any] | None:
        return self.stored

    async def save_document_sections(
        self,
        document_id: str,
        sections: dict[str, Any],
        *,
        base_revision: int | None,
        extraction_base: SectionExtractionBase,
    ) -> int:
        self.saved_bases.append(extraction_base)
        revision = (base_revision or 0) + 1
        self.stored = {
            "sections": sections,
            "revision": revision,
            "updated_at": None,
            "base_extraction_recipe_id": extraction_base.extraction_recipe_id,
            "base_outline_sha256": extraction_base.outline_sha256,
        }
        return revision

    async def delete_document_sections(
        self, document_id: str, *, extraction_base: SectionExtractionBase
    ) -> None:
        self.saved_bases.append(extraction_base)
        self.stored = None

    async def list_document_section_revisions(self, document_id: str) -> list[dict[str, Any]]:
        return [self.revisions[number] for number in sorted(self.revisions, reverse=True)]

    async def get_document_section_revision(
        self, document_id: str, revision_no: int
    ) -> dict[str, Any] | None:
        return self.revisions.get(revision_no)

    async def restore_document_section_revision(
        self,
        document_id: str,
        revision_no: int,
        *,
        expected_revision: int | None,
        extraction_base: SectionExtractionBase,
    ) -> int:
        self.restore_calls.append(
            {"revision_no": revision_no, "expected_revision": expected_revision}
        )
        if self.restore_error is not None:
            raise self.restore_error
        return revision_no + 1


async def _no_refresh(*_: Any, **__: Any) -> None:
    return None


@pytest.fixture
def oracle(monkeypatch: pytest.MonkeyPatch) -> FakeOracle:
    fake = FakeOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(documents_route, "refresh_document_section_map", _no_refresh)
    return fake


def _revision(
    number: int,
    *,
    kind: str = "EDITED",
    parent: int | None = None,
    restored_from: int | None = None,
    sections: list[DocumentSection] | None = None,
    base: SectionExtractionBase | None = None,
) -> dict[str, Any]:
    return {
        "revision_no": number,
        "parent_revision_no": parent,
        "restored_from_revision_no": restored_from,
        "revision_kind": kind,
        "base_extraction_recipe_id": base.extraction_recipe_id if base else None,
        "base_outline_sha256": base.outline_sha256 if base else None,
        "changed_section_count": 1,
        "created_at": _NOW,
        "sections_json": section_revision_payload(sections or []),
    }


def _navigation_base(oracle: FakeOracle) -> SectionExtractionBase:
    extracted = [
        DocumentSection(
            id=node.section_id,
            title=node.title,
            level=1,
            page_start=node.page_start,
            page_end=node.page_end,
            origin="extraction",
            source_section_id=node.section_id,
        )
        for node in oracle.navigation
    ]
    return extraction_base_of(oracle.active_extraction, extracted, has_extraction=True)


def test_save_records_the_extraction_the_edit_was_based_on(oracle: FakeOracle) -> None:
    sections = client.get(URL).json()["data"]["sections"]

    response = client.put(URL, json={"sections": sections, "base_revision": None})

    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["base_changed"], data["base_outline_changed"]) == (False, False)
    assert oracle.saved_bases[0].extraction_recipe_id == "er_a"
    assert oracle.saved_bases[0].outline_sha256 == _navigation_base(oracle).outline_sha256
    assert [row["id"] for row in oracle.saved_bases[0].outline["sections"]] == [  # type: ignore[attr-defined]
        "nav-1",
        "nav-2",
    ]


def test_reparse_is_shown_without_rewriting_the_edit(oracle: FakeOracle) -> None:
    sections = client.get(URL).json()["data"]["sections"]
    client.put(URL, json={"sections": sections, "base_revision": None})
    stored = dict(oracle.stored or {})

    # 抽出の文の修正: 抽出の ID だけが変わる。
    oracle.active_extraction = "er_b"
    data = client.get(URL).json()["data"]
    assert (data["base_changed"], data["base_outline_changed"]) == (True, False)

    # 同じ ID のまま抽出の章節が変わる（同じ設定の再解析）。
    oracle.active_extraction = "er_a"
    oracle.navigation[1].title = "合成の章 2 改"
    data = client.get(URL).json()["data"]
    assert (data["base_changed"], data["base_outline_changed"]) == (True, True)

    # 読むだけでは保存した修正を変えない。
    assert oracle.stored == stored


def test_reset_records_the_extraction_it_returned_to(oracle: FakeOracle) -> None:
    sections = client.get(URL).json()["data"]["sections"]
    client.put(URL, json={"sections": sections[:1], "base_revision": None})

    data = client.delete(URL).json()["data"]

    assert data["source"] == "extraction"
    assert data["base_changed"] is False
    assert oracle.saved_bases[-1].extraction_recipe_id == "er_a"


def test_history_marks_current_and_changed_base(oracle: FakeOracle) -> None:
    sections = client.get(URL).json()["data"]["sections"]
    client.put(URL, json={"sections": sections, "base_revision": None})
    assert oracle.stored is not None
    oracle.stored["revision"] = 3
    same = _navigation_base(oracle)
    older = extraction_base_of("er_old", [_section("x")], has_extraction=True)
    oracle.revisions = {
        1: _revision(1, kind="EXTRACTED", base=older),
        2: _revision(2, parent=1, base=older),
        3: _revision(3, parent=2, base=same),
    }

    response = client.get(f"{URL}/revisions")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["current_revision_no"] == 3
    assert data["max_revisions"] == max_section_revisions()
    rows = data["revisions"]
    assert [(row["revision_no"], row["kind"], row["is_current"]) for row in rows] == [
        (3, "edited", True),
        (2, "edited", False),
        (1, "extracted", False),
    ]
    assert (rows[0]["base_changed"], rows[0]["base_outline_changed"]) == (False, False)
    assert (rows[1]["base_changed"], rows[1]["base_outline_changed"]) == (True, True)
    assert rows[1]["base_extraction_recipe_id"] == "er_old"
    assert all("sections_json" not in row for row in rows)


def test_history_current_is_the_reset_revision_when_no_edit_remains(oracle: FakeOracle) -> None:
    oracle.revisions = {
        1: _revision(1, kind="EXTRACTED"),
        2: _revision(2, parent=1),
        3: _revision(3, kind="RESET", parent=2),
    }

    data = client.get(f"{URL}/revisions").json()["data"]

    assert data["current_revision_no"] == 3
    assert [row["is_current"] for row in data["revisions"]] == [True, False, False]


def test_history_empty_and_missing_document(oracle: FakeOracle) -> None:
    data = client.get(f"{URL}/revisions").json()["data"]
    assert data["revisions"] == []
    assert data["current_revision_no"] is None
    assert client.get("/api/documents/missing/sections/revisions").status_code == 404


def test_diff_defaults_to_parent_and_pages(oracle: FakeOracle) -> None:
    oracle.revisions = {
        1: _revision(1, kind="EXTRACTED", sections=[_section("a"), _section("b")]),
        2: _revision(2, parent=1, sections=[_section("a", title="改"), _section("c")]),
    }

    data = client.get(f"{URL}/revisions/2/diff").json()["data"]
    assert data["against_revision_no"] == 1
    assert data["total"] == 3
    assert [(row["section_id"], row["change"]) for row in data["changes"]] == [
        ("a", "changed"),
        ("c", "added"),
        ("b", "removed"),
    ]
    assert data["changes"][0]["before"]["title"] == "章節 a"
    assert data["changes"][0]["after"]["title"] == "改"

    paged = client.get(f"{URL}/revisions/2/diff?offset=2&limit=1").json()["data"]
    assert paged["total"] == 3
    assert [row["section_id"] for row in paged["changes"]] == ["b"]


def test_diff_against_a_chosen_revision_and_missing_baselines(oracle: FakeOracle) -> None:
    oracle.revisions = {
        1: _revision(1, kind="EXTRACTED", sections=[_section("a")]),
        3: _revision(3, parent=2, sections=[_section("a", title="改")]),
    }

    chosen = client.get(f"{URL}/revisions/1/diff?against=3").json()["data"]
    assert chosen["against_revision_no"] == 3
    assert chosen["total"] == 1

    # 親の版が上限で消えた・親が無い版は比べる版なし。
    pruned = client.get(f"{URL}/revisions/3/diff").json()["data"]
    assert pruned["against_revision_no"] is None
    assert pruned["changes"] == []

    assert client.get(f"{URL}/revisions/1/diff?against=9").status_code == 404
    assert client.get(f"{URL}/revisions/9/diff").status_code == 404


def test_restore_uses_expected_revision_and_maps_errors(oracle: FakeOracle) -> None:
    oracle.revisions = {1: _revision(1, sections=[_section("a")])}

    restored = client.post(f"{URL}/revisions/1/restore", json={"expected_revision": 2})
    assert restored.status_code == 200
    assert oracle.restore_calls == [{"revision_no": 1, "expected_revision": 2}]

    # 画面が読んだ版は必須（修正が無いときは null を渡す）。
    assert client.post(f"{URL}/revisions/1/restore", json={}).status_code == 422
    assert (
        client.post(f"{URL}/revisions/1/restore", json={"expected_revision": None}).status_code
        == 200
    )

    assert client.post(f"{URL}/revisions/9/restore", json={"expected_revision": 2}).status_code == (
        404
    )

    oracle.restore_error = DocumentSectionsConflictError("doc-1")
    conflict = client.post(f"{URL}/revisions/1/restore", json={"expected_revision": 2})
    assert conflict.status_code == 409

    oracle.restore_error = KeyError(1)
    assert client.post(f"{URL}/revisions/1/restore", json={"expected_revision": 2}).status_code == (
        404
    )


def test_restore_rejects_a_revision_that_does_not_fit_the_current_pages(
    oracle: FakeOracle,
) -> None:
    """再解析でページが減った後の版は、保存と同じ検証で止める（書き込まない）。"""
    oracle.revisions = {1: _revision(1, sections=[_section("a", start=9, end=9)])}

    response = client.post(f"{URL}/revisions/1/restore", json={"expected_revision": 2})

    assert response.status_code == 422
    assert "ページ数" in response.text
    assert oracle.restore_calls == []


def test_revision_routes_require_the_document_workspace_permission() -> None:
    from app.security.permissions import ROUTE_PERMISSIONS

    document = next(
        path.removesuffix("/sections")
        for method, path in ROUTE_PERMISSIONS
        if method == "PUT" and path.endswith("/sections")
    )
    workspace = ROUTE_PERMISSIONS[("PUT", f"{document}/sections")]
    assert ROUTE_PERMISSIONS[("GET", f"{document}/sections/revisions")] == workspace
    assert (
        ROUTE_PERMISSIONS[("GET", f"{document}/sections/revisions/{{revision_no}}/diff")]
        == workspace
    )
    assert (
        ROUTE_PERMISSIONS[("POST", f"{document}/sections/revisions/{{revision_no}}/restore")]
        == workspace
    )
