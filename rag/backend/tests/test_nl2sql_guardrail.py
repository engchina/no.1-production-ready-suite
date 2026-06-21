"""NL2SQL Guardrail アダプター(SQL 安全判定)のテスト。"""

from typing import Any

from app.config import Settings
from app.nl2sql.guardrail import (
    GUARDRAIL_POLICY_ORDER,
    GuardrailAdapterParams,
    classify_statement,
    enforce,
    guardrail_adapter_runtime_settings,
    normalize_guardrail_policy,
    resolve_guardrail_adapter,
    split_statements,
    strip_sql_comments,
)


def _params(policy: str = "read_only", **overrides: Any) -> GuardrailAdapterParams:
    return resolve_guardrail_adapter(Settings(nl2sql_guardrail_policy=policy, **overrides))


def test_read_only_allows_simple_select() -> None:
    v = enforce("SELECT employee_name FROM employee", _params("read_only"))
    assert v.allowed is True
    assert v.statement_type == "SELECT"
    assert v.violations == ()


def test_read_only_allows_cte() -> None:
    sql = "WITH d AS (SELECT * FROM department) SELECT * FROM d"
    v = enforce(sql, _params("read_only"))
    assert v.allowed is True
    assert v.statement_type == "WITH"


def test_read_only_blocks_dml_and_ddl() -> None:
    for sql in (
        "UPDATE employee SET salary = 0",
        "DELETE FROM employee",
        "INSERT INTO employee VALUES (1)",
        "DROP TABLE employee",
        "TRUNCATE TABLE employee",
    ):
        v = enforce(sql, _params("read_only"))
        assert v.allowed is False, sql


def test_blocks_multiple_statements_injection() -> None:
    v = enforce("SELECT 1 FROM dual; DROP TABLE secret", _params("read_only"))
    assert v.allowed is False
    assert "multiple_statements" in v.violations


def test_comment_hidden_semicolon_is_neutralized() -> None:
    # ';' と DROP がコメント内なら 1 文の SELECT として扱われ許可される。
    v = enforce("SELECT 1 FROM dual /* ; DROP TABLE x */", _params("read_only"))
    assert v.allowed is True
    assert v.statement_type == "SELECT"


def test_select_for_update_blocked() -> None:
    v = enforce("SELECT * FROM employee FOR UPDATE", _params("read_only"))
    assert v.allowed is False
    assert "select_for_update" in v.violations


def test_package_call_blocked() -> None:
    v = enforce("SELECT dbms_random.value FROM dual", _params("read_only"))
    assert v.allowed is False
    assert "package_call_present" in v.violations


def test_string_literal_keyword_is_not_a_false_positive() -> None:
    # リテラル内の UPDATE は書き込みと誤検出しない。
    v = enforce("SELECT * FROM audit_log WHERE action = 'UPDATE'", _params("read_only"))
    assert v.allowed is True


def test_strict_requires_object_allowlist() -> None:
    params = _params("strict")
    ok = enforce("SELECT * FROM employee", params, allowed_objects=("EMPLOYEE", "DEPARTMENT"))
    assert ok.allowed is True
    assert ok.semantic_verify_required is True  # strict は semantic_verify を要求

    bad = enforce("SELECT * FROM salaries", params, allowed_objects=("EMPLOYEE",))
    assert bad.allowed is False
    assert any(v.startswith("object_not_in_allowlist") for v in bad.violations)


def test_strict_without_allowlist_is_blocked() -> None:
    v = enforce("SELECT * FROM employee", _params("strict"), allowed_objects=())
    assert v.allowed is False
    assert "object_allowlist_unavailable" in v.violations


def test_sandboxed_sets_run_role_and_max_rows() -> None:
    params = _params(
        "sandboxed", nl2sql_guardrail_run_role="NL2SQL_RO", nl2sql_guardrail_max_rows=500
    )
    v = enforce("SELECT * FROM employee", params, allowed_objects=("EMPLOYEE",))
    assert v.allowed is True
    assert v.run_role == "NL2SQL_RO"
    assert v.max_rows == 500


def test_helpers_strip_comments_and_split() -> None:
    assert strip_sql_comments("SELECT 1 -- c\nFROM dual").strip() == "SELECT 1 \nFROM dual"
    assert split_statements("SELECT 1; SELECT 2;") == ["SELECT 1", "SELECT 2"]
    assert classify_statement("merge into t using s on (1=1)") == "MERGE"


def test_normalize_and_runtime_settings() -> None:
    assert normalize_guardrail_policy("nope") == "read_only"
    assert normalize_guardrail_policy("strict") == "strict"
    runtime = guardrail_adapter_runtime_settings(Settings(nl2sql_guardrail_policy="strict"))
    assert tuple(s.name for s in runtime.policies) == GUARDRAIL_POLICY_ORDER
    assert [s.name for s in runtime.policies if s.selected] == ["strict"]
    assert all(s.enforce_read_only for s in runtime.policies)
