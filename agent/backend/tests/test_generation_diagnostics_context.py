"""修正で失われた列名と候補を、MCP・圧縮・保存の境界で保つ。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from app.features.agent.context_window import (
    ContextBudget,
    ContextLimits,
    compact_items,
    compact_output,
)
from app.features.agent.generation_diagnostics import generation_diagnostics_summary
from app.features.agent.tools import parse_mcp_tool_result


def _record() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "recorded",
        "unavailable_reason": None,
        "repairs": [
            {
                "rejected_attempt": 1,
                "accepted_attempt": 2,
                "reason_codes": ["SQL_COLUMN_BINDING_INVALID"],
                "binding_diagnostics": [
                    {
                        "source": "schema_catalog",
                        "reference": {"qualifier": "A", "column": "Value_Diff", "quoted": True},
                        "exact_candidates": [
                            {
                                "owner": "Owner_A",
                                "table": "Table_B",
                                "column": "Value_Diff",
                                "sql_identifier": '"Owner_A"."Table_B"."Value_Diff"',
                            }
                        ],
                        "truncated": False,
                    }
                ],
                "projection_changes": {
                    "status": "compared",
                    "unavailable_reason": None,
                    "removed": [
                        {"ordinal": 2, "output_name": "差分 Alias", "expression_kind": "Column"}
                    ],
                    "added": [],
                    "changed": [],
                    "truncated": False,
                },
            }
        ],
        "truncated": False,
    }


@pytest.mark.parametrize("level", [1, 2, 3, 4])
@pytest.mark.parametrize("nested", [False, True])
def test_mcp_diagnostics_survive_compaction_checkpoint_resume(level: int, nested: bool) -> None:
    diagnostics = _record()
    body: dict[str, Any] = {"generation_diagnostics": diagnostics, "explanation": "x" * 20_000}
    wire = {"result": body} if nested else body
    result = parse_mcp_tool_result(
        {"structuredContent": wire, "content": [{"type": "text", "text": json.dumps(wire)}]},
        server_id="nl2sql",
        tool_name="nl2sql_get_job",
    )
    assert result.output == wire
    text = json.dumps(result.output, ensure_ascii=False)
    compacted = json.loads(compact_output(text, level))
    restored = json.loads(json.dumps(compacted, ensure_ascii=False))
    restored_text = compact_output(json.dumps(restored, ensure_ascii=False), 4)
    after = json.loads(restored_text)
    assert (after["result"] if nested else after)["generation_diagnostics"] == diagnostics
    assert wire == ({"result": body} if nested else body)
    assert len(restored_text) < len(text)


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_unavailable_comparison_is_not_changed_into_no_changes(level: int) -> None:
    record = _record()
    record["repairs"][0]["projection_changes"].update(
        status="unavailable", unavailable_reason="projection_not_resolved", removed=[]
    )
    output = json.dumps({"generation_diagnostics": record, "text": "x" * 20_000})
    assert json.loads(compact_output(output, level))["generation_diagnostics"] == record


@pytest.mark.parametrize("value", [None, [], {}, {"schema_version": 2}, "private"])
def test_invalid_or_old_metadata_is_not_invented(value: Any) -> None:
    assert generation_diagnostics_summary(value) is None


@pytest.mark.parametrize("field,value", [("column", "x" * 129), ("column", 2), ("quoted", "yes")])
def test_invalid_identifier_or_type_is_not_truncated_into_false_identity(
    field: str, value: Any
) -> None:
    record = _record()
    record["repairs"][0]["binding_diagnostics"][0]["reference"][field] = value
    assert generation_diagnostics_summary(record) is None


@pytest.mark.parametrize("field,value", [("schema_version", True), ("status", "unknown")])
def test_invalid_version_or_status_is_rejected(field: str, value: Any) -> None:
    record = _record()
    record[field] = value
    assert generation_diagnostics_summary(record) is None


def test_extras_do_not_retain_raw_sql_or_unbounded_engine_metadata() -> None:
    record = _record()
    record["raw_sql"] = "private"
    record["repairs"][0]["engine_meta"] = {"text": "private"}
    summary = generation_diagnostics_summary(record)
    assert summary == _record()


def test_limits_are_marked_and_names_remain_exact() -> None:
    record = _record()
    repair = record["repairs"][0]
    repair["projection_changes"]["removed"] *= 17
    repair["binding_diagnostics"][0]["exact_candidates"] *= 17
    record["repairs"] *= 2
    summary = generation_diagnostics_summary(record)
    assert summary is not None and summary["truncated"] is True
    assert len(summary["repairs"]) == 1
    reduced = summary["repairs"][0]
    assert len(reduced["projection_changes"]["removed"]) == 16
    assert reduced["projection_changes"]["truncated"] is True
    assert reduced["projection_changes"]["removed"][0]["output_name"] == "差分 Alias"
    assert len(reduced["binding_diagnostics"][0]["exact_candidates"]) == 16
    assert reduced["binding_diagnostics"][0]["truncated"] is True


def test_total_budget_prunes_candidates_before_removed_names_without_mutating_source() -> None:
    record = _record()
    candidate = {
        "owner": "O" * 128,
        "table": "T" * 128,
        "column": "C" * 128,
        "sql_identifier": "I" * 512,
    }
    record["repairs"][0]["binding_diagnostics"][0]["exact_candidates"] = [candidate] * 16
    before = deepcopy(record)
    summary = generation_diagnostics_summary(record)
    assert summary is not None
    assert len(json.dumps(summary, ensure_ascii=False, separators=(",", ":"))) <= 12_000
    assert summary["truncated"] is True
    assert (
        summary["repairs"][0]["projection_changes"]["removed"]
        == before["repairs"][0]["projection_changes"]["removed"]
    )
    assert record == before


def test_oversized_or_cyclic_records_are_dropped() -> None:
    record = _record()
    record["extra"] = "x" * 48_001
    assert generation_diagnostics_summary(record) is None
    record["extra"] = record
    assert generation_diagnostics_summary(record) is None


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("recorded", [False, True])
def test_output_version_and_absent_old_job_record_do_not_change_passthrough(
    version: int, recorded: bool
) -> None:
    body: dict[str, Any] = {"output_schema_version": version, "status": "done"}
    if recorded:
        body["generation_diagnostics"] = _record()
    original = deepcopy(body)
    result = parse_mcp_tool_result(
        {"structuredContent": body}, server_id="nl2sql", tool_name="nl2sql_get_job"
    )
    assert result.output == original
    if not recorded:
        assert "generation_diagnostics" not in result.output


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_invalid_saved_record_remains_explicitly_unavailable(level: int) -> None:
    record = {
        "schema_version": 1,
        "status": "unavailable",
        "unavailable_reason": "invalid_saved_diagnostics",
        "repairs": [],
        "truncated": False,
    }
    body = {"generation_diagnostics": record, "text": "x" * 20_000}
    compacted = json.loads(compact_output(json.dumps(body), level))
    assert compacted["generation_diagnostics"] == record


def test_preserved_diagnostics_still_count_against_total_context_budget() -> None:
    body = {"generation_diagnostics": _record(), "text": "x" * 20_000}
    items = [
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": json.dumps(body, ensure_ascii=False),
        }
    ]
    original = deepcopy(items)
    budget = ContextBudget(
        ContextLimits(model_id="m", max_input_tokens=64, max_output_tokens=16),
        margin_ratio=0,
    )
    compacted = compact_items(items, budget=budget, fixed_text="")
    assert not compacted.fits
    assert json.loads(compacted.items[0]["output"])["generation_diagnostics"] == _record()
    assert items == original


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_tail_job_records_keep_diagnostics_when_other_fields_become_ids(level: int) -> None:
    jobs: list[dict[str, Any]] = [{"job_id": f"j{i}", "text": "x" * 1000} for i in range(20)]
    jobs[-1]["generation_diagnostics"] = _record()
    body = {"jobs": jobs}
    compacted = json.loads(compact_output(json.dumps(body, ensure_ascii=False), level))
    assert compacted["jobs"][-1]["generation_diagnostics"] == _record()


@pytest.mark.parametrize("field", ["output_name", "qualifier"])
def test_empty_identifier_does_not_become_a_valid_saved_record(field: str) -> None:
    record = _record()
    repair = record["repairs"][0]
    if field == "output_name":
        repair["projection_changes"]["removed"][0][field] = ""
    else:
        repair["binding_diagnostics"][0]["reference"][field] = ""
    assert generation_diagnostics_summary(record) is None
