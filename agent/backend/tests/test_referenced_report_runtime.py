"""previewのSDK接続と公開履歴、版固定、入力状態の境界を合成で検証する。"""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest
from agents import FunctionTool
from agents.testing import ScriptedModel, assistant_message, function_call
from pydantic import ValidationError
from security_support import client

from app.features.agent import builtin_runtime, runtime, tool_grounding
from app.features.agent.output_fields import AgentOutputField
from app.features.agent.report_contract import AnswerBudget, FinalSelection, ReportOutputContract
from app.features.agent.report_fields import REPORT_FIELDS_KEY
from app.features.agent.report_preview import REPORT_PREVIEWS_METADATA_KEY
from app.features.agent.report_runtime import source_digest
from app.features.agent.runtime import (
    AgentProfile,
    AgentProfilePatch,
    AgentRuntimeRepository,
    RunCreateRequest,
    RunStatus,
    agent_for_run,
)


def contract(hard: int | None = None, soft: int | None = None) -> ReportOutputContract:
    return ReportOutputContract(
        budget=AnswerBudget(hard_max=hard, soft_target=soft) if hard or soft else None
    )


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> AgentRuntimeRepository:
    repository = AgentRuntimeRepository()
    monkeypatch.setattr(runtime, "runtime_repository", repository)
    return repository


def script(monkeypatch: pytest.MonkeyPatch, *steps: Any) -> ScriptedModel:
    model = ScriptedModel(list(steps))
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
    return model


def create(repo: AgentRuntimeRepository, mode: ReportOutputContract | None = None) -> str:
    repo.create_agent(
        AgentProfile(id="synthetic-report", name="参照付き回答の検証", output_contract=mode)
    )
    return "synthetic-report"


def draft(text: str) -> dict[str, Any]:
    return {"blocks": [{"kind": "narrative", "text": text}]}


def run(repo: AgentRuntimeRepository, agent: str) -> Any:
    item = repo.create_builtin_run(
        RunCreateRequest(goal="内容を説明してください。", agent_id=agent),
        created_by_user_uuid="synthetic-owner",
    )
    anyio.run(builtin_runtime.execute_run, item.id)
    return repo.get_run(item.id)


def answer(item: Any) -> str:
    value = next(a.content["text"] for a in item.artifacts if a.kind == "answer")
    assert isinstance(value, str)
    return value


