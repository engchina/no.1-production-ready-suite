"""業務 Agent のデータの範囲（使える RAG / NL2SQL のプロファイル。#1378）。

- 定義: 接続ごとの一覧と既定の検証、保存のときに編集者が使えるかを MCP で確かめる。
- 組み込み Runtime: 1 つなら埋めて上書きし schema から除く、複数なら範囲外を呼び先へ送らずに
  拒否し、無ければ既定を使う。一覧・推薦の結果を絞る。RAG は knowledge_base_ids を使わない。
- 範囲を設定していない Agent は今までどおり（既存のテストが確かめる。ここでも 1 件）。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from agents import FunctionTool
from agents.testing import ScriptedModel, assistant_message, function_call
from mcp_support import FakeProductMcp, fake_product_mcp
from pydantic import ValidationError
from pytest import MonkeyPatch
from security_support import ProductionAuth, client, enable_production_auth, login

from app.features.agent import builtin_runtime, data_scope
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.data_scope import AgentDataScope, scoped_input_schema
from app.features.agent.runtime import (
    AgentProfile,
    ApprovalDecisionRequest,
    RunCreateRequest,
    RunStatus,
    runtime_repository,
)
from app.features.agent.skills import AgentSkillDefinition, SkillMcpRequirement, skill_registry
from app.features.agent.tools import ToolPolicy
from app.security.service import set_security_service
from app.settings import get_settings

SKILL_ID = "test1378-skill"
AGENT_ID = "test1378-agent"
USER_UUID = "13781378-2222-3333-4444-555555555555"


def _scope(*ids: str, default: str = "") -> AgentDataScope:
    return AgentDataScope(profile_ids=list(ids), default_profile_id=default)


# ---------------------------------------------------------------------------
# 定義の検証
# ---------------------------------------------------------------------------


def test_single_profile_becomes_the_default() -> None:
    scope = _scope(" profile-sales ", "profile-sales")
    assert scope.profile_ids == ["profile-sales"]
    assert scope.default_profile_id == "profile-sales"


def test_multiple_profiles_need_a_default_in_the_list() -> None:
    with pytest.raises(ValidationError, match="既定のプロファイルを選んでください"):
        _scope("a", "b")
    with pytest.raises(ValidationError, match="使えるプロファイルの中から"):
        _scope("a", "b", default="c")
    assert _scope("a", "b", default="b").default_profile_id == "b"
    # 一覧が空は「範囲なし」（既定も持たない）。
    assert _scope(default="x").default_profile_id == ""


def test_only_builtin_connections_can_have_a_scope() -> None:
    with pytest.raises(ValidationError, match="データの範囲を設定できない接続"):
        AgentProfile(name="x", data_scopes={"crm": _scope("a")})
    # 一覧が空の接続は保存しない（範囲なし）。
    agent = AgentProfile(name="x", data_scopes={"rag": _scope(), "nl2sql": _scope("a")})
    assert list(agent.data_scopes) == ["nl2sql"]


def test_scope_is_part_of_the_published_version() -> None:
    agent_id = f"{AGENT_ID}-version"
    runtime_repository.create_agent(
        AgentProfile(id=agent_id, name="版", data_scopes={"nl2sql": _scope("a")})
    )
    try:
        published = runtime_repository.publish_agent(agent_id)
        assert published.published() is not None
        assert published.published().data_scopes["nl2sql"].profile_ids == ["a"]  # type: ignore[union-attr]
        assert published.unpublished_changes is False
        from app.features.agent.runtime import AgentProfilePatch

        changed = runtime_repository.patch_agent(
            agent_id, AgentProfilePatch(data_scopes={"nl2sql": _scope("a", "b", default="b")})
        )
        # 範囲の変更は下書きの変更（公開するまで利用者の Run には効かない）。
        assert changed.unpublished_changes is True
    finally:
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(agent_id)


def test_single_scope_hides_the_profile_argument_from_the_model() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string"},
            "search_answer_profile_id": {"type": "string"},
            "knowledge_base_ids": {"type": "array"},
        },
        "required": ["query", "search_answer_profile_id"],
    }
    single = scoped_input_schema("rag__rag_lookup_guides", schema, {"rag": _scope("bv-sales")})
    assert set(single["properties"]) == {"query", "knowledge_base_ids"}
    assert single["required"] == ["query"]
    searched = scoped_input_schema("rag__rag_search", schema, {"rag": _scope("bv-sales")})
    assert set(searched["properties"]) == {"query"}
    many = scoped_input_schema(
        "rag__rag_search", schema, {"rag": _scope("bv-a", "bv-b", default="bv-b")}
    )
    assert "bv-a、bv-b" in many["properties"]["search_answer_profile_id"]["description"]
    assert "knowledge_base_ids" not in many["properties"]
    # 範囲の無い接続・別の接続のツールは元のまま。
    assert scoped_input_schema("rag__rag_search", schema, {}) is schema
    assert scoped_input_schema("crm__rag_search", schema, {"rag": _scope("a")}) is schema


# ---------------------------------------------------------------------------
# 組み込み Runtime の強制
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp(monkeypatch: MonkeyPatch) -> Iterator[FakeProductMcp]:
    fake = fake_product_mcp(monkeypatch)
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name="範囲の Skill",
            instructions="RAG と NL2SQL で調べる。",
            mcp_requirements=[
                SkillMcpRequirement(server_id="rag"),
                SkillMcpRequirement(server_id="nl2sql"),
            ],
        )
    )
    # nl2sql_query は承認が要る（readOnlyHint=false）。強制の確認では承認なしで呼ぶ。
    monkeypatch.setattr(
        builtin_runtime,
        "_active_policy",
        lambda: ToolPolicy(allow={"nl2sql__nl2sql_query"}),
    )
    try:
        yield fake
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id for run_id, run in repository._runs.items() if run.agent_id == AGENT_ID
            ]:
                run = repository._runs.pop(run_id)
                for approval in run.approvals:
                    repository._approvals.pop(approval.id, None)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(AGENT_ID)
        with contextlib.suppress(KeyError, ValueError):
            skill_registry.remove(SKILL_ID)


def _agent(**scopes: AgentDataScope) -> None:
    runtime_repository.create_agent(
        AgentProfile(id=AGENT_ID, name="範囲の Agent", skill_ids=[SKILL_ID], data_scopes=scopes)
    )


def _script(monkeypatch: MonkeyPatch, *steps: Any) -> ScriptedModel:
    model = ScriptedModel(list(steps))
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": ModelTarget(
            model_id=model_id or "test-model",
            endpoint="https://oci.example",
            project_ocid="",
            api_key="k",
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    monkeypatch.setattr(get_settings(), "agent_final_validation_enabled", False)
    return model


def _run(goal: str = "今月の売上を教えて") -> str:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=goal, agent_id=AGENT_ID, draft=True), created_by_user_uuid=USER_UUID
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return run.id


def _tools(model: ScriptedModel) -> dict[str, FunctionTool]:
    return {tool.name: tool for tool in model.calls[0].tools if isinstance(tool, FunctionTool)}


def test_single_profile_is_filled_and_overrides_the_model(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _agent(nl2sql=_scope("profile-sales"))
    model = _script(
        monkeypatch,
        [
            function_call(
                "nl2sql__nl2sql_query",
                {"question": "今月の売上", "profile_id": "profile-hr"},
                call_id="call-1",
            )
        ],
        [assistant_message("売上は 1200 です。")],
    )
    run_id = _run()

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    # モデルに見せる schema に profile_id は無い（選ばせない）。
    params = _tools(model)["nl2sql__nl2sql_query"].params_json_schema
    assert "profile_id" not in params["properties"]
    # 指示に範囲の案内が入る（ツールの名前はモデルに渡す名前）。
    instructions = str(model.calls[0].system_instructions)
    assert "# データの範囲" in instructions
    assert "「profile-sales」に決まっている" in instructions
    # モデルが別の ID を渡しても、設定したプロファイルで呼ぶ。
    [call] = mcp.calls_of("nl2sql_query")
    assert call["arguments"]["profile_id"] == "profile-sales"
    [step] = run.steps
    assert step.tool_call is not None
    assert step.tool_call.arguments["profile_id"] == "profile-sales"
    assert step.tool_call.data_scope == {
        "connection": "nl2sql",
        "allowed_profile_ids": ["profile-sales"],
        "argument": "profile_id",
        "requested_profile_id": "profile-hr",
        "profile_id": "profile-sales",
        "action": "overridden",
    }


def test_out_of_scope_profile_is_rejected_without_calling_the_product(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _agent(nl2sql=_scope("profile-sales", "profile-cost", default="profile-cost"))
    model = _script(
        monkeypatch,
        [
            function_call(
                "nl2sql__nl2sql_query",
                {"question": "人事の数", "profile_id": "profile-hr"},
                call_id="call-1",
            )
        ],
        [function_call("nl2sql__nl2sql_query", {"question": "原価"}, call_id="call-2")],
        [assistant_message("原価を答えました。")],
    )
    run_id = _run()

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    # 範囲外の 1 回目は呼び先へ送らず、ID の無い 2 回目は Agent の既定で呼ぶ（"default" ではない）。
    calls = mcp.calls_of("nl2sql_query")
    assert [call["arguments"]["profile_id"] for call in calls] == ["profile-cost"]
    rejected, filled = run.steps
    assert rejected.status == "failed"
    assert rejected.tool_result is not None
    assert rejected.tool_result.error_code == data_scope.DATA_SCOPE_VIOLATION_CODE
    assert "profile-sales、profile-cost" in (rejected.tool_result.error or "")
    assert rejected.tool_call is not None and rejected.tool_call.data_scope is not None
    assert rejected.tool_call.data_scope["action"] == "rejected"
    assert filled.tool_call is not None and filled.tool_call.data_scope is not None
    assert filled.tool_call.data_scope["action"] == "filled"
    assert filled.tool_call.data_scope["profile_id"] == "profile-cost"
    # モデルにはエラーと使えるプロファイルを返す。
    tool_output = str(model.calls[1].input)
    assert data_scope.DATA_SCOPE_VIOLATION_CODE in tool_output
    # 複数なら schema に残し、使える ID を説明に足す。
    params = _tools(model)["nl2sql__nl2sql_query"].params_json_schema
    assert "profile-sales、profile-cost" in params["properties"]["profile_id"]["description"]


def test_list_and_recommendation_are_narrowed_to_the_scope(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    mcp.outputs["nl2sql_list_profiles"] = {
        "profiles": [
            {"id": "profile-sales", "name": "売上", "category": "", "description": ""},
            {"id": "profile-hr", "name": "人事", "category": "", "description": ""},
            {"id": "profile-cost", "name": "原価", "category": "", "description": ""},
        ]
    }
    mcp.outputs["nl2sql_recommend_profile"] = {
        "recommended_profile_id": "profile-hr",
        "rewritten_question": None,
        "candidates": [
            {"id": "profile-hr", "name": "人事", "reason": None, "score": 0.9},
            {"id": "profile-sales", "name": "売上", "reason": None, "score": 0.5},
        ],
    }
    _agent(nl2sql=_scope("profile-sales", "profile-cost", default="profile-sales"))
    _script(
        monkeypatch,
        [
            function_call("nl2sql__nl2sql_list_profiles", {"limit": 1}, call_id="call-1"),
            function_call(
                "nl2sql__nl2sql_recommend_profile", {"question": "売上"}, call_id="call-2"
            ),
        ],
        [assistant_message("売上の業務プロファイルで答えます。")],
    )
    run_id = _run()

    run = runtime_repository.get_run(run_id)
    listed, recommended = run.steps
    # 一覧は範囲で絞ってから、モデルが求めた件数に切る（呼び先には上限まで求める）。
    [list_call] = mcp.calls_of("nl2sql_list_profiles")
    assert list_call["arguments"]["limit"] == 100
    assert listed.tool_result is not None and listed.tool_result.output is not None
    assert [item["id"] for item in listed.tool_result.output["profiles"]] == ["profile-sales"]
    # 範囲外は 1 件（人事）。原価は範囲内だが、求めた件数（1）で切る。
    assert listed.tool_result.audit_metadata["data_scope"] == {"filtered_out": 1}
    # 範囲外の推薦は null、候補も範囲で絞る。
    assert recommended.tool_result is not None and recommended.tool_result.output is not None
    assert recommended.tool_result.output["recommended_profile_id"] is None
    assert [item["id"] for item in recommended.tool_result.output["candidates"]] == [
        "profile-sales"
    ]
    assert recommended.tool_call is not None and recommended.tool_call.data_scope is not None
    assert recommended.tool_call.data_scope["action"] == "filtered"


def test_rag_scope_ignores_knowledge_base_ids(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _agent(rag=_scope("bv-sales"))
    model = _script(
        monkeypatch,
        [
            function_call(
                "rag__rag_search",
                {"query": "契約の更新", "knowledge_base_ids": ["kb-hr"]},
                call_id="call-1",
            )
        ],
        [assistant_message("更新は第 5 条です。")],
    )
    run_id = _run()

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    [call] = mcp.calls_of("rag_search")
    # プロファイルの範囲を、ナレッジベースの直接の指定で迂回させない。
    assert call["arguments"] == {"query": "契約の更新", "search_answer_profile_id": "bv-sales"}
    params = _tools(model)["rag__rag_search"].params_json_schema
    assert "knowledge_base_ids" not in params["properties"]
    assert "search_answer_profile_id" not in params["properties"]
    step = run.steps[0]
    assert step.tool_call is not None and step.tool_call.data_scope is not None
    assert step.tool_call.data_scope["action"] == "filled"
    assert step.tool_call.data_scope["ignored_knowledge_base_ids"] == ["kb-hr"]


def test_approval_shows_the_scoped_arguments(monkeypatch: MonkeyPatch, mcp: FakeProductMcp) -> None:
    _agent(nl2sql=_scope("profile-sales"))
    # 既定のポリシー（nl2sql_query は承認が要る）。
    monkeypatch.setattr(
        builtin_runtime, "_active_policy", lambda: ToolPolicy(default_mode="approval")
    )
    _script(
        monkeypatch,
        [function_call("nl2sql__nl2sql_query", {"question": "売上"}, call_id="call-1")],
        [assistant_message("売上は 1200 です。")],
    )
    run_id = _run()

    waiting = runtime_repository.get_run(run_id)
    assert waiting.status == RunStatus.WAITING_APPROVAL
    [approval] = waiting.approvals
    # 承認者には、実行するときに送る（範囲を当てた）引数を見せる。
    assert approval.tool_call.arguments["profile_id"] == "profile-sales"
    assert approval.tool_call.data_scope is not None
    runtime_repository.decide_approval(
        approval.id, ApprovalDecisionRequest(approved=True, decided_by="approver")
    )
    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    [call] = mcp.calls_of("nl2sql_query")
    assert call["arguments"]["profile_id"] == "profile-sales"
    [step] = run.steps
    assert step.tool_call is not None and step.tool_call.data_scope is not None
    assert step.tool_call.data_scope["action"] == "filled"


def test_agent_without_scope_keeps_the_model_choice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _agent()
    model = _script(
        monkeypatch,
        [
            function_call(
                "rag__rag_search",
                {"query": "契約", "knowledge_base_ids": ["kb-1"]},
                call_id="call-1",
            )
        ],
        [assistant_message("回答です。")],
    )
    run_id = _run()

    [call] = mcp.calls_of("rag_search")
    assert call["arguments"] == {"query": "契約", "knowledge_base_ids": ["kb-1"]}
    assert "# データの範囲" not in str(model.calls[0].system_instructions)
    step = runtime_repository.get_run(run_id).steps[0]
    assert step.tool_call is not None and step.tool_call.data_scope is None


# ---------------------------------------------------------------------------
# 編集画面の候補と保存の確認（編集者のサービストークン）
# ---------------------------------------------------------------------------


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def test_candidates_are_listed_as_the_editor(auth: ProductionAuth, mcp: FakeProductMcp) -> None:
    editor = auth.create_user("scope-editor", system_admin=True)
    headers = login("scope-editor")

    response = client.get("/api/agent-data-scopes/nl2sql/candidates", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["connection"] == "nl2sql"
    assert [item["id"] for item in data["profiles"]] == ["profile-sales"]
    [call] = mcp.calls_of("nl2sql_list_profiles")
    # 編集者として呼ぶ（編集者が使えるものだけが返る）。
    assert call["claims"]["sub"] == editor.user_uuid
    assert call["arguments"] == {"limit": 100}

    unknown = client.get("/api/agent-data-scopes/crm/candidates", headers=headers)
    assert unknown.status_code == 404


def test_candidates_report_an_unconfigured_connection(
    auth: ProductionAuth, mcp: FakeProductMcp
) -> None:
    del mcp
    from app.features.agent.config import runtime_config_store

    runtime_config_store.upsert_mcp_server("rag", base_url="")
    auth.create_user("scope-editor2", system_admin=True)
    headers = login("scope-editor2")

    response = client.get("/api/agent-data-scopes/rag/candidates", headers=headers)

    assert response.status_code == 409
    assert "URL が設定されていない" in response.json()["error_messages"][0]


def test_profiles_the_editor_cannot_use_are_not_saved(
    auth: ProductionAuth, mcp: FakeProductMcp
) -> None:
    auth.create_user("scope-editor3", system_admin=True)
    headers = login("scope-editor3")
    body = {
        "id": AGENT_ID,
        "name": "範囲の Agent",
        "data_scopes": {"nl2sql": {"profile_ids": ["profile-hr"], "default_profile_id": ""}},
    }

    rejected = client.post("/api/agents", json=body, headers=headers)

    assert rejected.status_code == 400, rejected.text
    assert "profile-hr" in rejected.json()["error_messages"][0]
    assert all(agent.id != AGENT_ID for agent in runtime_repository.list_agents())

    body["data_scopes"] = {"nl2sql": {"profile_ids": ["profile-sales"], "default_profile_id": ""}}
    created = client.post("/api/agents", json=body, headers=headers)
    assert created.status_code == 200, created.text
    assert created.json()["data"]["data_scopes"] == {
        "nl2sql": {"profile_ids": ["profile-sales"], "default_profile_id": "profile-sales"}
    }

    # 変更でも、加えたプロファイルを確かめる（既に範囲にあるものは確かめない）。
    patched = client.patch(
        f"/api/agents/{AGENT_ID}",
        json={
            "data_scopes": {
                "nl2sql": {
                    "profile_ids": ["profile-sales", "profile-hr"],
                    "default_profile_id": "profile-sales",
                }
            }
        },
        headers=headers,
    )
    assert patched.status_code == 400
    calls_before = len(mcp.calls_of("nl2sql_list_profiles"))
    unchanged = client.patch(
        f"/api/agents/{AGENT_ID}",
        json={
            "name": "名前だけ変える",
            "data_scopes": {"nl2sql": {"profile_ids": ["profile-sales"]}},
        },
        headers=headers,
    )
    assert unchanged.status_code == 200, unchanged.text
    assert len(mcp.calls_of("nl2sql_list_profiles")) == calls_before
    # 範囲を外す（未設定に戻す）のは確かめずに保存できる。
    cleared = client.patch(f"/api/agents/{AGENT_ID}", json={"data_scopes": {}}, headers=headers)
    assert cleared.status_code == 200
    assert cleared.json()["data"]["data_scopes"] == {}
