"""2 つのプロセスが同じ checkpoint に書いても更新を消さないことを、実の Oracle で確かめる（#1916）。

CI の実 Oracle の結合テスト（`.github/workflows/oracle-integration.yml`。システムテーブルを
作った後の Oracle AI Database Free）だけで実行する（`AGENT_RUN_ORACLE_INTEGRATION=1`。接続は
`platform/.env` の `PLATFORM_ORACLE_*`）。ほかでは skip する。テストごとに別の CHECKPOINT_KEY で
書き、終わったら消す（並列でも互いの行を読まない）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest

from app.features.agent.runtime import (
    RUNTIME_CHECKPOINT_TABLE,
    AgentProfile,
    AgentRuntimeOracleCheckpointRepository,
    AgentRuntimeSnapshot,
)
from app.features.agent.runtime_checkpoint_store import WRITER_ID
from app.oracle_connection import connect_platform_oracle

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_RUN_ORACLE_INTEGRATION") != "1",
    reason="実 Oracle の結合テスト（AGENT_RUN_ORACLE_INTEGRATION=1）だけで実行する",
)


@pytest.fixture
def checkpoint_key() -> Iterator[str]:
    value = f"live-{uuid4().hex}"
    yield value
    with connect_platform_oracle() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {RUNTIME_CHECKPOINT_TABLE} WHERE CHECKPOINT_KEY = :checkpoint_key",  # nosec B608
            {"checkpoint_key": value},
        )
        connection.commit()


def _row(checkpoint_key: str) -> tuple[AgentRuntimeSnapshot, int, str]:
    with connect_platform_oracle() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"SELECT SNAPSHOT_JSON, VERSION_NO, WRITER_ID FROM {RUNTIME_CHECKPOINT_TABLE} "  # nosec B608
            "WHERE CHECKPOINT_KEY = :checkpoint_key",
            {"checkpoint_key": checkpoint_key},
        )
        row = cursor.fetchone()
        assert row is not None
        snapshot = AgentRuntimeSnapshot.model_validate_json(row[0].read())
        return snapshot, int(row[1]), str(row[2])


def test_two_processes_keep_each_others_updates_in_oracle(checkpoint_key: str) -> None:
    # どちらも行が無い時点で読み込んだ 2 つのプロセス（同じ .env の 2 つの backend）。
    first = AgentRuntimeOracleCheckpointRepository(checkpoint_key=checkpoint_key)
    second = AgentRuntimeOracleCheckpointRepository(checkpoint_key=checkpoint_key)

    # 行を作る INSERT が重なる（後の方は ORA-00001 で、行を lock して読み直してまとめ直す）。
    first.create_agent(AgentProfile(id="agent_live_first", name="一つ目"))
    second.create_agent(AgentProfile(id="agent_live_second", name="二つ目"))
    # 版の UPDATE が重なる（first は版 1 のつもりで書くが、保存先は版 2）。
    first.create_agent(AgentProfile(id="agent_live_third", name="三つ目"))

    snapshot, version, writer_id = _row(checkpoint_key)
    agent_ids = {agent.id for agent in snapshot.agents}
    assert {"agent_live_first", "agent_live_second", "agent_live_third"} <= agent_ids
    assert version == 3
    assert writer_id == WRITER_ID
    # 2 つのプロセスのメモリも、相手の更新を取り込んでいる。
    assert "agent_live_second" in {agent.id for agent in first.list_agents()}
    assert "agent_live_first" in {agent.id for agent in second.list_agents()}
