"""schema MCP の範囲、非データ性、有界な圧縮を検証する。"""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from app.features.agent.answer_data import model_data_references, register_data_result
from app.features.agent.config import McpConnectionConfig
from app.features.agent.context_window import (
    ContextBudget,
    ContextLimits,
    compact_items,
    compact_output,
)
from app.features.agent.data_scope import (
    AgentDataScope,
    enforce,
    scoped_input_schema,
    token_profile_scopes,
)
from app.features.agent.profile_schema_context import profile_schema_summary
from app.features.agent.tools import ExternalMcpToolInfo, mcp_tool_definition, parse_mcp_tool_result

TOOL = "nl2sql__nl2sql_get_profile_schema"


def _schema(count: int = 2) -> dict[str, Any]:
    return {
        "schema_version": 4,
        "profile_id": "profile-a",
        "source": "cached",
        "status": "available",
        "unavailable_reason": None,
        "refreshed_at": "snapshot-a",
        "objects": [
            {
                "owner": "Owner_A",
                "name": "Table_A",
                "object_type": "table",
                "logical_name": "Object A",
                "description": "cached description",
                "description_truncated": False,
                "columns": [
                    {
                        "name": f"Column_{i}",
                        "data_type": "NUMBER",
                        "nullable": False,
                        "logical_name": f"Name {i}",
                        "description": "column description",
                        "description_truncated": False,
                    }
                    for i in range(count)
                ],
                "columns_complete": True,
            }
        ],
        "page_complete": True,
        "next_cursor": None,
        "truncated": False,
        "total_objects": 1,
        "total_columns": count,
    }


@pytest.mark.parametrize("supplied", [None, "profile-a", "outside"])
def test_single_profile_is_forced_before_schema_tool_delivery(supplied: str | None) -> None:
    schema = {
        "type": "object",
        "properties": {"profile_id": {"type": "string"}},
        "required": ["profile_id"],
    }
    scopes = {"nl2sql": AgentDataScope(profile_ids=["profile-a"])}
    assert "profile_id" not in scoped_input_schema(TOOL, schema, scopes)["properties"]
    arguments = {} if supplied is None else {"profile_id": supplied}
    result = enforce(TOOL, arguments, scopes)
    assert result is not None and result.error is None
    assert result.arguments["profile_id"] == "profile-a"


@pytest.mark.parametrize("supplied,valid", [(None, True), ("profile-b", True), ("outside", False)])
def test_multiple_profile_scope_does_not_expand(supplied: str | None, valid: bool) -> None:
    scopes = {
        "nl2sql": AgentDataScope(
            profile_ids=["profile-a", "profile-b"], default_profile_id="profile-a"
        )
    }
    result = enforce(TOOL, {} if supplied is None else {"profile_id": supplied}, scopes)
    assert result is not None
    assert (result.error is None) == valid
    assert scopes["nl2sql"].profile_ids == ["profile-a", "profile-b"]


def test_schema_tool_is_trusted_read_only_and_never_auto_approves_writes() -> None:
    config = McpConnectionConfig(
        server_id="nl2sql", source="builtin", base_url="http://nl2sql.test/api/mcp"
    )
    tool = ExternalMcpToolInfo(
        name="nl2sql_get_profile_schema", server_id="nl2sql", read_only=True, idempotent=True
    )
    definition = mcp_tool_definition(config, tool)
    assert definition.trusted_read_only
    assert not definition.side_effects
    assert not definition.standard_auto_approve


@pytest.mark.parametrize("level", [1, 2, 3, 4])
@pytest.mark.parametrize("nested", [False, True])
def test_names_and_page_state_survive_passthrough_compaction_and_restore(
    level: int, nested: bool
) -> None:
    schema = _schema()
    schema.update(page_complete=False, next_cursor="cursor-exact-" + "x" * 900)
    body = (
        {"schema": schema, "extra": "x" * 30_000} if nested else {**schema, "extra": "x" * 30_000}
    )
    original = deepcopy(body)
    output = parse_mcp_tool_result(
        {"structuredContent": body}, server_id="nl2sql", tool_name="nl2sql_get_profile_schema"
    ).output
    compacted = json.loads(compact_output(json.dumps(output, ensure_ascii=False), level))
    restored = json.loads(json.dumps(compacted, ensure_ascii=False))
    restored = json.loads(compact_output(json.dumps(restored, ensure_ascii=False), 4))
    actual = (
        restored["schema"]
        if nested
        else {k: v for k, v in restored.items() if k != "context_compaction"}
    )
    assert actual == schema
    assert body == original


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_tail_schema_list_items_keep_names(level: int) -> None:
    body = {"items": [{"id": str(i), "text": "x" * 1000} for i in range(20)] + [_schema()]}
    compacted = json.loads(compact_output(json.dumps(body), level))
    assert compacted["items"][-1] == _schema()