def test_report_real_SDK_and_canonical_history(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = create(repo, contract())
    model = script(
        monkeypatch,
        [
            function_call(
                "draft_report", json.dumps(draft("確認できた内容です。")), call_id="draft-a"
            )
        ],
        [assistant_message('{"preview_id":"A1"}')],
    )
    completed = run(repo, agent)
    assert completed.status == RunStatus.COMPLETED and answer(completed) == "確認できた内容です。"
    assert len(model.calls) == 2 and any(t.name == "draft_report" for t in model.calls[0].tools)
    assert model.calls[0].output_schema is not None
    assert REPORT_PREVIEWS_METADATA_KEY in completed.metadata
    follow = repo.create_builtin_run(
        RunCreateRequest(goal="続けてください。", agent_id=agent, thread_id=completed.thread_id),
        created_by_user_uuid="synthetic-owner",
    )
    history = json.dumps(
        builtin_runtime.conversation_input(follow.id, follow.goal), ensure_ascii=False
    )
    assert "確認できた内容です。" in history and "preview_id" not in history
    assert REPORT_PREVIEWS_METADATA_KEY not in follow.metadata


def test_plain_mode_unchanged(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = create(repo)
    model = script(monkeypatch, [assistant_message("通常の回答です。")])
    completed = run(repo, agent)
    assert completed.status == RunStatus.COMPLETED and answer(completed) == "通常の回答です。"
    assert all(t.name != "draft_report" for t in model.calls[0].tools)
    assert model.calls[0].output_schema is None


@pytest.mark.parametrize(
    "selection", ['{"preview_id":"A9"}', '{"preview_id":"A0"}', '{"preview_id":"A1","text":"変更"}']
)
def test_invalid_selection_fails_without_replay(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    agent = create(repo, contract())
    model = script(
        monkeypatch,
        [function_call("draft_report", json.dumps(draft("確認済みです。")), call_id="draft-a")],
        [assistant_message(selection)],
    )
    completed = run(repo, agent)
    assert completed.status == RunStatus.FAILED and not any(
        a.kind == "answer" for a in completed.artifacts
    )
    assert len(model.calls) == 2 and len(completed.steps) == 1


@pytest.mark.parametrize("hard", [True, False])
def test_budget_status_owned_by_program(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch, hard: bool
) -> None:
    agent = create(repo, contract(hard=3) if hard else contract(soft=3))
    model = script(
        monkeypatch,
        [function_call("draft_report", json.dumps(draft("完全な内容です。")), call_id="draft-a")],
        [assistant_message('{"preview_id":"A1"}')],
    )
    completed = run(repo, agent)
    output = completed.steps[0].tool_result.output
    assert len(model.calls) == 2 and "body" not in output and "hash" not in json.dumps(output)
    if hard:
        assert completed.status == RunStatus.FAILED and output["status"] == "REJECTED_HARD_MAX"
        assert not any(a.kind == "answer" for a in completed.artifacts)
    else:
        assert completed.status == RunStatus.COMPLETED and output["status"] == "OVER_TARGET"
        assert answer(completed) == "完全な内容です。" and output["total_codepoints"] == len(
            answer(completed)
        )


def test_two_runs_A1_independent(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = create(repo, contract())
    results = []
    for text in ("最初の内容です。", "次の内容です。"):
        script(
            monkeypatch,
            [function_call("draft_report", json.dumps(draft(text)), call_id="same-call")],
            [assistant_message('{"preview_id":"A1"}')],
        )
        results.append(run(repo, agent))
    assert answer(results[0]) != answer(results[1])
    assert (
        results[0].metadata[REPORT_PREVIEWS_METADATA_KEY]["run_id"]
        != results[1].metadata[REPORT_PREVIEWS_METADATA_KEY]["run_id"]
    )


def test_version_pinned_and_custom_fields_exclusive(repo: AgentRuntimeRepository) -> None:
    agent = create(repo)
    first = repo.create_builtin_run(RunCreateRequest(goal="先の質問", agent_id=agent))
    repo.patch_agent(agent, AgentProfilePatch(output_contract=contract()))
    repo.publish_agent(agent)
    current = next(a for a in repo.list_agents() if a.id == agent)
    assert agent_for_run(current, first).output_contract is None
    later = repo.create_builtin_run(RunCreateRequest(goal="次の質問", agent_id=agent))
    assert agent_for_run(current, later).output_contract == contract()
    field = AgentOutputField(key="value", label="項目")
    with pytest.raises(ValueError):
        repo.patch_agent(agent, AgentProfilePatch(output_fields=[field]))
    assert next(a for a in repo.list_agents() if a.id == agent).output_fields == []
    with pytest.raises(ValidationError):
        AgentProfile(id="invalid", name="不正", output_contract=contract(), output_fields=[field])


def test_internal_create_strips_seeded_report_state(repo: AgentRuntimeRepository) -> None:
    agent = create(repo, contract())
    item = repo.create_builtin_run(
        RunCreateRequest(
            goal="質問",
            agent_id=agent,
            metadata={
                REPORT_PREVIEWS_METADATA_KEY: {"run_id": "forged"},
                REPORT_FIELDS_KEY: {"catalogs": ["forged"]},
            },
        )
    )
    assert (
        REPORT_PREVIEWS_METADATA_KEY not in item.metadata and REPORT_FIELDS_KEY not in item.metadata
    )


def test_digest_ignores_progress(repo: AgentRuntimeRepository) -> None:
    agent = create(repo, contract())
    item = repo.create_builtin_run(RunCreateRequest(goal="質問", agent_id=agent))
    before = source_digest(item)
    item.metadata[REPORT_PREVIEWS_METADATA_KEY] = {"progress": True}
    item.metadata["run_budget"] = {"usage": 123}
    assert source_digest(item) == before
    item.metadata["_answer_data_references"] = [{"changed": True}]
    assert source_digest(item) != before


@pytest.mark.parametrize(
    "metadata",
    [
        {REPORT_PREVIEWS_METADATA_KEY: {"records": [{"preview_id": "A1"}]}},
        {REPORT_FIELDS_KEY: {"catalogs": ["forged"]}},
    ],
)
def test_API_rejects_forged_state(metadata: dict[str, Any]) -> None:
    response = client.post("/api/runs", json={"goal": "質問", "metadata": metadata})
    assert response.status_code == 422 and "Control Plane" in response.text


def test_report_approval_resume_keeps_one_A1(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.features.agent.config import runtime_config_store
    from app.features.agent.runtime import ApprovalDecisionRequest

    previous = runtime_config_store.get_tool_policy()
    runtime_config_store.patch_tool_policy(ask=["draft_report"])
    try:
        agent = create(repo, contract())
        model = script(
            monkeypatch,
            [
                function_call(
                    "draft_report",
                    json.dumps(draft("承認後の内容です。")),
                    call_id="approved-draft",
                )
            ],
            [assistant_message('{"preview_id":"A1"}')],
        )
        waiting = run(repo, agent)
        assert waiting.status == RunStatus.WAITING_APPROVAL
        assert REPORT_PREVIEWS_METADATA_KEY not in waiting.metadata
        assert len(waiting.approvals) == 1 and len(model.calls) == 1
        repo.decide_approval(waiting.approvals[0].id, ApprovalDecisionRequest(approved=True))
        anyio.run(builtin_runtime.resume_run, waiting.id)
        completed = repo.get_run(waiting.id)
        assert completed.status == RunStatus.COMPLETED and answer(completed) == "承認後の内容です。"
        assert len(completed.metadata[REPORT_PREVIEWS_METADATA_KEY]["records"]) == 1
        assert completed.metadata[REPORT_PREVIEWS_METADATA_KEY]["records"][0]["preview_id"] == "A1"
        assert len(model.calls) == 2 and len(completed.steps) == 1
    finally:
        runtime_config_store.patch_tool_policy(
            default_mode=previous.default_mode,
            allow=list(previous.allow),
            ask=list(previous.ask),
            deny=list(previous.deny),
        )


def test_public_MCP_does_not_accept_seeded_state(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.features.agent import mcp_server, router

    agent = create(repo, contract())
    monkeypatch.setattr(router, "runtime_repository", repo)
    monkeypatch.setattr(mcp_server, "runtime_repository", repo)
    monkeypatch.setattr(router, "_schedule_builtin_run", lambda current: None)
    response = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "agent_chat",
                "arguments": {
                    "agent_id": agent,
                    "question": "質問",
                    "wait_seconds": 0,
                    "metadata": {
                        REPORT_PREVIEWS_METADATA_KEY: {"records": ["forged"]},
                        REPORT_FIELDS_KEY: {"catalogs": ["forged"]},
                    },
                },
            },
        },
    )
    assert response.status_code == 200
    content = response.json()["result"]
    if not content["isError"]:
        item = repo.get_run(content["structuredContent"]["run_id"])
        assert (
            REPORT_PREVIEWS_METADATA_KEY not in item.metadata
            and REPORT_FIELDS_KEY not in item.metadata
        )
    else:
        assert not repo.list_runs()


def test_API_create_patch_contract_and_conflict() -> None:
    agent = "report-config-api-synthetic"
    try:
        created = client.post(
            "/api/agents",
            json={
                "id": agent,
                "name": "出力設定検証",
                "output_contract": {"mode": "referenced_report", "budget": {"soft_target": 100}},
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["data"]["output_contract"]["budget"]["soft_target"] == 100
        refused = client.patch(
            "/api/agents/" + agent, json={"output_fields": [{"key": "value", "label": "項目"}]}
        )
        assert refused.status_code == 400
        retained = client.get("/api/agents")
        saved = next(a for a in retained.json()["data"]["agents"] if a["id"] == agent)
        assert saved["output_fields"] == []
        cleared = client.patch(
            "/api/agents/" + agent,
            json={"output_contract": None, "output_fields": [{"key": "value", "label": "項目"}]},
        )
        assert cleared.status_code == 200 and cleared.json()["data"]["output_contract"] is None
    finally:
        client.delete("/api/agents/" + agent)


def finish_tool(
    repo: AgentRuntimeRepository,
    run_id: str,
    name: str,
    output: dict[str, Any],
    *,
    trusted: bool = False,
) -> None:
    from app.features.agent.tools import ToolCall, ToolResult

    step, _ = repo.start_builtin_tool_step(run_id, ToolCall(name=name, arguments={}))
    repo.finish_builtin_tool_step(
        run_id,
        step,
        ToolResult(name=name, success=True, output=output),
        trusted_report_source=trusted,
    )


@pytest.mark.parametrize("enabled,trusted", [(True, True), (True, False), (False, True)])
def test_schema_registry_only_for_report_and_verified_connection(
    repo: AgentRuntimeRepository, enabled: bool, trusted: bool
) -> None:
    from test_report_fields import schema

    agent = create(repo, contract() if enabled else None)
    item = repo.create_builtin_run(RunCreateRequest(goal="質問", agent_id=agent))
    repo.begin_builtin_run(item.id)
    output = schema(1)
    finish_tool(repo, item.id, "nl2sql__nl2sql_get_profile_schema", output, trusted=trusted)
    saved = repo.get_run(item.id)
    assert (REPORT_FIELDS_KEY in saved.metadata) == (enabled and trusted)
    assert saved.steps[0].tool_result is not None
    assert saved.steps[0].tool_result.output == output and "schema_ref" not in output


def test_render_preserves_program_table_cell_and_confirmation_literals(
    repo: AgentRuntimeRepository,
) -> None:
    from test_report_fields import schema

    from app.features.agent.report_contract import AnswerPlan

    agent = create(repo, contract())
    item = repo.create_builtin_run(RunCreateRequest(goal="質問", agent_id=agent))
    repo.begin_builtin_run(item.id)
    original = schema(1)
    original["objects"][0]["columns"][0]["name"] = "VALUE A—B"
    finish_tool(repo, item.id, "nl2sql__nl2sql_get_profile_schema", original, trusted=True)
    finish_tool(
        repo,
        item.id,
        "nl2sql__nl2sql_query",
        {
            "status": "done",
            "columns": ["VALUE A—B", "ID"],
            "rows": [{"VALUE A—B": "A—B", "ID": "A-B"}],
            "column_metadata": [],
        },
        trusted=True,
    )
    saved = repo.get_run(item.id)
    plan = AnswerPlan.model_validate(
        {
            "blocks": [
                {"kind": "narrative", "text": "値は[[cell:T1:R1:C1]]です。"},
                {"kind": "table", "table": "T1"},
                {
                    "kind": "confirmation_section",
                    "items": [{"field_ref": "S1:F1", "result_ref": None}],
                },
            ]
        }
    )
    rendered = builtin_runtime._render_report_body(saved, plan, None)
    assert "値はA—Bです。" in rendered.body
    assert "VALUE A—B" in rendered.body and "VALUE A-B" not in rendered.body
    assert "APP.TABLE_A.VALUE A—B" in rendered.body
    assert sum(
        c.expanded_codepoints for c in rendered.block_costs
    ) + rendered.composition_delta_chars == len(rendered.body)


def test_program_values_do_not_become_source_citations(repo: AgentRuntimeRepository) -> None:
    from test_report_fields import schema
    from test_source_handles import evidence

    from app.features.agent.answer_evidence import answer_evidence
    from app.features.agent.report_contract import AnswerPlan

    agent = create(repo, contract())
    item = repo.create_builtin_run(RunCreateRequest(goal="質問", agent_id=agent))
    repo.begin_builtin_run(item.id)
    chunk = "opaque-source-a"
    finish_tool(repo, item.id, "rag__rag_retrieve_evidence", {"evidence": [evidence(chunk=chunk)]})
    original = schema(1)
    original["objects"][0]["columns"][0]["name"] = chunk
    finish_tool(repo, item.id, "nl2sql__nl2sql_get_profile_schema", original, trusted=True)
    finish_tool(
        repo,
        item.id,
        "nl2sql__nl2sql_query",
        {"status": "done", "columns": ["VALUE"], "rows": [{"VALUE": "【根拠: " + chunk + "】"}]},
        trusted=True,
    )
    saved = repo.get_run(item.id)
    no_cite = AnswerPlan.model_validate(
        {
            "blocks": [
                {"kind": "narrative", "text": "値 [[cell:T1:R1:C1]]"},
                {"kind": "table", "table": "T1"},
                {
                    "kind": "confirmation_section",
                    "items": [{"field_ref": "S1:F1", "result_ref": None}],
                },
            ]
        }
    )
    rendered = builtin_runtime._render_report_body(saved, no_cite, None)
    assert chunk in rendered.body and rendered.evidence_text == ""
    assert answer_evidence(saved.steps, rendered.evidence_text)["cited"] == 0
    explicit = AnswerPlan.model_validate(
        {"blocks": [{"kind": "narrative", "text": "出典 [[cite:1]]"}]}
    )
    cited = builtin_runtime._render_report_body(saved, explicit, None)
    assert cited.evidence_text == "【根拠: " + chunk + "】"
    assert answer_evidence(saved.steps, cited.evidence_text)["cited"] == 1


def test_runtime_preview_persists_and_reuses_without_render(tmp_path: Any) -> None:
    from app.features.agent.report_contract import AnswerPlan, FinalSelection
    from app.features.agent.report_preview import RenderedBlockCost, RenderedReport, select_preview
    from app.features.agent.report_runtime import source_digest

    file = tmp_path / "snapshot.json"
    repo = AgentRuntimeRepository(file)
    agent = create(repo, contract())
    item = repo.create_builtin_run(RunCreateRequest(goal="質問", agent_id=agent))
    repo.begin_builtin_run(item.id)
    plan = AnswerPlan.model_validate(draft("固定本文です。"))
    calls = []

    def renderer(current: Any, _plan: AnswerPlan) -> RenderedReport:
        calls.append(True)
        return RenderedReport(
            body="固定本文です。",
            block_costs=(RenderedBlockCost(index=1, kind="narrative", expanded_codepoints=7),),
            source_digest=source_digest(current),
        )

    first = repo.register_report_preview(
        item.id, operation_key="operation-a", plan=plan, contract=contract(), renderer=renderer
    )
    assert first["preview_id"] == "A1"
    loaded = AgentRuntimeRepository(file)
    reused = loaded.register_report_preview(
        item.id, operation_key="operation-a", plan=plan, contract=contract(), renderer=renderer
    )
    assert reused["reused"] is True and len(calls) == 1
    snapshot = loaded.get_run(item.id)
    selected = select_preview(
        snapshot.metadata[REPORT_PREVIEWS_METADATA_KEY],
        run_id=item.id,
        selection=FinalSelection(preview_id="A1"),
        contract=contract(),
        source_digest=source_digest(snapshot),
    )
    assert selected.body == "固定本文です。"


def test_SDK_citation_survives_selection_and_final_evidence(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_source_handles import evidence

    agent = create(repo, contract())
    item = repo.create_builtin_run(
        RunCreateRequest(goal="根拠を示して説明してください。", agent_id=agent)
    )
    finish_tool(repo, item.id, "rag__rag_retrieve_evidence", {"evidence": [evidence()]})
    model = script(
        monkeypatch,
        [
            function_call(
                "draft_report",
                json.dumps(draft("取得した根拠です。[[cite:1]]")),
                call_id="source-draft",
            )
        ],
        [assistant_message('{"preview_id":"A1"}')],
    )
    anyio.run(builtin_runtime.execute_run, item.id)
    completed = repo.get_run(item.id)
    assert completed.status == RunStatus.COMPLETED
    assert "【根拠: doc-a:set-a:1】" in answer(completed)
    artifact = next(a for a in completed.artifacts if a.kind == "answer_evidence")
    assert artifact.content["cited"] == 1 and artifact.content["markers"]
    assert len(model.calls) == 2


def test_SDK_result_and_recording_keep_raw_final_selector(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = create(repo, contract())
    item = repo.create_builtin_run(RunCreateRequest(goal="説明してください。", agent_id=agent))
    repo.begin_builtin_run(item.id)
    script(
        monkeypatch,
        [function_call("draft_report", json.dumps(draft("保存本文です。")), call_id="raw-draft")],
        [assistant_message('{"preview_id":"A1"}')],
    )
    sdk_agent = builtin_runtime.build_sdk_agent(
        item.id,
        name="test",
        instructions="説明する。",
        skill_ids=[],
        model_id="",
        output_contract=contract(),
    )

    async def execute() -> Any:
        raw = await builtin_runtime._run_sdk(item.id, sdk_agent, item.goal)
        canonical = builtin_runtime._resolve_report_result(item.id, raw, contract())
        assert (
            raw.final_output == {"preview_id": "A1"}
            or getattr(raw.final_output, "preview_id", None) == "A1"
        )
        assert canonical.final_output == "保存本文です。"
        assert canonical.raw_responses == raw.raw_responses
        assert "preview_id" in json.dumps(raw.to_input_list(), default=str)
        last = canonical.to_input_list()[-1]
        assert last["type"] == "message" and "保存本文です。" in json.dumps(
            last, ensure_ascii=False
        )
        assert "preview_id" not in json.dumps(last)
        return canonical

    anyio.run(execute)


def test_SDK_schema_output_gets_short_refs_only_in_model_copy(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from copy import deepcopy

    from test_report_fields import schema

    from app.features.agent.tools import ToolDefinition

    original = schema(1)
    definition = ToolDefinition(
        name="nl2sql__nl2sql_get_profile_schema",
        description="合成schemaを返す。",
        input_schema={
            "type": "object",
            "properties": {"profile_id": {"type": "string"}},
            "required": ["profile_id"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        mcp_server_id="nl2sql",
        mcp_tool_name="nl2sql_get_profile_schema",
    )
    monkeypatch.setattr(
        builtin_runtime,
        "discover_mcp_tools",
        lambda *a, **kw: ([(definition, lambda *a: deepcopy(original))], []),
    )
    agent = create(repo, contract())
    model = script(
        monkeypatch,
        [
            function_call(
                definition.name, json.dumps({"profile_id": "profile-a"}), call_id="schema-call"
            )
        ],
        [
            function_call(
                "draft_report",
                json.dumps(
                    {
                        "blocks": [
                            {
                                "kind": "confirmation_section",
                                "items": [{"field_ref": "S1:F1", "result_ref": None}],
                            }
                        ]
                    }
                ),
                call_id="field-draft",
            )
        ],
        [assistant_message('{"preview_id":"A1"}')],
    )
    completed = run(repo, agent)
    assert completed.status == RunStatus.COMPLETED, json.dumps(
        [x.tool_result.model_dump(mode="json") if x.tool_result else None for x in completed.steps],
        ensure_ascii=False,
    )
    assert "APP.TABLE_A.VALUE_0" in answer(completed)
    received = [
        json.loads(item["output"])
        for item in model.calls[1].input
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert (
        received[0]["schema_ref"] == "S1"
        and received[0]["objects"][0]["columns"][0]["field_ref"] == "S1:F1"
    )
    assert completed.steps[0].tool_result is not None
    assert completed.steps[0].tool_result.output == original and "schema_ref" not in original
    assert REPORT_FIELDS_KEY in completed.metadata


@pytest.mark.parametrize("origin", ["source", "derived"])
@pytest.mark.parametrize("with_data", [False, True])
def test_report_numeric_source_domain_is_separate_from_evidence(
    origin: str, with_data: bool
) -> None:
    from test_source_table_patches import fixture

    from app.features.agent import source_handles
    from app.features.agent.answer_data import DataReferenceError
    from app.features.agent.report_contract import AnswerPlan

    current, _, _, _ = fixture(with_data, "123")
    output = current.steps[0].tool_result.output
    output["evidence"][0]["origin"] = origin
    current.metadata[source_handles.REGISTRY_KEY] = []
    source_handles.register_output(current.metadata, "rag", output, tool="rag_retrieve_evidence")
    text = "| 出典 |\n|---|\n| [[cite:1]] |"
    plan = AnswerPlan.model_validate({"blocks": [{"kind": "narrative", "text": text}]})
    if with_data and origin == "derived":
        # 正式な引用でも、SQL数値検査の原文numeric-mask資格は広げない。
        with pytest.raises(DataReferenceError):
            builtin_runtime._render_report_body(current, plan, None)
    else:
        rendered = builtin_runtime._render_report_body(current, plan, None)
        assert rendered.evidence_text == "【根拠: 123】"


def test_report_ambiguous_numeric_source_is_not_resolved_by_short_number() -> None:
    from test_answer_evidence import _step
    from test_source_table_patches import fixture

    from app.features.agent import source_handles
    from app.features.agent.answer_data import DataReferenceError
    from app.features.agent.report_contract import AnswerPlan

    current, _, _, _ = fixture(True, "123")
    output = {"evidence": [{"document_id": "document-b", "chunk_id": "123", "excerpt": "原文"}]}
    current.steps.append(_step("rag_retrieve_evidence", output))
    source_handles.register_output(current.metadata, "rag", output, tool="rag_retrieve_evidence")
    plan = AnswerPlan.model_validate({"blocks": [{"kind": "narrative", "text": "[[cite:1]]"}]})
    with pytest.raises((DataReferenceError, source_handles.SourceHandleError)):
        builtin_runtime._render_report_body(current, plan, None)


def test_existing_grounding_retry_receives_frozen_body_instead_of_selector(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = create(repo, contract())
    seen: list[str] = []

    def needs(text: str, **kwargs: Any) -> bool:
        seen.append(text)
        return len(seen) == 1

    monkeypatch.setattr(tool_grounding, "grounding_tools", lambda _: {"synthetic"})
    monkeypatch.setattr(tool_grounding, "needs_grounding", needs)
    model = script(
        monkeypatch,
        [function_call("draft_report", json.dumps(draft("固定の本文です。")), call_id="draft-a")],
        [assistant_message('{"preview_id":"A1"}')],
        [assistant_message('{"preview_id":"A1"}')],
    )
    completed = run(repo, agent)
    assert completed.status == RunStatus.COMPLETED
    assert answer(completed) == "固定の本文です。"
    assert seen == ["固定の本文です。", "固定の本文です。"]
    assert len(model.calls) == 3 and len(completed.steps) == 1


def test_registry_limit_preserves_successful_raw_step_before_termination(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_report_fields import schema

    from app.features.agent.answer_data import DataReferenceError

    file = tmp_path / "snapshot.json"
    repository = AgentRuntimeRepository(file)
    agent = create(repository, contract())
    item = repository.create_builtin_run(RunCreateRequest(goal="質問", agent_id=agent))
    repository.begin_builtin_run(item.id)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise DataReferenceError("確認状況の保存上限です。")

    monkeypatch.setattr(runtime, "register_report_step", fail)
    output = schema(1)
    with pytest.raises(DataReferenceError, match="保存上限"):
        finish_tool(repository, item.id, "nl2sql__nl2sql_get_profile_schema", output, trusted=True)
    reloaded = AgentRuntimeRepository(file).get_run(item.id)
    assert len(reloaded.steps) == 1 and reloaded.steps[0].status.value == "completed"
    result = reloaded.steps[0].tool_result
    assert result is not None and result.success and result.output == output
    assert any(event.type.value == "tool.completed" for event in reloaded.events)


@pytest.mark.parametrize("budget", [None, AnswerBudget(soft_target=900, hard_max=1200)])
def test_report_model_instructions_match_typed_plan_and_trusted_budget(
    repo: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch, budget: AnswerBudget | None
) -> None:
    from app.features.agent.answer_data import DATA_INSTRUCTIONS
    from app.features.agent.report_runtime import REPORT_DATA_INSTRUCTIONS
    from app.features.agent.tools import ToolDefinition

    definition = ToolDefinition(
        name="nl2sql__nl2sql_query",
        description="合成結果の取得",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object"},
        mcp_server_id="nl2sql",
        mcp_tool_name="nl2sql_query",
    )
    monkeypatch.setattr(
        builtin_runtime,
        "discover_mcp_tools",
        lambda *args, **kwargs: ([(definition, lambda *a: {})], []),
    )
    script(monkeypatch)
    agent = create(repo, ReportOutputContract(budget=budget))
    item = repo.create_builtin_run(RunCreateRequest(goal="質問", agent_id=agent))
    common: dict[str, Any] = {
        "name": "構成検証",
        "instructions": "",
        "skill_ids": [],
        "model_id": "scripted",
    }
    legacy = builtin_runtime.build_sdk_agent(item.id, **common)
    report = builtin_runtime.build_sdk_agent(
        item.id, **common, output_contract=ReportOutputContract(budget=budget)
    )
    assert isinstance(legacy.instructions, str) and DATA_INSTRUCTIONS in legacy.instructions
    assert isinstance(report.instructions, str) and REPORT_DATA_INSTRUCTIONS in report.instructions
    assert DATA_INSTRUCTIONS not in report.instructions
    assert "[[table:" not in REPORT_DATA_INSTRUCTIONS
    tool = next(t for t in report.tools if t.name == "draft_report")
    assert isinstance(tool, FunctionTool)
    assert '"metric":"expanded_markdown_codepoints"' in tool.description
    assert ('"soft_target":900' if budget else '"soft_target":null') in tool.description
    assert ('"hard_max":1200' if budget else '"hard_max":null') in tool.description
    schema = tool.params_json_schema
    table = schema["$defs"]["TableBlock"]
    assert table["properties"]["table"]["pattern"] == r"^T[1-9]\d*$"
    assert {"table", "rows", "cols", "layout"}.issubset(table["properties"])
    assert "budget" not in schema["properties"]
    assert report.output_type is FinalSelection
    assert legacy.output_type is None
