"""読み取りで追加した source を短引用・成果物・原文照合で同じ集合として扱う。"""

from copy import deepcopy
from typing import Any

import pytest

from app.features.agent import source_handles as handles
from app.features.agent.answer_data import DataReferenceError
from app.features.agent.answer_evidence import MAX_ANSWER_EVIDENCE, answer_evidence, passed_evidence
from app.features.agent.answer_references import evidence_items, evidence_refs
from app.features.agent.runtime import RunStep, StepStatus
from app.features.agent.tools import ToolCall, ToolResult


def step(
    tool: str, output: dict[str, Any], *, connection: str = "rag", success: bool = True
) -> RunStep:
    name = f"{connection}__{tool}"
    return RunStep(
        run_id="run-synthetic",
        status=StepStatus.COMPLETED if success else StepStatus.FAILED,
        tool_call=ToolCall(name=name, arguments={}),
        tool_result=ToolResult(name=name, success=success, output=output),
    )


def read_output() -> dict[str, Any]:
    body = "合成の項目 A。\n合成の項目 B。"
    split = body.index("合成の項目 B")
    return {
        "document_id": "document-a",
        "file_name": "source-a.txt",
        "text": body,
        "chunks": [
            {"chunk_id": "chunk-a", "start": 0, "end": split, "page_start": 1, "page_end": 1},
            {"chunk_id": "chunk-b", "start": split, "end": len(body), "section_path": ["合成の節"]},
        ],
    }


@pytest.mark.parametrize("tool", ["rag_read_document", "rag_read_section", "rag_read_source"])
def test_read_sources_are_citable_and_present_in_final_items_and_markers(tool: str) -> None:
    retrieval = {
        "evidence": [
            {
                "document_id": "document-a",
                "chunk_id": "chunk-a",
                "excerpt": "合成の項目 A。",
                "score": 0.9,
            }
        ]
    }
    output = read_output()
    if tool == "rag_read_source":
        output = {
            "document_id": "document-a",
            "chunk_id": "chunk-b",
            "file_name": "source-a.txt",
            "text": "合成の項目 B。",
            "locator": {"page_start": 2},
        }
    original = deepcopy(output)
    metadata: dict[str, Any] = {}
    handles.register_output(metadata, "rag", retrieval, tool="rag_retrieve_evidence")
    handles.register_output(metadata, "rag", output, tool=tool)
    encoded = handles.encode_output(metadata, "rag", output)
    marker = encoded["citation"] if tool == "rag_read_source" else encoded["chunks"][1]["citation"]
    resolved = handles.render_citations(metadata, "合成の項目 B。" + marker)
    steps = [step("rag_retrieve_evidence", retrieval), step(tool, output)]
    content = answer_evidence(steps, resolved)
    assert [item["chunk_id"] for item in content["items"]] == ["chunk-a", "chunk-b"]
    assert content["items"][0]["score"] == 0.9
    added = content["items"][1]
    assert added["excerpt"] == "合成の項目 B。" and added["cited"]
    assert content["markers"] == [{"text": "【根拠: chunk-b】", "chunk_ids": ["chunk-b"]}]
    assert output == original
    assert {ref for ref, *_ in evidence_refs(tool, output)} <= {
        (item["document_id"], item["chunk_id"]) for item in passed_evidence(steps)
    }


@pytest.mark.parametrize(
    "bounds", [(-1, 2), (0, 999), (2, 1), (True, 2), (0, False), (0, 0), ("0", 2)]
)
def test_invalid_read_ranges_neither_register_citations_nor_enter_artifacts(
    bounds: tuple[Any, Any],
) -> None:
    output = read_output()
    output["chunks"] = [{"chunk_id": "invalid-chunk", "start": bounds[0], "end": bounds[1]}]
    assert evidence_items("rag_read_document", output) == []
    assert passed_evidence([step("rag_read_document", output)]) == []
    metadata: dict[str, Any] = {}
    handles.register_output(metadata, "rag", output, tool="rag_read_document")
    assert not any(item.get("citable") for item in metadata[handles.REGISTRY_KEY])


def test_read_child_cannot_change_its_document_identity() -> None:
    output = read_output()
    output["chunks"][1]["document_id"] = "different-document"
    assert [item["chunk_id"] for item in evidence_items("rag_read_document", output)] == ["chunk-a"]


def test_failed_or_unfinished_read_never_becomes_evidence() -> None:
    failed = step("rag_read_document", read_output(), success=False)
    unfinished = step("rag_read_document", read_output())
    unfinished.status = StepStatus.RUNNING
    assert passed_evidence([failed, unfinished]) == []


@pytest.mark.parametrize("different_connection", [False, True])
def test_colliding_chunk_ids_do_not_resolve_to_another_source(different_connection: bool) -> None:
    metadata: dict[str, Any] = {}
    output = {"document_id": "document-a", "chunk_id": "shared-chunk", "text": "合成の本文 A。"}
    handles.register_output(metadata, "rag", output, tool="rag_read_source")
    other = {**output, "document_id": "document-a" if different_connection else "document-b"}
    handles.register_output(
        metadata, "other" if different_connection else "rag", other, tool="rag_read_source"
    )
    with pytest.raises(handles.SourceHandleError):
        handles.render_citations(metadata, "[[cite:1]]")
    if not different_connection:
        with pytest.raises(DataReferenceError):
            answer_evidence(
                [step("rag_read_source", output), step("rag_read_source", other)],
                "【根拠: shared-chunk】",
            )


