"""推測した等価条件の列に値がなく、主キーに存在する誤対応を検出する。"""

from __future__ import annotations

from typing import Any

import pytest
from test_sql_requirement_columns import _table

from app.features.nl2sql.identifier_binding import binding_conflicts, identifier_checks
from app.features.nl2sql.models import AllowedObjects, SchemaConstraintDetail, SchemaTable


def table() -> SchemaTable:
    return _table(columns=("ID", "OTHER_ID", "LABEL")).model_copy(
        update={
            "constraint_details": [
                SchemaConstraintDetail(
                    constraint_name="PK_A",
                    constraint_type="P",
                    owner="APP",
                    table_name="TABLE_A",
                    columns=["ID"],
                )
            ]
        }
    )


def test_wrong_identifier_is_not_reported_as_empty_data() -> None:
    checks = identifier_checks(
        "SELECT ID FROM APP.TABLE_A a WHERE a.OTHER_ID='A001'",
        [table()],
        question="A001 のデータ",
        owner="APP",
        allowed=AllowedObjects(),
    )
    assert len(checks) == 1
    assert checks[0].column == "OTHER_ID" and checks[0].primary_key == "ID"
    assert ":lookup_value" in checks[0].sql and "A001" not in checks[0].sql
    assert binding_conflicts(checks, lambda check: (False, True))
    assert not binding_conflicts(checks, lambda check: (False, False))
    assert not binding_conflicts(checks, lambda check: (True, True))


@pytest.mark.parametrize(
    "sql,question",
    [
        ("SELECT ID FROM APP.TABLE_A WHERE ID='A001'", "A001 のデータ"),
        ("SELECT ID FROM APP.TABLE_A WHERE OTHER_ID='A001'", "OTHER_IDがA001"),
        ("SELECT ID FROM APP.TABLE_A WHERE OTHER_ID='A001'", "別の値を調べる"),
        ("SELECT ID FROM APP.TABLE_A WHERE OTHER_ID='A001' OR ID='B001'", "A001 B001"),
    ],
)
def test_explicit_columns_keys_and_disjunctions_are_preserved(sql: str, question: str) -> None:
    assert not identifier_checks(
        sql, [table()], question=question, owner="APP", allowed=AllowedObjects()
    )


def test_user_confirmed_columns_and_column_permissions_are_preserved() -> None:
    sql = "SELECT ID FROM APP.TABLE_A WHERE OTHER_ID='A001'"
    assert not identifier_checks(
        sql,
        [table()],
        question="A001",
        owner="APP",
        allowed=AllowedObjects(),
        explicit_columns=["TABLE_A.OTHER_ID"],
    )
    assert not identifier_checks(
        sql,
        [table()],
        question="A001",
        owner="APP",
        allowed=AllowedObjects(columns={"APP.TABLE_A": ["OTHER_ID"]}),
    )


def test_derived_column_uses_physical_key_metadata() -> None:
    sql = "WITH x AS (SELECT ID,OTHER_ID AS k FROM APP.TABLE_A) SELECT ID FROM x WHERE k='A001'"
    checks = identifier_checks(
        sql, [table()], question="A001", owner="APP", allowed=AllowedObjects()
    )
    assert len(checks) == 1


def test_generation_rejects_wrong_column_before_empty_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql.identifier_binding import IdentifierBindingConflict
    from app.features.nl2sql.models import (
        Nl2SqlEngine,
        Nl2SqlProfile,
        QueryResults,
        SchemaCatalog,
        SqlRequirementSpec,
    )
    from app.features.nl2sql.service import GeneratedSql, Nl2SqlService, _job_failure_error_code
    from app.features.nl2sql.store import MemoryNl2SqlStore

    service = Nl2SqlService(store=MemoryNl2SqlStore())
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service, "_current_schema_owner", lambda: "APP")
    catalog = SchemaCatalog(refreshed_at="2026-01-01", tables=[table()])
    monkeypatch.setattr(
        service,
        "_generate_sql_with_engine",
        lambda *a, **kw: GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql="SELECT COUNT(*) FROM APP.TABLE_A WHERE OTHER_ID='A001'",
            explanation="",
            engine_meta={},
            schema_catalog=catalog,
        ),
    )
    probes = []

    def probe(sql: str, max_rows: int | None, **kwargs: Any) -> QueryResults:
        probes.append((sql, kwargs))
        assert ":lookup_value" in sql and "A001" not in sql
        assert kwargs == {"scalar_binds": {"lookup_value": "A001"}}
        return QueryResults(columns=[], rows=[{"COLUMN_HIT": 0, "KEY_HIT": 1}], total=1)

    monkeypatch.setattr(service._oracle_adapter, "execute_select", probe)
    with pytest.raises(IdentifierBindingConflict) as error:
        service._generate_sql(
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            "OTHER_IDがA001",
            Nl2SqlProfile(id="p", name="p"),
            AllowedObjects(),
            100,
            [],
            sql_requirement=SqlRequirementSpec(
                session_id="s",
                profile_id="p",
                original_question="A001 のデータ",
                clarified_question="OTHER_IDがA001",
            ),
        )
    assert len(probes) == 1
    assert (
        _job_failure_error_code(error.value, fallback="error") == "IDENTIFIER_BINDING_UNCONFIRMED"
    )


def test_scalar_probe_bind_is_not_forced_to_clob() -> None:
    from test_health import _FakeOracleDb, _FakeRuntimeOracleAdapter

    db = _FakeOracleDb()
    adapter = _FakeRuntimeOracleAdapter(db)
    adapter.execute_select(
        "SELECT :lookup_value FROM DUAL", 1, scalar_binds={"lookup_value": "A'001"}
    )
    assert {"lookup_value": "A'001"} in db.executed_params
    assert not any("lookup_value" in sizes for sizes in db.input_sizes)
