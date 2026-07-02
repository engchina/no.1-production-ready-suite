"""実 Oracle 26ai を使う統合テスト用のヘルパー。

`backend/.env` の接続情報で実 DB に接続し、RAG スキーマの存在保証と
テストが作成した行のクリーンアップを提供する。DB が未到達の環境では
`db_available()` が False を返し、依存テストは skip できる。
"""

from __future__ import annotations

import importlib
from contextlib import suppress
from functools import lru_cache
from typing import Any

from app.clients.oracle import _init_oracle_client, _oracle_connect_kwargs
from app.config import Settings
from app.rag.oracle_schema import (
    oracle_schema_migration_sql,
    oracle_schema_sql,
    split_sql_statements,
)

# .env を読み込んだ実接続設定（テスト中に singleton が書き換わっても影響を受けない）
_REAL_SETTINGS = Settings()

# 既存（テスト開始前から存在する）ドキュメント ID。実運用データを誤って消さない基準。
_BASELINE_DOCUMENT_IDS: set[str] = set()
_BASELINE_KNOWLEDGE_BASE_IDS: set[str] = set()

# 冪等適用で無視できる Oracle エラーコード（既に存在 / 列が既に索引済み）。
_IDEMPOTENT_DDL_CODES = {955, 1408}


def real_oracle_connection_kwargs() -> dict[str, Any]:
    """実 Oracle へ直接 connect するための kwargs を返す。"""
    return _oracle_connect_kwargs(_REAL_SETTINGS)


def apply_real_oracle_settings(settings: Settings) -> None:
    """テスト用 singleton に実 Oracle 接続設定を反映する。"""
    settings.oracle_user = _REAL_SETTINGS.oracle_user
    settings.oracle_password = _REAL_SETTINGS.oracle_password
    settings.oracle_dsn = _REAL_SETTINGS.oracle_dsn
    settings.oracle_client_lib_dir = _REAL_SETTINGS.oracle_client_lib_dir
    settings.oracle_wallet_dir = _REAL_SETTINGS.oracle_wallet_dir
    settings.oracle_wallet_password = _REAL_SETTINGS.oracle_wallet_password


def _connect() -> Any:
    oracledb = importlib.import_module("oracledb")
    # 実 DB は thick client(instant client)を使う。thin 接続を先に作るとアプリ側の
    # thick 初期化が DPY-2019 で失敗するため、connect 前に thick を初期化する(冪等)。
    _init_oracle_client(oracledb, _REAL_SETTINGS)
    return oracledb.connect(**real_oracle_connection_kwargs())


@lru_cache(maxsize=1)
def db_available() -> bool:
    """実 Oracle に接続できるかを 1 回だけ判定する。"""
    if not _REAL_SETTINGS.oracle_dsn.strip():
        return False
    try:
        connection = _connect()
    except Exception:
        return False
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1 FROM dual")
        cursor.fetchone()
        return True
    except Exception:
        return False
    finally:
        connection.close()


def _clean_ddl_statement(statement: str) -> str:
    """section コメント行を除いた実行用 DDL を返す。"""
    lines = [line for line in statement.splitlines() if not line.strip().startswith("--")]
    return "\n".join(lines).strip()


@lru_cache(maxsize=1)
def ensure_schema() -> None:
    """RAG スキーマ（rag_documents / rag_chunks など）を冪等に作成する。"""
    oracledb = importlib.import_module("oracledb")
    connection = _connect()
    try:
        cursor = connection.cursor()
        for statement in split_sql_statements(oracle_schema_sql()):
            sql = _clean_ddl_statement(statement)
            if not sql:
                continue
            try:
                cursor.execute(sql)
            except oracledb.DatabaseError as exc:  # noqa: PERF203
                code = exc.args[0].code if exc.args else None
                if code in _IDEMPOTENT_DDL_CODES:
                    continue
                # rag_search_audit は予約語 mode の既知バグ(ORA-03050)で作成できず、
                # その索引も ORA-00942 になる。ランタイムは当該テーブルへ書き込まない
                # ためテストには影響しない。詳細は spawn 済みフォローアップ参照。
                if code in (3050, 942):
                    continue
                # 既存 schema では新列を migration で補うため、当該索引だけ先に失敗し得る。
                if code == 904 and "RESULT_SHA256" in sql.upper():
                    continue
                if code == 904 and "RAG_CHUNK_SETS_EXTRACTION_IDX" in sql.upper():
                    continue
                if code == 904 and "RAG_CHUNK_SETS_SERVING_IDX" in sql.upper():
                    continue
                if code == 904 and "RAG_DOC_EXT_STATUS_IDX" in sql.upper():
                    continue
                if code == 904 and "RAG_ARTIFACT_LAYERS_PARENT_IDX" in sql.upper():
                    continue
                if (
                    code == 904
                    and "RAG_CHUNKS_TEXT_IDX" in sql.upper()
                    and "SEARCH_TEXT" in sql.upper()
                ):
                    continue
                if code == 904 and "RAG_INGESTION_AUDIT_PARSER_CREATED_IDX" in sql.upper():
                    continue
                if (
                    code == 904
                    and "RAG_AGENT_MEMORIES" in sql.upper()
                    and "ROLE_ID_HASH" in sql.upper()
                ):
                    continue
                raise
        for statement in split_sql_statements(oracle_schema_migration_sql()):
            sql = _clean_ddl_statement(statement)
            if not sql:
                continue
            try:
                cursor.execute(sql)
            except oracledb.DatabaseError as exc:  # noqa: PERF203
                code = exc.args[0].code if exc.args else None
                if code in _IDEMPOTENT_DDL_CODES:
                    continue
                if code in (3050, 942):
                    continue
                raise
        connection.commit()
    finally:
        connection.close()


