"""文書の一覧の「状態」をレシピ1の状態から出すテスト(#2012)。

取込の job はレシピの job で、工程の状態はレシピの行だけに書く(#504)。一覧の `status`・状態の
絞り込み・件数は、文書の詳細が既定で出す状態(`slot_no` の最も小さいレシピの状態。レシピの行が
無ければ文書の状態)と同じ正本から出す。SQL の形は fake の pool で、実際の値は実 Oracle で確かめる
(未到達なら oracle_db fixture が skip し、作成行は cleanup_to_baseline で後始末する)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.schemas.document import FileStatus, IngestionJob, IngestionJobStatus
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline
from tests.test_oracle_db_paths import _document_state_row

_RECIPE_STATUS_SQL = "MIN(r.status) KEEP (DENSE_RANK FIRST ORDER BY r.slot_no)"


def _list_selects(pool: FakeOraclePool) -> list[str]:
    return [
        call.statement
        for call in pool.connection.calls
        if call.statement.lstrip().upper().startswith("SELECT")
        and "FROM rag_documents" in call.statement
    ]


async def test_list_documents_selects_status_from_first_recipe() -> None:
    """一覧の列の状態は、レシピ1の状態(無ければ文書の状態)を `status` として読む。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                _document_state_row(document_id="doc-1", status="REVIEW", ingestion_active=0),
                _document_state_row(document_id="doc-2", status="UPLOADED", ingestion_active=1),
            ],
            [],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    documents = await client.list_documents(limit=10)

    assert [document.status for document in documents] == [FileStatus.REVIEW, FileStatus.UPLOADED]
    # 待機中・実行中の job がある文書は、状態が変わる前でも一覧の取り直しの対象にする。
    assert [document.ingestion_active for document in documents] == [False, True]
    [statement, *_] = _list_selects(pool)
    # SQL は空白を詰めて送る。外側の FROM より前が列の一覧。
    select_list = statement.rsplit(" FROM rag_documents WHERE ", 1)[0]
    assert _RECIPE_STATUS_SQL in select_list
    assert "rag_documents.status ) AS status," in select_list
    assert "FROM rag_ingestion_jobs j" in select_list
    assert "j.status IN ('QUEUED', 'RUNNING')" in select_list


async def test_list_and_count_filter_status_by_first_recipe() -> None:
    """状態の絞り込みと件数は、一覧に出す状態と同じ式で判定する(文書の列だけで判定しない)。"""
    pool = FakeOraclePool(execute_results=[[], [{"count_value": 0}]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    await client.list_documents(status=FileStatus.REVIEW, limit=10)
    await client.count_documents(status=FileStatus.REVIEW)

    statements = _list_selects(pool)
    assert len(statements) == 2
    for statement in statements:
        where_clause = statement.rsplit(" FROM rag_documents WHERE ", 1)[1]
        assert _RECIPE_STATUS_SQL in where_clause
        assert ") = :status" in where_clause
    binds = [call.parameters for call in pool.connection.calls if "= :status" in call.statement]
    assert all(bind["status"] == "REVIEW" for bind in binds)


@pytest.mark.usefixtures("oracle_db")
async def test_list_documents_status_follows_recipe_status_in_oracle() -> None:
    """レシピの job が進めたレシピ1の状態が、一覧の状態・絞り込み・件数に出る(#2012)。"""
    client = OracleClient()
    file_name = f"list-status-{uuid4().hex[:12]}.txt"
    detail = await client.create_document(
        file_name=file_name,
        object_storage_path=f"oci://bucket/{file_name}",
        content_type="text/plain",
    )
    recipe = await client.ensure_default_document_recipe(detail.id)

    # 登録直後はレシピ1も文書も UPLOADED。
    [listed] = await client.list_documents(query=file_name, limit=10)
    assert listed.status == FileStatus.UPLOADED

    # レシピの job は文書の status を変えず、レシピの行だけを進める(#504)。
    await client.update_document_recipe_status(
        recipe_id=str(recipe["recipe_id"]), status=FileStatus.REVIEW
    )
    stored = await client.get_document(detail.id)
    assert stored is not None
    assert stored.status == FileStatus.UPLOADED

    [listed] = await client.list_documents(query=file_name, limit=10)
    assert listed.status == FileStatus.REVIEW
    assert [
        document.id
        for document in await client.list_documents(
            status=FileStatus.REVIEW, query=file_name, limit=10
        )
    ] == [detail.id]
    assert await client.list_documents(status=FileStatus.UPLOADED, query=file_name, limit=10) == []
    assert await client.count_documents(status=FileStatus.REVIEW, query=file_name) == 1
    assert await client.count_documents(status=FileStatus.UPLOADED, query=file_name) == 0
    assert listed.ingestion_active is False

    # 待機中の job がある間は、レシピの状態が変わる前でも ingestion_active が立つ。
    await client.create_ingestion_job(
        IngestionJob(
            id=f"job_list_status_{uuid4().hex[:16]}",
            document_id=detail.id,
            recipe_id=str(recipe["recipe_id"]),
            recipe_revision=int(str(recipe["config_revision"])),
            status=IngestionJobStatus.QUEUED,
            parser_profile="local_text_structure",
            queued_at=datetime.now(UTC),
        )
    )
    [listed] = await client.list_documents(query=file_name, limit=10)
    assert listed.ingestion_active is True
