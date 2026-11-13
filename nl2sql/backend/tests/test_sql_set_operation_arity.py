"""集合演算の列数はscope解決済みASTで照合し、未解決starを推測しない。"""

from __future__ import annotations

from typing import Any

import pytest
from test_nl2sql_job_runtime import _repository, _request, _worker
from test_sql_binding import TABLES

from app.features.nl2sql.models import (
    JobStatus,
    Nl2SqlEngine,
    QueryResults,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.service import GeneratedSql
from app.features.nl2sql.sql_binding import column_binding_violations
from app.settings import get_settings


@pytest.mark.parametrize("operator", ["UNION", "UNION ALL", "INTERSECT", "MINUS", "EXCEPT"])
@pytest.mark.parametrize("cte", [False, True])
def test_determinate_set_operation_arity_mismatch_is_rejected(operator: str, cte: bool) -> None:
    sql = (
        f"SELECT ID FROM APP.TABLE_A {operator} SELECT ID,AMOUNT FROM APP.TABLE_A"
        if not cte
        else "WITH a AS(SELECT ID FROM APP.TABLE_A),b AS(SELECT ID,AMOUNT FROM APP.TABLE_A) "
        f"SELECT * FROM a {operator} SELECT b.* FROM b"
    )
    errors = column_binding_violations(sql, TABLES, owner="APP")
    assert len(errors) == 1
    assert errors[0].startswith("SQL_COLUMN_BINDING_INVALID: Set operation")
    assert errors[0].endswith("1 and 2")


@pytest.mark.parametrize("operator", ["UNION", "UNION ALL", "INTERSECT", "MINUS", "EXCEPT"])
def test_equal_counts_need_not_have_matching_aliases(operator: str) -> None:
    sql = (
        "WITH a(x,y) AS(SELECT ID AS a,AMOUNT AS b FROM APP.TABLE_A),"
        "b AS(SELECT ID AS other,AMOUNT AS another FROM APP.TABLE_A) "
        f"SELECT * FROM a {operator} SELECT * FROM b"
    )
    assert column_binding_violations(sql, TABLES, owner="APP") == []


@pytest.mark.parametrize(
    "sql",
    [
        (
            "SELECT ID FROM APP.TABLE_A UNION ALL (SELECT ID FROM APP.TABLE_A "
            "UNION ALL SELECT ID,AMOUNT FROM APP.TABLE_A)"
        ),
        (
            "(SELECT ID FROM APP.TABLE_A UNION SELECT AMOUNT FROM APP.TABLE_A) "
            "INTERSECT SELECT ID,AMOUNT FROM APP.TABLE_A"
        ),
        (
            "WITH q AS(SELECT ID FROM APP.TABLE_A UNION SELECT ID,AMOUNT FROM "
            "APP.TABLE_A) SELECT * FROM q"
        ),
    ],
)
def test_nested_set_operations_are_checked_independently(sql: str) -> None:
    assert any(
        "projection counts: 1 and 2" in error
        for error in column_binding_violations(sql, TABLES, owner="APP")
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM UNKNOWN_A UNION ALL SELECT 1,2 FROM DUAL",
        "WITH q AS(SELECT * FROM UNKNOWN_A)SELECT * FROM q UNION ALL SELECT 1,2 FROM DUAL",
        "SELECT 1 FROM DUAL UNION ALL (SELECT * FROM UNKNOWN_A UNION ALL SELECT 1,2 FROM DUAL)",
    ],
)
def test_unresolved_stars_do_not_supply_an_invented_count(sql: str) -> None:
    assert column_binding_violations(sql, [], owner="APP") == []


def test_count_star_is_one_projection_without_schema() -> None:
    assert (
        column_binding_violations(
            "SELECT COUNT(*) FROM UNKNOWN_A UNION SELECT 1 FROM DUAL", [], owner="APP"
        )
        == []
    )
    assert (
        "1 and 2"
        in column_binding_violations(
            "SELECT COUNT(*) FROM UNKNOWN_A UNION SELECT 1,2 FROM DUAL", [], owner="APP"
        )[0]
    )


