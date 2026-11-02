"""人が修正した章節の版（#4171。実 Oracle）。

各シナリオは ``OracleClient`` だけを使う（fixture に依存しない）。CI では ``oracle_db`` の下で流す。
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from app.clients.oracle import (
    DocumentSectionsConflictError,
    OracleClient,
    OracleConnectionProtocol,
    _execute,
    _json_bind,
    _json_input_sizes,
)
from app.rag.document_sections import stored_sections_payload
from app.rag.section_revisions import (
    SectionExtractionBase,
    extraction_base_of,
    section_revision_payload,
)
from app.schemas.document import DocumentSection


def _section(section_id: str, title: str = "") -> DocumentSection:
    return DocumentSection(
        id=section_id,
        title=title or f"合成の章節 {section_id}",
        level=1,
        page_start=1,
        page_end=1,
        origin="extraction",
        source_section_id=section_id,
    )


def _base(extraction_id: str = "er_a", *outline_ids: str) -> SectionExtractionBase:
    extracted = [_section(section_id) for section_id in (outline_ids or ("a", "b"))]
    return extraction_base_of(extraction_id, extracted, has_extraction=True)


def _payload(*titles: str, removed: tuple[str, ...] = ()) -> dict[str, object]:
    return section_revision_payload(
        [_section(f"s{index}", title) for index, title in enumerate(titles)], removed
    )


async def _new_document(client: OracleClient) -> str:
    document = await client.create_document(
        file_name=f"synthetic-section-revisions-{uuid4().hex[:8]}.txt",
        object_storage_path=f"synthetic-section-revisions-{uuid4().hex[:8]}.txt",
        content_type="text/plain",
    )
    return document.id


async def _titles(client: OracleClient, document_id: str, revision_no: int) -> list[str]:
    row = await client.get_document_section_revision(document_id, revision_no)
    assert row is not None
    return [section.title for section in stored_sections_payload(row["sections_json"])[0]]


async def _kinds(client: OracleClient, document_id: str) -> list[tuple[int, str]]:
    rows = await client.list_document_section_revisions(document_id)
    return [(int(str(row["revision_no"])), str(row["revision_kind"])) for row in rows]


async def check_saves_keep_past_revisions(client: OracleClient) -> str:
    """最初の保存は抽出の章節も版にし、保存のたびに過去の版が残る。"""
    document_id = await _new_document(client)

    first = await client.save_document_sections(
        document_id, _payload("A1"), base_revision=None, extraction_base=_base()
    )
    second = await client.save_document_sections(
        document_id, _payload("A2"), base_revision=first, extraction_base=_base()
    )

    assert (first, second) == (2, 3)
    assert await _kinds(client, document_id) == [(3, "EDITED"), (2, "EDITED"), (1, "EXTRACTED")]
    assert await _titles(client, document_id, 1) == ["合成の章節 a", "合成の章節 b"]
    assert await _titles(client, document_id, 2) == ["A1"]
    assert await _titles(client, document_id, 3) == ["A2"]
    stored = await client.get_document_sections(document_id)
    assert stored is not None
    assert stored["revision"] == 3
    assert stored["base_extraction_recipe_id"] == "er_a"
    assert stored["base_outline_sha256"] == _base().outline_sha256
    rows = await client.list_document_section_revisions(document_id)
    assert rows[0]["parent_revision_no"] == 2
    assert rows[0]["changed_section_count"] == 1
    return document_id


async def check_save_without_change_makes_no_revision(client: OracleClient) -> str:
    """内容も基も同じ保存は版を作らない。基の抽出が変わっていれば内容が同じでも版を作る。"""
    document_id = await _new_document(client)
    revision = await client.save_document_sections(
        document_id, _payload("A1"), base_revision=None, extraction_base=_base()
    )

    same = await client.save_document_sections(
        document_id, _payload("A1"), base_revision=revision, extraction_base=_base()
    )
    assert same == revision
    assert len(await _kinds(client, document_id)) == 2

    confirmed = await client.save_document_sections(
        document_id, _payload("A1"), base_revision=revision, extraction_base=_base("er_b")
    )
    assert confirmed == revision + 1
    rows = await client.list_document_section_revisions(document_id)
    assert rows[0]["base_extraction_recipe_id"] == "er_b"
    assert rows[0]["changed_section_count"] == 0
    return document_id


async def check_conflicts_are_detected(client: OracleClient) -> str:
    """読み込んだ後の保存は衝突にする（最初の保存どうしも）。"""
    document_id = await _new_document(client)
    results = await asyncio.gather(
        *(
            client.save_document_sections(
                document_id, _payload(f"A{index}"), base_revision=None, extraction_base=_base()
            )
            for index in range(2)
        ),
        return_exceptions=True,
    )
    assert sorted(type(result).__name__ for result in results) == [
        "DocumentSectionsConflictError",
        "int",
    ]
    assert await _kinds(client, document_id) == [(2, "EDITED"), (1, "EXTRACTED")]

    with pytest.raises(DocumentSectionsConflictError):
        await client.save_document_sections(
            document_id, _payload("B"), base_revision=None, extraction_base=_base()
        )
    with pytest.raises(DocumentSectionsConflictError):
        await client.save_document_sections(
            document_id, _payload("B"), base_revision=1, extraction_base=_base()
        )
    assert await _kinds(client, document_id) == [(2, "EDITED"), (1, "EXTRACTED")]
    return document_id


async def check_restore_is_a_new_revision(client: OracleClient) -> str:
    """復元は新しい版にし、過去の版を変えず、基の抽出は元の版を引き継ぐ。"""
    document_id = await _new_document(client)
    first = await client.save_document_sections(
        document_id, _payload("A1"), base_revision=None, extraction_base=_base("er_a")
    )
    second = await client.save_document_sections(
        document_id, _payload("A2"), base_revision=first, extraction_base=_base("er_b")
    )

    with pytest.raises(DocumentSectionsConflictError):
        await client.restore_document_section_revision(
            document_id, first, expected_revision=first, extraction_base=_base("er_b")
        )
    with pytest.raises(DocumentSectionsConflictError):
        await client.restore_document_section_revision(
            document_id, second, expected_revision=second, extraction_base=_base("er_b")
        )
    with pytest.raises(KeyError):
        await client.restore_document_section_revision(
            document_id, 99, expected_revision=second, extraction_base=_base("er_b")
        )

    restored = await client.restore_document_section_revision(
        document_id, first, expected_revision=second, extraction_base=_base("er_b")
    )

    assert restored == second + 1
    assert await _titles(client, document_id, restored) == ["A1"]
    assert await _titles(client, document_id, first) == ["A1"]
    assert await _titles(client, document_id, second) == ["A2"]
    rows = await client.list_document_section_revisions(document_id)
    assert rows[0]["revision_kind"] == "RESTORED"
    assert rows[0]["restored_from_revision_no"] == first
    assert rows[0]["parent_revision_no"] == second
    assert rows[0]["base_extraction_recipe_id"] == "er_a"
    stored = await client.get_document_sections(document_id)
    assert stored is not None
    assert stored["revision"] == restored
    assert stored["base_extraction_recipe_id"] == "er_a"
    return document_id


async def check_reset_keeps_history_and_numbers(client: OracleClient) -> str:
    """抽出結果へ戻すと今の修正は消えるが版は残り、番号は続く。戻した版も復元できる。"""
    document_id = await _new_document(client)
    edited = await client.save_document_sections(
        document_id, _payload("A1"), base_revision=None, extraction_base=_base()
    )

    await client.delete_document_sections(document_id, extraction_base=_base())
    assert await client.get_document_sections(document_id) is None
    assert await _kinds(client, document_id) == [(3, "RESET"), (2, "EDITED"), (1, "EXTRACTED")]
    assert await _titles(client, document_id, 3) == ["合成の章節 a", "合成の章節 b"]

    # 修正が無ければ戻しても版を作らない。
    await client.delete_document_sections(document_id, extraction_base=_base())
    assert len(await _kinds(client, document_id)) == 3

    # 抽出の章節が同じなら戻した版が親になる。
    after = await client.save_document_sections(
        document_id, _payload("A2"), base_revision=None, extraction_base=_base()
    )
    assert after == 4
    rows = await client.list_document_section_revisions(document_id)
    assert rows[0]["parent_revision_no"] == 3

    await client.delete_document_sections(document_id, extraction_base=_base())
    # 抽出の章節が変わっていれば、修正の前に今の抽出の章節を版にする。
    changed = await client.save_document_sections(
        document_id, _payload("A3"), base_revision=None, extraction_base=_base("er_b", "a", "c")
    )
    assert await _kinds(client, document_id) == [
        (changed, "EDITED"),
        (changed - 1, "EXTRACTED"),
        (5, "RESET"),
        (4, "EDITED"),
        (3, "RESET"),
        (edited, "EDITED"),
        (1, "EXTRACTED"),
    ]
    return document_id


async def check_edits_before_history_are_recorded(client: OracleClient) -> str:
    """履歴の表より前の修正は、次の保存のときに基の記録なしの版にする（古い形も読む）。"""
    document_id = await _new_document(client)
    legacy = [_section("old", "A0").model_dump()]

    def insert_legacy(connection: OracleConnectionProtocol) -> None:
        _execute(
            connection,
            """
            INSERT INTO rag_document_sections (document_id, sections_json, revision)
            VALUES (:document_id, :sections_json, 5)
            """,
            {"document_id": document_id, "sections_json": _json_bind(legacy)},
            input_sizes=_json_input_sizes("sections_json"),
        )

    await client._run_transaction(insert_legacy)
    stored = await client.get_document_sections(document_id)
    assert stored is not None
    assert stored["base_extraction_recipe_id"] is None

    saved = await client.save_document_sections(
        document_id, _payload("A1"), base_revision=5, extraction_base=_base()
    )

    assert saved == 6
    assert await _kinds(client, document_id) == [(6, "EDITED"), (5, "EDITED")]
    assert await _titles(client, document_id, 5) == ["A0"]
    rows = await client.list_document_section_revisions(document_id)
    assert rows[1]["base_extraction_recipe_id"] is None
    assert rows[0]["base_extraction_recipe_id"] == "er_a"
    return document_id


async def check_deleting_the_document_deletes_revisions(client: OracleClient) -> str:
    """文書の削除で版が消える（FK の ON DELETE CASCADE）。"""
    document_id = await _new_document(client)
    await client.save_document_sections(
        document_id, _payload("A1"), base_revision=None, extraction_base=_base()
    )

    await client.delete_document(document_id)

    assert await client.list_document_section_revisions(document_id) == []
    return document_id


async def check_retention_keeps_the_newest(
    client: OracleClient, monkeypatch: pytest.MonkeyPatch
) -> str:
    """上限を超えた古い版を消し、番号は振り直さない。親の版は番号のまま残る。"""
    from app.clients import section_revision_store

    monkeypatch.setattr(section_revision_store, "max_section_revisions", lambda: 3)
    document_id = await _new_document(client)
    revision: int | None = None
    for index in range(5):
        revision = await client.save_document_sections(
            document_id, _payload(f"A{index}"), base_revision=revision, extraction_base=_base()
        )

    rows = await client.list_document_section_revisions(document_id)
    assert [row["revision_no"] for row in rows] == [6, 5, 4]
    assert rows[-1]["parent_revision_no"] == 3
    assert await client.get_document_section_revision(document_id, 3) is None
    return document_id


SCENARIOS = (
    check_saves_keep_past_revisions,
    check_save_without_change_makes_no_revision,
    check_conflicts_are_detected,
    check_restore_is_a_new_revision,
    check_reset_keeps_history_and_numbers,
    check_edits_before_history_are_recorded,
    check_deleting_the_document_deletes_revisions,
)


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.__name__)
async def test_section_revision_scenarios(scenario: Any) -> None:
    await scenario(OracleClient())


@pytest.mark.usefixtures("oracle_db")
async def test_section_revision_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    await check_retention_keeps_the_newest(OracleClient(), monkeypatch)
