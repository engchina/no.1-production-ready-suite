"""二つの RAG ツールの資料選択・再回答と即時の判定。"""

from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from mcp_support import RAG_OUTPUT_SCHEMA_VERSION, McpToolError, fake_product_mcp
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
    monkeypatch.setattr(file_review, "run_answer_notice", lambda _: "初回の注意書き")
    monkeypatch.setattr(file_review, "current_answer_notice", lambda: "新版の注意書き")
    monkeypatch.setattr(file_review, "unreadable_documents", lambda *args: set())
    monkeypatch.setattr(file_review, "selection_limit", lambda *args: 5)
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
    # RAG が返した上限をそのまま画面へ渡す（#5981）。
    assert state.selection_limit == 5
    state = await file_review.change_review(
        run, "owner", ReviewAction(revision=2, task="task", action="answer", selected=["C"])
    )
    assert [version.notice for version in state.versions] == ["初回の注意書き", "新版の注意書き"]
    assert state.versions[-1].sources["task"].documents() == {"C": "C.txt"}
    assert calls[1]["include"] == ["C"] and set(calls[1]["exclude"]) == {"A", "B"}
    await file_review.rate_version(
        run, "owner", state.versions[-1].id, RunFeedback(rating="helpful")
    )
    assert repo.record_review_outcome.call_args.args[2] == "retried_resolved"
    assert len(calls) == 2


def _evidence_output(documents: str, trace: str = "first") -> dict[str, Any]:
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
            for doc in documents
        ],
    }


def _review_run(
    monkeypatch: pytest.MonkeyPatch, documents: str, limit: int | None
) -> tuple[RunState, Mock, list[dict[str, Any]]]:
    """RAG の上限が ``limit``（None は読めない）のときの、``documents`` を根拠に持つ Run。"""
    name = "rag__rag_search"
    step = RunStep(
        id="task",
        run_id="run",
        status="completed",
        tool_call=ToolCall(name=name, arguments={"query": "質問"}),
        tool_result=ToolResult(name=name, success=True, output=_evidence_output(documents)),
    )
    run = RunState(id="run", agent_id="agent", goal="質問", status="completed", steps=[step])
    repo = Mock()
    repo.read_file_review.return_value = None
    repo.list_agents.return_value = [AgentProfile(id="agent", name="合成の Agent")]
    repo.record_review_outcome.return_value = run
    monkeypatch.setattr(file_review, "runtime_repository", repo)
    monkeypatch.setattr(file_review, "run_answer_text", lambda _: "初回の回答")
    monkeypatch.setattr(file_review, "run_answer_notice", lambda _: None)
    monkeypatch.setattr(file_review, "unreadable_documents", lambda *args: set())
    monkeypatch.setattr(file_review, "selection_limit", lambda *args: limit)
    config = Mock()
    config.get_tool_policy.return_value = Mock(ask=[], deny=[])
    config.get_mcp.return_value = McpConnectionConfig(server_id="rag", auth_mode="service_token")
    monkeypatch.setattr(file_review, "runtime_config_store", config)
    calls: list[dict[str, Any]] = []

    class Client:
        def __init__(self, cfg: object, *, context: object) -> None:
            pass

        def call_tool(self, key: str, args: dict[str, Any], **kwargs: object) -> dict[str, Any]:
            calls.append(args["file_selection"])
            return _evidence_output("Z", "next")

    monkeypatch.setattr(file_review, "McpConnectionClient", Client)
    return run, repo, calls


@pytest.mark.parametrize(
    ("action", "method"),
    [("answer", "only"), ("discover", "mix"), ("answer", None), ("discover", None)],
    ids=["only", "mix", "candidates-answer", "candidates-more"],
)
@pytest.mark.asyncio
async def test_selection_over_the_rag_limit_is_rejected_with_422(
    action: str, method: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RAG が返した上限を超える選択は 422 にし、再検索も保存もしない（#5981）。"""
    run, repo, calls = _review_run(monkeypatch, "ABCD", limit=3)
    assert (await file_review.read_review(run, "owner")).selection_limit == 3
    command = ReviewAction.model_validate(
        {
            "revision": 0,
            "task": "task",
            "action": action,
            "selected": list("ABCD"),
            "method": method,
        }
    )
    with pytest.raises(HTTPException) as error:
        await file_review.change_review(run, "owner", command)
    assert error.value.status_code == 422
    assert error.value.detail == "一度に選べる資料は 3 件までです。"
    assert calls == []
    repo.write_file_review.assert_not_called()


@pytest.mark.asyncio
async def test_replace_is_not_limited_and_returns_the_rag_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「別の資料を探す」は選んだ資料の数を確かめない（#5981）。"""
    run, repo, calls = _review_run(monkeypatch, "ABCD", limit=3)
    state = await file_review.change_review(
        run,
        "owner",
        ReviewAction(
            revision=0, task="task", action="discover", selected=list("ABCD"), method="replace"
        ),
    )
    assert state.selection_limit == 3
    assert state.kept["task"] == ["A", "B", "C", "D"]
    assert len(calls) == 1 and calls[0]["candidates_only"]
    repo.write_file_review.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_limit_stops_selection_but_not_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RAG から上限を読めないとき、状態は上限なし（None）で返し、選択を持ち越す操作は止める。"""
    run, repo, calls = _review_run(monkeypatch, "AB", limit=None)
    assert (await file_review.read_review(run, "owner")).selection_limit is None
    with pytest.raises(HTTPException) as error:
        await file_review.change_review(
            run,
            "owner",
            ReviewAction(revision=0, task="task", action="answer", selected=["A"], method="only"),
        )
    assert error.value.status_code == 502
    assert error.value.detail == "資料の再検索に失敗しました。"
    assert calls == []
    repo.write_file_review.assert_not_called()


def test_selection_limit_is_read_from_rag_as_the_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    """上限は RAG の内部のツールから、画面を見ている利用者として読む（Agent は設定を持たない）。"""
    rag = fake_product_mcp(
        monkeypatch,
        outputs={
            "rag_file_review_limits": {
                "schema_version": RAG_OUTPUT_SCHEMA_VERSION,
                "selection_limit": 7,
            }
        },
    )
    run = RunState(id="run", agent_id="agent", goal="質問", status="completed")
    assert file_review.selection_limit(run, "viewer-1") == 7
    (call,) = rag.calls_of("rag_file_review_limits")
    assert call["arguments"] == {}
    assert (call["claims"]["sub"], call["claims"]["purpose"]) == ("viewer-1", "file_review")
    # 読めない・形の違う値は None（別の値で補わない）。
    rag.outputs["rag_file_review_limits"] = McpToolError("unavailable", "失敗", status=503)
    assert file_review.selection_limit(run, "viewer-1") is None
    rag.outputs["rag_file_review_limits"] = {
        "schema_version": RAG_OUTPUT_SCHEMA_VERSION,
        "selection_limit": 0,
    }
    assert file_review.selection_limit(run, "viewer-1") is None
