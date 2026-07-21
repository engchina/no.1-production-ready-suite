"""Oracle Deep Data Security V001 plan、適用、検証。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from app.clients.oracle_runtime import OraclePoolManager, get_oracle_pool_manager
from app.clients.oracle_statement_executor import oracle_statement_executor
from app.settings import Settings, get_settings

from .domain import Principal
from .service import SecurityApiError, SecurityService, get_security_service

PLAN_VERSION = "V001"
PASSWORD_PLACEHOLDER = "<secret:ORACLE_DEEPSEC_END_USER_PASSWORD>"  # nosec B105


def _strict_identifier(value: str) -> str:
    normalized = value.strip().strip('"').upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", normalized):
        raise SecurityApiError(400, f"安全でない Oracle identifier です: {value}")
    return normalized


def _quoted_password(value: str) -> str:
    if not value or len(value) > 256 or any(ord(char) < 32 for char in value):
        raise SecurityApiError(
            503, "ORACLE_DEEPSEC_END_USER_PASSWORD を安全な値で設定してください。"
        )
    return '"' + value.replace('"', '""') + '"'


def _trusted_identifier_sql(template: str, **identifiers: str) -> str:
    """固定 SQL template へ検証済み Oracle identifier だけを埋め込む。"""

    rendered = template
    for key, value in identifiers.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}(?:\.[A-Z][A-Z0-9_$#]{0,127})*", value):
            raise SecurityApiError(400, f"安全でない Oracle identifier です: {value}")
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


@dataclass(frozen=True, slots=True)
class DeepSecStep:
    step_no: int
    key: str
    title: str
    description: str
    statements: tuple[str, ...]
    ignored_error_codes: frozenset[str] = frozenset()

    @property
    def checksum(self) -> str:
        payload = "\n-- statement --\n".join(statement.strip() for statement in self.statements)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_v001_plan(settings: Settings) -> tuple[DeepSecStep, ...]:
    owner = _strict_identifier(settings.oracle_user)
    end_user = _strict_identifier(settings.oracle_deepsec_end_user)
    role = "NL2SQL_APP_DB_ROLE"
    data_role = "NL2SQL_APP_DATA_ROLE"
    probe = f"{owner}.NL2SQL_DEEPSEC_PROBE"

    role_step = DeepSecStep(
        step_no=1,
        key="principals_and_roles",
        title="共有 END USER とロール",
        description="共有 local END USER、最小 DB role、local DATA ROLE を作成して関連付けます。",
        statements=(
            f"CREATE ROLE {role}",
            f"GRANT CREATE SESSION TO {role}",
            f"CREATE DATA ROLE IF NOT EXISTS {data_role}",
            (
                f"CREATE END USER IF NOT EXISTS {end_user} IDENTIFIED BY {PASSWORD_PLACEHOLDER} "
                f"SCHEMA {owner}"
            ),
            f"GRANT {role} TO {data_role}",
            f"GRANT DATA ROLE {data_role} TO {end_user}",
        ),
        ignored_error_codes=frozenset({"ORA-01921"}),
    )
    context_package_spec = _trusted_identifier_sql(
        """
        CREATE OR REPLACE PACKAGE {owner}.NL2SQL_DEEPSEC_CTX_PKG AUTHID DEFINER AS
          PROCEDURE SET_APP_USER(p_user_id IN VARCHAR2);
          PROCEDURE CLEAR_APP_USER;
        END NL2SQL_DEEPSEC_CTX_PKG
        """,
        owner=owner,
    )
    context_package_body = _trusted_identifier_sql(
        """
        CREATE OR REPLACE PACKAGE BODY {owner}.NL2SQL_DEEPSEC_CTX_PKG AS
          PROCEDURE SET_APP_USER(p_user_id IN VARCHAR2) IS
            v_count NUMBER;
          BEGIN
            SELECT COUNT(*) INTO v_count
              FROM {owner}.NL2SQL_APP_USERS
             WHERE USER_ID = p_user_id AND STATUS = 'ACTIVE';
            IF v_count <> 1 THEN
              RAISE_APPLICATION_ERROR(-20001, 'invalid application user');
            END IF;
            DBMS_SESSION.SET_CONTEXT('NL2SQL_APP_USER_CTX', 'APP_USER_ID', p_user_id);
          END SET_APP_USER;

          PROCEDURE CLEAR_APP_USER IS
          BEGIN
            DBMS_SESSION.CLEAR_CONTEXT('NL2SQL_APP_USER_CTX');
          END CLEAR_APP_USER;
        END NL2SQL_DEEPSEC_CTX_PKG
        """,
        owner=owner,
    )
    context_step = DeepSecStep(
        step_no=2,
        key="application_context",
        title="アプリケーションコンテキスト",
        description="認証済み application user UUID を検証して session context へ設定します。",
        statements=(
            context_package_spec,
            context_package_body,
            f"CREATE OR REPLACE CONTEXT NL2SQL_APP_USER_CTX USING {owner}.NL2SQL_DEEPSEC_CTX_PKG",
            f"GRANT EXECUTE ON {owner}.NL2SQL_DEEPSEC_CTX_PKG TO {role}",
        ),
    )
    probe_create_sql = _trusted_identifier_sql(
        """
        DECLARE
          v_count NUMBER;
        BEGIN
          SELECT COUNT(*) INTO v_count FROM ALL_TABLES
           WHERE OWNER = '{owner}' AND TABLE_NAME = 'NL2SQL_DEEPSEC_PROBE';
          IF v_count = 0 THEN
            EXECUTE IMMEDIATE '
              CREATE TABLE {probe} (
                PROBE_ID NUMBER PRIMARY KEY,
                SCOPE_CODE VARCHAR2(64) NOT NULL,
                PUBLIC_TEXT VARCHAR2(256) NOT NULL,
                SENSITIVE_TEXT VARCHAR2(256) NOT NULL
              )';
          END IF;
        END
        """,
        owner=owner,
        probe=probe,
    )
    probe_merge_sql = _trusted_identifier_sql(
        """
        MERGE INTO {probe} p
        USING (
          SELECT 1 PROBE_ID, 'SALES' SCOPE_CODE, '営業公開データ' PUBLIC_TEXT,
                 '営業機密データ' SENSITIVE_TEXT FROM DUAL
          UNION ALL
          SELECT 2, 'HR', '人事公開データ', '人事機密データ' FROM DUAL
        ) s
        ON (p.PROBE_ID = s.PROBE_ID)
        WHEN MATCHED THEN UPDATE SET
          p.SCOPE_CODE = s.SCOPE_CODE, p.PUBLIC_TEXT = s.PUBLIC_TEXT,
          p.SENSITIVE_TEXT = s.SENSITIVE_TEXT
        WHEN NOT MATCHED THEN INSERT
          (PROBE_ID, SCOPE_CODE, PUBLIC_TEXT, SENSITIVE_TEXT)
        VALUES
          (s.PROBE_ID, s.SCOPE_CODE, s.PUBLIC_TEXT, s.SENSITIVE_TEXT)
        """,
        probe=probe,
    )
    probe_step = DeepSecStep(
        step_no=3,
        key="verification_object",
        title="検証オブジェクト",
        description="行・列分離を証明する専用 probe table と固定 fixture を作成します。",
        statements=(
            probe_create_sql,
            probe_merge_sql,
            f"GRANT SELECT ON {probe} TO {role}",
        ),
    )
    row_predicate = _trusted_identifier_sql(
        """
        EXISTS (
          SELECT 1
            FROM {owner}.NL2SQL_APP_USER_ROLES ur
            JOIN {owner}.NL2SQL_APP_ROLES r ON r.ROLE_ID = ur.ROLE_ID
            JOIN {owner}.NL2SQL_APP_DATA_ENTITLEMENTS e ON e.ROLE_ID = r.ROLE_ID
           WHERE ur.USER_ID = SYS_CONTEXT('NL2SQL_APP_USER_CTX', 'APP_USER_ID')
             AND r.ARCHIVED = 0
             AND e.RESOURCE_CODE = 'NL2SQL_DEEPSEC_PROBE'
             AND e.CAPABILITY IN ('ROW_READ', 'FULL')
             AND (e.SCOPE_CODE = '*' OR e.SCOPE_CODE = SCOPE_CODE)
        )
        """,
        owner=owner,
    ).strip()
    sensitive_predicate = _trusted_identifier_sql(
        """
        EXISTS (
          SELECT 1
            FROM {owner}.NL2SQL_APP_USER_ROLES ur
            JOIN {owner}.NL2SQL_APP_ROLES r ON r.ROLE_ID = ur.ROLE_ID
            JOIN {owner}.NL2SQL_APP_DATA_ENTITLEMENTS e ON e.ROLE_ID = r.ROLE_ID
           WHERE ur.USER_ID = SYS_CONTEXT('NL2SQL_APP_USER_CTX', 'APP_USER_ID')
             AND r.ARCHIVED = 0
             AND e.RESOURCE_CODE = 'NL2SQL_DEEPSEC_PROBE'
             AND e.CAPABILITY IN ('SENSITIVE_READ', 'FULL')
             AND (e.SCOPE_CODE = '*' OR e.SCOPE_CODE = SCOPE_CODE)
        )
        """,
        owner=owner,
    ).strip()
    grants_step = DeepSecStep(
        step_no=4,
        key="data_grants",
        title="Data Grants と mandatory enforcement",
        description="classic context を参照する加法型の行・列 Data Grant を適用します。",
        statements=(
            f"""
            CREATE OR REPLACE DATA GRANT {owner}.NL2SQL_DEEPSEC_PROBE_ROWS
              AS SELECT (PROBE_ID, SCOPE_CODE, PUBLIC_TEXT)
              ON {probe}
              WHERE {row_predicate}
              TO {data_role}
            """,
            f"""
            CREATE OR REPLACE DATA GRANT {owner}.NL2SQL_DEEPSEC_PROBE_SENSITIVE
              AS SELECT (SENSITIVE_TEXT)
              ON {probe}
              WHERE {sensitive_predicate}
              TO {data_role}
            """,
            f"SET USE DATA GRANTS ONLY ON {probe} ENABLED",
        ),
    )
    return (role_step, context_step, probe_step, grants_step)


class DeepSecService:
    def __init__(
        self,
        settings: Settings,
        security: SecurityService,
        pools: OraclePoolManager,
    ) -> None:
        self.settings = settings
        self.security = security
        self.pools = pools

    def plan(self) -> dict[str, object]:
        states = self.security.store.get_deepsec_states()
        steps = []
        for step in build_v001_plan(self.settings):
            state = states.get((PLAN_VERSION, step.step_no), {})
            steps.append(
                {
                    "step_no": step.step_no,
                    "key": step.key,
                    "title": step.title,
                    "description": step.description,
                    "checksum": step.checksum,
                    "status": state.get("status", "PENDING"),
                    "error_message": state.get("error_message", ""),
                    "executed_at": state.get("executed_at"),
                    "sql": [self._preview_sql(statement) for statement in step.statements],
                }
            )
        return {
            "version": PLAN_VERSION,
            "driver_mode": self.settings.oracle_driver_mode,
            "deepsec_enabled": self.settings.oracle_deepsec_enabled,
            "end_user": self.settings.oracle_deepsec_end_user,
            "steps": steps,
        }

    def status(self) -> dict[str, object]:
        result: dict[str, object] = {
            "configured": False,
            "driver_mode": self.settings.oracle_driver_mode,
            "deepsec_enabled": self.settings.oracle_deepsec_enabled,
            "end_user": self.settings.oracle_deepsec_end_user,
            "objects": {},
            "message": "Deep Data Security は未設定です。",
        }
        if not self.settings.oracle_user or not self.settings.oracle_dsn:
            return result
        owner = _strict_identifier(self.settings.oracle_user)
        try:
            with self.pools.control_connection() as conn, conn.cursor() as cursor:
                checks = {
                    "end_user": (
                        "SELECT COUNT(*) FROM DBA_END_USERS WHERE USERNAME = :name",
                        _strict_identifier(self.settings.oracle_deepsec_end_user),
                    ),
                    "data_role": (
                        "SELECT COUNT(*) FROM DBA_DATA_ROLES WHERE DATA_ROLE = :name",
                        "NL2SQL_APP_DATA_ROLE",
                    ),
                    "context": (
                        "SELECT COUNT(*) FROM ALL_CONTEXT WHERE NAMESPACE = :name",
                        "NL2SQL_APP_USER_CTX",
                    ),
                    "probe_table": (
                        "SELECT COUNT(*) FROM ALL_TABLES WHERE OWNER = :owner "
                        "AND TABLE_NAME = 'NL2SQL_DEEPSEC_PROBE'",
                        owner,
                    ),
                    "data_grants": (
                        "SELECT COUNT(*) FROM DBA_DATA_GRANTS WHERE OBJECT_OWNER = :owner "
                        "AND OBJECT_NAME = 'NL2SQL_DEEPSEC_PROBE'",
                        owner,
                    ),
                }
                objects: dict[str, int] = {}
                for key, (sql, value) in checks.items():
                    bind_name = "owner" if ":owner" in sql else "name"
                    cursor.execute(sql, {bind_name: value})
                    objects[key] = int(cursor.fetchone()[0])
                result["objects"] = objects
                result["configured"] = (
                    all(
                        objects[key] > 0
                        for key in ("end_user", "data_role", "context", "probe_table")
                    )
                    and objects["data_grants"] >= 2
                )
                result["message"] = (
                    "Deep Data Security の検証オブジェクトは構成済みです。"
                    if result["configured"]
                    else "DeepSec V001 に未適用のオブジェクトがあります。"
                )
        except Exception as exc:
            result["message"] = f"DeepSec 状態を確認できませんでした: {self._safe_error(exc)}"
        return result

    def apply_step(self, step_no: int, checksum: str, actor: Principal) -> dict[str, object]:
        if not self.settings.oracle_deepsec_enabled:
            raise SecurityApiError(409, "ORACLE_DEEPSEC_ENABLED=true を設定してください。")
        self.pools.validate_deepsec_configuration()
        plan = {step.step_no: step for step in build_v001_plan(self.settings)}
        step = plan.get(step_no)
        if step is None:
            raise SecurityApiError(404, "DeepSec plan step が見つかりません。")
        if not checksum or checksum != step.checksum:
            raise SecurityApiError(
                409, "SQL plan のチェックサムが一致しません。画面を再読込してください。"
            )
        states = self.security.store.get_deepsec_states()
        for previous in range(1, step_no):
            if states.get((PLAN_VERSION, previous), {}).get("status") != "APPLIED":
                raise SecurityApiError(409, "前の DeepSec step を先に適用してください。")
        self.security.store.set_deepsec_state(
            version=PLAN_VERSION,
            step_no=step.step_no,
            step_key=step.key,
            checksum=step.checksum,
            status="RUNNING",
            error_message="",
            executed_by=actor.user_id,
        )
        statements = [self._execution_sql(statement) for statement in step.statements]
        try:
            with self.pools.control_connection() as conn:
                results = oracle_statement_executor.execute(
                    conn,
                    statements,
                    atomic=False,
                    include_sql=False,
                    ignored_error_codes=step.ignored_error_codes,
                )
            errors = [item for item in results if item["status"] == "error"]
            if errors:
                raise SecurityApiError(
                    500, str(errors[0].get("error_message") or "SQL execution failed")
                )
            self.security.store.set_deepsec_state(
                version=PLAN_VERSION,
                step_no=step.step_no,
                step_key=step.key,
                checksum=step.checksum,
                status="APPLIED",
                error_message="",
                executed_by=actor.user_id,
            )
            self.security._audit_mutation(
                actor,
                "DEEPSEC_STEP_APPLIED",
                "DEEPSEC_STEP",
                f"{PLAN_VERSION}:{step.step_no}",
                "",
                "",
            )
            return {
                "version": PLAN_VERSION,
                "step_no": step.step_no,
                "status": "APPLIED",
                "results": results,
            }
        except Exception as exc:
            safe_error = self._safe_error(exc)
            self.security.store.set_deepsec_state(
                version=PLAN_VERSION,
                step_no=step.step_no,
                step_key=step.key,
                checksum=step.checksum,
                status="FAILED",
                error_message=safe_error,
                executed_by=actor.user_id,
            )
            self.security._audit(
                actor=actor.user_id,
                event="DEEPSEC_STEP_FAILED",
                target_type="DEEPSEC_STEP",
                target_id=f"{PLAN_VERSION}:{step.step_no}",
                outcome="FAILED",
                detail={"error": safe_error},
                request_id="",
                client_ip="",
            )
            if isinstance(exc, SecurityApiError):
                raise
            raise SecurityApiError(500, f"DeepSec step の実行に失敗しました: {safe_error}") from exc

    def verify(self, actor: Principal) -> dict[str, object]:
        if not self.settings.oracle_deepsec_enabled:
            raise SecurityApiError(409, "ORACLE_DEEPSEC_ENABLED=true を設定してください。")
        owner = _strict_identifier(self.settings.oracle_user)
        probe = f"{owner}.NL2SQL_DEEPSEC_PROBE"
        checks: list[dict[str, object]] = []
        try:
            with self.pools.unscoped_data_connection() as conn, conn.cursor() as cursor:
                no_context_sql = _trusted_identifier_sql(
                    "SELECT COUNT(*) FROM {probe}", probe=probe
                )
                cursor.execute(no_context_sql)
                no_context_count = int(cursor.fetchone()[0])
            checks.append(
                {
                    "key": "no_context",
                    "passed": no_context_count == 0,
                    "detail": f"context 未設定の取得行数: {no_context_count}",
                }
            )
            with self.pools.data_connection(actor.user_id) as conn, conn.cursor() as cursor:
                full_sql = _trusted_identifier_sql(
                    "SELECT * FROM {probe} ORDER BY PROBE_ID", probe=probe
                )
                cursor.execute(full_sql)
                full_rows = cursor.fetchall()
            checks.append(
                {
                    "key": "full_subject",
                    "passed": len(full_rows) == 2 and all(row[3] is not None for row in full_rows),
                    "detail": f"SYSTEM_ADMIN probe rows: {len(full_rows)}",
                }
            )
            limited = self._find_limited_subject(actor.user_id)
            if limited is None:
                checks.append(
                    {
                        "key": "limited_subject",
                        "passed": False,
                        "detail": (
                            "ROW_READ の限定 role を持つ有効ユーザーを作成して"
                            "再検証してください。"
                        ),
                    }
                )
            else:
                limited_user_id, expected_scopes = limited
                with self.pools.data_connection(limited_user_id) as conn, conn.cursor() as cursor:
                    limited_sql = _trusted_identifier_sql(
                        "SELECT PROBE_ID, SCOPE_CODE, PUBLIC_TEXT, SENSITIVE_TEXT, "
                        "ORA_IS_COLUMN_AUTHORIZED(SENSITIVE_TEXT) FROM {probe} "
                        "ORDER BY PROBE_ID",
                        probe=probe,
                    )
                    cursor.execute(limited_sql)
                    limited_rows = cursor.fetchall()
                actual_scopes = {str(row[1]) for row in limited_rows}
                # Deep Sec の SELECT は未許可セルをエラーではなく NULL として返す。
                # access-check function も併用し、実データ NULL と認可マスクを区別する。
                sensitive_masked = all(
                    row[3] is None and not bool(row[4]) for row in limited_rows
                )
                checks.append(
                    {
                        "key": "limited_subject",
                        "passed": actual_scopes == expected_scopes and sensitive_masked,
                        "detail": (
                            f"限定 subject scopes={sorted(actual_scopes)}, "
                            f"sensitive_masked={sensitive_masked}"
                        ),
                    }
                )
        except Exception as exc:
            safe_error = self._safe_error(exc)
            self.security._audit(
                actor=actor.user_id,
                event="DEEPSEC_VERIFICATION_FAILED",
                target_type="DEEPSEC_PLAN",
                target_id=PLAN_VERSION,
                outcome="FAILED",
                detail={"error": safe_error},
                request_id="",
                client_ip="",
            )
            raise SecurityApiError(
                500, f"DeepSec 検証に失敗しました: {safe_error}"
            ) from exc
        passed = all(bool(item["passed"]) for item in checks)
        self.security._audit_mutation(
            actor,
            "DEEPSEC_VERIFIED" if passed else "DEEPSEC_VERIFICATION_INCOMPLETE",
            "DEEPSEC_PLAN",
            PLAN_VERSION,
            "",
            "",
        )
        return {
            "version": PLAN_VERSION,
            "passed": passed,
            "checked_at": datetime.now(UTC).isoformat(),
            "checks": checks,
        }

    def _find_limited_subject(self, excluded_user_id: str) -> tuple[str, set[str]] | None:
        for user in self.security.store.list_users():
            if user.user_id == excluded_user_id or user.status != "ACTIVE":
                continue
            scopes: set[str] = set()
            has_full = False
            for role_id in user.role_ids:
                role = self.security.store.get_role(role_id)
                if role is None or role.archived:
                    continue
                for entitlement in role.entitlements:
                    if entitlement.resource_code != "NL2SQL_DEEPSEC_PROBE":
                        continue
                    if entitlement.capability == "FULL" or entitlement.scope_code == "*":
                        has_full = True
                    elif entitlement.capability == "ROW_READ":
                        scopes.add(entitlement.scope_code)
            if scopes and not has_full:
                return user.user_id, scopes
        return None

    def _execution_sql(self, statement: str) -> str:
        if PASSWORD_PLACEHOLDER not in statement:
            return statement.strip()
        return statement.replace(
            PASSWORD_PLACEHOLDER,
            _quoted_password(self.settings.oracle_deepsec_end_user_password),
        ).strip()

    @staticmethod
    def _preview_sql(statement: str) -> str:
        return re.sub(r"\s+$", "", statement.strip())

    def _safe_error(self, exc: Exception) -> str:
        text = str(exc).replace("\n", " ")
        secret = self.settings.oracle_deepsec_end_user_password
        if secret:
            text = text.replace(secret, "[REDACTED]")
        # END USER password は exception に出ない前提だが、長い driver detail は切り捨てる。
        return text[:1000]


def get_deepsec_service() -> DeepSecService:
    return DeepSecService(get_settings(), get_security_service(), get_oracle_pool_manager())