def many_sources() -> list[RunStep]:
    return [
        step(
            "rag_retrieve_evidence",
            {
                "evidence": [
                    {
                        "document_id": "document-a",
                        "chunk_id": f"chunk-{i:04d}",
                        "excerpt": f"合成の項目 {i}。",
                    }
                    for i in range(MAX_ANSWER_EVIDENCE + 2)
                ]
            },
        )
    ]


def test_late_explicit_citation_survives_the_evidence_limit_in_original_order() -> None:
    content = answer_evidence(many_sources(), f"【根拠: chunk-{MAX_ANSWER_EVIDENCE + 1:04d}】")
    assert len(content["items"]) == MAX_ANSWER_EVIDENCE
    assert content["items"][-1]["chunk_id"] == f"chunk-{MAX_ANSWER_EVIDENCE + 1:04d}"
    assert content["items"][0]["chunk_id"] == "chunk-0000"
    assert content["markers"][-1]["chunk_ids"] == [f"chunk-{MAX_ANSWER_EVIDENCE + 1:04d}"]


def test_explicit_citations_alone_over_the_limit_fail_instead_of_losing_sources() -> None:
    answer = "\n".join(f"【根拠: chunk-{i:04d}】" for i in range(MAX_ANSWER_EVIDENCE + 1))
    with pytest.raises(DataReferenceError, match="上限"):
        answer_evidence(many_sources(), answer)


async def test_evidence_overflow_uses_only_one_tool_free_format_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from agents import Agent
    from agents.usage import Usage

    from app.features.agent import builtin_runtime as runtime
    from app.features.agent import tool_grounding
    from app.features.agent.runtime import runtime_repository

    answer = "\n".join(f"【根拠: chunk-{i:04d}】" for i in range(MAX_ANSWER_EVIDENCE + 1))
    current = SimpleNamespace(id="run-synthetic", metadata={}, steps=many_sources())
    result = SimpleNamespace(
        final_output=answer,
        interruptions=[],
        context_wrapper=SimpleNamespace(context={}, usage=Usage()),
        to_input_list=lambda: [{"role": "assistant", "content": answer}],
    )
    repaired = SimpleNamespace(final_output="【根拠: chunk-0000】")
    calls = []

    async def run_sdk(run_id: str, agent: Agent[Any], inputs: Any, *, context: Any) -> Any:
        calls.append((run_id, agent.tools, inputs))
        return repaired

    async def approvals(run: Any, agent: Any, value: Any, scopes: Any) -> Any:
        return value

    def record(run_id: str, key: str, value: str) -> None:
        current.metadata[tool_grounding.METADATA_KEY] = {key: True}

    monkeypatch.setattr(runtime_repository, "get_run", lambda _: current)
    monkeypatch.setattr(runtime_repository, "record_tool_grounding", record)
    monkeypatch.setattr(runtime, "_run_sdk", run_sdk)
    monkeypatch.setattr(runtime, "_unattended_approvals", approvals)
    assert (
        await runtime._require_data_references(current, Agent(name="合成の検証"), result, None)
        is repaired
    )
    assert len(calls) == 1 and calls[0][1] == []
    assert (
        await runtime._require_data_references(current, Agent(name="合成の検証"), result, None)
        is result
    )
    assert len(calls) == 1


def test_old_citable_metadata_cannot_replace_missing_actual_source_output() -> None:
    from types import SimpleNamespace

    from app.features.agent.builtin_runtime import _render_source_citations

    metadata: dict[str, Any] = {}
    output = {"document_id": "document-a", "chunk_id": "chunk-a", "text": "合成の本文。"}
    handles.register_output(metadata, "rag", output, tool="rag_read_source")
    current = SimpleNamespace(metadata=metadata, steps=[])
    with pytest.raises(handles.SourceHandleError, match="実行結果"):
        _render_source_citations(current, "[[cite:1]]")
    invalid_read = read_output()
    invalid_read["chunks"] = [{"chunk_id": "chunk-a", "start": -1, "end": 3}]
    current.steps = [step("rag_read_document", invalid_read)]
    with pytest.raises(handles.SourceHandleError, match="実行結果"):
        _render_source_citations(current, "[[cite:1]]")
    current.steps = [step("rag_read_source", output)]
    assert _render_source_citations(current, "[[cite:1]]") == "【根拠: chunk-a】"


@pytest.mark.parametrize("tool", ["rag_read_document", "rag_read_section"])
def test_cursor_body_offsets_and_repeated_header_do_not_invent_original_chunk_ranges(
    tool: str,
) -> None:
    from app.features.agent import mcp_server

    prefix, header, partial = "別の取得片。\n", "列A | 列B\n", "値A | 値B\n"
    output = {
        "document_id": "document-a",
        "file_name": "source-a.txt",
        "text": prefix + header + partial,
        "next_cursor": "opaque-next-cursor",
        "chunks": [
            {
                "chunk_id": "chunk-partial",
                "start": len(prefix),
                "end": len(prefix + header + partial),
                "sheet_name": "sheet-a",
                "row_start": 20,
                "row_end": 21,
                "header_range": {"start": 0, "end": len(header)},
            }
        ],
    }
    [item] = evidence_items(tool, output)
    assert item["excerpt"] == header + partial
    assert item["truncated"] is True
    assert item["header_range"] == {"start": 0, "end": len(header)}
    assert item["locator"]["row_start"] == 20
    assert not {"text_length", "excerpt_range", "text_omitted", "next_offset"} & item.keys()
    [source], _omitted = mcp_server._fit_sources([item], queries=[], reserved_tokens=0)
    assert source.excerpt == header + partial and source.truncated
    assert (
        source.text_length is None and source.excerpt_range is None and source.next_offset is None
    )
    assert source.header_range is not None and source.header_range.end == len(header)
