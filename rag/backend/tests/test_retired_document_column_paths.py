"""文書の行の旧列（extraction・processing_config）を読み書きする入口を持たない（#4185）。

抽出と処理設定の正本はレシピの層にあり、文書の行の列で代用する入口を残さない。
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from rag_pipeline_core.chunking import Chunk

from app.clients import oracle as oracle_module
from app.clients.oracle import OracleClient, StoredDocument
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline


@pytest.mark.parametrize(
    "name",
    [
        "list_document_extractions",
        "_list_document_extractions_with_oracle",
        "get_document_processing_config",
        "update_document_processing_config",
        "save_extraction",
        "_save_extraction_with_oracle",
    ],
)
def test_oracle_client_has_no_document_column_entry(name: str) -> None:
    assert not hasattr(OracleClient, name)


def _writer(monkeypatch: pytest.MonkeyPatch) -> tuple[OracleClient, FakeOraclePool]:
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    document = StoredDocument(
        id="doc-a",
        file_name="a.txt",
        status=FileStatus.CHUNKED,
        uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(oracle_module, "_select_document_state", lambda *_: document)
    return client, pool


def _document_updates(pool: FakeOraclePool) -> list[str]:
    return [
        call.statement
        for call in pool.connection.calls
        if "UPDATE RAG_DOCUMENTS" in " ".join(call.statement.upper().split())
    ]


@pytest.mark.parametrize("writer", ["save_index", "save_chunk_preview"])
@pytest.mark.parametrize("chunk_set_id", ["chunk-set-a", None], ids=["chunk_set", "no_chunk_set"])
async def test_index_writers_do_not_write_document_extraction_column(
    monkeypatch: pytest.MonkeyPatch, writer: str, chunk_set_id: str | None
) -> None:
    """chunk の保存は文書の行の extraction を書かない（抽出の正本はレシピの抽出の版）。"""
    client, pool = _writer(monkeypatch)
    extraction = StructuredExtraction(raw_text="合成の本文 A")
    chunks = [Chunk(text="合成の本文 A", index=0, start_offset=0, end_offset=7)]
    arguments: list[Any] = ["doc-a", extraction, chunks]
    if writer == "save_index":
        arguments.append([[0.1, 0.2, 0.3]])

    await getattr(client, writer)(*arguments, chunk_set_id=chunk_set_id)

    assert pool.connection.commits == 1
    assert _document_updates(pool) == []
    assert pool.connection.many_calls, "chunk の行は保存する"


async def test_serving_layer_fingerprints_do_not_read_document_processing_config() -> None:
    """一覧の作り直しの判定は文書の行の processing_config を読まない（判定はレシピの設定）。"""
    pool = FakeOraclePool(execute_results=[[]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    await client.list_serving_artifact_layer_fingerprints(["doc-a"])

    statement = " ".join(pool.connection.calls[0].statement.split())
    assert "d.processing_config" not in statement
    assert "r.processing_config AS recipe_processing_config" in statement