def capture_baseline() -> None:
    """テスト開始前に存在する document_id を基準として記録する。"""
    connection = _connect()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT document_id FROM rag_documents")
        _BASELINE_DOCUMENT_IDS.clear()
        _BASELINE_DOCUMENT_IDS.update(row[0] for row in cursor.fetchall())
        try:
            cursor.execute("SELECT knowledge_base_id FROM rag_knowledge_bases")
        except Exception:
            _BASELINE_KNOWLEDGE_BASE_IDS.clear()
        else:
            _BASELINE_KNOWLEDGE_BASE_IDS.clear()
            _BASELINE_KNOWLEDGE_BASE_IDS.update(row[0] for row in cursor.fetchall())
    finally:
        connection.close()


def cleanup_to_baseline() -> None:
    """テストが作成した rag_documents / rag_chunks 行だけを削除する。

    テスト開始前から存在した baseline ドキュメント（将来の実運用データ）は
    残し、テスト中に作成された行のみ削除して分離を担保する。
    """
    connection = _connect()
    try:
        cursor = connection.cursor()
        kb_baseline = tuple(_BASELINE_KNOWLEDGE_BASE_IDS)
        baseline = tuple(_BASELINE_DOCUMENT_IDS)
        if kb_baseline:
            kb_placeholders = ", ".join(f":kb{i}" for i in range(len(kb_baseline)))
            kb_params = {f"kb{i}": value for i, value in enumerate(kb_baseline)}
            cursor.execute(
                f"""
                DELETE FROM rag_document_knowledge_bases
                WHERE knowledge_base_id NOT IN ({kb_placeholders})
                """,
                kb_params,
            )
        else:
            with suppress(Exception):
                cursor.execute("DELETE FROM rag_document_knowledge_bases")
        with suppress(Exception):
            if baseline:
                placeholders = ", ".join(f":b{i}" for i in range(len(baseline)))
                params = {f"b{i}": value for i, value in enumerate(baseline)}
                cursor.execute(
                    f"DELETE FROM rag_ingestion_segments WHERE document_id NOT IN ({placeholders})",
                    params,
                )
            else:
                cursor.execute("DELETE FROM rag_ingestion_segments")
        if baseline:
            placeholders = ", ".join(f":b{i}" for i in range(len(baseline)))
            params = {f"b{i}": value for i, value in enumerate(baseline)}
            cursor.execute(
                f"DELETE FROM rag_chunks WHERE document_id NOT IN ({placeholders})",
                params,
            )
            cursor.execute(
                f"DELETE FROM rag_documents WHERE document_id NOT IN ({placeholders})",
                params,
            )
        else:
            cursor.execute("DELETE FROM rag_chunks")
            cursor.execute("DELETE FROM rag_documents")
        if kb_baseline:
            kb_placeholders = ", ".join(f":kb{i}" for i in range(len(kb_baseline)))
            kb_params = {f"kb{i}": value for i, value in enumerate(kb_baseline)}
            cursor.execute(
                f"""
                DELETE FROM rag_knowledge_bases
                WHERE knowledge_base_id NOT IN ({kb_placeholders})
                """,
                kb_params,
            )
        else:
            with suppress(Exception):
                cursor.execute("DELETE FROM rag_knowledge_bases")
        connection.commit()
    finally:
        connection.close()
