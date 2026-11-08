"""文書の行の旧列 ``rag_documents.extraction``・``processing_config`` を読み書きしない（#4617）。

抽出の正本はレシピの抽出の版（``rag_document_extractions``）、処理設定の正本はレシピの行
（``rag_document_recipes.processing_config``）にある。API の型・文書の SELECT・状態の更新・
再取込の初期化・削除の対象の収集・レシピ1の作成のどれも、文書の行の 2 列を読まず、消さない。
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.api.routes import documents as documents_route
from app.clients import oracle as oracle_module
from app.clients.oracle import OracleClient, StoredDocument
from app.schemas.document import DocumentDetail, FileStatus
from tests.test_oracle_adapter import (
    FakeOraclePool,
    _oci_settings,
    _oracle_document_row,
    _run_inline,
)

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# SQL の列の一覧・SET 句に文書の行の 2 列が単独で現れる書き方（`extraction,`・`... = NULL`）。
# レシピの行（r.processing_config）・抽出の版の列（extraction_json など）は対象外。
_DOCUMENT_COLUMN_IN_SQL = re.compile(r"(?<![\w.])(extraction|processing_config)\s*(,|=|$)", re.M)


def _normalized(statement: str) -> str:
    return " ".join(statement.split())


def _document_statements(pool: FakeOraclePool) -> list[str]:
    return [
        _normalized(call.statement)
        for call in pool.connection.calls
        if re.search(r"\brag_documents\b", call.statement)
    ]


def test_document_detail_and_stored_document_have_no_extraction() -> None:
    """文書の API の型とテスト補助の行は、文書の行の抽出を持たない。"""
    assert "extraction" not in DocumentDetail.model_fields
    assert "extraction" not in {field.name for field in dataclasses.fields(StoredDocument)}


async def test_get_document_does_not_select_document_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文書詳細の SELECT は文書の行の extraction を読まない。"""
    pool = FakeOraclePool(execute_results=[[_oracle_document_row()], []])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    monkeypatch.setattr(client, "document_language_pack_uses", AsyncMock(return_value={}))

    detail = await client.get_document("doc-1")

    assert detail is not None
    statements = [s for s in _document_statements(pool) if "FROM rag_documents" in s]
    assert statements, "文書の行は読む"
    assert all(not _DOCUMENT_COLUMN_IN_SQL.search(s) for s in statements)


@pytest.mark.parametrize(
    "function",
    [
        oracle_module._select_document,
        OracleClient._find_document_by_content_hash_with_oracle,
        OracleClient._get_document_with_oracle,
        OracleClient._update_document_status_with_oracle,
        OracleClient._reset_document_ingestion_outputs_with_oracle,
    ],
    ids=["select_document", "find_by_hash", "get_document", "update_status", "reset_outputs"],
)
def test_document_sql_does_not_name_retired_columns(function: object) -> None:
    """文書の行を読む・更新する SQL に旧列の名前が無い。"""
    source = inspect.getsource(function)  # type: ignore[arg-type]

    assert not _DOCUMENT_COLUMN_IN_SQL.search(source)


async def test_update_status_ingesting_does_not_clear_document_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INGESTING への更新は状態と誤りだけを書き、文書の行の抽出を消さない。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    document = StoredDocument(
        id="doc-a",
        file_name="a.txt",
        status=FileStatus.UPLOADED,
        uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(oracle_module, "_select_document_state", lambda *_: document)
    monkeypatch.setattr(oracle_module, "_select_document", lambda *_: document)
    monkeypatch.setattr(oracle_module, "_select_document_knowledge_base_refs", lambda *_: [])

    await client.update_document_status("doc-a", FileStatus.INGESTING)

    updates = [s for s in _document_statements(pool) if s.startswith("UPDATE rag_documents")]
    assert updates, "文書の行の状態は更新する"
    assert all("extraction" not in s for s in updates)


async def test_reset_document_ingestion_outputs_does_not_clear_document_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再取込の初期化の UPDATE rag_documents は文書の行の抽出を消さない（読み手が無い）。"""
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

    updates = [s for s in _document_statements(pool) if s.startswith("UPDATE rag_documents")]
    assert updates, "文書の行の状態は戻す"
    assert all("extraction" not in s for s in updates)


def test_document_artifact_paths_does_not_read_document_extraction() -> None:
    """削除の対象の収集は文書の行の抽出を読まない（segment・レシピの行・prefix で集める）。"""
    source = inspect.getsource(documents_route._document_artifact_paths)

    assert "extraction" not in source
    assert not hasattr(documents_route, "_extraction_artifact_path")


def test_default_recipe_seed_starts_with_empty_processing_config() -> None:
    """レシピ1は文書の行の処理設定を写さず、空の処理設定で始める（値は従来どおり ``{}``）。"""
    values = oracle_module._default_document_recipe_values(
        {
            "document_id": "doc-a",
            "status": FileStatus.UPLOADED.value,
            "processing_config": '{"chunk_size": 123}',
        }
    )
    binds = oracle_module.default_document_recipe_merge_binds(values)

    assert "processing_config" not in values
    assert json.loads(str(binds["processing_config"])) == {}


# 文書の行の列を読む・消す書き方（DocumentDetail・StoredDocument の属性、文書の行の別名 d の列）。
_RETIRED_DOCUMENT_COLUMN_PATTERNS = (
    re.compile(r"\bdetail\.extraction\b"),
    re.compile(r"\bdocument\.extraction\b"),
    re.compile(r"\bd\.extraction\b"),
    re.compile(r"\bd\.processing_config\b"),
    re.compile(r"(?<![\w.])extraction\s*=\s*NULL\b"),
)


def test_app_has_no_document_extraction_or_config_reader() -> None:
    """app の下に文書の行の extraction・processing_config を読む・消す書き方が残っていない。"""
    offenders = [
        f"{path.relative_to(APP_DIR)}:{line_no}"
        for path in sorted(APP_DIR.rglob("*.py"))
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if any(pattern.search(line) for pattern in _RETIRED_DOCUMENT_COLUMN_PATTERNS)
    ]

    assert offenders == []
