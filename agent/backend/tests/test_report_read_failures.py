"""DB の読み取りの失敗を、メモリの Run の集計に戻さず失敗として返す（#4222）の決定論テスト。

Oracle には接続しない。Run の保存先は偽の表（`checkpoint_support`）、Run の事実の保存先は
`MemoryRunFactsStore`（偽物）で、同じ保存先に書く 2 つのプロセスを再現する。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from checkpoint_support import CheckpointTable
from pytest import MonkeyPatch
from security_support import client

from app.features.agent import run_facts_store
from app.features.agent import runtime as agent_runtime
from app.features.agent.run_facts_store import MemoryRunFactsStore
from app.features.agent.runtime import (
    AgentRuntimeOracleCheckpointRepository,
    FeedbackRating,
    RunCreateRequest,
    RunFeedback,
    RunUsage,
)

ALICE = "aaaaaaaa-0000-0000-0000-000000000001"
REPORTS = ("/api/usage", "/api/feedback", "/api/feedback/signals")


class _FlakyStore(MemoryRunFactsStore):
    """`error` を入れている間、集計の読み取りが失敗する保存先。"""

    error: Exception | None = None

    def _read(self) -> None:
        if self.error is not None:
            raise self.error

    def usage_report(self, **kwargs: Any) -> Any:
        self._read()
        return super().usage_report(**kwargs)

    def feedback_report(self, **kwargs: Any) -> Any:
        self._read()
        return super().feedback_report(**kwargs)

    def signal_report(self, **kwargs: Any) -> Any:
        self._read()
        return super().signal_report(**kwargs)


@dataclass
class _Processes:
    other: AgentRuntimeOracleCheckpointRepository
    store: _FlakyStore


@pytest.fixture
def processes() -> Iterator[_Processes]:
    """同じ保存先を読み込んだ 2 つのプロセス。API のプロセスの repository は 1 つ目。"""
    previous = agent_runtime.loaded_runtime_repository()
    table = CheckpointTable()
    api = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)
    other = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)
    store = _FlakyStore()
    run_facts_store.configure(store)
    agent_runtime.set_runtime_repository(api)
    try:
        yield _Processes(other=other, store=store)
    finally:
        agent_runtime.set_runtime_repository(previous)


def _rated_run_by_the_other_process(processes: _Processes) -> None:
    """別のプロセス（runtime-dispatcher・2 つ目の backend）が Run を実行し、評価が付く。"""
    other = processes.other
    run = other.create_builtin_run(
        RunCreateRequest(agent_id="default", goal="質問"), created_by_user_uuid=ALICE
    )
    assert other.begin_builtin_run(run.id) is not None
    other.record_builtin_usage(
        run.id, RunUsage(model="m", requests=1, input_tokens=3, output_tokens=4, total_tokens=7)
    )
    other.complete_builtin_run(run.id, "回答")
    other.set_run_feedback(run.id, RunFeedback(rating=FeedbackRating.NOT_HELPFUL, user_uuid=ALICE))
    assert run_facts_store.flush()


def _run_count(path: str, data: dict[str, Any]) -> int:
    return int(data["totals"]["runs"] if path == "/api/usage" else data["total"])


@pytest.mark.parametrize("path", REPORTS)
def test_report_read_failure_is_not_answered_from_the_runs_in_memory(
    processes: _Processes, path: str
) -> None:
    """別のプロセスの Run は API のプロセスのメモリに無い。失敗時に 0 件の集計を 200 で返さない。"""
    _rated_run_by_the_other_process(processes)
    # API のプロセスは保存先へ書いていないので、メモリに別のプロセスの Run が無い。
    assert agent_runtime.runtime_repository.list_runs() == []

    saved = client.get(path)
    assert saved.status_code == 200, saved.text
    assert (saved.json()["data"]["source"], _run_count(path, saved.json()["data"])) == (
        "history",
        1,
    )

    processes.store.error = RuntimeError("ORA-00600: internal error code")
    failed = client.get(path)

    assert failed.status_code == 500, failed.text
    body = failed.json()
    assert body["data"] is None
    assert body["error_code"] == "database_read_failed"
    assert body["error_messages"] == ["データを読み取れませんでした。データは失われていません。"]

    # 読み取れるようになれば、同じ要求で全件の集計が返る（画面の再試行）。
    processes.store.error = None
    assert _run_count(path, client.get(path).json()["data"]) == 1


@pytest.mark.parametrize("path", REPORTS)
def test_report_connection_failure_is_storage_unavailable(processes: _Processes, path: str) -> None:
    processes.store.error = RuntimeError("DPY-6005: cannot connect to database")

    response = client.get(path)

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error_code"] == "agent_storage_unavailable"
    # コードだけを添える（接続先・資格情報は出さない）。
    assert body["error_messages"] == [
        "Agent の保存先のデータベースに接続できません（DPY-6005）。データは失われていません。"
    ]
    assert response.headers["Retry-After"] == "15"


@pytest.mark.parametrize("path", REPORTS)
def test_report_pool_wait_timeout_is_a_read_timeout(processes: _Processes, path: str) -> None:
    """pool の待ちの timeout は混雑の可能性。DB に接続できないとは言わない。"""
    processes.store.error = RuntimeError("DPY-4005: timed out waiting for the connection pool")

    response = client.get(path)

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error_code"] == "database_read_timeout"
    assert body["error_messages"] == [
        "データの読み取りがタイムアウトしました。データは失われていません。"
    ]
    assert response.headers["Retry-After"] == "5"


# ---- 監査の一覧 ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/audit/tool-calls", "/api/audit/tool-calls.csv"])
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (RuntimeError("PLATFORM_ORACLE_DSN is not set"), 500, "database_read_failed"),
        (ValueError("ORA-00942: table or view does not exist"), 500, "database_read_failed"),
        (
            RuntimeError("ORA-03113: end-of-file on communication channel"),
            503,
            "agent_storage_unavailable",
        ),
        (RuntimeError("DPY-4005: timed out"), 503, "database_read_timeout"),
    ],
    ids=["runtime-error", "sql-error", "connection", "pool-timeout"],
)
def test_audit_projection_read_failure_is_not_answered_from_the_runs_in_memory(
    monkeypatch: MonkeyPatch, path: str, error: Exception, status: int, code: str
) -> None:
    """監査の記録（projection）を読めないときは、メモリの Run の記録を 200 で返さない。"""

    def failing(**_kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(
        agent_runtime.runtime_repository, "list_tool_call_audit_projection", failing, raising=False
    )

    response = client.get(path)

    assert response.status_code == status, response.text
    assert response.json()["error_code"] == code
    assert response.json()["data"] is None


def test_audit_without_a_projection_reads_the_runs_in_memory() -> None:
    """memory / file の構成（projection が無い）は、今までどおりメモリの Run の記録を返す。"""
    assert not hasattr(agent_runtime.runtime_repository, "list_tool_call_audit_projection")

    response = client.get("/api/audit/tool-calls")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["items"] is not None
