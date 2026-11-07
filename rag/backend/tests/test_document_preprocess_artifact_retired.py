"""文書の行の旧列 ``rag_documents.preprocess_artifact`` を読み書きする入口を持たない（#4185）。

ファイル準備の成果物（処理後のファイル）の正本はレシピの行（``rag_document_recipes``）にあり、
レシピの経路は文書の行の列を書かない。API の型・SELECT・再取込の初期化・後始末の参照の確認・
レシピ1の作成のどれも、文書の行の列を読まず、レシピの層だけを見る。
"""

from __future__ import annotations

import dataclasses
import inspect
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import anyio
import pytest

from app.clients import oracle as oracle_module
from app.clients.chunk_set_retention_store import _unreferenced_paths
from app.clients.document_cleanup_store import DocumentCleanupStore
from app.clients.oracle import OracleClient, StoredDocument
from app.rag.oracle_schema import oracle_schema_migration_sections
from app.schemas.document import DocumentDetail, FileStatus
from tests.test_oracle_adapter import (
    FakeOraclePool,
    _oci_settings,
    _oracle_document_row,
    _run_inline,
)

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _normalized(statement: str) -> str:
    return " ".join(statement.split())


def test_document_detail_and_stored_document_have_no_preprocess_artifact() -> None:
    """文書の API の型とテスト補助の行は、文書の行の処理後のファイルを持たない。"""
    assert "preprocess_artifact" not in DocumentDetail.model_fields
    assert "preprocess_artifact" not in {field.name for field in dataclasses.fields(StoredDocument)}


