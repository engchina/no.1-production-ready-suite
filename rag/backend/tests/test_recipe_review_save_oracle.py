"""抽出確認の修正の保存を、レシピの行ロックの下で行うテスト(#2616)。

`save_document_recipe_review_extraction` はレシピの行を ``FOR UPDATE`` で押さえてから状態と
抽出成果を読み、修正を当てて書く。SQL の順序と競合の扱いは fake の pool で、並行の保存が
互いの修正を消さないことは実 Oracle で確かめる(未到達なら oracle_db fixture が skip し、
作成行は cleanup_to_baseline で後始末する)。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest

from app.clients.oracle import (
    DocumentReviewEditConflictError,
    OracleClient,
    ReviewedExtractionWrite,
)
from app.schemas.document import FileStatus
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline


def _recipe_row(*, status: str = "REVIEW", active: str | None = "er_base") -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "recipe_id": "recipe-2",
        "document_id": "doc-1",
        "slot_no": 2,
        "processing_config": "{}",
        "status": status,
        "active_extraction_recipe_id": active,
        "config_revision": 3,
        "created_at": now,
        "updated_at": now,
    }


def _artifact_row() -> dict[str, object]:
    return {
        "document_id": "doc-1",
        "extraction_recipe_id": "er_base",
        "source_sha256": "a" * 64,
        "status": "materialized",
        "reason": None,
        "recipe_subset": '{"rag_preprocess_profile": "passthrough"}',
        "extraction_json": '{"raw_text": "A0", "elements": [{"element_id": "a", "text": "A0"}]}',
        "metrics_json": None,
    }


def _write(
    _recipe: Mapping[str, object], artifact: Mapping[str, object]
) -> ReviewedExtractionWrite:
    extraction = artifact["extraction_json"]
    assert isinstance(extraction, Mapping)
    return ReviewedExtractionWrite(
        extraction_recipe_id="er_reviewed",
        source_sha256="a" * 64,
        extraction={**extraction, "raw_text": "A1"},
        recipe_subset=None,
        status="materialized",
        parse_extraction_recipe_id="er_parse",
    )


async def test_review_save_locks_recipe_before_reading_and_writes_in_one_transaction() -> None:
    """レシピの行を押さえてから成果を読み、書き込みと参照の切り替えを同じ transaction で行う。"""
    pool = FakeOraclePool(execute_results=[[_recipe_row()], [], [_artifact_row()], []])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    seen: list[tuple[Mapping[str, object], Mapping[str, object]]] = []

    def build(
        recipe: Mapping[str, object], artifact: Mapping[str, object]
    ) -> ReviewedExtractionWrite:
        seen.append((recipe, artifact))
        return _write(recipe, artifact)

    saved = await client.save_document_recipe_review_extraction(
        document_id="doc-1", recipe_id="recipe-2", build=build
    )

    assert saved == "er_reviewed"
    statements = [call.statement for call in pool.connection.calls]
    assert "FOR UPDATE OF r.recipe_id" in statements[0]
    assert "FROM rag_ingestion_jobs" in statements[1]
    assert "FROM rag_document_extractions" in statements[2]
    # 版が無いので、今の抽出を解析の版として記録してから新しい版を書く(#2644)。
    assert "FROM rag_document_extraction_revisions" in statements[3]
    assert statements[4].lstrip().startswith("INSERT INTO rag_document_extraction_revisions")
    assert statements[5].lstrip().startswith("MERGE INTO rag_document_extractions")
    assert statements[6].lstrip().startswith("UPDATE rag_document_recipes")
    assert statements[7].lstrip().startswith("INSERT INTO rag_document_extraction_revisions")
    assert statements[8].lstrip().startswith("DELETE FROM rag_document_extractions")
    assert pool.connection.calls[2].parameters["extraction_recipe_id"] == "er_base"
    parsed = pool.connection.calls[4].parameters
    assert (parsed["revision_no"], parsed["revision_kind"]) == (1, "PARSED")
    assert parsed["parent_revision_no"] is None
    assert parsed["extraction_recipe_id"] == "er_base"
    assert pool.connection.calls[5].parameters["extraction_recipe_id"] == "er_reviewed"
    assert pool.connection.calls[6].parameters == {
        "recipe_id": "recipe-2",
        "extraction_recipe_id": "er_reviewed",
    }
    edited = pool.connection.calls[7].parameters
    assert (edited["revision_no"], edited["revision_kind"]) == (2, "EDITED")
    assert (edited["parent_revision_no"], edited["base_revision_no"]) == (1, 1)
    assert edited["extraction_recipe_id"] == "er_reviewed"
    # 切替の前の抽出は、参照が無ければ消す(内容は版に残っている)。
    assert pool.connection.calls[8].parameters["extraction_recipe_id"] == "er_base"
    assert pool.connection.commits == 1
    [(recipe, artifact)] = seen
    assert recipe["config_revision"] == 3
    assert artifact["extraction_json"] == {
        "raw_text": "A0",
        "elements": [{"element_id": "a", "text": "A0"}],
    }
    assert artifact["recipe_subset"] == {"rag_preprocess_profile": "passthrough"}


@pytest.mark.parametrize(
    ("results", "message"),
    [
        ([[_recipe_row(status="CHUNKED")]], "確認待ちのレシピのみ修正できます。"),
        ([[_recipe_row()], [{"job_id": "job-1"}]], "このレシピは処理中または待機中です。"),
        ([[_recipe_row(active=None)], []], "修正対象の抽出結果がありません。"),
        ([[_recipe_row()], [], []], "修正対象の抽出結果がありません。"),
    ],
    ids=["not-review", "job-active", "no-pointer", "no-artifact"],
)
async def test_review_save_rejects_without_writing(
    results: list[list[dict[str, object]]], message: str
) -> None:
    """確認待ちでない・処理中・抽出結果が無いときは書かずに競合を返し、rollback する。"""
    pool = FakeOraclePool(execute_results=results)
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    with pytest.raises(DocumentReviewEditConflictError, match=message):
        await client.save_document_recipe_review_extraction(
            document_id="doc-1", recipe_id="recipe-2", build=_write
        )

    statements = [call.statement for call in pool.connection.calls]
    assert not any(
        statement.lstrip().startswith(("MERGE", "UPDATE", "INSERT", "DELETE"))
        for statement in statements
    )
    assert pool.connection.commits == 0
    assert pool.connection.rollbacks == 1


async def test_review_save_missing_recipe_is_key_error() -> None:
    """見えないレシピは KeyError(画面は 404)。"""
    pool = FakeOraclePool(execute_results=[[]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    with pytest.raises(KeyError):
        await client.save_document_recipe_review_extraction(
            document_id="doc-1", recipe_id="recipe-2", build=_write
        )


_Build = Callable[[Mapping[str, object], Mapping[str, object]], ReviewedExtractionWrite]


def _element_edit_build(element_id: str, text: str) -> tuple[list[str], _Build]:
    """要素 1 件の文を差し替える build。読んだ成果の要素の文を記録し、重なるよう少し待つ。"""
    seen: list[str] = []

    def build(
        recipe: Mapping[str, object], artifact: Mapping[str, object]
    ) -> ReviewedExtractionWrite:
        extraction = artifact["extraction_json"]
        assert isinstance(extraction, Mapping)
        raw_elements = extraction["elements"]
        assert isinstance(raw_elements, list)
        elements = [dict(element) for element in raw_elements]
        seen.append(",".join(str(element["text"]) for element in elements))
        # 行ロックが無ければ、もう一方の保存がこの間に同じ成果を読んで後から上書きする。
        time.sleep(0.5)
        for element in elements:
            if element["element_id"] == element_id:
                element["text"] = text
        return ReviewedExtractionWrite(
            extraction_recipe_id=f"er_rv_{uuid4().hex[:24]}",
            source_sha256="b" * 64,
            extraction={**extraction, "elements": elements},
            recipe_subset=None,
            status="materialized",
            parse_extraction_recipe_id="er_parse",
        )

    return seen, build


@pytest.mark.usefixtures("oracle_db")
async def test_concurrent_review_saves_keep_both_element_edits() -> None:
    """同じレシピへ別の要素の修正を同時に保存しても、両方の修正が残る(行ロックで直列)。"""
    client = OracleClient()
    detail = await client.create_document(
        file_name="review-concurrency.txt",
        object_storage_path="oci://bucket/review-concurrency.txt",
        content_type="text/plain",
    )
    recipe = await client.ensure_default_document_recipe(detail.id)
    recipe_id = str(recipe["recipe_id"])
    base_id = f"er_base_{uuid4().hex[:16]}"
    original = {
        "raw_text": "A1\nB1",
        "elements": [
            {"element_id": "a", "kind": "text", "text": "A1", "order": 0},
            {"element_id": "b", "kind": "text", "text": "B1", "order": 1},
        ],
    }
    await client.upsert_document_extraction_artifact(
        document_id=detail.id,
        extraction_recipe_id=base_id,
        source_sha256="b" * 64,
        extraction=original,
        status="materialized",
    )
    await client.update_document_recipe_status(
        recipe_id=recipe_id, status=FileStatus.REVIEW, active_extraction_recipe_id=base_id
    )
    seen_a, build_a = _element_edit_build("a", "A2")
    seen_b, build_b = _element_edit_build("b", "B2")

    saved = await asyncio.gather(
        client.save_document_recipe_review_extraction(
            document_id=detail.id, recipe_id=recipe_id, build=build_a
        ),
        client.save_document_recipe_review_extraction(
            document_id=detail.id, recipe_id=recipe_id, build=build_b
        ),
    )

    # 保存ごとに新しい ID の版になる(#2644)。今の成果は後に入った保存のもの。
    assert saved[0] != saved[1]
    row = await client.get_document_recipe(detail.id, recipe_id)
    assert row is not None
    reviewed_id = str(row["active_extraction_recipe_id"])
    assert reviewed_id in saved
    # 後に入った保存は、先の保存の修正を含む成果を読んでいる。
    assert sorted([*seen_a, *seen_b]) in (
        sorted(["A1,B1", "A2,B1"]),
        sorted(["A1,B1", "A1,B2"]),
    )
    reviewed = await client.get_document_extraction_artifact(
        document_id=detail.id, extraction_recipe_id=reviewed_id
    )
    assert reviewed is not None
    reviewed_json = reviewed["extraction_json"]
    assert isinstance(reviewed_json, dict)
    assert [element["text"] for element in reviewed_json["elements"]] == ["A2", "B2"]
    # 元の抽出の内容は解析の版として残る(#2644)。
    parsed = await client.get_document_recipe_extraction_revision(recipe_id, 1)
    assert parsed is not None
    assert parsed["revision_kind"] == "PARSED"
    assert parsed["extraction_recipe_id"] == base_id
    parsed_content = dict(cast("dict[str, object]", parsed["extraction_json"]))
    assert parsed_content.pop("parser_artifacts") == {
        "inspection_index": {"version": 1, "elements": {"a": 0, "b": 1}}
    }
    assert parsed_content == original
    assert row["status"] == FileStatus.REVIEW.value
