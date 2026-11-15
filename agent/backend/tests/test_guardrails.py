"""Agent の入力と最終回答の安全チェック（合成の入力と決定論のスタブ。#5062・#5109）。"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message
from guardrail_support import ATTACK, EMAIL, MASKED, RecordedOci, enable_guardrails
from security_support import client

from app.features.agent import builtin_runtime, guardrails, runtime
from app.features.agent.confirmation import ConfirmationRequest, item_from_arguments
from app.features.agent.runtime import (
    AgentProfile,
    AgentRuntimeRepository,
    RunCreateRequest,
    RunStatus,
)


@pytest.fixture
def oci(monkeypatch: pytest.MonkeyPatch) -> RecordedOci:
    return enable_guardrails(monkeypatch, output=True)


def test_blocked_goal_does_not_create_a_run(oci: RecordedOci) -> None:
    before = len(runtime.runtime_repository.list_runs())
    response = client.post("/api/runs", json={"goal": ATTACK})
    assert response.status_code == 422, response.text
    assert "guardrail_blocked" in response.text
    assert len(runtime.runtime_repository.list_runs()) == before


def test_goal_pii_is_masked_before_the_run_is_saved(
    oci: RecordedOci, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.features.agent.router._schedule_builtin_run", lambda run: None)
    response = client.post("/api/runs", json={"goal": f"{EMAIL} の申請を確認して"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["goal"] == f"{MASKED} の申請を確認して"


def test_final_answer_is_masked(oci: RecordedOci, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = AgentRuntimeRepository()
    monkeypatch.setattr(runtime, "runtime_repository", repo)
    model = ScriptedModel([[assistant_message(f"担当は {EMAIL} です。")]])
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": builtin_runtime.ModelTarget(
            model_id=model_id or "scripted",
            endpoint="https://model.example",
            project_ocid="",
            api_key="synthetic",
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda target: model)
    repo.create_agent(AgentProfile(id="synthetic-guardrail", name="安全チェックの検証"))
    item = repo.create_builtin_run(
        RunCreateRequest(goal="窓口を教えて", agent_id="synthetic-guardrail"),
        created_by_user_uuid="synthetic-owner",
    )
    anyio.run(builtin_runtime.execute_run, item.id)
    done = repo.get_run(item.id)
    text: Any = next(a.content["text"] for a in done.artifacts if a.kind == "answer")
    assert EMAIL not in text
    assert MASKED in text


# ---- 入力を受け取るすべての入口で、安全チェックが文を使う最初の処理になる（#5109） ----------


def test_create_builtin_run_checks_the_goal_before_creating_a_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run を作る共通の経路（チャット・MCP・自動実行・品質評価・再実行が通る）で検査する。"""
    oci = enable_guardrails(monkeypatch)
    repo = AgentRuntimeRepository()
    repo.create_agent(AgentProfile(id="synthetic-entry", name="入口の検証"))

    with pytest.raises(guardrails.GuardrailBlockedError):
        repo.create_builtin_run(
            RunCreateRequest(goal=ATTACK, agent_id="synthetic-entry"),
            created_by_user_uuid="synthetic-owner",
        )
    assert repo.list_runs() == []
    masked = repo.create_builtin_run(
        RunCreateRequest(goal=f"{EMAIL} の申請", agent_id="synthetic-entry"),
        created_by_user_uuid="synthetic-owner",
    )
    assert masked.goal == f"{MASKED} の申請"
    assert repo.get_run(masked.id).goal == f"{MASKED} の申請"
    assert oci.calls == [ATTACK, f"{EMAIL} の申請"]


def test_run_api_checks_the_goal_once(oci: RecordedOci, monkeypatch: pytest.MonkeyPatch) -> None:
    """画面の Run の作成は共通の経路の 1 回だけ検査する（二重に OCI を呼ばない）。"""
    monkeypatch.setattr("app.features.agent.router._schedule_builtin_run", lambda run: None)
    response = client.post("/api/runs", json={"goal": "窓口を教えて"})
    assert response.status_code == 200, response.text
    assert oci.calls == ["窓口を教えて"]


def test_replay_checks_the_saved_goal_with_the_current_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """安全チェックを ON にする前に保存した目標も、再実行の前に検査し、止めたら Run を作らない。"""
    monkeypatch.setattr("app.features.agent.router._schedule_builtin_run", lambda run: None)
    saved = runtime.runtime_repository.create_builtin_run(RunCreateRequest(goal=ATTACK))
    enable_guardrails(monkeypatch)
    before = len(runtime.runtime_repository.list_runs())

    response = client.post(f"/api/runs/{saved.id}/replay")

    assert response.status_code == 422, response.text
    assert "guardrail_blocked" in response.text
    assert len(runtime.runtime_repository.list_runs()) == before


ASK = {
    "question": "どの期間を見ますか？",
    "reason": "期間が質問から決められません。",
    "options": [{"label": "今月", "description": ""}],
    "multiple": False,
}


def _waiting_confirmation(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setattr("app.features.agent.router._schedule_builtin_run", lambda run: None)
    created = client.post("/api/runs", json={"goal": "期間ごとの件数を教えて"})
    assert created.status_code == 200, created.text
    run_id = created.json()["data"]["id"]
    request = ConfirmationRequest(
        source="agent", call_id="call-ask", items=[item_from_arguments(ASK)]
    )
    runtime.runtime_repository.request_builtin_approvals(
        run_id, [], state="{}", confirmations=[request]
    )
    return run_id, request.id


def test_confirmation_free_text_is_checked_before_it_is_recorded(
    oci: RecordedOci, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回答前の確認の自由入力も、記録してモデル・次の Run に渡す前に検査する。"""
    run_id, confirmation_id = _waiting_confirmation(monkeypatch)
    url = f"/api/runs/{run_id}/confirmations/{confirmation_id}"

    blocked = client.post(
        url, json={"action": "answer", "answers": [{"item_id": "item_1", "other_text": ATTACK}]}
    )

    assert blocked.status_code == 422, blocked.text
    assert "guardrail_blocked" in blocked.text
    run = runtime.runtime_repository.get_run(run_id)
    assert run.status == RunStatus.WAITING_CONFIRMATION
    assert run.confirmations[0].status == "pending"

    masked = client.post(
        url,
        json={
            "action": "answer",
            "answers": [{"item_id": "item_1", "other_text": f"{EMAIL} の分"}],
        },
    )

    assert masked.status_code == 200, masked.text
    [answer] = runtime.runtime_repository.get_run(run_id).confirmations[0].answers
    assert answer.other_text == f"{MASKED} の分"
    assert oci.calls[-2:] == [ATTACK, f"{EMAIL} の分"]
