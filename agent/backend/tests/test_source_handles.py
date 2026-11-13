"""根拠の短参照は原文を書き換えず、同じ取得結果の実IDへ戻る。"""

import json
from copy import deepcopy
from typing import Any

import pytest

from app.features.agent import source_handles as handles
from app.features.agent.context_window import OLDER_MAX_LEVEL, compact_output


def evidence(document: str = "doc-a", chunk: str = "doc-a:set-a:1") -> dict[str, Any]:
    return {
        "document_id": document,
        "chunk_id": chunk,
        "evidence_id": chunk,
        "excerpt": "原文の doc-a:set-a:1 は書き換えない。",
        "file_name": "合成資料.txt",
        "section": {"section_id": "section-a", "title": "合成の章"},
    }


def test_roundtrip_original_text_repeat_and_versions() -> None:
    import json

    output = {"evidence": [evidence(), evidence(chunk="doc-a:set-b:1")]}
    original = deepcopy(output)
    metadata: dict[str, Any] = {}
    handles.register_output(metadata, "rag", output, tool="rag_retrieve_evidence")
    encoded = handles.encode_output(metadata, "rag", output)
    first, second = encoded["evidence"]
    assert first["document_id"] == second["document_id"] == "doc://1"
    assert first["chunk_id"] == first["evidence_id"] == "chunk://1"
    assert second["chunk_id"] == "chunk://2"
    assert first["excerpt"] == original["evidence"][0]["excerpt"]
    assert first["citation"] == "[[cite:1]]"
    assert output == original
    before = deepcopy(metadata)
    handles.register_output(metadata, "rag", output, tool="rag_retrieve_evidence")
    assert metadata == before
    restored = json.loads(json.dumps(metadata))
    assert handles.decode_arguments(
        restored,
        "rag",
        {
            "document_id": "doc://1",
            "chunk_id": "chunk://2",
        },
    ) == {"document_id": "doc-a", "chunk_id": "doc-a:set-b:1"}
    assert handles.render_citations(restored, "根拠 [[cite:2]]") == "根拠 【根拠: doc-a:set-b:1】"
    # 圧縮が本文を省略しても、番号と引用参照はそのまま。
    compacted = json.loads(compact_output(json.dumps(encoded), OLDER_MAX_LEVEL))
    assert compacted["evidence"][0]["citation"] == "[[cite:1]]"


@pytest.mark.parametrize(
    "arguments",
    [
        {"document_id": "doc://99"},
        {"document_id": "doc://1", "chunk_id": "chunk://99"},
        {"document_id": "doc-b", "chunk_id": "chunk://1"},
        {"document_id": "doc://1", "section_id": "doc://1"},
    ],
)
def test_unknown_and_mismatched_handle_never_becomes_an_id(arguments: dict[str, Any]) -> None:
    metadata: dict[str, Any] = {}
    handles.register_output(
        metadata, "rag", {"evidence": [evidence()]}, tool="rag_retrieve_evidence"
    )
    with pytest.raises(handles.SourceHandleError):
        handles.decode_arguments(metadata, "rag", arguments)
    with pytest.raises(handles.SourceHandleError):
        handles.decode_arguments(metadata, "other", {"document_id": "doc://1"})


@pytest.mark.parametrize("answer", ["[[cite:0]]", "[[cite:99]]", "[[cite:bad]]"])
def test_unknown_citation_fails_closed(answer: str) -> None:
    with pytest.raises(handles.SourceHandleError):
        handles.render_citations({}, answer)


def test_a_link_is_readable_but_not_citable_until_its_source_is_returned() -> None:
    metadata: dict[str, Any] = {}
    output = {
        "document_id": "doc-a",
        "chunk_id": "doc-a:set-a:1",
        "text": "合成原文。",
        "references": [{"document_id": "doc-b", "chunk_id": "doc-b:set-b:1"}],
    }
    handles.register_output(metadata, "rag", output, tool="rag_read_source")
    encoded = handles.encode_output(metadata, "rag", output)
    assert encoded["citation"] == "[[cite:1]]"
    assert "citation" not in encoded["references"][0]
    with pytest.raises(handles.SourceHandleError):
        handles.render_citations(metadata, "[[cite:2]]")
    linked = {"document_id": "doc-b", "chunk_id": "doc-b:set-b:1", "text": "リンク先の原文。"}
    handles.register_output(metadata, "rag", linked, tool="rag_read_source")
    assert handles.encode_output(metadata, "rag", linked)["citation"] == "[[cite:2]]"
    assert handles.render_citations(metadata, "[[cite:2]]") == "【根拠: doc-b:set-b:1】"


