"""Application auth/RBAC migration 004 を idempotent に適用する。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from app.clients.oracle_runtime import get_oracle_pool_manager
from app.clients.oracle_statement_executor import oracle_statement_executor
from app.security.service import get_security_service

LEGACY_TABLE_RENAMES: tuple[tuple[str, str], ...] = (
    ("RAG_APP_USERS", "NL2SQL_APP_USERS"),
    ("RAG_APP_ROLES", "NL2SQL_APP_ROLES"),
    ("RAG_APP_USER_ROLES", "NL2SQL_APP_USER_ROLES"),
    ("RAG_APP_ROLE_PERMISSIONS", "NL2SQL_APP_ROLE_PERMISSIONS"),
    ("RAG_APP_DATA_ENTITLEMENTS", "NL2SQL_APP_DATA_ENTITLEMENTS"),
    ("RAG_AUTH_SESSIONS", "NL2SQL_AUTH_SESSIONS"),
    ("RAG_AUTH_AUDIT_LOG", "NL2SQL_AUTH_AUDIT_LOG"),
    ("RAG_DEEPSEC_MIGRATIONS", "NL2SQL_DEEPSEC_MIGRATIONS"),
)


def split_ddl(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        current.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(current).strip().removesuffix(";").strip()
            if statement:
                statements.append(statement)
            current = []
    trailing = "\n".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


def _assert_no_namespace_conflicts(connection: Any) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT OBJECT_NAME FROM USER_OBJECTS WHERE OBJECT_TYPE = 'TABLE'"
        )
        tables = {str(row[0]) for row in cursor.fetchall()}
    conflicts = [
        f"{source}/{target}"
        for source, target in LEGACY_TABLE_RENAMES
        if source in tables and target in tables
    ]
    if conflicts:
        raise RuntimeError(
            "旧 RAG security table と NL2SQL security table が同時に存在します: "
            + ", ".join(conflicts)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Oracle へ migration を適用する")
    parser.add_argument(
        "--skip-bootstrap",
        action="store_true",
        help="初回 SYSTEM_ADMIN の作成を行わない",
    )
    args = parser.parse_args()
    migration_dir = Path(__file__).resolve().parents[2] / "migrations"
    namespace_migration = migration_dir / "005_security_namespace_nl2sql.sql"
    namespace_statements = split_ddl(namespace_migration.read_text(encoding="utf-8"))
    migration = migration_dir / "004_app_security_rbac.sql"
    statements = split_ddl(migration.read_text(encoding="utf-8"))
    if not args.apply:
        print(
            f"migration=005 statements={len(namespace_statements)} mode=preview "
            f"migration=004 statements={len(statements)}"
        )
        return 0

    with get_oracle_pool_manager().control_connection() as connection:
        _assert_no_namespace_conflicts(connection)
        namespace_results = oracle_statement_executor.execute(
            connection,
            namespace_statements,
            atomic=False,
            include_sql=False,
            ignored_error_codes=frozenset(
                {"ORA-00942", "ORA-01418", "ORA-02443", "ORA-04043", "ORA-23292"}
            ),
        )
        results = oracle_statement_executor.execute(
            connection,
            statements,
            atomic=False,
            include_sql=False,
            ignored_error_codes=frozenset({"ORA-00955", "ORA-00001"}),
        )
    errors = [
        result
        for result in (*namespace_results, *results)
        if result["status"] == "error"
    ]
    if errors:
        raise RuntimeError(str(errors[0].get("error_message") or "migration 004 failed"))
    bootstrapped = False
    if not args.skip_bootstrap:
        bootstrapped = get_security_service().bootstrap()
    print(
        f"migration=005 statements={len(namespace_statements)} mode=applied "
        f"migration=004 statements={len(statements)} "
        f"bootstrap_created={str(bootstrapped).lower()}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    raise SystemExit(main())
