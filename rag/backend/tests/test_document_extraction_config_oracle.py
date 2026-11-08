"""実 Oracle で、文書の行の旧列（extraction・processing_config）を使わずに文書を扱う（#4617）。

文書の登録（レシピ1の作成）・詳細・状態の更新・再取込の初期化の SQL が、文書の行の旧列を
名指ししないで動くこと、レシピ1が空の処理設定で始まることを確かめる。
"""

import hashlib

import pytest

from app.clients.oracle import OracleClient
from app.schemas.document import FileStatus
from tests.support import test_audit_request_context as audit_request_context


@pytest.mark.usefixtures("oracle_db")
async def test_document_lifecycle_does_not_use_document_extraction_or_config() -> None:
    with audit_request_context():
        client = OracleClient()
        document = await client.create_document(
            file_name="synthetic.txt",
            object_storage_path="local://synthetic/legacy-columns.txt",
            content_type="text/plain",
            content_sha256=hashlib.sha256(b"synthetic legacy columns").hexdigest(),
        )

        # レシピ1は文書の行から処理設定を写さず、空の処理設定（{}）で始まる。
        rows = await client._fetch_all(
            """
            SELECT JSON_SERIALIZE(processing_config RETURNING VARCHAR2(4000)) AS config
            FROM rag_document_recipes
            WHERE document_id = :document_id AND slot_no = 1
            """,
            {"document_id": document.id},
        )
        assert [str(row["config"]).replace(" ", "") for row in rows] == ["{}"]

        detail = await client.get_document(document.id)
        assert detail is not None
        assert "extraction" not in detail.model_dump()

        ingesting = await client.update_document_status(document.id, FileStatus.INGESTING)
        assert ingesting.status == FileStatus.INGESTING

        reset = await client.reset_document_ingestion_outputs(document.id)
        assert reset.status == FileStatus.UPLOADED
        assert reset.indexed_at is None
        assert "extraction" not in reset.model_dump()
