"""実 Oracle AI Database で、DB 管理チャットの会話の履歴が表（migration 024）に残るか（#2319）。

通常 CI では実行しない。`NL2SQL_RUN_ORACLE_INTEGRATION=1` の明示指定時だけ、migration 024 の DDL を
一意な名前に置き換えた一時の表に作り（system schema の台帳・本物の会話の表には触れない）、会話と
ターンを保存して読み戻し、finally で一時の表を消す。LLM は決定論のスタブ（SQL の実行は実 Oracle）。
資格情報は出力しない。
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from dotenv import dotenv_values

from app.features.nl2sql.db_admin_chat_store import DbAdminChatStore
from app.features.nl2sql.models import (
    DbAdminChatGenerateRequest,
    DbAdminChatPlan,
    DbAdminChatTurn,
    DbAdminChatTurnExecuteRequest,
    SchemaCatalog,
)
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.features.settings.system_schema import split_migration_sql
from app.security.request_actor import actor_scope
from app.settings import get_settings

pytestmark = pytest.mark.skipif(
    os.getenv("NL2SQL_RUN_ORACLE_INTEGRATION") != "1",
    reason="NL2SQL_RUN_ORACLE_INTEGRATION=1 の明示指定が必要です。",
)

_MIGRATION = Path(__file__).parents[1] / "migrations" / "024_db_admin_chat_conversations.sql"
_NAME = "NL2SQL_DB_ADMIN_CHAT"


@pytest.fixture
def live_settings() -> Iterator[Any]:
    """conftest が PLATFORM_ORACLE_USER=APP を強制するため、共通 .env の接続ユーザーへ戻す。"""
    settings = get_settings()
    env_user = str(
        dotenv_values(Path(__file__).resolve().parents[3] / "platform" / ".env").get(
            "PLATFORM_ORACLE_USER"
        )
        or os.environ.get("PLATFORM_ORACLE_USER", "")
    )
    original_user = settings.oracle_user
    if env_user:
        settings.oracle_user = env_user
    try:
        yield settings
    finally:
        settings.oracle_user = original_user


class _RemapCursor:
    def __init__(self, cursor: Any, prefix: str) -> None:
        self.cursor = cursor
        self.prefix = prefix

    def __getattr__(self, name: str) -> Any:
        return getattr(self.cursor, name)

    def execute(self, sql: str, *args: Any) -> Any:
        return self.cursor.execute(sql.replace(_NAME, self.prefix), *args)

    def __enter__(self) -> _RemapCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        self.cursor.close()


class _RemapConnection:
    def __init__(self, connection: Any, prefix: str) -> None:
        self.connection = connection
        self.prefix = prefix

    def cursor(self) -> _RemapCursor:
        return _RemapCursor(self.connection.cursor(), self.prefix)

    def commit(self) -> None:
        self.connection.commit()


@pytest.fixture
def chat_store(live_settings: Any) -> Iterator[tuple[OracleNl2SqlAdapter, DbAdminChatStore]]:
    """migration 024 の DDL を一意な名前の一時の表に作った store（終わったら消す）。"""

    adapter = OracleNl2SqlAdapter(live_settings)
    prefix = "T2319_" + uuid.uuid4().hex[:8].upper()

    @contextmanager
    def connection() -> Iterator[Any]:
        with adapter.connection() as conn:
            yield _RemapConnection(conn, prefix)

    with adapter.connection() as conn, conn.cursor() as cursor:
        for statement in split_migration_sql(_MIGRATION.read_text(encoding="utf-8")):
            cursor.execute(statement.replace(_NAME, prefix))
    try:
        yield adapter, DbAdminChatStore(connection)
    finally:
        with adapter.connection() as conn, conn.cursor() as cursor:
            for table in (f"{prefix}_TURNS", f"{prefix}S"):
                with contextlib.suppress(Exception):
                    cursor.execute(f"DROP TABLE {table} CASCADE CONSTRAINTS PURGE")  # nosec B608


def _turn(turn_id: str, question: str, sql: str) -> DbAdminChatTurn:
    return DbAdminChatTurn(
        turn_id=turn_id,
        question=question,
        created_at="2026-10-18T00:00:00+00:00",
        generated_at="2026-10-18T00:00:01+00:00",
        plan=DbAdminChatPlan(sql=sql),
    )


def test_store_round_trips_conversations_and_turns_on_oracle(
    chat_store: tuple[OracleNl2SqlAdapter, DbAdminChatStore],
) -> None:
    _adapter, store = chat_store
    first = store.create_conversation("c-1", owner_user_uuid="u-1", title="会話 1")
    store.create_conversation("c-2", owner_user_uuid="u-2", title="会話 2")
    store.create_conversation("c-3", owner_user_uuid="u-1", title="会話 3")
    assert first.actor_user_uuid == "u-1" and first.created_at

    # SQL の全文は 32KB を超えることがあるので CLOB で渡す。
    long_sql = "SELECT 1 FROM DUAL\n-- " + "あ" * 40000
    store.append_turn("c-1", _turn("t-1", "最初の質問", long_sql))
    store.append_turn(
        "c-1",
        _turn("t-2", "続き", "SELECT 2 FROM DUAL").model_copy(update={"sender_user_uuid": "a"}),
    )
    turns = store.list_turns("c-1")
    assert [turn.turn_id for turn in turns] == ["t-1", "t-2"]
    assert turns[0].plan.sql == long_sql
    assert [turn.sender_user_uuid for turn in turns] == [None, "a"]

    store.update_turn("c-1", turns[1].model_copy(update={"revision": 1, "corrected": True}))
    updated = store.get_turn("c-1", "t-2")
    assert updated is not None and updated.revision == 1 and updated.corrected is True
    assert updated.sender_user_uuid == "a"
    with pytest.raises(LookupError):
        store.update_turn("c-1", _turn("missing", "x", "SELECT 3 FROM DUAL"))

    mine, total = store.list_conversations(owner_user_uuid="u-1", offset=0, limit=1)
    assert total == 2 and [item.id for item in mine] == ["c-3"]
    rest, _ = store.list_conversations(owner_user_uuid="u-1", offset=1, limit=1)
    assert [item.id for item in rest] == ["c-1"]
    everyone, total_all = store.list_conversations(owner_user_uuid=None, offset=0, limit=10)
    assert total_all == 3 and [item.id for item in everyone] == ["c-3", "c-2", "c-1"]

    renamed = store.rename_conversation("c-1", "新しい名前")
    assert renamed is not None and renamed.title == "新しい名前"
    # 会話を消すとターンも消える（ON DELETE CASCADE）。
    assert store.delete_conversation("c-1") is True
    assert store.get_conversation("c-1") is None
    assert store.list_turns("c-1") == []
    assert store.delete_conversation("c-1") is False


class _FakeEnterpriseAi:
    def __init__(self, sql: str) -> None:
        self.sql = sql

    def is_configured(self) -> bool:
        return True

    def model_id(self) -> str:
        return "test-text-model"

    def generate(self, **_kwargs: Any) -> str:
        return json.dumps({"sql": self.sql, "explanation": "説明"}, ensure_ascii=False)


class _LiveService(Nl2SqlService):
    def __init__(self, adapter: OracleNl2SqlAdapter, store: DbAdminChatStore) -> None:
        super().__init__(store=MemoryNl2SqlStore())
        self._oracle_adapter = adapter
        self._db_admin_chat_store = store
        self._enterprise_ai_client = _FakeEnterpriseAi("SELECT 1 AS N FROM DUAL")

    def _use_oracle_runtime(self) -> bool:
        return True

    def get_catalog(self, *_args: Any, **_kwargs: Any) -> SchemaCatalog:
        return SchemaCatalog(refreshed_at="2026-10-18T00:00:00+00:00", tables=[])


def test_sent_turn_and_execution_summary_are_saved_on_oracle(
    chat_store: tuple[OracleNl2SqlAdapter, DbAdminChatStore],
) -> None:
    adapter, store = chat_store
    service = _LiveService(adapter, store)
    turn_id = uuid.uuid4()
    with actor_scope("t2319-actor", is_system_admin=True):
        sent = service.send_db_admin_chat_question(
            DbAdminChatGenerateRequest(question="DUAL を読む", client_turn_id=turn_id),
            actor="t2319-actor",
            all_users=True,
        )
        assert sent is not None
        executed = service.execute_db_admin_chat_turn(
            DbAdminChatTurnExecuteRequest(
                conversation_id=sent.conversation.id, turn_id=sent.turn.turn_id
            ),
            actor="t2319-actor",
            all_users=True,
        )
    assert executed.executed is True
    assert executed.execution_context == "admin_control_plane"
    data = service.get_db_admin_chat(sent.conversation.id, actor="t2319-actor", all_users=True)
    [turn] = data.turns
    assert turn.turn_id == str(turn_id)
    assert turn.plan.read_only is True
    assert turn.last_execution is not None
    assert turn.last_execution.succeeded is True
    assert turn.last_execution.row_count == 1
    # 結果の行は保存しない。
    assert turn.last_execution.result.select_result is None