def test_oversized_summary_never_claims_complete_or_changes_retained_names() -> None:
    schema = _schema(100)
    for column in schema["objects"][0]["columns"]:
        column["name"] += "x" * 350
        column["description"] = "d" * 256
    schema["next_cursor"] = "unchanged-cursor"
    original = deepcopy(schema)
    summary = profile_schema_summary(schema)
    assert summary is not None and summary["truncated"] and not summary["page_complete"]
    assert not summary["objects"][0]["columns_complete"]
    assert summary["next_cursor"] == "unchanged-cursor"
    assert len(json.dumps(summary, ensure_ascii=False, separators=(",", ":"))) <= 16_000
    assert [c["name"] for c in summary["objects"][0]["columns"]] == [
        c["name"] for c in original["objects"][0]["columns"]
    ][: len(summary["objects"][0]["columns"])]
    assert original == schema


@pytest.mark.parametrize("change", ["name", "cursor", "too_large"])
def test_invalid_metadata_becomes_explicitly_unavailable(change: str) -> None:
    schema = _schema()
    if change == "name":
        schema["objects"][0]["columns"][0]["name"] = "x" * 513
    elif change == "cursor":
        schema["next_cursor"] = "x" * 1025
    else:
        schema["extra"] = "x" * (128 * 1024)
    summary = profile_schema_summary(schema)
    assert summary is not None and summary["status"] == "unavailable"
    assert summary["truncated"] and summary["objects"] == [] and not summary["page_complete"]


def test_metadata_never_registers_as_business_values_or_bypasses_budget() -> None:
    schema = _schema()
    metadata: dict[str, Any] = {}
    register_data_result(metadata, "step-a", schema)
    assert metadata == {} and model_data_references("step-a", schema) is None
    body = {**schema, "extra": "x" * 20_000}
    result = compact_items(
        [{"type": "function_call_output", "call_id": "c1", "output": json.dumps(body)}],
        budget=ContextBudget(
            ContextLimits(model_id="m", max_input_tokens=64, max_output_tokens=16), margin_ratio=0
        ),
        fixed_text="",
    )
    assert not result.fits


def test_non_schema_business_result_is_not_reclassified() -> None:
    assert (
        profile_schema_summary({"schema_version": 4, "status": "done", "rows": [{"VALUE": 1}]})
        is None
    )


def test_scope_claims_unchanged_and_metadata_does_not_count_as_business_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.agent import builtin_runtime
    from app.features.agent.runtime import runtime_repository

    scopes = {"nl2sql": AgentDataScope(profile_ids=["profile-a"])}
    assert token_profile_scopes(scopes)["nl2sql"] == ("profile-a",)
    steps = [SimpleNamespace(tool_call=SimpleNamespace(name=TOOL))]
    monkeypatch.setattr(runtime_repository, "get_run", lambda _id: SimpleNamespace(steps=steps))
    assert builtin_runtime._nl2sql_unqueried("run-a")
    steps.append(SimpleNamespace(tool_call=SimpleNamespace(name="nl2sql__nl2sql_query")))
    assert not builtin_runtime._nl2sql_unqueried("run-a")


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_redundant_descriptions_are_removed_before_canonical_column_names(level: int) -> None:
    schema = _schema(126)
    for column in schema["objects"][0]["columns"]:
        column["description"] = "d" * 256
        column["logical_name"] = "repeated display label"
    compacted = json.loads(compact_output(json.dumps(schema), level))
    restored = json.loads(compact_output(json.dumps(json.loads(json.dumps(compacted))), 4))
    assert [column["name"] for column in restored["objects"][0]["columns"]] == [
        column["name"] for column in schema["objects"][0]["columns"]
    ]
    assert restored["objects"][0]["columns_complete"]
    assert restored["truncated"] and not restored["page_complete"]
    assert all("description" not in column for column in restored["objects"][0]["columns"])


def test_optional_short_refs_do_not_expand_legacy_payloads_with_nulls() -> None:
    old = _schema()
    summary = profile_schema_summary(old)
    assert summary == old
    assert summary is not None and "schema_ref" not in summary
    assert all("field_ref" not in column for column in summary["objects"][0]["columns"])
    new = deepcopy(old)
    new["schema_ref"] = "S2"
    for i, column in enumerate(new["objects"][0]["columns"], 1):
        column["field_ref"] = f"S2:F{i}"
    assert profile_schema_summary(new) == new


def test_invalid_short_ref_does_not_become_a_valid_schema_reference() -> None:
    payload = _schema()
    payload["objects"][0]["columns"][0]["field_ref"] = "long-value-not-a-field-ref"
    summary = profile_schema_summary(payload)
    assert summary is not None and summary["status"] == "unavailable"


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_report_short_refs_aggregate_description_flags_before_losing_columns(level: int) -> None:
    payload = _schema(126)
    payload["schema_ref"] = "S1"
    for i, col in enumerate(payload["objects"][0]["columns"], 1):
        col.update(
            name=f"VALUE_WITH_LONG_CANONICAL_NAME_{i}", field_ref=f"S1:F{i}", description="d" * 256
        )
    compacted = json.loads(compact_output(json.dumps(payload), level))
    again = json.loads(compact_output(json.dumps(compacted), 4))
    assert len(again["objects"][0]["columns"]) == 126
    assert again["descriptions_omitted"]
    assert all("description_truncated" not in c for c in again["objects"][0]["columns"])
    assert again["truncated"] and not again["page_complete"]
    assert len(json.dumps(again, ensure_ascii=False, separators=(",", ":"))) < 16_500
