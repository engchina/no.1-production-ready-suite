"""共通認証の DDL（PLATFORM_* テーブル。#212）。

3 製品が同じ Oracle schema で共有する。各製品の migration
（NL2SQL の `app_security_migrate`、RAG / Agent のシステムテーブル初期化）から冪等に実行する。
既存 object は ignored error で読み飛ばす。
旧名からの改名（NL2SQL の `NL2SQL_APP_*`）は、それを持つ製品の migration が先に行う。
"""

from __future__ import annotations

from typing import Any

# 既に存在する table / index / constraint は正常として読み飛ばす。
PLATFORM_AUTH_DDL_IGNORED_ERRORS = frozenset(
    {
        "ORA-00955",  # name is already used by an existing object
        "ORA-01408",  # such column list already indexed
        "ORA-02260",  # table can have only one primary key
        "ORA-02261",  # such unique or primary key already exists
        "ORA-02275",  # such a referential constraint already exists
    }
)

PLATFORM_AUTH_DDL: tuple[str, ...] = (
    """
    CREATE TABLE PLATFORM_USERS (
        USER_UUID VARCHAR2(36) NOT NULL,
        LOGIN_USER_ID VARCHAR2(64) NOT NULL,
        LOGIN_USER_ID_NORMALIZED VARCHAR2(64) NOT NULL,
        DISPLAY_NAME VARCHAR2(256) NOT NULL,
        PASSWORD_HASH VARCHAR2(512) NOT NULL,
        STATUS VARCHAR2(16) DEFAULT 'ACTIVE' NOT NULL,
        FORCE_PASSWORD_CHANGE NUMBER(1) DEFAULT 1 NOT NULL,
        FAILED_LOGIN_COUNT NUMBER(10) DEFAULT 0 NOT NULL,
        LOCKED_UNTIL TIMESTAMP WITH TIME ZONE,
        VERSION_NO NUMBER(19) DEFAULT 1 NOT NULL,
        CREATED_AT TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        UPDATED_AT TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        CONSTRAINT PK_PLATFORM_USERS PRIMARY KEY (USER_UUID),
        CONSTRAINT CK_PLATFORM_USERS_STATUS CHECK (STATUS IN ('ACTIVE', 'DISABLED')),
        CONSTRAINT CK_PLATFORM_USERS_FORCE_PWD CHECK (FORCE_PASSWORD_CHANGE IN (0, 1))
    )
    """,
    "CREATE UNIQUE INDEX UX_PLATFORM_USERS_LOGIN_USER_ID "
    "ON PLATFORM_USERS (LOGIN_USER_ID_NORMALIZED)",
    """
    CREATE TABLE PLATFORM_ROLES (
        ROLE_ID VARCHAR2(36) NOT NULL,
        ROLE_CODE VARCHAR2(64) NOT NULL,
        DISPLAY_NAME VARCHAR2(256) NOT NULL,
        DESCRIPTION VARCHAR2(1000) DEFAULT '-' NOT NULL,
        IS_BUILT_IN NUMBER(1) DEFAULT 0 NOT NULL,
        ARCHIVED NUMBER(1) DEFAULT 0 NOT NULL,
        VERSION_NO NUMBER(19) DEFAULT 1 NOT NULL,
        CREATED_AT TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        UPDATED_AT TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        CONSTRAINT PK_PLATFORM_ROLES PRIMARY KEY (ROLE_ID),
        CONSTRAINT CK_PLATFORM_ROLES_FLAGS CHECK (IS_BUILT_IN IN (0, 1) AND ARCHIVED IN (0, 1))
    )
    """,
    "CREATE UNIQUE INDEX UX_PLATFORM_ROLES_CODE ON PLATFORM_ROLES (ROLE_CODE)",
    """
    CREATE TABLE PLATFORM_USER_ROLES (
        USER_UUID VARCHAR2(36) NOT NULL,
        ROLE_ID VARCHAR2(36) NOT NULL,
        CREATED_AT TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        CONSTRAINT PK_PLATFORM_USER_ROLES PRIMARY KEY (USER_UUID, ROLE_ID),
        CONSTRAINT FK_PLATFORM_UR_USER_UUID FOREIGN KEY (USER_UUID)
            REFERENCES PLATFORM_USERS (USER_UUID),
        CONSTRAINT FK_PLATFORM_UR_ROLE FOREIGN KEY (ROLE_ID)
            REFERENCES PLATFORM_ROLES (ROLE_ID)
    )
    """,
    """
    CREATE TABLE PLATFORM_AUTH_SESSIONS (
        SESSION_ID VARCHAR2(36) NOT NULL,
        USER_UUID VARCHAR2(36) NOT NULL,
        TOKEN_HASH VARCHAR2(64) NOT NULL,
        CSRF_TOKEN_HASH VARCHAR2(64) NOT NULL,
        IDLE_EXPIRES_AT TIMESTAMP WITH TIME ZONE NOT NULL,
        ABSOLUTE_EXPIRES_AT TIMESTAMP WITH TIME ZONE NOT NULL,
        LAST_SEEN_AT TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        REVOKED_AT TIMESTAMP WITH TIME ZONE,
        CREATED_AT TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        CONSTRAINT PK_PLATFORM_AUTH_SESSIONS PRIMARY KEY (SESSION_ID),
        CONSTRAINT FK_PLATFORM_AUTH_SESSION_USER_UUID FOREIGN KEY (USER_UUID)
            REFERENCES PLATFORM_USERS (USER_UUID)
    )
    """,
    "CREATE UNIQUE INDEX UX_PLATFORM_AUTH_SESSION_TOKEN ON PLATFORM_AUTH_SESSIONS (TOKEN_HASH)",
    "CREATE INDEX IX_PLATFORM_AUTH_SESSION_USER_UUID "
    "ON PLATFORM_AUTH_SESSIONS (USER_UUID, REVOKED_AT)",
)


def oracle_error_code(exc: Exception) -> str | None:
    import re

    match = re.search(r"\bORA-\d{5}\b", str(exc).upper())
    return match.group(0) if match else None


def apply_platform_auth_schema(connection: Any) -> list[dict[str, str]]:
    """PLATFORM_* を冪等に作る。既存 object は読み飛ばし、各文の結果を返す。"""
    results: list[dict[str, str]] = []
    with connection.cursor() as cursor:
        for index, statement in enumerate(PLATFORM_AUTH_DDL, start=1):
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
