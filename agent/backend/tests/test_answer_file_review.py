"""二つの RAG ツールの資料選択・再回答と即時の判定。"""

from typing import Any
from unittest.mock import Mock

import pytest
from pr_backend_core.file_review import ReviewAction

from app.features.agent import builtin_runtime, file_review
from app.features.agent.config import McpConnectionConfig
from app.features.agent.runtime import AgentProfile, RunFeedback, RunState, RunStep
from app.features.agent.tools import ToolCall, ToolResult


@pytest.mark.parametrize("tool", ["rag_chat", "rag_search"])
@pytest.mark.asyncio
async def test_rag_run_keeps_versions_and_does_not_rerun_other_actions(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def output(ids: str, trace: str) -> dict[str, Any]:
        return {
            "trace_id": trace,
            "answer": "根拠からの回答",
            "evidence": [
                {
                    "document_id": doc,
                    "chunk_id": doc + ":1",
                    "file_name": doc + ".txt",
                    "excerpt": "合成の根拠",
                    "score": 1.0,
                }
                for doc in ids
            ],
        }

    name = "rag__" + tool
    step = RunStep(
        id="task",
        run_id="run",
        status="completed",
        tool_call=ToolCall(name=name, arguments={"query": "質問"}),
        tool_result=ToolResult(name=name, success=True, output=output("AB", "first")),
    )
    run = RunState(id="run", agent_id="agent", goal="質問", status="completed", steps=[step])
    repo = Mock()
    content = {}

    def persist(run_id: str, revision: int, value: str) -> None:
        content[run_id] = value

    repo.read_file_review.side_effect = content.get
    repo.write_file_review.side_effect = persist
    repo.list_agents.return_value = [AgentProfile(id="agent", name="合成の Agent")]
    repo.record_review_outcome.return_value = run
    monkeypatch.setattr(file_review, "runtime_repository", repo)
    monkeypatch.setattr(file_review, "run_answer_text", lambda _: "初回の回答")
    monkeypatch.setattr(file_review, "unreadable_documents", lambda *args: set())
    monkeypatch.setattr(builtin_runtime, "_guarded_answer", lambda _, body: body)
    config = Mock()
    config.get_tool_policy.return_value = Mock(ask=[], deny=[])
    config.get_mcp.return_value = McpConnectionConfig(server_id="rag", auth_mode="service_token")
    monkeypatch.setattr(file_review, "runtime_config_store", config)
    calls = []

    class Client:
        def __init__(self, cfg: object, *, context: object) -> None:
            pass

        def call_tool(self, key: str, args: dict[str, Any], **kwargs: object) -> dict[str, Any]:
            assert key == tool
            selection = args["file_selection"]
            calls.append(selection)
            return output(
                "C" if selection["candidates_only"] else "".join(selection["include"]), "next"
            )

    monkeypatch.setattr(file_review, "McpConnectionClient", Client)
    await file_review.rate_version(run, "owner", "run", RunFeedback(rating="not_helpful"))
    assert not calls and repo.record_review_outcome.call_args.args[2] == "unresolved"
    state = await file_review.change_review(
        run, "owner", ReviewAction(revision=1, task="task", action="discover", selected=["A"])
    )
    assert len(state.versions) == 1 and calls[0]["candidates_only"]
    state = await file_review.change_review(
        run, "owner", ReviewAction(revision=2, task="task", action="answer", selected=["C"])
    )
    assert state.versions[-1].sources["task"].documents() == {"C": "C.txt"}
    assert calls[1]["include"] == ["C"] and set(calls[1]["exclude"]) == {"A", "B"}
    await file_review.rate_version(
        run, "owner", state.versions[-1].id, RunFeedback(rating="helpful")
    )
    assert repo.record_review_outcome.call_args.args[2] == "retried_resolved"
    assert len(calls) == 2
