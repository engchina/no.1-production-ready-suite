"""Run の事実（`AGENT_RUN_FACTS`）を実の Oracle で書き、集計の SQL を実行する結合テスト（#1822）。

CI の実 Oracle の結合テスト（`.github/workflows/oracle-integration.yml`。システムテーブルを
作った後の Oracle AI Database Free）だけで実行する（`AGENT_RUN_ORACLE_INTEGRATION=1`。接続は
`platform/.env` の `PLATFORM_ORACLE_*`）。ほかでは skip する。テストごとに別の業務 Agent の ID で
書き、終わったら消す（並列でも互いの行を数えない）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.features.agent.run_facts import VALIDATION_FAILED, VALIDATION_UNVALIDATED, RunFact
from app.features.agent.run_facts_store import RUN_FACTS_TABLE, OracleRunFactsStore
from app.features.agent.run_signals import SignalKind
from app.features.agent.runtime import FeedbackRating, FeedbackReason, RunFeedback
from app.oracle_connection import connect_platform_oracle

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_RUN_ORACLE_INTEGRATION") != "1",
    reason="実 Oracle の結合テスト（AGENT_RUN_ORACLE_INTEGRATION=1）だけで実行する",
)


@pytest.fixture
def agent_id() -> Iterator[str]:
    value = f"live-{uuid4().hex}"
    yield value
    with connect_platform_oracle() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {RUN_FACTS_TABLE} WHERE AGENT_ID = :agent_id",  # nosec B608
            {"agent_id": value},
        )
        connection.commit()


def _fact(agent_id: str, run_id: str, created_at: datetime, **fields: object) -> RunFact:
    return RunFact.model_validate(
        {
            "run_id": f"{agent_id}-{run_id}",
            "agent_id": agent_id,
            "agent_name": "結合テスト",
            "status": "completed",
            "user_uuid": "aaaaaaaa-0000-0000-0000-000000000001",
            "question": f"{run_id} の質問" + "あ" * 3000,
            "answer": f"{run_id} の回答",
            "created_at": created_at,
            **fields,
        }
    )


def test_facts_are_merged_and_runs_to_review_are_aggregated_in_oracle(agent_id: str) -> None:
    store = OracleRunFactsStore()
    now = datetime.now(UTC)
    bad = RunFeedback(
        rating=FeedbackRating.NOT_HELPFUL,
        reason=FeedbackReason.INCORRECT,
        comment="違う",
        updated_at=now - timedelta(hours=1),
    )
    facts = [
        _fact(agent_id, "tool", now - timedelta(hours=1), tool_failures=2),
        _fact(agent_id, "bad", now - timedelta(hours=2), feedback=bad),
        _fact(
            agent_id,
            "many",
            now - timedelta(hours=3),
            status="failed",
            validation_result=VALIDATION_FAILED,
            answer_outcome="needs_human",
            approvals_rejected=1,
        ),
        _fact(
            agent_id,
            "unvalidated",
            now - timedelta(hours=4),
            validation_result=VALIDATION_UNVALIDATED,
        ),
        _fact(agent_id, "ok", now - timedelta(hours=5), answer_outcome="answered"),
        _fact(
            agent_id, "evaluation", now - timedelta(hours=1), status="failed", source="evaluation"
        ),
        _fact(agent_id, "running", now - timedelta(hours=1), status="running", tool_failures=1),
        _fact(agent_id, "previous", now - timedelta(days=9), status="cancelled"),
    ]
    store.upsert(facts)
    # 書き直し（MERGE の UPDATE）も通る。
    store.upsert([facts[0].model_copy(update={"tool_failures": 3})])

    scope = frozenset({agent_id})
    report = store.signal_report(
        days=7,
        now=now,
        agent_ids=scope,
        agent_id=agent_id,
        kind=None,
        offset=0,
        limit=10,
        agent_names={},
        user_names=lambda uuids: {},
    )

    assert (report.summary.runs, report.summary.flagged, report.total) == (5, 4, 4)
    counts = {item.kind: item.count for item in report.summary.counts}
    assert counts == {
        SignalKind.NOT_HELPFUL: 1,
        SignalKind.VALIDATION_FAILED: 1,
        SignalKind.UNVALIDATED: 1,
        SignalKind.UNANSWERED: 1,
        SignalKind.APPROVAL_REJECTED: 1,
        SignalKind.TOOL_FAILED: 1,
        SignalKind.RUN_FAILED: 1,
    }
    assert (report.previous.runs, report.previous.flagged) == (1, 1)
    assert [item.run_id.removeprefix(f"{agent_id}-") for item in report.items] == [
        "tool",
        "bad",
        "many",
        "unvalidated",
    ]
    tool = report.items[0]
    assert (tool.tool_failures, tool.signals, len(tool.question)) == (
        3,
        [SignalKind.TOOL_FAILED],
        len("tool の質問") + 3000,
    )
    many = report.items[2]
    assert many.signals == [
        SignalKind.VALIDATION_FAILED,
        SignalKind.UNANSWERED,
        SignalKind.APPROVAL_REJECTED,
        SignalKind.RUN_FAILED,
    ]
    assert report.items[1].feedback is not None and report.items[1].feedback.comment == "違う"

    page = store.signal_report(
        days=7,
        now=now,
        agent_ids=scope,
        agent_id=None,
        kind=SignalKind.RUN_FAILED,
        offset=0,
        limit=1,
        agent_names={},
        user_names=lambda uuids: {},
    )
    assert (page.total, [item.run_id for item in page.items]) == (1, [f"{agent_id}-many"])

    # 同じ事実から利用状況・フィードバックの集計の SQL も通る。
    usage = store.usage_report(
        days=7,
        now=now,
        tz=ZoneInfo("Asia/Tokyo"),
        agent_ids=scope,
        agent_names={},
        user_names=lambda uuids: {},
    )
    assert usage.totals.runs == 7
    feedback = store.feedback_report(
        days=7,
        now=now,
        agent_ids=scope,
        agent_id=None,
        rating=FeedbackRating.NOT_HELPFUL,
        reason=None,
        offset=0,
        limit=10,
        agent_names={},
        user_names=lambda uuids: {},
    )
    assert (feedback.summary.not_helpful, feedback.total) == (1, 1)
