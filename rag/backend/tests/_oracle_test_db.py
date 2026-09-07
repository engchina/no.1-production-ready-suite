"""実 Oracle 26ai を使う統合テスト用のヘルパー。

`backend/.env` の接続情報で実 DB に接続し、RAG スキーマの存在保証と
テストが作成した行のクリーンアップを提供する。DB が未到達の環境では
`db_available()` が False を返し、依存テストは skip できる。
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from functools import lru_cache
from typing import Any

from app.clients.oracle import _init_oracle_client, _oracle_connect_kwargs
from app.config import Settings
from app.rag.system_schema import SystemSchemaManager

# .env を読み込んだ実接続設定（テスト中に singleton が書き換わっても影響を受けない）
_REAL_SETTINGS = Settings()

# 既存（テスト開始前から存在する）ドキュメント ID。実運用データを誤って消さない基準。
_BASELINE_DOCUMENT_IDS: set[str] = set()
_BASELINE_KNOWLEDGE_BASE_IDS: set[str] = set()


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


@contextmanager
def _schema_connection() -> Iterator[Any]:
    connection = _connect()
    try:
        yield connection
    finally:
        connection.close()


@lru_cache(maxsize=1)
def ensure_schema() -> None:
    """RAG スキーマ（rag_documents / rag_chunks など）を冪等に作成する。"""
    result = SystemSchemaManager(_schema_connection).initialize()
    if result["status"] != "ready":
        raise RuntimeError("RAG system schema の初期化後 status が ready ではありません。")


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
