"""原文参照表とSQLデータ表を、失敗した形式検証の層で区別する。"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from agents import Agent
from test_answer_data_patches import sdk_result
from test_answer_evidence import _step

from app.features.agent import source_handles
from app.features.agent.answer_data import (
    DataReferenceError,
    data_index,
    has_data_reference,
    register_data_result,
)
from app.features.agent.answer_data_patches import ReferencePatch, ReferencePatches, plan_patches
from app.features.agent.answer_evidence import selected_answer_evidence
from app.features.agent.answer_references import cited_chunk_ids


def fixture(with_data: bool, chunk: str = "chunk-a") -> tuple[Any, Any, Any, Any]:
    evidence: dict[str, Any] = {
        "evidence": [{"document_id": "document-a", "chunk_id": chunk, "excerpt": "合成の原文。"}]
    }
    source = _step("rag_retrieve_evidence", evidence)
    current = SimpleNamespace(id="run-synthetic", metadata={}, steps=[source])
    source_handles.register_output(current.metadata, "rag", evidence, tool="rag_retrieve_evidence")
    if with_data:
        output: dict[str, Any] = {"status": "done", "columns": ["VALUE"], "rows": [{"VALUE": 1}]}
        data = _step("nl2sql_query", output)
        current.steps.append(data)
        register_data_result(current.metadata, data.id, output)
    index = data_index(current.steps, current.metadata, trusted_sources=[chunk])

    def validate_source(text: str) -> str:
        resolved = source_handles.render_citations(
            current.metadata, text, available_sources={("document-a", chunk)}
        )
        selected_answer_evidence(current.steps, resolved)
        return resolved

    def references(text: str) -> frozenset[str]:
        resolved = validate_source(text)
        index.render(resolved)
        result: set[str] = set()
        if has_data_reference(text):
            result.add("data")
        if cited_chunk_ids(resolved, [chunk]):
            result.add("source")
        return frozenset(result)

    return current, index, validate_source, references


@pytest.mark.parametrize("chunk", ["chunk-a", "123", "document-a:123:7", " 123 "])
@pytest.mark.parametrize("with_data", [False, True])
@pytest.mark.parametrize("citation", ["[[cite:1]]", "【根拠: chunk-a】"])
def test_source_only_text_table_requires_only_actual_source(
    with_data: bool, citation: str, chunk: str
) -> None:
    current, index, source, references = fixture(with_data, chunk)
    before = deepcopy(current.metadata)
    bad = "| 項目 | 出典 |\n|---|---|\n| 合成の項目 | [[cite:99]] |\n"
    plan = plan_patches(bad, index.render, validate_source=source)
    assert len(plan.targets) == 1 and plan.targets[0].required_references == ("source",)
    citation = citation.replace("chunk-a", chunk)
    fixed = bad.replace("[[cite:99]]", citation)
    response = ReferencePatches(patches=[ReferencePatch(target_id="P1", replacement=fixed)])
    assert references(fixed) == frozenset({"source"})
    assert plan.apply(response, current_answer=bad, references=references) == fixed
    expected = source(fixed).rstrip("\n") if with_data else source(fixed)
    assert index.render(source(fixed)) == expected
    assert current.metadata == before


@pytest.mark.parametrize("replacement", ["", " \n", "説明だけ", "[[cell:T1:R1:C1]]"])
def test_source_only_table_still_rejects_deletion_and_data_only(replacement: str) -> None:
    _, index, source, references = fixture(True)
    bad = "| 項目 | 出典 |\n|---|---|\n| 合成の項目 | [[cite:99]] |\n"
    plan = plan_patches(bad, index.render, validate_source=source)
    with pytest.raises(DataReferenceError, match="取得済み参照"):
        plan.apply(
            ReferencePatches(patches=[ReferencePatch(target_id="P1", replacement=replacement)]),
            current_answer=bad,
            references=references,
        )


def test_bad_source_does_not_hide_independent_literal_data_error() -> None:
    _, index, source, references = fixture(True)
    bad = "| VALUE | 出典 |\n|---|---|\n| 99 | [[cite:99]] |\n"
    with pytest.raises(source_handles.SourceHandleError):
        source(bad)
    with pytest.raises(DataReferenceError):
        index.render(bad)
    plan = plan_patches(bad, index.render, validate_source=source)
    assert plan.targets[0].required_references == ("data", "source")
    source_only = "| 項目 | 出典 |\n|---|---|\n| 合成の項目 | [[cite:1]] |\n"
    assert references(source_only) == frozenset({"source"})
    with pytest.raises(DataReferenceError, match="取得済み参照"):
        plan.apply(
            ReferencePatches(patches=[ReferencePatch(target_id="P1", replacement=source_only)]),
            current_answer=bad,
            references=references,
        )
    both = "[[table:T1]]\n\n[[cite:1]]\n"
    assert (
        plan.apply(
            ReferencePatches(patches=[ReferencePatch(target_id="P1", replacement=both)]),
            current_answer=bad,
            references=references,
        )
        == both
    )


def test_literal_numeric_table_still_requires_data_not_just_source() -> None:
    _, index, source, references = fixture(True)
    bad = "| VALUE |\n|---|\n| 99 |\n"
    plan = plan_patches(bad, index.render, validate_source=source)
    assert plan.targets[0].required_references == ("data",)
    with pytest.raises(DataReferenceError, match="取得済み参照"):
        plan.apply(
            ReferencePatches(patches=[ReferencePatch(target_id="P1", replacement="[[cite:1]]")]),
            current_answer=bad,
            references=references,
        )
    assert (
        plan.apply(
            ReferencePatches(patches=[ReferencePatch(target_id="P1", replacement="[[table:T1]]")]),
            current_answer=bad,
            references=references,
        )
        == "[[table:T1]]"
    )


async def test_runtime_source_only_table_repairs_once_without_sql_or_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.agent import builtin_runtime as runtime
    from app.features.agent import tool_grounding
    from app.features.agent.runtime import runtime_repository

    current, index, source, references = fixture(False)
    assert not index.tables
    bad = "保持\n\n| 項目 | 出典 |\n|---|---|\n| 合成の項目 | [[cite:99]] |\n\n終了"
    original = sdk_result(bad)
    plan = plan_patches(bad, index.render, validate_source=source)
    target = plan.targets[0]
    fixed = target.text.replace("[[cite:99]]", "[[cite:1]]")
    calls: list[int] = []

    async def run_sdk(run_id: str, agent: Agent[Any], inputs: Any, *, context: Any) -> Any:
        calls.append(1)
        assert agent.tools == [] and agent.output_type is ReferencePatches
        result = sdk_result("patch")
        result.final_output = ReferencePatches(
            patches=[ReferencePatch(target_id="P1", replacement=fixed)]
        )
        return result

    def record(run_id: str, key: str, value: str) -> None:
        current.metadata[tool_grounding.METADATA_KEY] = {key: True}

    monkeypatch.setattr(runtime_repository, "get_run", lambda _: current)
    monkeypatch.setattr(runtime_repository, "record_tool_grounding", record)
    monkeypatch.setattr(runtime, "_run_sdk", run_sdk)
    result = await runtime._require_data_references(current, Agent(name="合成"), original, None)
    assert result.final_output == bad.replace("[[cite:99]]", "[[cite:1]]")
    assert references(result.final_output) == frozenset({"source"}) and len(calls) == 1
    assert len(current.steps) == 1


@pytest.mark.parametrize("trusted", [(), ("chunk-a",)])
def test_unknown_numeric_canonical_never_bypasses_literal_data_validation(
    trusted: tuple[str, ...],
) -> None:
    from app.features.agent.answer_data_tables import data_detection_text

    current, _, _, _ = fixture(True)
    index = data_index(current.steps, current.metadata, trusted_sources=trusted)
    fake = "| VALUE |\n|---|\n| 【根拠: 999】 |\n"
    assert data_detection_text("【根拠: 999】", trusted_sources=trusted) == "【根拠: 999】"
    with pytest.raises(DataReferenceError):
        index.render(fake)


def test_old_call_without_trusted_sources_keeps_numeric_canonical_strict() -> None:
    current, _, _, _ = fixture(True, "123")
    raw = "| 出典 |\n|---|\n| 【根拠: 123】 |\n"
    with pytest.raises(DataReferenceError):
        data_index(current.steps, current.metadata).render(raw)
    assert data_index(current.steps, current.metadata, trusted_sources=["123"]).render(
        raw
    ) == raw.rstrip("\n")


@pytest.mark.parametrize("malformed", ["[[cite:99 77", "[[cite:bad]] 77", "[[cite:99]] 77"])
def test_source_mask_never_swallows_literal_numbers_after_malformed_or_closed_cite(
    malformed: str,
) -> None:
    from app.features.agent.answer_data_tables import data_detection_text

    _, index, source, references = fixture(True)
    bad = f"| 項目 | 出典 |\n|---|---|\n| 合成 | {malformed} |\n"
    detected = data_detection_text(malformed, allow_unresolved_short=True)
    assert "77" in detected
    with pytest.raises(DataReferenceError):
        index.render(bad)
    plan = plan_patches(bad, index.render, validate_source=source)
    assert plan.targets[0].required_references == ("data", "source")
    source_only = "| 項目 | 出典 |\n|---|---|\n| 合成 | [[cite:1]] |\n"
    with pytest.raises(DataReferenceError, match="取得済み参照"):
        plan.apply(
            ReferencePatches(patches=[ReferencePatch(target_id="P1", replacement=source_only)]),
            current_answer=bad,
            references=references,
        )


@pytest.mark.parametrize(
    "mode", ["missing_registry", "noncitable", "ambiguous", "not_available", "derived"]
)
def test_runtime_trusted_sources_exclude_unconfirmed_or_ambiguous_registry_entries(
    mode: str,
) -> None:
    from app.features.agent.answer_evidence import passed_evidence
    from app.features.agent.builtin_runtime import _citable_source_ids

    current, _, _, _ = fixture(True, "123")
    assert _citable_source_ids(current) == frozenset({"123"})
    rows = current.metadata[source_handles.REGISTRY_KEY]
    chunk = next(row for row in rows if row["kind"] == "chunk")
    if mode == "missing_registry":
        current.metadata.pop(source_handles.REGISTRY_KEY)
    elif mode == "noncitable":
        chunk["citable"] = False
    elif mode == "ambiguous":
        rows.append({**chunk, "document": "document-b"})
    else:
        output = current.steps[0].tool_result.output
        if mode == "not_available":
            output["evidence"][0]["chunk_id"] = "other"
        else:
            output["evidence"][0]["origin"] = "derived"
            current.metadata[source_handles.REGISTRY_KEY] = []
            source_handles.register_output(
                current.metadata, "rag", output, tool="rag_retrieve_evidence"
            )
            assert any(
                item["chunk_id"] == "123" for item in passed_evidence(current.steps, limit=None)
            )
    trusted = _citable_source_ids(current)
    assert "123" not in trusted
    raw = "| 出典 |\n|---|\n| 【根拠: 123】 |\n"
    with pytest.raises(DataReferenceError):
        data_index(current.steps, current.metadata, trusted_sources=trusted).render(raw)
