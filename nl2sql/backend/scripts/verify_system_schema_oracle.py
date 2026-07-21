"""一次性 Oracle user で NL2SQL system schema lifecycle を検証する。"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.clients.oracle_runtime import get_oracle_pool_manager
from app.features.nl2sql.oracle_adapter import oracle_connect_kwargs
from app.features.settings.system_schema import (
    RECREATE_CONFIRMATION,
    SystemSchemaManager,
)
from app.settings import get_settings


def _safe_identifier(value: str) -> str:
    normalized = value.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", normalized):
        raise RuntimeError("Oracle identifier is not safe")
    return normalized


def main() -> int:
    settings = get_settings()
    username = _safe_identifier(f"NL2SQL_IT_{secrets.token_hex(4).upper()}")
    password = f"Tmp-{secrets.token_urlsafe(24)}-9aA"
    legacy_table = _safe_identifier(settings.nl2sql_oracle_state_table)
    if username == settings.oracle_user.strip().upper():
        raise RuntimeError("temporary user must differ from the configured application user")

    created = False
    result: dict[str, Any] = {"temporary_user_created": False}
    try:
        with get_oracle_pool_manager().control_connection() as admin:
            with admin.cursor() as cursor:
                cursor.execute(f'CREATE USER "{username}" IDENTIFIED BY "{password}"')
                cursor.execute(
                    f'GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE TO "{username}"'
                )
                cursor.execute(f'GRANT UNLIMITED TABLESPACE TO "{username}"')
            admin.commit()
        created = True
        result["temporary_user_created"] = True

        @contextmanager
        def temporary_connection() -> Iterator[Any]:
            import oracledb

            connection = oracledb.connect(
                **oracle_connect_kwargs(settings, user=username, password=password)
            )
            try:
                yield connection
            finally:
                connection.close()

        manager = SystemSchemaManager(temporary_connection)
        with temporary_connection() as connection, connection.cursor() as cursor:
            cursor.execute("CREATE TABLE NL2SQL_APP_USERS (ID NUMBER PRIMARY KEY)")
            cursor.execute("INSERT INTO NL2SQL_APP_USERS (ID) VALUES (1)")
            cursor.execute("CREATE TABLE USER_BUSINESS_SENTINEL (ID NUMBER PRIMARY KEY)")
            cursor.execute("INSERT INTO USER_BUSINESS_SENTINEL (ID) VALUES (1)")
            cursor.execute(
                f"CREATE TABLE {legacy_table} "  # nosec B608 - validated identifier
                "(STATE_KEY VARCHAR2(32) PRIMARY KEY, STATE_JSON CLOB)"
            )
            cursor.execute(
                f"INSERT INTO {legacy_table} (STATE_KEY, STATE_JSON) "  # nosec B608
                "VALUES ('default', '{}')"
            )
            cursor.execute(
                "CREATE TABLE NL2SQL_FEEDBACK_VECTORS "
                "(ID NUMBER PRIMARY KEY, EMBEDDING VECTOR(1536, FLOAT32))"
            )
            cursor.execute("INSERT INTO NL2SQL_FEEDBACK_VECTORS (ID) VALUES (1)")
            connection.commit()

        initialized = manager.initialize()
        with temporary_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO NL2SQL_PROFILES
                    (PROFILE_ID, NAME, CATEGORY, DESCRIPTION, ARCHIVED,
                     ALLOWED_TABLE_COUNT, ALLOWED_VIEW_COUNT, GLOSSARY_COUNT,
                     FEW_SHOT_COUNT, VERSION_NO, ETAG, PAYLOAD_JSON)
                VALUES
                    ('integration-profile', 'Integration', NULL, NULL, 0,
                     0, 0, 0, 0, 1, 'integration-etag', '{}')
                """
            )
            cursor.execute(
                "UPDATE NL2SQL_SCHEMA_MIGRATIONS SET CHECKSUM = 'stale' WHERE VERSION_NO = 5"
            )
            connection.commit()

        assert manager.status()["status"] == "outdated"
        migrated = manager.initialize()
        with temporary_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM NL2SQL_PROFILES "
                "WHERE PROFILE_ID = 'integration-profile'"
            )
            profile_preserved = int(cursor.fetchone()[0]) == 1
        assert profile_preserved

        recreated = manager.initialize(
            recreate=True,
            confirmation=RECREATE_CONFIRMATION,
        )
        with temporary_connection() as connection, connection.cursor() as cursor:
            preserved_counts: dict[str, int] = {}
            for table_name in (
                "NL2SQL_APP_USERS",
                "USER_BUSINESS_SENTINEL",
                legacy_table,
                "NL2SQL_FEEDBACK_VECTORS",
            ):
                cursor.execute(
                    f"SELECT COUNT(*) FROM {table_name}"  # nosec B608 - validated constants
                )
                preserved_counts[table_name] = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM NL2SQL_PROFILES")
            core_profile_count = int(cursor.fetchone()[0])

        assert all(count == 1 for count in preserved_counts.values())
        assert core_profile_count == 0
        assert manager.status()["status"] == "ready"
        result.update(
            initialized_operation=initialized["operation"],
            migrated_operation=migrated["operation"],
            recreated_operation=recreated["operation"],
            profile_preserved_during_upgrade=profile_preserved,
            core_profile_count_after_recreate=core_profile_count,
            preserved_counts=preserved_counts,
            final_status="ready",
        )
    finally:
        if created:
            with (
                get_oracle_pool_manager().control_connection() as admin,
                admin.cursor() as cursor,
            ):
                cursor.execute(f'DROP USER "{username}" CASCADE')
                admin.commit()
            result["temporary_user_deleted"] = True

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover - operator boundary
    raise SystemExit(main())
