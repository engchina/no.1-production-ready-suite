"""修正の構造的事実だけを、同じ所有者の保存結果/MCPへ渡す。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
import test_mcp as mcp_test
from pydantic import ValidationError
from test_sql_binding_candidates import catalog, profile

from app.features.mcp import tools as mcp_tools
from app.features.nl2sql import generation_diagnostics as diagnostics_module
from app.features.nl2sql import service as service_module
from app.features.nl2sql.generation_diagnostics import (
    MAX_GENERATION_DIAGNOSTIC_CHARS,
    BindingDiagnostic,
    GenerationDiagnostics,
    build_generation_diagnostics,
    projection_changes,
    recorded_generation_diagnostics,
)
from app.features.nl2sql.models import AllowedObjects, JobData, Nl2SqlEngine
from app.features.nl2sql.service import GeneratedSql, Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore

fake_service = mcp_test.fake_service
users = mcp_test.users


def binding() -> BindingDiagnostic:
    return {
        "source": "schema_catalog",
        "reference": {"qualifier": "A", "column": "VALUE_DIFF", "quoted": False},
        "exact_candidates": [
            {
                "owner": "APP",
                "table": "TABLE_B",
                "column": "VALUE_DIFF",
                "sql_identifier": '"APP"."TABLE_B"."VALUE_DIFF"',
            }
        ],
        "truncated": False,
    }


def recorded() -> GenerationDiagnostics:
    return build_generation_diagnostics(
        "SELECT a.ID,a.VALUE_DIFF AS diff FROM APP.TABLE_A a",
        "SELECT a.ID FROM APP.TABLE_A a",
        [binding()],
    )


def test_removed_projection_is_formal_data_with_original_ordinal_not_a_semantic_veto() -> None:
    value = recorded()
    assert value.status == "recorded" and len(value.repairs) == 1
    repair = value.repairs[0]
    assert repair.rejected_attempt == 1 and repair.accepted_attempt == 2
    assert repair.reason_codes == ["SQL_COLUMN_BINDING_INVALID"]
    assert repair.projection_changes.model_dump() == {
        "status": "compared",
        "unavailable_reason": None,
        "removed": [{"ordinal": 2, "output_name": "DIFF", "expression_kind": "Column"}],
        "added": [],
        "changed": [],
        "truncated": False,
    }
    assert repair.binding_diagnostics == [binding()]


@pytest.mark.parametrize(
    "before,after,kind",
    [
        (
            "SELECT ID AS a FROM APP.TABLE_A",
            "SELECT ID AS b FROM APP.TABLE_A",
            "output_name_changed",
        ),
        (
            "SELECT ID AS a FROM APP.TABLE_A",
            "SELECT ID+1 AS a FROM APP.TABLE_A",
            "expression_changed",
        ),
        (
            "SELECT ID,VALUE_DIFF FROM APP.TABLE_B",
            "SELECT VALUE_DIFF,ID FROM APP.TABLE_B",
            "position_changed",
        ),
    ],
)
def test_only_certain_named_projection_changes_are_classified(
    before: str, after: str, kind: str
) -> None:
    value = projection_changes(before, after)
    assert value.status == "compared" and value.changed
    assert all(item.kind == kind for item in value.changed)
    assert not value.removed and not value.added


def test_added_projection_and_repeated_expression_rename_are_not_guessed() -> None:
    added = projection_changes("SELECT ID FROM APP.TABLE_A", "SELECT ID,1 AS n FROM APP.TABLE_A")
    assert [item.output_name for item in added.added] == ["N"]
    ambiguous = projection_changes(
        "SELECT ID AS a, ID AS b FROM APP.TABLE_A", "SELECT ID AS c, ID AS d FROM APP.TABLE_A"
    )
    assert ambiguous.status == "compared" and not ambiguous.changed
    assert [item.output_name for item in ambiguous.removed] == ["A", "B"]
    assert [item.output_name for item in ambiguous.added] == ["C", "D"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM APP.TABLE_A",
        "SELECT a.* FROM APP.TABLE_A a",
        "SELECT 1 FROM DUAL",
        "SELECT ID,ID FROM APP.TABLE_A",
        "SELECT ID FROM APP.TABLE_A UNION ALL SELECT ID FROM APP.TABLE_A",
        "WITH x AS (SELECT ID FROM APP.TABLE_A) SELECT ID FROM x",
        "SELECT 'unclosed FROM DUAL",
    ],
)
def test_uncertain_projection_scope_is_explicitly_unavailable(sql: str) -> None:
    value = projection_changes(sql, "SELECT ID FROM APP.TABLE_A")
    assert value.status == "unavailable" and value.unavailable_reason
    assert not value.removed and not value.added and not value.changed


def test_unicode_and_quoted_output_names_stay_exact_and_comments_are_not_changes() -> None:
    value = projection_changes(
        'SELECT ID AS "値😀", ID+1 AS "別名" FROM APP.TABLE_A',
        'SELECT ID /* private comment */ AS "値😀" FROM APP.TABLE_A',
    )
    assert value.status == "compared" and not value.changed
    assert value.removed[0].output_name == "別名" and value.removed[0].ordinal == 2
    assert "private comment" not in value.model_dump_json()


def test_literal_contents_and_sql_text_never_leave_projection_diagnostic() -> None:
    value = build_generation_diagnostics(
        "SELECT 'private_original' AS label FROM DUAL",
        "SELECT 'private_changed' AS label FROM DUAL",
        [],
    )
    dumped = value.model_dump_json()
    assert all(
        text not in dumped for text in ["private_original", "private_changed", "SELECT", "FROM"]
    )
    assert value.repairs[0].projection_changes.changed[0].kind == "expression_changed"
    assert value.repairs[0].projection_changes.changed[0].before.expression_kind == "Literal"


def test_diagnostic_failure_keeps_original_flow_without_leaking_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("private_diagnostic_failure")

    monkeypatch.setattr(diagnostics_module, "_projection_facts", broken)
    value = recorded()
    assert (
        value.status == "recorded" and value.repairs[0].projection_changes.status == "unavailable"
    )
    assert "private_diagnostic_failure" not in value.model_dump_json()


def test_large_diagnostics_are_item_bounded_without_cutting_identifiers_or_mutating_input() -> None:
    base = binding()
    base["exact_candidates"] = [
        {
            "owner": "O" * 128,
            "table": f"T{i:02}" + "T" * 125,
            "column": "C" * 128,
            "sql_identifier": "S" * 512,
        }
        for i in range(16)
    ]
    bindings = [deepcopy(base) for _ in range(16)]
    before = deepcopy(bindings)
    value = build_generation_diagnostics(
        "SELECT a.ID,a.VALUE_DIFF AS diff FROM APP.TABLE_A a",
        "SELECT a.ID FROM APP.TABLE_A a",
        bindings,
    )
    serialized = json.dumps(
        value.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )
    assert value.status == "recorded" and value.truncated
    assert len(serialized) <= MAX_GENERATION_DIAGNOSTIC_CHARS
    assert value.repairs[0].projection_changes.removed[0].output_name == "DIFF"
    assert bindings == before
    for diagnostic in value.repairs[0].binding_diagnostics:
        for candidate in diagnostic["exact_candidates"]:
            assert len(candidate["owner"]) == 128 and len(candidate["column"]) == 128


def test_projection_list_limit_and_malformed_binding_are_explicit() -> None:
    before = "SELECT " + ",".join(f"ID AS v{i}" for i in range(40)) + " FROM APP.TABLE_A"
    value = build_generation_diagnostics(before, "SELECT ID FROM APP.TABLE_A", [binding()])
    changes = value.repairs[0].projection_changes
    assert len(changes.removed) == 16 and changes.truncated
    malformed = deepcopy(binding())
    malformed["reference"]["column"] = "X" * 129
    result = build_generation_diagnostics(
        "SELECT ID FROM APP.TABLE_A", "SELECT ID FROM APP.TABLE_A", [malformed]
    )
    assert result.truncated and result.repairs[0].binding_diagnostics == []


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"schema_version": 99},
        {"status": "recorded", "repairs": []},
        {"status": "unavailable", "repairs": [], "unavailable_reason": "SELECT private_literal"},
    ],
)
def test_invalid_saved_metadata_is_unavailable_and_old_absence_is_none(invalid: object) -> None:
    assert recorded_generation_diagnostics(None) is None
    value = recorded_generation_diagnostics(invalid)
    assert value is not None and value.status == "unavailable"
    assert value.unavailable_reason == "invalid_saved_diagnostics"
    assert "private_literal" not in value.model_dump_json()


def test_public_dto_drops_extra_sql_and_engine_metadata() -> None:
    raw = recorded().model_dump(mode="json")
    raw["raw_sql"] = "SELECT private_literal FROM secret"
    raw["repairs"][0]["rejected_sql"] = "SELECT private_literal"
    raw["repairs"][0]["projection_changes"]["removed"][0]["expression"] = "private_literal"
    value = recorded_generation_diagnostics(raw)
    assert value is not None and value.status == "recorded"
    assert "private_literal" not in value.model_dump_json()
    with pytest.raises(ValidationError):
        GenerationDiagnostics.model_validate({**raw, "repairs": raw["repairs"] * 2})


def test_service_records_only_real_binding_repair_even_if_model_drops_requested_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    snap = catalog()
    calls = []
    bad = "SELECT a.ID,a.VALUE_DIFF AS diff FROM APP.TABLE_A a"
    good = "SELECT a.ID FROM APP.TABLE_A a"

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        calls.append(kwargs)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=bad if len(calls) == 1 else good,
            explanation="",
            engine_meta={},
            schema_catalog=snap,
        )

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    result = service._generate_sql(
        Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
        "必要な差を取得",
        profile("APP.TABLE_A", "APP.TABLE_B"),
        AllowedObjects(),
        10,
        [],
    )
    assert result.generated_sql == good and len(calls) == 2  # 意味の checker/自動補足照会はない。
    value = recorded_generation_diagnostics(result.engine_meta["generation_diagnostics"])
    assert (
        value is not None and value.repairs[0].projection_changes.removed[0].output_name == "DIFF"
    )
    assert value.repairs[0].binding_diagnostics[0]["exact_candidates"][0]["table"] == "TABLE_B"
    calls.clear()
    monkeypatch.setattr(
        service,
        "_generate_sql_with_engine",
        lambda *a, **kw: GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=good,
            explanation="",
            engine_meta={},
            schema_catalog=snap,
        ),
    )
    first = service._generate_sql(
        Nl2SqlEngine.ENTERPRISE_AI_DIRECT, "一覧", profile("APP.TABLE_A"), AllowedObjects(), 10, []
    )
    assert "generation_diagnostics" not in first.engine_meta


def test_unexpected_builder_failure_does_not_change_success_or_leak_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    snap = catalog()
    calls = 0

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        nonlocal calls
        calls += 1
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql="SELECT a.VALUE_DIFF FROM APP.TABLE_A a"
            if calls == 1
            else "SELECT ID FROM APP.TABLE_A",
            explanation="",
            engine_meta={},
            schema_catalog=snap,
        )

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("private builder error")

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service_module, "build_generation_diagnostics", broken)
    result = service._generate_sql(
        Nl2SqlEngine.ENTERPRISE_AI_DIRECT, "一覧", profile("APP.TABLE_A"), AllowedObjects(), 10, []
    )
    assert result.generated_sql == "SELECT ID FROM APP.TABLE_A"
    assert result.engine_meta["generation_diagnostics"]["status"] == "unavailable"
    assert "private builder error" not in json.dumps(result.engine_meta["generation_diagnostics"])


def test_result_dto_json_roundtrip_and_mcp_query_get_share_validated_substructure(
    users: mcp_test._Users,
    fake_service: mcp_test._FakeNl2SqlService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = mcp_test._result
    diagnostic = recorded().model_dump(mode="json")

    def result(request: Any) -> Any:
        value = original(request)
        value.engine_meta = {
            "generation_diagnostics": diagnostic,
            "sql_validation_rejected_sql": "SELECT private_literal",
            "secret": "private_secret",
        }
        return value

    monkeypatch.setattr(mcp_test, "_result", result)
    user = users.create(
        "diagnostics.user", permissions={"menu.query"}, allowed_profile_ids={"sales"}
    )
    token = mcp_test._token(user.user_uuid)
    error, query = mcp_test._structured(
        mcp_test._run(
            mcp_test._call(
                "nl2sql_query",
                {
                    "question": "列一覧",
                    "profile_id": "sales",
                    "wait_seconds": 0,
                },
            ),
            token=token,
        )
    )
    assert not error and query["schema_version"] == 4
    assert query["generation_diagnostics"] == diagnostic
    job = fake_service.get_job(query["job_id"], actor_user_uuid=user.user_uuid)
    assert job is not None
    restored = JobData.model_validate_json(job.model_dump_json())
    assert mcp_tools._job_result(restored).generation_diagnostics == recorded()
    error, again = mcp_test._structured(
        mcp_test._run(mcp_test._call("nl2sql_get_job", {"job_id": query["job_id"]}), token=token)
    )
    assert not error and again["generation_diagnostics"] == diagnostic
    assert all(
        text not in json.dumps(again)
        for text in ["private_literal", "private_secret", "engine_meta"]
    )
    assert restored.result is not None
    restored.result.engine_meta = {}
    assert mcp_tools._job_result(restored).generation_diagnostics is None
    restored.result.engine_meta = {"generation_diagnostics": {"schema_version": 999}}
    unavailable = mcp_tools._job_result(restored).generation_diagnostics
    assert unavailable is not None and unavailable.status == "unavailable"


def test_completed_repair_survives_store_restart_and_owner_boundary() -> None:
    from app.features.nl2sql.models import (
        JobCreateRequest,
        JobStatus,
        Nl2SqlResult,
        QueryResults,
        SafetyReport,
        TimingEnvelope,
    )
    from app.features.nl2sql.service import StoredJob

    store = MemoryNl2SqlStore()
    producer = Nl2SqlService(store=store)
    value = recorded().model_dump(mode="json")
    result = Nl2SqlResult(
        engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
        engine_meta={"generation_diagnostics": value},
        original_question="一覧",
        rewritten_question="一覧",
        generated_sql="SELECT ID FROM APP.TABLE_A",
        executable_sql="SELECT ID FROM APP.TABLE_A",
        explanation="説明",
        safety=SafetyReport(is_safe=True, is_select_only=True, row_limit_applied=10),
        results=QueryResults(columns=["ID"], rows=[{"ID": 1}], total=1),
        timing=TimingEnvelope(created_at="t"),
    )
    producer._jobs["j"] = StoredJob(
        job_id="j",
        request=JobCreateRequest(question="一覧", profile_id="p"),
        actor_user_uuid="actor-a",
        status=JobStatus.DONE,
        result=result,
    )
    store.save_snapshot(producer._snapshot_locked())
    consumer = Nl2SqlService(store=store)
    restored = consumer.get_job("j", actor_user_uuid="actor-a")
    assert restored is not None and restored.result is not None
    assert restored.result.engine_meta["generation_diagnostics"] == value
    assert mcp_tools._job_result(restored).generation_diagnostics == recorded()
    with pytest.raises(PermissionError):
        consumer.get_job("j", actor_user_uuid="actor-b")


@pytest.mark.parametrize("field", ["schema_version", "rejected_attempt", "accepted_attempt"])
def test_bool_is_not_a_version_or_attempt_number(field: str) -> None:
    raw = recorded().model_dump(mode="json")
    if field == "schema_version":
        raw[field] = True
    else:
        raw["repairs"][0][field] = True
    value = recorded_generation_diagnostics(raw)
    assert value is not None and value.status == "unavailable"
