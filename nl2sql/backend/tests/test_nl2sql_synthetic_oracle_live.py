"""明示 opt-in: 一時メタデータ表のみで Oracle CAS/rollback/CLOB 契約を検証する。"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.features.nl2sql.synthetic_models import SyntheticRun, SyntheticTarget
from app.features.nl2sql.synthetic_oracle import capture_session
from app.features.nl2sql.synthetic_store import SyntheticConflict, SyntheticStore
from app.features.settings.system_schema import split_migration_sql
from app.settings import Settings


@pytest.mark.skipif(
    os.environ.get("NL2SQL_SYNTHETIC_LIVE_TEST") != "1", reason="isolated Oracle opt-in"
)
def test_oracle_persistence_roundtrip_and_conflict_rollback() -> None:
    # conftest の APP 上書きを使わず、ローカル接続設定を明示的に読込む。
    from dotenv import dotenv_values

    env = dotenv_values(Path(__file__).parents[1] / ".env")
    connection_settings: dict[str, Any] = {
        key.lower(): value
        for key, value in env.items()
        if key.startswith("ORACLE_") and key.lower() in Settings.model_fields and value is not None
    }
    settings = Settings(_env_file=None, **connection_settings)
    adapter = OracleNl2SqlAdapter(settings)
    prefix = "NL2SQL_T" + uuid4().hex[:8].upper()

    def remap(sql: Any) -> Any:
        return sql.replace("NL2SQL_SYNTHETIC", prefix)

    class Cursor:
        def __init__(self, cursor: Any) -> None:
            self.cursor = cursor

        def __getattr__(self, name: Any) -> Any:
            return getattr(self.cursor, name)

        def execute(self, sql: Any, *args: Any) -> Any:
            return self.cursor.execute(remap(sql), *args)

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_: Any) -> Any:
            self.cursor.close()

    class Connection:
        def __init__(self, connection: Any) -> None:
            self.connection = connection

        def cursor(self) -> Any:
            return Cursor(self.connection.cursor())

        def commit(self) -> Any:
            self.connection.commit()

    @contextmanager
    def connection() -> Iterator[Any]:
        with adapter.connection() as conn:
            yield Connection(conn)

    ddl = Path(__file__).parents[1] / "migrations/019_synthetic_runs.sql"
    created = []
    try:
        with adapter.connection() as conn, conn.cursor() as cur:
            assert capture_session(conn)["sid"] > 0
            for sql in split_migration_sql(ddl.read_text()):
                cur.execute(remap(sql))
                if sql.startswith("CREATE TABLE"):
                    created.append(remap(sql.split()[2]))
        store: Any = SyntheticStore(connection)
        value = SyntheticRun(
            run_id=str(uuid4()),
            actor_id="test",
            context_id="test",
            idempotency_key="key",
            request_hash="hash",
            request={"prompt": "日" * 50000},
            targets=[SyntheticTarget(table_name="APP.FIXTURE_ONLY", requested_rows=1)],
        )
        store.create(value)
        assert store.get(value.run_id).request == value.request
        assert store.by_key("test", "test", "key").run_id == value.run_id
        # 旧版の表 lock が残っていても新しい受付を阻害しない。
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO NL2SQL_SYNTHETIC_LOCKS (CONTEXT_ID,TARGET_NAME,RUN_ID) "
                "VALUES (:ctx,:target,:id)",
                {"ctx": "test", "target": "APP.FIXTURE_ONLY", "id": value.run_id},
            )
            conn.commit()
        independent = value.model_copy(
            update={"run_id": str(uuid4()), "idempotency_key": "another"}
        )
        assert store.create(independent).run_id == independent.run_id
        replay = value.model_copy(update={"run_id": str(uuid4())})
        assert store.create(replay).run_id == value.run_id
        assert store.get(replay.run_id) is None
        conflict = value.model_copy(update={"run_id": str(uuid4()), "request_hash": "changed"})
        with pytest.raises(SyntheticConflict):
            store.create(conflict)
        assert store.get(conflict.run_id) is None
        value.status = "completed"
        assert store.save(value)
        assert not store.save(value)  # compare-and-swap fencing
        assert store.get(independent.run_id).status == "pending"
        assert len(store.list("test")) == 2
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM NL2SQL_SYNTHETIC_LOCKS")
            assert cur.fetchone()[0] == 0
    finally:
        with adapter.connection() as conn, conn.cursor() as cur:
            for name in reversed(created):
                cur.execute(
                    f"DROP TABLE {name} CASCADE CONSTRAINTS PURGE"
                )  # nosec B608 - generated name
