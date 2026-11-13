"""確認節の状態とモデルの参照選択を分離する。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError
from test_answer_evidence import _step

from app.features.agent.answer_data import DataReferenceError, data_index, register_data_result
from app.features.agent.context_window import compact_output
from app.features.agent.report_fields import (
    REPORT_FIELDS_KEY,
    ConfirmationItem,
    ConfirmationSection,
    SchemaFieldIndex,
    model_report_fields,
    register_report_step,
    render_confirmation,
)

TOOL = "nl2sql__nl2sql_get_profile_schema"


def schema(count: int = 2) -> dict[str, Any]:
    return {
        "schema_version": 4,
        "profile_id": "profile-a",
        "source": "cached",
        "status": "available",
        "unavailable_reason": None,
        "refreshed_at": "revision-a",
        "objects": [
            {
                "owner": "APP",
                "name": "TABLE_A",
                "object_type": "table",
                "columns": [
                    {"name": f"VALUE_{i}", "data_type": "NUMBER", "nullable": True}
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


def register(
    metadata: dict[str, Any], step: str = "schema-a", output: dict[str, Any] | None = None
) -> None:
    register_report_step(
        metadata, step, TOOL, schema() if output is None else output, trusted_builtin=True
    )


def section(result: str | None = None) -> ConfirmationSection:
    return ConfirmationSection(items=[ConfirmationItem(field_ref="S1:F1", result_ref=result)])


@pytest.mark.parametrize(
    "key,value",
    [
        ("exists", True),
        ("absent", True),
        ("queried", False),
        ("null", 0),
        ("reason", "未取得"),
        ("explanation", "説明"),
    ],
)
def test_model_only_selects_refs_not_status(key: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        ConfirmationItem.model_validate({"field_ref": "S1:F1", key: value})


@pytest.mark.parametrize("ref", ["S0:F1", "S1:F0", "APP.TABLE_A.VALUE_0", "S1:F1\n", "S1:F01"])
def test_noncanonical_field_refs_rejected(ref: str) -> None:
    with pytest.raises(ValidationError):
        ConfirmationItem(field_ref=ref)


def test_external_lookalike_does_not_register() -> None:
    metadata: dict[str, Any] = {}
    register_report_step(metadata, "a", TOOL, schema(), trusted_builtin=False)
    register_report_step(
        metadata, "b", "external__nl2sql_get_profile_schema", schema(), trusted_builtin=True
    )
    assert metadata == {}
    assert model_report_fields(metadata, "a", schema()) == schema()


def test_independent_calls_and_json_restore_never_renumber() -> None:
    metadata: dict[str, Any] = {}
    original = schema()
    register(metadata, "a", original)
    before = deepcopy(metadata)
    register(metadata, "a", original)
    assert metadata == before
    register(metadata, "b", original)
    restored = json.loads(json.dumps(metadata))
    assert restored == metadata
    a = model_report_fields(restored, "a", original)
    b = model_report_fields(restored, "b", original)
    assert a["schema_ref"] == "S1" and b["schema_ref"] == "S2"
    assert a["objects"][0]["columns"][0]["field_ref"] == "S1:F1"
    assert b["objects"][0]["columns"][0]["field_ref"] == "S2:F1"
    assert original == schema()
    assert SchemaFieldIndex(restored[REPORT_FIELDS_KEY]).snapshot() == restored[REPORT_FIELDS_KEY]


def test_changed_result_cannot_reuse_fixed_call_number() -> None:
    metadata: dict[str, Any] = {}
    register(metadata)
    changed = schema(3)
    with pytest.raises(DataReferenceError):
        register(metadata, output=changed)
    with pytest.raises(DataReferenceError):
        model_report_fields(metadata, "schema-a", changed)


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"page_complete": False},
        {"truncated": True},
        {"next_cursor": "page2"},
        {"status": "stale"},
    ],
)
def test_missing_result_never_means_unqueried_or_absent(changes: dict[str, Any]) -> None:
    metadata: dict[str, Any] = {}
    payload = schema()
    payload.update(changes)
    register(metadata, output=payload)
    text = render_confirmation(metadata, section())
    assert "未確認" in text
    assert "取得していません" not in text and "存在しません" not in text


def test_unknown_field_or_result_fails_closed() -> None:
    metadata: dict[str, Any] = {}
    register(metadata)
    with pytest.raises(DataReferenceError):
        render_confirmation(
            metadata, ConfirmationSection(items=[ConfirmationItem(field_ref="S1:F99")])
        )
    with pytest.raises(DataReferenceError):
        render_confirmation(metadata, section("T1"))


def sql_result(
    values: list[Any], *, sources: list[str] | None = None, profile: str = "profile-a"
) -> dict[str, Any]:
    return {
        "profile_id": profile,
        "status": "done",
        "columns": ["ALIAS_A"],
        "rows": [{"ALIAS_A": value} for value in values],
        "truncated": False,
        "column_metadata": [
            {
                "name": "ALIAS_A",
                "unit_status": "unknown",
                "unit": None,
                "source_columns": ["APP.TABLE_A.VALUE_0"] if sources is None else sources,
                "provenance": [],
            }
        ],
    }


@pytest.mark.parametrize(
    "values,expected",
    [([], "0行"), ([None], "NULL 1件"), ([0, False, None], "非NULL 2件、NULL 1件")],
)
def test_positive_lineage_counts_only_actual_result_rows(values: list[Any], expected: str) -> None:
    metadata: dict[str, Any] = {}
    register(metadata)
    output = sql_result(values)
    step = _step("nl2sql_query", output)
    register_data_result(metadata, step.id, output)
    register_report_step(metadata, step.id, "nl2sql__nl2sql_query", output, trusted_builtin=True)
    text = render_confirmation(metadata, section("T1"), data_index=data_index([step], metadata))
    assert expected in text
    assert "全データ" in text or "データ全体" in text
    assert "存在しません" not in text


@pytest.mark.parametrize(
    "sources,profile",
    [
        ([], "profile-a"),
        (["APP.TABLE_B.VALUE_0"], "profile-a"),
        (["APP.TABLE_A.VALUE_0", "APP.TABLE_B.VALUE_0"], "profile-a"),
        (["APP.TABLE_A.VALUE_0"], "profile-b"),
    ],
)
def test_ambiguous_or_different_profile_lineage_is_unknown(
    sources: list[str], profile: str
) -> None:
    metadata: dict[str, Any] = {}
    register(metadata)
    output = sql_result([1], sources=sources, profile=profile)
    step = _step("nl2sql_query", output)
    register_data_result(metadata, step.id, output)
    register_report_step(metadata, step.id, "nl2sql__nl2sql_query", output, trusted_builtin=True)
    assert "未確認" in render_confirmation(
        metadata, section("T1"), data_index=data_index([step], metadata)
    )


def test_new_schema_revision_makes_old_definition_conservative() -> None:
    metadata: dict[str, Any] = {}
    register(metadata)
    newer = schema()
    newer["refreshed_at"] = "revision-b"
    register(metadata, "schema-b", newer)
    assert "現在の状態は未確認" in render_confirmation(metadata, section())


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_126_names_and_refs_survive_real_compaction_and_restore(level: int) -> None:
    metadata: dict[str, Any] = {}
    payload = schema(126)
    for col in payload["objects"][0]["columns"]:
        col["description"] = "d" * 256
    register(metadata, output=payload)
    encoded = model_report_fields(metadata, "schema-a", payload)
    compacted = json.loads(compact_output(json.dumps(encoded), level))
    restored = json.loads(compact_output(json.dumps(json.loads(json.dumps(compacted))), 4))
    assert [(c["name"], c["field_ref"]) for c in restored["objects"][0]["columns"]] == [
        (c["name"], c["field_ref"]) for c in encoded["objects"][0]["columns"]
    ]
    assert restored["schema_ref"] == "S1"
    assert restored["objects"][0]["columns_complete"]
    assert restored["truncated"] and not restored["page_complete"]
    assert (
        SchemaFieldIndex(json.loads(json.dumps(metadata[REPORT_FIELDS_KEY]))).encode(
            "schema-a", payload
        )
        == encoded
    )


def test_truncated_and_unknown_metadata_remain_honest() -> None:
    metadata: dict[str, Any] = {}
    register(metadata)
    output = sql_result([None, 1])
    output["truncated"] = True
    step = _step("nl2sql_query", output)
    register_data_result(metadata, step.id, output)
    register_report_step(metadata, step.id, "nl2sql__nl2sql_query", output, trusted_builtin=True)
    index = data_index([step], metadata)
    text = render_confirmation(metadata, section("T1"), data_index=index)
    assert "未取得行があります" in text and "NULL 1件" in text
    changed = deepcopy(index)
    changed.aliases["T1"] = "wrong"
    with pytest.raises(DataReferenceError):
        render_confirmation(metadata, section("T1"), data_index=changed)


def test_duplicate_snapshot_numbers_fail_closed() -> None:
    metadata: dict[str, Any] = {}
    register(metadata)
    snapshot = metadata[REPORT_FIELDS_KEY]
    snapshot["catalogs"].append(deepcopy(snapshot["catalogs"][0]))
    with pytest.raises(DataReferenceError):
        SchemaFieldIndex(snapshot)


def test_related_result_is_not_claimed_to_be_passthrough_field_values() -> None:
    metadata: dict[str, Any] = {}
    register(metadata)
    output = sql_result([10])
    step = _step("nl2sql_query", output)
    register_data_result(metadata, step.id, output)
    register_report_step(metadata, step.id, "nl2sql__nl2sql_query", output, trusted_builtin=True)
    text = render_confirmation(metadata, section("T1"), data_index=data_index([step], metadata))
    assert "この列を参照した取得結果1行" in text
    assert "元の列そのものの値か計算結果かは未確認" in text


def test_null_count_cannot_exceed_result_rows_after_restore() -> None:
    metadata: dict[str, Any] = {}
    register(metadata)
    output = sql_result([None])
    step = _step("nl2sql_query", output)
    register_data_result(metadata, step.id, output)
    register_report_step(metadata, step.id, "nl2sql__nl2sql_query", output, trusted_builtin=True)
    snapshot = deepcopy(metadata[REPORT_FIELDS_KEY])
    snapshot["results"][0]["observations"][0]["nulls"] = 2
    with pytest.raises(DataReferenceError):
        SchemaFieldIndex(snapshot)
    snapshot = deepcopy(metadata[REPORT_FIELDS_KEY])
    snapshot["results"].append(deepcopy(snapshot["results"][0]))
    with pytest.raises(DataReferenceError):
        SchemaFieldIndex(snapshot)


def test_stale_definition_does_not_skip_unknown_result_validation() -> None:
    metadata: dict[str, Any] = {}
    payload = schema()
    payload["status"] = "stale"
    register(metadata, output=payload)
    with pytest.raises(DataReferenceError):
        render_confirmation(metadata, section("T99"))


@pytest.mark.parametrize(
    "name",
    [
        "VALUE_[[table:T1]]",
        "VALUE_[[cite:1]]",
        "VALUE_【根拠: doc-a:set-a:1】",
        "VALUE|`<b>_&[]()",
        "値😀\n改行\r\n次\t列",
        "&#91;already-encoded&#93;",
        "名\\前",
    ],
)
def test_source_names_are_literal_without_mutating_canonical_state(name: str) -> None:

    from app.features.agent import source_handles
    from app.features.agent.answer_data import DataIndex

    metadata: dict[str, Any] = {}
    payload = schema()
    payload["objects"][0]["owner"] = "OWNER_[x]"
    payload["objects"][0]["name"] = "TABLE_<i>|"
    payload["objects"][0]["columns"][0]["name"] = name
    register(metadata, output=payload)
    before = deepcopy(metadata)
    result = render_confirmation(metadata, section())
    label = result.split("\n- ", 1)[1].split("`: ", 1)[0] + "`"
    assert json.loads(label[1:-1]) == f"OWNER_[x].TABLE_<i>|.{name}"
    assert "[[" not in label and "【" not in label
    assert label.count("`") == 2
    assert result.count("列の識別名はJSON表記です。") == 1
    assert metadata == before
    encoded = model_report_fields(metadata, "schema-a", payload)
    assert encoded["objects"][0]["columns"][0]["name"] == name
    index = DataIndex(cells={}, tables={"key": (["VALUE"], [{"VALUE": 77}])}, aliases={"T1": "key"})
    assert index.render(result) == result
    assert source_handles.render_citations({}, result) == result


@pytest.mark.parametrize("object_name,column", [("A.B", "C"), ("A", "B.C"), ('A"B', "C")])
def test_quoted_or_dot_components_never_bind_by_ambiguous_joined_identity(
    object_name: str,
    column: str,
) -> None:
    metadata: dict[str, Any] = {}
    payload = schema()
    payload["objects"][0]["name"] = object_name
    payload["objects"][0]["columns"][0]["name"] = column
    register(metadata, output=payload)
    output = sql_result([None], sources=[f"APP.{object_name}.{column}"])
    step = _step("nl2sql_query", output)
    register_data_result(metadata, step.id, output)
    register_report_step(metadata, step.id, "nl2sql__nl2sql_query", output, trusted_builtin=True)
    text = render_confirmation(metadata, section("T1"), data_index=data_index([step], metadata))
    assert "未確認" in text and "NULL 1件" not in text


@pytest.mark.parametrize(
    "limit",
    [
        "MAX_REPORT_FIELD_CATALOGS",
        "MAX_REPORT_FIELD_ENTRIES",
        "MAX_REPORT_FIELD_RESULTS",
        "MAX_REPORT_FIELDS_SNAPSHOT_BYTES",
    ],
)
def test_registry_limits_are_atomic_and_do_not_reuse_or_trim_refs(
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    from app.features.agent import report_fields

    metadata: dict[str, Any] = {}
    register(metadata)
    output = sql_result([1])
    step = _step("nl2sql_query", output)
    register_data_result(metadata, step.id, output)
    before = deepcopy(metadata)
    current = SchemaFieldIndex(metadata[REPORT_FIELDS_KEY])
    if limit == "MAX_REPORT_FIELD_CATALOGS":
        monkeypatch.setattr(report_fields, limit, 1)
    elif limit == "MAX_REPORT_FIELD_ENTRIES":
        monkeypatch.setattr(report_fields, limit, 2)
    elif limit == "MAX_REPORT_FIELD_RESULTS":
        monkeypatch.setattr(report_fields, limit, 0)
    else:
        size = len(json.dumps(current.snapshot(), ensure_ascii=False).encode())
        monkeypatch.setattr(report_fields, limit, size)
    with pytest.raises(DataReferenceError, match="保存上限"):
        if limit == "MAX_REPORT_FIELD_RESULTS":
            register_report_step(
                metadata, step.id, "nl2sql__nl2sql_query", output, trusted_builtin=True
            )
        else:
            register(metadata, "another-call")
    assert metadata == before
    encoded = model_report_fields(metadata, "schema-a", schema())
    assert encoded["objects"][0]["columns"][0]["field_ref"] == "S1:F1"


def test_restore_rejects_over_limit_snapshot_without_silent_trimming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.agent import report_fields

    metadata: dict[str, Any] = {}
    register(metadata)
    snapshot = deepcopy(metadata[REPORT_FIELDS_KEY])
    monkeypatch.setattr(report_fields, "MAX_REPORT_FIELD_ENTRIES", 1)
    with pytest.raises(DataReferenceError, match="保存上限"):
        SchemaFieldIndex(snapshot)
    assert len(snapshot["catalogs"][0]["fields"]) == 2
