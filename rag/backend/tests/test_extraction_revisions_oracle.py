"""抽出の修訂の版（#2644。実 Oracle）。

各シナリオは ``OracleClient`` だけを使う（fixture に依存しない）。CI では ``oracle_db`` の下で流す。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast
from uuid import uuid4

import pytest

from app.clients.oracle import (
    DocumentReviewEditConflictError,
    OracleClient,
    ReviewedExtractionWrite,
)
from app.schemas.document import FileStatus

_SHA = "a" * 64
_PARSE_ID = "er_parse_synthetic"


def _extraction(*texts: str) -> dict[str, object]:
    return {
        "raw_text": "\n".join(texts),
        "elements": [
            {"element_id": f"el-{index}", "kind": "text", "text": text, "order": index}
            for index, text in enumerate(texts)
        ],
    }


def _write(
    extraction: Mapping[str, object], *, parse_id: str = _PARSE_ID
) -> Callable[..., ReviewedExtractionWrite]:
    def build(*_args: Mapping[str, object]) -> ReviewedExtractionWrite:
        return ReviewedExtractionWrite(
            extraction_recipe_id=f"er_rv_{uuid4().hex[:24]}",
            source_sha256=_SHA,
            extraction=extraction,
            recipe_subset=None,
            status="materialized",
            parse_extraction_recipe_id=parse_id,
        )

    return build


async def _parse(
    client: OracleClient,
    document_id: str,
    recipe_id: str,
    *texts: str,
    extraction_id: str | None = None,
) -> str:
    """解析の結果を入れ、レシピを確認待ちにする（解析の worker の書き方と同じ。版は作らない）。"""
    extraction_id = extraction_id or f"er_parsed_{uuid4().hex[:24]}"
    await client.upsert_document_extraction_artifact(
        document_id=document_id,
        extraction_recipe_id=extraction_id,
        source_sha256=_SHA,
        extraction=_extraction(*texts),
        status="materialized",
    )
    await client.update_document_recipe_status(
        recipe_id=recipe_id, status=FileStatus.REVIEW, active_extraction_recipe_id=extraction_id
    )
    return extraction_id


async def _new_recipe(client: OracleClient) -> tuple[str, str]:
    document = await client.create_document(
        file_name=f"synthetic-revisions-{uuid4().hex[:8]}.txt",
        object_storage_path=f"synthetic-revisions-{uuid4().hex[:8]}.txt",
        content_type="text/plain",
    )
    recipe = await client.ensure_default_document_recipe(document.id)
    return document.id, str(recipe["recipe_id"])


async def _save(client: OracleClient, document_id: str, recipe_id: str, *texts: str) -> str:
    return await client.save_document_recipe_review_extraction(
        document_id=document_id, recipe_id=recipe_id, build=_write(_extraction(*texts))
    )


async def _revision_texts(client: OracleClient, recipe_id: str, revision_no: int) -> list[str]:
    revision = await client.get_document_recipe_extraction_revision(recipe_id, revision_no)
    assert revision is not None
    extraction = cast("dict[str, Any]", revision["extraction_json"])
    return [element["text"] for element in extraction["elements"]]


async def check_saves_keep_past_revisions(client: OracleClient) -> None:
    """保存ごとに新しい ID の版を作り、解析の結果と過去の修訂の内容が残る。"""
    document_id, recipe_id = await _new_recipe(client)
    parsed_id = await _parse(client, document_id, recipe_id, "A0", "B0")

    first = await _save(client, document_id, recipe_id, "A1", "B0")
    second = await _save(client, document_id, recipe_id, "A1", "B1")

    assert len({parsed_id, first, second}) == 3
    revisions = await client.list_document_recipe_extraction_revisions(recipe_id)
    assert [
        (row["revision_no"], row["revision_kind"], row["parent_revision_no"]) for row in revisions
    ] == [(3, "EDITED", 2), (2, "EDITED", 1), (1, "PARSED", None)]
    assert [row["base_revision_no"] for row in revisions] == [1, 1, 1]
    assert [row["changed_element_count"] for row in revisions] == [1, 1, 0]
    assert await _revision_texts(client, recipe_id, 1) == ["A0", "B0"]
    assert await _revision_texts(client, recipe_id, 2) == ["A1", "B0"]
    assert await _revision_texts(client, recipe_id, 3) == ["A1", "B1"]
    # 参照の無くなった前の抽出の行は消え、今の抽出だけが残る（内容は版にある）。
    assert (
        await client.get_document_extraction_artifact(
            document_id=document_id, extraction_recipe_id=first
        )
        is None
    )
    row = await client.get_document_recipe(document_id, recipe_id)
    assert row is not None
    assert row["active_extraction_recipe_id"] == second


async def check_save_without_change_makes_no_revision(client: OracleClient) -> None:
    """文が変わらない保存は版を作らない。"""
    document_id, recipe_id = await _new_recipe(client)
    parsed_id = await _parse(client, document_id, recipe_id, "A0")

    saved = await _save(client, document_id, recipe_id, "A0")

    assert saved == parsed_id
    assert await client.list_document_recipe_extraction_revisions(recipe_id) == []


async def check_restore_is_a_new_revision(client: OracleClient) -> None:
    """復元は新しい版で、過去の版を書き換えない。古い画面からの復元は 409 にする。"""
    document_id, recipe_id = await _new_recipe(client)
    await _parse(client, document_id, recipe_id, "A0")
    edited = await _save(client, document_id, recipe_id, "A1")

    with pytest.raises(DocumentReviewEditConflictError):
        await client.restore_document_recipe_extraction_revision(
            document_id=document_id,
            recipe_id=recipe_id,
            revision_no=1,
            expected_extraction_recipe_id="er_stale",
            build=_restore_build,
        )

    restored_no = await client.restore_document_recipe_extraction_revision(
        document_id=document_id,
        recipe_id=recipe_id,
        revision_no=1,
        expected_extraction_recipe_id=edited,
        build=_restore_build,
    )

    assert restored_no == 3
    revisions = await client.list_document_recipe_extraction_revisions(recipe_id)
    latest = revisions[0]
    assert (latest["revision_kind"], latest["restored_from_revision_no"]) == ("RESTORED", 1)
    assert (latest["parent_revision_no"], latest["changed_element_count"]) == (2, 1)
    assert await _revision_texts(client, recipe_id, 3) == ["A0"]
    assert await _revision_texts(client, recipe_id, 2) == ["A1"]
    row = await client.get_document_recipe(document_id, recipe_id)
    assert row is not None
    assert row["active_extraction_recipe_id"] == latest["extraction_recipe_id"]
    current = await client.get_document_extraction_artifact(
        document_id=document_id, extraction_recipe_id=str(latest["extraction_recipe_id"])
    )
    assert current is not None
    extraction = cast("dict[str, Any]", current["extraction_json"])
    assert [element["text"] for element in extraction["elements"]] == ["A0"]

    # 今の版と同じ抽出の版は復元しない。
    with pytest.raises(DocumentReviewEditConflictError, match="今の版"):
        await client.restore_document_recipe_extraction_revision(
            document_id=document_id,
            recipe_id=recipe_id,
            revision_no=3,
            expected_extraction_recipe_id=str(latest["extraction_recipe_id"]),
            build=_restore_build,
        )


def _restore_build(
    _recipe: Mapping[str, object],
    _artifact: Mapping[str, object],
    revision: Mapping[str, object],
) -> ReviewedExtractionWrite:
    return _write(cast("Mapping[str, object]", revision["extraction_json"]))()


async def check_reparse_is_recorded_before_the_next_save(client: OracleClient) -> None:
    """解析をやり直した後の保存は、再解析の結果を基の解析の版として記録する（自動で書き換えない）。"""
    document_id, recipe_id = await _new_recipe(client)
    await _parse(client, document_id, recipe_id, "A0")
    await _save(client, document_id, recipe_id, "A1")

    reparsed_id = await _parse(client, document_id, recipe_id, "A-new")
    # 再解析の直後は今の抽出が版に無い。過去の版はそのまま残る。
    assert await _revision_texts(client, recipe_id, 2) == ["A1"]
    revisions = await client.list_document_recipe_extraction_revisions(recipe_id)
    assert reparsed_id not in {row["extraction_recipe_id"] for row in revisions}

    await _save(client, document_id, recipe_id, "A-new-edited")

    revisions = await client.list_document_recipe_extraction_revisions(recipe_id)
    assert [
        (
            row["revision_no"],
            row["revision_kind"],
            row["parent_revision_no"],
            row["base_revision_no"],
        )
        for row in revisions
    ] == [
        (4, "EDITED", 3, 3),
        (3, "PARSED", 2, 3),
        (2, "EDITED", 1, 1),
        (1, "PARSED", None, 1),
    ]
    assert await _revision_texts(client, recipe_id, 3) == ["A-new"]


async def check_reparse_under_the_same_id_is_a_new_revision(client: OracleClient) -> None:
    """同じ設定の再解析は解析の ID が同じになる。古い解析の版を今の版にせず、復元もできる。"""
    document_id, recipe_id = await _new_recipe(client)
    parsed_id = await _parse(client, document_id, recipe_id, "A0")
    await _save(client, document_id, recipe_id, "A1")

    await _parse(client, document_id, recipe_id, "A-new", extraction_id=parsed_id)
    # 古い解析の版の内容は再解析で変わらない。
    assert await _revision_texts(client, recipe_id, 1) == ["A0"]

    restored_no = await client.restore_document_recipe_extraction_revision(
        document_id=document_id,
        recipe_id=recipe_id,
        revision_no=1,
        expected_extraction_recipe_id=parsed_id,
        build=_restore_build,
    )

    assert restored_no == 4
    revisions = await client.list_document_recipe_extraction_revisions(recipe_id)
    assert [(row["revision_no"], row["revision_kind"]) for row in revisions] == [
        (4, "RESTORED"),
        (3, "PARSED"),
        (2, "EDITED"),
        (1, "PARSED"),
    ]
    assert await _revision_texts(client, recipe_id, 3) == ["A-new"]
    assert await _revision_texts(client, recipe_id, 4) == ["A0"]


async def check_serving_chunk_set_keeps_its_extraction(client: OracleClient) -> None:
    """保存・復元は、検索に出している版の抽出と active の chunk_set を変えない。"""
    document_id, recipe_id = await _new_recipe(client)
    served_id = await _parse(client, document_id, recipe_id, "A0")
    chunk_set_id = f"cs_rev_{uuid4().hex[:24]}"
    await client.upsert_chunk_set(
        chunk_set_id=chunk_set_id,
        document_id=document_id,
        recipe_id=recipe_id,
        extraction_recipe_id=served_id,
    )
    await client.mark_chunk_set_indexed(chunk_set_id=chunk_set_id, chunk_count=0, vector_count=0)
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id, chunk_set_id=chunk_set_id, extraction_recipe_id=served_id
    )
    await client.update_document_recipe_status(recipe_id=recipe_id, status=FileStatus.REVIEW)

    edited = await _save(client, document_id, recipe_id, "A1")
    await client.restore_document_recipe_extraction_revision(
        document_id=document_id,
        recipe_id=recipe_id,
        revision_no=1,
        expected_extraction_recipe_id=edited,
        build=_restore_build,
    )

    served = await client.get_document_extraction_artifact(
        document_id=document_id, extraction_recipe_id=served_id
    )
    assert served is not None
    extraction = cast("dict[str, Any]", served["extraction_json"])
    assert [element["text"] for element in extraction["elements"]] == ["A0"]
    chunk_set = await client.get_chunk_set(chunk_set_id)
    assert chunk_set is not None
    assert (chunk_set["extraction_recipe_id"], chunk_set["is_active"]) == (served_id, 1)
    assert await client.recipe_served_extraction_ids(recipe_id) == {served_id}


async def check_retention_keeps_current_and_base(
    client: OracleClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上限を超えた古い版を消し、今の版と基の解析の版は残す。番号は振り直さない。"""
    from app.clients import extraction_revision_store

    monkeypatch.setattr(extraction_revision_store, "max_extraction_revisions", lambda: 3)
    document_id, recipe_id = await _new_recipe(client)
    await _parse(client, document_id, recipe_id, "A0")
    for index in range(1, 6):
        await _save(client, document_id, recipe_id, f"A{index}")

    revisions = await client.list_document_recipe_extraction_revisions(recipe_id)
    assert [row["revision_no"] for row in revisions] == [6, 5, 1]
    assert await _revision_texts(client, recipe_id, 1) == ["A0"]
    # 親の版は番号のまま残る（消えた版を指してもよい）。
    assert revisions[1]["parent_revision_no"] == 4


async def check_deleting_the_recipe_deletes_its_revisions(client: OracleClient) -> None:
    """文書の削除でレシピごと版が消える（FK の ON DELETE CASCADE）。"""
    document_id, recipe_id = await _new_recipe(client)
    await _parse(client, document_id, recipe_id, "A0")
    await _save(client, document_id, recipe_id, "A1")

    await client.delete_document(document_id)

    assert await client.list_document_recipe_extraction_revisions(recipe_id) == []


SCENARIOS = (
    check_saves_keep_past_revisions,
    check_save_without_change_makes_no_revision,
    check_restore_is_a_new_revision,
    check_reparse_is_recorded_before_the_next_save,
    check_reparse_under_the_same_id_is_a_new_revision,
    check_serving_chunk_set_keeps_its_extraction,
    check_deleting_the_recipe_deletes_its_revisions,
)


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.__name__)
async def test_extraction_revision_scenarios(scenario: Any) -> None:
    await scenario(OracleClient())


@pytest.mark.usefixtures("oracle_db")
async def test_extraction_revision_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    await check_retention_keeps_current_and_base(OracleClient(), monkeypatch)