async def test_get_document_does_not_select_document_preprocess_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文書詳細の SELECT は文書の行の preprocess_artifact を読まない。"""
    pool = FakeOraclePool(execute_results=[[_oracle_document_row(status="PREPROCESSED")], []])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    monkeypatch.setattr(client, "document_language_pack_uses", AsyncMock(return_value={}))

    detail = await client.get_document("doc-1")

    assert detail is not None
    document_selects = [
        call.statement for call in pool.connection.calls if "FROM rag_documents" in call.statement
    ]
    assert document_selects, "文書の行は読む"
    assert all("preprocess_artifact" not in statement for statement in document_selects)


def test_select_document_does_not_read_document_preprocess_artifact() -> None:
    """transaction の中の文書の読み取りも文書の行の preprocess_artifact を読まない。"""
    source = inspect.getsource(oracle_module._select_document)

    assert "preprocess_artifact" not in source


def test_reset_document_ingestion_outputs_has_no_preprocess_artifact_option() -> None:
    """再取込の初期化は文書の行の preprocess_artifact を消す引数を持たない（読み手が無い）。"""
    for function in (
        OracleClient.reset_document_ingestion_outputs,
        OracleClient._reset_document_ingestion_outputs_with_oracle,
    ):
        assert "clear_preprocess_artifact" not in inspect.signature(function).parameters
    source = inspect.getsource(OracleClient._reset_document_ingestion_outputs_with_oracle)
    assert "preprocess_artifact" not in source


async def test_reset_document_ingestion_outputs_update_does_not_touch_preprocess_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """初期化の UPDATE rag_documents は preprocess_artifact を書かない。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    document = StoredDocument(
        id="doc-a",
        file_name="a.txt",
        status=FileStatus.INDEXED,
        uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(oracle_module, "_select_document_state", lambda *_: document)
    monkeypatch.setattr(oracle_module, "_lock_document_with_served_output", lambda *_: False)
    monkeypatch.setattr(oracle_module, "_select_graph_entity_ids_for_document", lambda *_: [])
    monkeypatch.setattr(oracle_module, "_delete_graph_rows_for_document", lambda *_, **__: None)
    monkeypatch.setattr(oracle_module, "_document_detail_in_transaction", lambda *_: object())

    await client.reset_document_ingestion_outputs("doc-a")

    updates = [
        _normalized(call.statement)
        for call in pool.connection.calls
        if "UPDATE rag_documents" in _normalized(call.statement)
    ]
    assert updates, "文書の行の状態は戻す"
    assert all("preprocess_artifact" not in statement for statement in updates)


class _CapturingOracle:
    def __init__(self) -> None:
        self.fetches: list[tuple[str, dict[str, Any]]] = []

    async def _fetch_all(self, statement: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
        self.fetches.append((statement, binds))
        return []


def test_cleanup_referenced_uris_reads_recipe_artifact_not_document_column() -> None:
    """後始末の参照の確認は、処理後のファイルをレシピの行で守り、文書の行の列を読まない。"""
    oracle = _CapturingOracle()
    store = DocumentCleanupStore(oracle)  # type: ignore[arg-type]

    anyio.run(store.referenced_uris, ["local://prepared/a.pdf"])

    statement = _normalized(oracle.fetches[-1][0])
    parts = [part.strip() for part in statement.split("UNION")]
    document_parts = [part for part in parts if re.search(r"FROM rag_documents\b", part)]
    recipe_parts = [part for part in parts if "FROM rag_document_recipes" in part]
    assert document_parts and all("preprocess_artifact" not in part for part in document_parts)
    assert recipe_parts and all("preprocess_artifact" in part for part in recipe_parts)


def test_retention_unreferenced_paths_reads_recipe_artifact_not_document_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """古い版の後始末の参照の確認も、レシピの行だけで処理後のファイルを守る。"""
    statements: list[str] = []

    def fake_fetch_one(
        _connection: object, statement: str, _binds: dict[str, object]
    ) -> dict[str, object] | None:
        statements.append(_normalized(statement))
        return None

    monkeypatch.setattr("app.clients.chunk_set_retention_store._fetch_one", fake_fetch_one)

    _unreferenced_paths(
        object(),  # type: ignore[arg-type]
        "d",
        ["oci://b/n/artifacts/canonical/d/x.pdf"],
        prefix="artifacts/canonical/d/",
    )

    [statement] = statements
    assert "r.preprocess_artifact" in statement
    assert "d.preprocess_artifact" not in statement
    assert "d.object_storage_path = :path" in statement


def test_default_recipe_seed_does_not_copy_document_preprocess_artifact() -> None:
    """レシピ1の作成は文書の行の処理後のファイルを写さない（新しいレシピは未準備で始まる）。"""
    values = oracle_module._default_document_recipe_values(
        {
            "document_id": "doc-a",
            "status": FileStatus.UPLOADED.value,
            "preprocess_artifact": '{"object_storage_path": "local://prepared/a.pdf"}',
        }
    )

    assert "preprocess_artifact" not in values
    assert "preprocess_artifact" not in oracle_module.DEFAULT_DOCUMENT_RECIPE_MERGE_SQL
    assert "preprocess_artifact" not in oracle_module.DEFAULT_DOCUMENT_RECIPE_MERGE_JSON_BINDS


# 文書の行の列を読む書き方（DocumentDetail・StoredDocument の属性、文書の行の別名 d の列、
# 初期化の引数）。レシピの行（r.preprocess_artifact・recipe["preprocess_artifact"]）は対象外。
_RETIRED_DOCUMENT_COLUMN_PATTERNS = (
    re.compile(r"\bdetail\.preprocess_artifact\b"),
    re.compile(r"\bdocument\.preprocess_artifact\b"),
    re.compile(r"\bd\.preprocess_artifact\b"),
    re.compile(r"\bclear_preprocess_artifact\b"),
)


def test_app_has_no_document_preprocess_artifact_reader() -> None:
    """app の下に文書の行の preprocess_artifact を読む・消す書き方が残っていない。"""
    offenders = [
        f"{path.relative_to(APP_DIR)}:{line_no}"
        for path in sorted(APP_DIR.rglob("*.py"))
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if any(pattern.search(line) for pattern in _RETIRED_DOCUMENT_COLUMN_PATTERNS)
    ]

    assert offenders == []


def test_document_base_schema_has_no_preprocess_artifact_column() -> None:
    """新しい DB の rag_documents は旧列 preprocess_artifact を持たない（レシピの行は持つ）。"""
    assert "preprocess_artifact" not in oracle_module.oracle_document_schema_sql()
    assert "preprocess_artifact" in oracle_module.oracle_document_recipe_schema_sql()


def test_drop_migration_removes_document_column_only_when_present() -> None:
    """既存の DB の旧列は destructive な migration で消す。列が無ければ何もしない。"""
    [section] = [
        section
        for section in oracle_schema_migration_sections()
        if section.name == "20261107_4185_drop_document_preprocess_artifact"
    ]
    sql = _normalized(section.sql)

    assert section.destructive
    assert section.table_name == "rag_documents"
    assert "WHERE table_name = 'RAG_DOCUMENTS' AND column_name = 'PREPROCESS_ARTIFACT'" in sql
    assert "EXECUTE IMMEDIATE 'ALTER TABLE rag_documents DROP COLUMN preprocess_artifact'" in sql
    assert "rag_document_recipes" not in sql.lower()
