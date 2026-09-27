"""Agent の認証/RBAC の DDL（#215）。

`python -m app.cli.agent_security_migrate` が次の順で冪等に適用する。

1. 3 製品共通の `PLATFORM_*`（platform の `apply_platform_auth_schema`）
2. Agent のロールのデータ（`AGENT_ROLE_*`。このモジュール）
3. 組み込み SYSTEM_ADMIN ロールの確認（ユーザーは作らない。最初は構成管理者 `system_admin`）

エージェントと業務ビューは Oracle のテーブルを持たない（エージェントは Runtime repository の状態、
業務ビューは Run の metadata の文字列）ため、`AGENT_ROLE_AGENTS` / `AGENT_ROLE_BUSINESS_VIEWS` は
`PLATFORM_ROLES` への FK だけを持つ。
"""

from __future__ import annotations

from typing import Any

from pr_system_settings.auth.migrations import (
    PLATFORM_AUTH_DDL_IGNORED_ERRORS,
    apply_platform_auth_schema,
    oracle_error_code,
)
from pr_system_settings.auth.store import OracleAuthStore

AGENT_SECURITY_DDL: tuple[str, ...] = (
    """
    CREATE TABLE AGENT_ROLE_PERMISSIONS (
        ROLE_ID VARCHAR2(36) NOT NULL,
        PERMISSION_CODE VARCHAR2(128) NOT NULL,
        CONSTRAINT PK_AGENT_ROLE_PERMISSIONS PRIMARY KEY (ROLE_ID, PERMISSION_CODE),
        CONSTRAINT FK_AGENT_ROLE_PERMISSIONS_ROLE FOREIGN KEY (ROLE_ID)
            REFERENCES PLATFORM_ROLES (ROLE_ID) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE AGENT_ROLE_AGENTS (
        ROLE_ID VARCHAR2(36) NOT NULL,
        AGENT_ID VARCHAR2(128) NOT NULL,
        CONSTRAINT PK_AGENT_ROLE_AGENTS PRIMARY KEY (ROLE_ID, AGENT_ID),
        CONSTRAINT FK_AGENT_ROLE_AGENTS_ROLE FOREIGN KEY (ROLE_ID)
            REFERENCES PLATFORM_ROLES (ROLE_ID) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE AGENT_ROLE_BUSINESS_VIEWS (
        ROLE_ID VARCHAR2(36) NOT NULL,
        BUSINESS_VIEW_ID VARCHAR2(64) NOT NULL,
        CONSTRAINT PK_AGENT_ROLE_BUSINESS_VIEWS PRIMARY KEY (ROLE_ID, BUSINESS_VIEW_ID),
        CONSTRAINT FK_AGENT_ROLE_BVIEWS_ROLE FOREIGN KEY (ROLE_ID)
            REFERENCES PLATFORM_ROLES (ROLE_ID) ON DELETE CASCADE
    )
    """,
)


def apply_agent_security_schema(connection: Any) -> list[dict[str, str]]:
    """`AGENT_ROLE_*` を冪等に作る。既存 object は読み飛ばし、各文の結果を返す。"""
    results: list[dict[str, str]] = []
    with connection.cursor() as cursor:
        for index, statement in enumerate(AGENT_SECURITY_DDL, start=1):
            try:
                cursor.execute(statement)
                results.append({"index": str(index), "status": "ok"})
            except Exception as exc:
                code = oracle_error_code(exc)
                if code in PLATFORM_AUTH_DDL_IGNORED_ERRORS:
                    results.append({"index": str(index), "status": "skipped", "code": code or ""})
                    continue
                raise
    connection.commit()
    return results


def ensure_system_admin_role(connection: Any) -> None:
    """組み込み SYSTEM_ADMIN ロールを冪等に用意する（ユーザーは作らない）。"""
    with connection.cursor() as cursor:
        OracleAuthStore._merge_system_admin_role(cursor)
    connection.commit()


def apply_security_schema(connection: Any) -> dict[str, list[dict[str, str]]]:
    """PLATFORM_* → AGENT_ROLE_* → SYSTEM_ADMIN ロールの順に適用する。"""
    platform_results = apply_platform_auth_schema(connection)
    agent_results = apply_agent_security_schema(connection)
    ensure_system_admin_role(connection)
    return {"platform": platform_results, "agent": agent_results}
