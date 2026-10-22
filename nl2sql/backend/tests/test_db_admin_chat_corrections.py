"""失敗を繰り返した管理 SQL の分析（モデルと DB は決定論スタブ）。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException
from test_nl2sql_db_admin_chat import (
    _catalog_service,
    _FakeAdapter,
    _FakeEnterpriseAi,
    _saved_analysis_turn,
)
from test_nl2sql_db_admin_chat_history import _http, _principal

from app.features.nl2sql import router
from app.features.nl2sql.db_admin_chat_corrections import (
    ATTEMPT_LIMIT,
    record_failures,
    sql_identity,
)
from app.features.nl2sql.models import (
    DbAdminChatAnalyzeRequest,
    DbAdminChatStatementLastExecution,
    DbAdminChatTurnExecuteRequest,
)

OWNER: dict[str, Any] = {"actor": "chat-user", "all_users": False}


def test_second_analysis_reads_saved_failed_sql_and_error() -> None:
    ai = _FakeEnterpriseAi(
        [
            {"analysis": "別の表を確認します。", "corrected_sql": "DELETE FROM T2"},
            {"analysis": "参照を直します。", "corrected_sql": "DELETE FROM T3"},
        ]
    )
    service = _catalog_service(_FakeAdapter(script_fail_at=1), ai)
    ids = _saved_analysis_turn(service, "DELETE FROM T1")
    first = service.analyze_db_admin_chat_failure(
        *ids, DbAdminChatAnalyzeRequest(statement_index=1), **OWNER
    )
    assert first.corrected_plan is not None
    assert first.previous_attempts == []
    service.replace_db_admin_chat_turn_sql(
        *ids, first.corrected_plan.sql, statement_index=1, **OWNER
    )
    service.execute_db_admin_chat_turn(
        DbAdminChatTurnExecuteRequest(
            conversation_id=ids[0],
            turn_id=ids[1],
            mode="all",
            confirmation="ADMIN_EXECUTE",
        ),
        **OWNER,
    )
    second = service.analyze_db_admin_chat_failure(
        *ids, DbAdminChatAnalyzeRequest(statement_index=1), **OWNER
    )
    context = ai.calls[-1]["context"]
    history = json.loads(context.split("previous_attempts:\n", 1)[1])
    assert [(a["revision"], a["sql"], a["error_code"]) for a in history] == [
        (0, "DELETE FROM T1", "ORA-00942")
    ]
    assert "DELETE FROM T2" in context and "ORA-01722" in context
    assert len(second.previous_attempts) == 1
    saved = service._db_admin_chat_store.get_turn(*ids)
    assert saved is not None
    assert [a.revision for a in saved.correction_attempts] == [0, 1]
    assert "select_result" not in saved.correction_attempts[0].model_dump()


@pytest.mark.parametrize("alternative", ["DELETE FROM T3", " /* same */ delete  FROM t1 ;"])
def test_repeated_candidate_is_retried_once_then_suppressed(alternative: str) -> None:
    ai = _FakeEnterpriseAi(
        [
            {"analysis": "修正", "corrected_sql": "delete from t1; -- comment"},
            {"analysis": "再分析", "corrected_sql": alternative},
        ]
    )
    service = _catalog_service(_FakeAdapter(), ai)
    ids = _saved_analysis_turn(service, "DELETE FROM T1")
    result = service.analyze_db_admin_chat_failure(
        *ids, DbAdminChatAnalyzeRequest(statement_index=1), **OWNER
    )
    assert len(ai.calls) == 2
    assert "correction_rejected" in ai.calls[1]["context"]
    if alternative == "DELETE FROM T3":
        assert result.corrected_plan is not None and not result.warnings
    else:
        assert result.corrected_plan is None and result.warnings


def test_previous_revision_candidate_is_also_suppressed() -> None:
    ai = _FakeEnterpriseAi([{"analysis": "修正", "corrected_sql": "DELETE FROM T1"}] * 2)
    service = _catalog_service(_FakeAdapter(), ai)
    ids = _saved_analysis_turn(service, "DELETE FROM T1")
    updated = service.replace_db_admin_chat_turn_sql(
        *ids, "DELETE FROM T2", statement_index=1, **OWNER
    )
    updated.statement_executions = [
        DbAdminChatStatementLastExecution(
            index=1, status="failed", outcome="ORA-00942", executed_at="2026-10-22"
        )
    ]
    service._db_admin_chat_store.update_turn(ids[0], updated)
    result = service.analyze_db_admin_chat_failure(
        *ids, DbAdminChatAnalyzeRequest(statement_index=1), **OWNER
    )
    assert result.corrected_plan is None and len(ai.calls) == 2


def test_normalization_preserves_literal_and_quoted_identifier_contents() -> None:
    assert sql_identity("SELECT 'A /*x*/ B' FROM t1; -- end") == sql_identity(
        "select 'A /*x*/ B' from T1"
    )
    assert sql_identity("SELECT 'A' FROM T1") != sql_identity("SELECT 'a' FROM T1")
    assert sql_identity('SELECT "Name" FROM T1') != sql_identity('SELECT "NAME" FROM T1')
    assert sql_identity("SELECT 'a  b' FROM T1") != sql_identity("SELECT 'a b' FROM T1")


def test_histories_are_bounded_per_statement_and_do_not_include_success() -> None:
    service = _catalog_service(_FakeAdapter(), _FakeEnterpriseAi([]))
    ids = _saved_analysis_turn(service, "DELETE FROM T1; DELETE FROM T2")
    turn = service._db_admin_chat_store.get_turn(*ids)
    assert turn is not None
    for revision in range(6):
        turn.revision = revision
        turn.correction_attempts = record_failures(
            turn,
            [
                DbAdminChatStatementLastExecution(
                    index=1,
                    status="failed",
                    outcome="ORA-00942: failed\nsecond line",
                    executed_at=str(revision),
                ),
                DbAdminChatStatementLastExecution(
                    index=2, status="succeeded", outcome="成功", executed_at=str(revision)
                ),
            ],
        )
    assert len(turn.correction_attempts) == ATTEMPT_LIMIT + 1
    assert [a.revision for a in turn.correction_attempts] == [2, 3, 4, 5]
    assert all(
        a.statement_index == 1 and "second line" not in a.error_summary
        for a in turn.correction_attempts
    )


def test_analysis_does_not_trust_client_sql_or_other_statement_history() -> None:
    ai = _FakeEnterpriseAi([{"analysis": "修正", "corrected_sql": "DELETE FROM T4"}])
    service = _catalog_service(_FakeAdapter(), ai)
    ids = _saved_analysis_turn(service, "DELETE FROM T1; DELETE FROM T2")
    updated = service.replace_db_admin_chat_turn_sql(
        *ids, "DELETE FROM T3", statement_index=1, **OWNER
    )
    updated.statement_executions = [
        DbAdminChatStatementLastExecution(
            index=2, status="failed", outcome="ORA-00942", executed_at="2026-10-22"
        )
    ]
    service._db_admin_chat_store.update_turn(ids[0], updated)
    req = DbAdminChatAnalyzeRequest.model_validate(
        {
            "statement_index": 2,
            "sql": "untrusted SQL",
            "previous_attempts": [{"sql": "untrusted history"}],
        }
    )
    result = service.analyze_db_admin_chat_failure(*ids, req, **OWNER)
    assert result.previous_attempts == []
    assert "DELETE FROM T2" in ai.calls[0]["context"]
    assert "DELETE FROM T1" not in ai.calls[0]["context"]
    assert "untrusted" not in ai.calls[0]["context"]


def test_analysis_route_hides_other_users_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _catalog_service(_FakeAdapter(), _FakeEnterpriseAi([]))
    ids = _saved_analysis_turn(service, "DELETE FROM T1")
    monkeypatch.setattr(router, "nl2sql_service", service)
    with pytest.raises(HTTPException) as caught:
        router.db_admin_chat_analyze_error(
            *ids, DbAdminChatAnalyzeRequest(statement_index=1), _http(_principal("another-user"))
        )
    assert caught.value.status_code == 404


def test_analysis_requires_saved_failure() -> None:
    service = _catalog_service(_FakeAdapter(), _FakeEnterpriseAi([]))
    ids = _saved_analysis_turn(service, "DELETE FROM T1")
    turn = service._db_admin_chat_store.get_turn(*ids)
    assert turn is not None
    turn.statement_executions = []
    service._db_admin_chat_store.update_turn(ids[0], turn)
    with pytest.raises(ValueError, match="保存された失敗"):
        service.analyze_db_admin_chat_failure(
            *ids, DbAdminChatAnalyzeRequest(statement_index=1), **OWNER
        )


def test_failure_history_round_trips_through_turn_json() -> None:
    from app.features.nl2sql.db_admin_chat_store import _decode_turn, _turn_json

    service = _catalog_service(_FakeAdapter(), _FakeEnterpriseAi([]))
    ids = _saved_analysis_turn(service, "DELETE FROM T1")
    turn = service.replace_db_admin_chat_turn_sql(
        *ids, "DELETE FROM T2", statement_index=1, **OWNER
    )
    restored = _decode_turn(turn.turn_id, None, _turn_json(turn))
    assert restored.correction_attempts == turn.correction_attempts
    assert len(restored.correction_attempts) == 1


def test_failure_without_error_message_can_be_saved() -> None:
    service = _catalog_service(_FakeAdapter(), _FakeEnterpriseAi([]))
    ids = _saved_analysis_turn(service, "DELETE FROM T1")
    turn = service._db_admin_chat_store.get_turn(*ids)
    assert turn is not None
    attempts = record_failures(
        turn,
        [
            DbAdminChatStatementLastExecution(
                index=1,
                status="failed",
                outcome="",
                executed_at="2026-10-22",
            )
        ],
    )
    assert len(attempts) == 1
    assert attempts[0].error_summary == ""