@pytest.mark.parametrize("approval", [False, True])
@pytest.mark.parametrize("invalid_replies", [0, 1, 2])
def test_runtime_decodes_before_mcp_and_retains_real_source_after_approval(
    monkeypatch: pytest.MonkeyPatch, approval: bool, invalid_replies: int
) -> None:
    import anyio
    from agents.testing import assistant_message, function_call
    from answer_run_fixtures import USER_UUID, _script
    from mcp_support import fake_product_mcp

    from app.features.agent import builtin_runtime
    from app.features.agent.config import runtime_config_store
    from app.features.agent.runtime import (
        AgentProfile,
        ApprovalDecisionRequest,
        RunCreateRequest,
        run_answer_text,
        runtime_repository,
    )
    from app.features.agent.skills import AgentSkillDefinition, SkillMcpRequirement, skill_registry

    fake = fake_product_mcp(monkeypatch)
    found = fake.outputs["rag_retrieve_evidence"]
    assert isinstance(found, dict)
    original = found["evidence"][0]
    doc, chunk = original["document_id"], original["chunk_id"]
    read = fake.outputs["rag_read_source"]
    assert isinstance(read, dict)
    fake.outputs["rag_read_source"] = {
        **read,
        "document_id": doc,
        "chunk_id": chunk,
        "evidence_id": chunk,
        "text": "合成の原文です。",
    }
    skill_id, agent_id = "test-source-handle-skill", "test-source-handle-agent"
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=skill_id,
            name="合成検証",
            instructions="取得して原文を読んで回答する。",
            mcp_requirements=[
                SkillMcpRequirement(
                    server_id="rag",
                    tool_names=["rag_retrieve_evidence", "rag_read_source"],
                )
            ],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(id=agent_id, name="合成検証", skill_ids=[skill_id])
    )
    original_policy = runtime_config_store.get_tool_policy()
    if approval:
        runtime_config_store.patch_tool_policy(allow=[], ask=["rag__rag_read_source"])
    answers = [[assistant_message("合成の原文です。 [[cite:1]]")]]
    if invalid_replies:
        replacement = "[[cite:1]]" if invalid_replies == 1 else "[[cite:99]]"
        last = json.dumps({"patches": [{"target_id": "P1", "replacement": replacement}]})
        answers = [[assistant_message("根拠 [[cite:99]]")], [assistant_message(last)]]
    model = _script(
        monkeypatch,
        [function_call("rag__rag_retrieve_evidence", {"query": "合成質問"}, call_id="get")],
        [
            function_call(
                "rag__rag_read_source",
                {"document_id": "doc://99", "chunk_id": "chunk://1"},
                call_id="bad",
            )
        ],
        [
            function_call(
                "rag__rag_read_source",
                {"document_id": "doc://1", "chunk_id": "chunk://1"},
                call_id="read",
            )
        ],
        *answers,
    )
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="合成資料を確認して回答", agent_id=agent_id),
        created_by_user_uuid=USER_UUID,
    )
    try:
        anyio.run(builtin_runtime.execute_run, run.id)
        if approval:
            waiting = runtime_repository.get_run(run.id)
            assert waiting.status.value == "waiting_approval"
            assert not fake.calls_of("rag_read_source")
            saved = deepcopy(waiting.metadata[handles.REGISTRY_KEY])
            item = waiting.approvals[0]
            assert item.tool_call.arguments["document_id"] == doc
            assert item.tool_call.arguments["chunk_id"] == chunk
            runtime_repository.decide_approval(
                item.id,
                ApprovalDecisionRequest(
                    approved=True,
                    decided_by="synthetic-approver",
                ),
            )
            anyio.run(builtin_runtime.resume_run, run.id)
            assert runtime_repository.get_run(run.id).metadata[handles.REGISTRY_KEY] == saved
        actual = runtime_repository.get_run(run.id)
        assert len(fake.calls_of("rag_read_source")) == 1
        if invalid_replies:
            assert not model.calls[-1].tools
        if invalid_replies == 2:
            assert actual.status.value == "failed"
            assert actual.events[-1].payload["error_code"] == "runtime.answer_source_invalid"
            assert run_answer_text(actual) is None
            model.assert_complete()
            return
        assert actual.status.value == "completed", actual.events[-1].model_dump_json()
        step = next(s for s in actual.steps if s.tool_call and s.tool_call.trace_id == "read")
        assert step.tool_call is not None
        assert step.tool_call.arguments["document_id"] == doc
        assert step.tool_call.arguments["chunk_id"] == chunk
        answer = run_answer_text(actual)
        assert answer and chunk in answer and "[[cite:" not in answer
        artifact = next(a for a in actual.artifacts if a.kind == "answer_evidence")
        cited = [item for item in artifact.content["items"] if item.get("cited")]
        assert any(item["document_id"] == doc and item["chunk_id"] == chunk for item in cited)
        assert "source_handle_invalid" in str(model.calls[2].input)
        assert "chunk://1" in str(model.calls[1].input)
        model.assert_complete()
    finally:
        runtime_config_store.patch_tool_policy(
            allow=list(original_policy.allow),
            ask=list(original_policy.ask),
            deny=list(original_policy.deny),
        )
        runtime_repository.delete_agent(agent_id)
        skill_registry.remove(skill_id)