def test_quoted_identifiers_and_physical_star_expansion() -> None:
    tables = [
        SchemaTable(
            owner="Tenant",
            table_name="Mixed",
            logical_name="合成表",
            columns=[
                SchemaColumn(column_name=name, logical_name=name, data_type="NUMBER")
                for name in ["valueA", "valueB"]
            ],
        )
    ]
    errors = column_binding_violations(
        'SELECT "valueA" AS "x" FROM "Tenant"."Mixed" UNION ALL SELECT t.* FROM "Tenant"."Mixed" t',
        tables,
        owner="Tenant",
    )
    assert errors and "1 and 2" in errors[0]
    assert (
        column_binding_violations(
            (
                'SELECT "valueA" AS "x" FROM "Tenant"."Mixed" UNION ALL SELECT '
                '"valueB" AS "y" FROM "Tenant"."Mixed"'
            ),
            tables,
            owner="Tenant",
        )
        == []
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ID a,AMOUNT a FROM APP.TABLE_A UNION ALL SELECT ID b,AMOUNT c FROM APP.TABLE_A",
        (
            "SELECT ID FROM APP.TABLE_A UNION ALL (SELECT ID FROM APP.TABLE_A "
            "INTERSECT SELECT AMOUNT FROM APP.TABLE_A)"
        ),
        "WITH a AS(SELECT COUNT(*) n FROM APP.TABLE_A)SELECT * FROM a UNION ALL SELECT 1 FROM DUAL",
        (
            "SELECT a.ID FROM APP.TABLE_A a WHERE EXISTS(SELECT 1 FROM APP.TABLE_A "
            "b WHERE b.ID=a.ID) UNION ALL SELECT ID FROM APP.TABLE_A"
        ),
    ],
)
def test_legal_set_operations_preserve_existing_bindings(sql: str) -> None:
    assert column_binding_violations(sql, TABLES, owner="APP") == []


@pytest.mark.parametrize("repair", [False, True])
def test_generation_retry_rejects_mismatch_before_execution(
    monkeypatch: pytest.MonkeyPatch, repair: bool
) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_job_worker_mode", "external")
    repository = _repository()
    service = _worker(repository)
    catalog = repository.load_catalog()
    bad = (
        "WITH a AS(SELECT ID FROM APP.ORDERS),b AS(SELECT ID,AMOUNT FROM APP.ORDERS)"
        " SELECT * FROM a UNION ALL SELECT * FROM b"
    )
    good = "SELECT ID FROM APP.ORDERS UNION ALL SELECT ID FROM APP.ORDERS"
    attempts: list[dict[str, Any]] = []
    executed: list[str] = []

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        attempts.append(kwargs)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=good if repair and len(attempts) == 2 else bad,
            explanation="",
            engine_meta={},
            schema_catalog=catalog,
        )

    def execute(sql: str, row_limit: int | None) -> QueryResults:
        assert sql == good
        executed.append(sql)
        return QueryResults(columns=["ID"], rows=[{"ID": 1}], total=1)

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service, "_mock_execute", execute)
    request = _request().model_copy(update={"use_ontology_context": False})
    created = service.start_job(request, actor_user_uuid="user-1")
    assert service.run_next_nl2sql_job(worker_id="arity", job_id=created.job_id)
    job = service.get_job(created.job_id, actor_user_uuid="user-1")
    assert job is not None
    assert len(attempts) == 2
    assert attempts[1]["generation_feedback"]["rejected_sql"] == bad
    assert "projection counts" in str(attempts[1]["generation_feedback"]["violations"])
    if repair:
        assert job.status == JobStatus.DONE and executed == [good]
    else:
        assert job.status == JobStatus.ERROR and executed == []
        assert job.error_code == "SQL_GENERATION_VALIDATION_FAILED"
