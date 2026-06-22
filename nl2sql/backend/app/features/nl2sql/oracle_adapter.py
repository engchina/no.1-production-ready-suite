"""Optional Oracle runtime adapter for NL2SQL.

この module は `oracledb` を import-time dependency にしない。local / CI は deterministic
adapter のまま動き、`NL2SQL_RUNTIME_MODE=oracle` のときだけ runtime import する。
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.settings import Settings

from .models import CsvImportColumn, QueryResults, SchemaCatalog, SchemaColumn, SchemaTable


class OracleAdapterError(RuntimeError):
    """Oracle adapter の実行時エラー。"""


WALLET_PASSWORD_REQUIRED_ERROR = (
    "Oracle Wallet がパスワードを必要としています。"  # nosec B105
    "ORACLE_WALLET_PASSWORD または ORACLE_PASSWORD を設定してください。"
)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    read = getattr(value, "read", None)
    if callable(read):
        return str(read())
    return str(value)


def _coerce_result_value(value: Any) -> Any:
    read = getattr(value, "read", None)
    if callable(read):
        return _coerce_text(value)
    return value


def _extract_select_statement(text: str) -> str:
    """LLM/Select AI response から最初の SELECT/WITH statement を抽出する。"""
    cleaned = text.strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        for key in ("sql", "generated_sql", "query", "result"):
            candidate = str(payload.get(key) or "").strip()
            if candidate:
                extracted = _extract_select_statement(candidate)
                if extracted:
                    return extracted
    candidates: list[str] = []
    for match in re.finditer(r"\b(with|select)\b", cleaned, flags=re.IGNORECASE):
        candidate = cleaned[match.start() :].strip()
        if match.group(1).lower() == "with" or re.search(
            r"\bfrom\b", candidate, flags=re.IGNORECASE
        ):
            candidates.append(candidate)
    if not candidates:
        return ""
    statement = candidates[-1]
    error_match = re.search(r"\bException encountered\s*:", statement, flags=re.IGNORECASE)
    if error_match:
        statement = statement[: error_match.start()].strip()
    return statement.split(";", 1)[0].strip()


def _quote_identifier(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _strict_sql_name(value: str) -> str:
    normalized = value.strip().strip('"').upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", normalized):
        raise OracleAdapterError(f"安全でない Oracle object name です: {value}")
    return normalized


def _oracle_connect_kwargs(settings: Settings) -> dict[str, object]:
    """python-oracledb connect に渡す共通 kwargs を作る。"""
    kwargs: dict[str, object] = {
        "user": settings.oracle_user,
        "dsn": _oracle_connection_test_dsn(settings),
        "tcp_connect_timeout": settings.nl2sql_oracle_connect_timeout_seconds,
    }
    if settings.oracle_password.strip():
        kwargs["password"] = settings.oracle_password
    _add_wallet_kwargs(settings, kwargs)
    return kwargs


def _oracle_connection_test_dsn(settings: Settings) -> str:
    """Wallet alias の descriptor が取れれば、長い retry 設定を外して接続テストする。"""
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    if not wallet_dir:
        return settings.oracle_dsn
    descriptor = _tns_alias_descriptor(Path(wallet_dir).expanduser(), settings.oracle_dsn)
    if not descriptor:
        return settings.oracle_dsn
    return _strip_tns_retry_settings(descriptor)


def _tns_alias_descriptor(wallet_path: Path, alias: str) -> str | None:
    """tnsnames.ora から指定 alias の connect descriptor を抜き出す。"""
    tnsnames = wallet_path / "tnsnames.ora"
    if not alias.strip() or not tnsnames.is_file():
        return None
    try:
        content = tnsnames.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    for match in re.finditer(r"(?im)^\s*([A-Za-z0-9_.-]+)\s*=\s*", content):
        if match.group(1).lower() != alias.lower():
            continue
        descriptor_start = content.find("(", match.end())
        if descriptor_start < 0:
            return None
        return _balanced_parenthesized_text(content, descriptor_start)
    return None


def _balanced_parenthesized_text(content: str, start: int) -> str | None:
    """start 位置から始まる括弧式を top-level まで読み取る。"""
    depth = 0
    for index in range(start, len(content)):
        char = content[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]
        if depth < 0:
            return None
    return None


def _strip_tns_retry_settings(descriptor: str) -> str:
    """ADB Wallet の長い retry 設定を接続テスト用に取り除く。"""
    without_retry_count = re.sub(r"\(\s*retry_count\s*=\s*\d+\s*\)", "", descriptor, flags=re.I)
    return re.sub(r"\(\s*retry_delay\s*=\s*\d+\s*\)", "", without_retry_count, flags=re.I)


def _add_wallet_kwargs(settings: Settings, kwargs: dict[str, object]) -> None:
    """Wallet 設定を kwargs に追加する。"""
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    if not wallet_dir:
        return

    wallet_path = Path(wallet_dir).expanduser()
    if not wallet_path.is_dir():
        return

    wallet_password = settings.oracle_wallet_password.strip() or settings.oracle_password.strip()
    if not wallet_password and _wallet_requires_password(wallet_path):
        raise OracleAdapterError(WALLET_PASSWORD_REQUIRED_ERROR)

    resolved_wallet_path = str(wallet_path)
    kwargs["config_dir"] = resolved_wallet_path
    kwargs["wallet_location"] = resolved_wallet_path
    if wallet_password:
        kwargs["wallet_password"] = wallet_password


def _wallet_dir_exists(settings: Settings) -> bool:
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    return bool(wallet_dir and Path(wallet_dir).expanduser().is_dir())


def _wallet_requires_password(wallet_path: Path) -> bool:
    """自動ログイン Wallet がなく、秘密鍵が暗号化されていればパスワード必須。"""
    try:
        files = [path for path in wallet_path.iterdir() if path.is_file()]
    except OSError:
        return False
    names = {path.name.lower() for path in files}
    if "ewallet.p12" in names:
        return True
    encrypted_pem_exists = any(
        path.suffix.lower() == ".pem" and _pem_file_is_encrypted(path) for path in files
    )
    if encrypted_pem_exists:
        return True
    return "cwallet.sso" not in names


def _pem_file_is_encrypted(path: Path) -> bool:
    """暗号化 PEM の代表的な marker だけを少量読み取って判定する。"""
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    text = head.decode("utf-8", errors="ignore").upper()
    return "BEGIN ENCRYPTED PRIVATE KEY" in text or "PROC-TYPE: 4,ENCRYPTED" in text


class OracleNl2SqlAdapter:
    """Thin python-oracledb wrapper.

    実 SQL 生成・実行はここへ閉じ込める。呼び出し側 service は同じ API shape のまま
    deterministic / oracle runtime を切り替える。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._oracledb: Any | None = None
        self._client_initialized = False

    def is_configured(self) -> bool:
        if not self.settings.oracle_user.strip() or not self.settings.oracle_dsn.strip():
            return False
        return bool(self.settings.oracle_password.strip() or _wallet_dir_exists(self.settings))

    def module_available(self) -> bool:
        try:
            self._load_oracledb()
        except OracleAdapterError:
            return False
        return True

    def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "Oracle 接続情報が不足しています。"
        try:
            with self.connection() as conn, conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM DUAL")
                cursor.fetchone()
            return True, "Oracle 接続に成功しました。"
        except Exception as exc:
            return False, f"Oracle 接続に失敗しました: {exc}"

    @contextmanager
    def connection(self) -> Iterator[Any]:
        oracledb = self._load_oracledb()
        self._init_client(oracledb)
        if not self.is_configured():
            raise OracleAdapterError("Oracle 接続情報が不足しています。")
        conn = oracledb.connect(**_oracle_connect_kwargs(self.settings))
        try:
            yield conn
        finally:
            conn.close()

    def fetch_catalog(self) -> SchemaCatalog:
        sql = """
            SELECT
                c.table_name,
                NVL(tc.comments, c.table_name) AS table_comment,
                c.column_name,
                NVL(cc.comments, c.column_name) AS column_comment,
                c.data_type,
                c.nullable,
                c.column_id,
                t.num_rows
            FROM user_tab_columns c
            LEFT JOIN user_tables t ON t.table_name = c.table_name
            LEFT JOIN user_tab_comments tc ON tc.table_name = c.table_name
            LEFT JOIN user_col_comments cc
              ON cc.table_name = c.table_name AND cc.column_name = c.column_name
            ORDER BY c.table_name, c.column_id
        """
        tables: dict[str, SchemaTable] = {}
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(sql)
            for row in cursor:
                table_name = str(row[0])
                table_comment = str(row[1] or table_name)
                column_name = str(row[2])
                column_comment = str(row[3] or column_name)
                data_type = str(row[4])
                nullable = str(row[5]).upper() == "Y"
                row_count = int(row[7]) if row[7] is not None else None
                table = tables.setdefault(
                    table_name,
                    SchemaTable(
                        table_name=table_name,
                        logical_name=table_comment,
                        comment=table_comment,
                        row_count=row_count,
                    ),
                )
                table.columns.append(
                    SchemaColumn(
                        column_name=column_name,
                        logical_name=column_comment,
                        data_type=data_type,
                        nullable=nullable,
                    )
                )
            self._load_constraints(cursor, tables)
            self._load_sample_values(cursor, tables)
        return SchemaCatalog(
            refreshed_at=datetime.now(UTC).isoformat(), tables=list(tables.values())
        )

    def _load_constraints(self, cursor: Any, tables: dict[str, SchemaTable]) -> None:
        cursor.execute("""
            SELECT
                uc.table_name,
                uc.constraint_name,
                uc.constraint_type,
                LISTAGG(ucc.column_name, ', ') WITHIN GROUP (ORDER BY ucc.position) AS columns
            FROM user_constraints uc
            LEFT JOIN user_cons_columns ucc
              ON ucc.constraint_name = uc.constraint_name
             AND ucc.table_name = uc.table_name
            WHERE uc.constraint_type IN ('P', 'R', 'U', 'C')
            GROUP BY uc.table_name, uc.constraint_name, uc.constraint_type
            ORDER BY uc.table_name, uc.constraint_name
            """)
        for table_name, constraint_name, constraint_type, columns in cursor:
            table = tables.get(str(table_name))
            if not table:
                continue
            column_text = str(columns or "").strip()
            suffix = f"({column_text})" if column_text else ""
            table.constraints.append(f"{constraint_name} {constraint_type}{suffix}")

    def _load_sample_values(self, cursor: Any, tables: dict[str, SchemaTable]) -> None:
        sample_rows = max(self.settings.nl2sql_schema_sample_rows, 0)
        sample_columns = max(self.settings.nl2sql_schema_sample_columns_per_table, 0)
        if sample_rows == 0 or sample_columns == 0:
            return
        for table in tables.values():
            quoted_table = _quote_identifier(table.table_name)
            for column in table.columns[:sample_columns]:
                quoted_column = _quote_identifier(column.column_name)
                try:
                    # Safe: identifiers come from Oracle catalog metadata and are quoted.
                    sample_sql = (
                        f"SELECT DISTINCT {quoted_column} "  # nosec B608
                        f"FROM {quoted_table} "
                        f"WHERE {quoted_column} IS NOT NULL "
                        "FETCH FIRST :sample_rows ROWS ONLY"
                    )
                    cursor.execute(
                        sample_sql,
                        {"sample_rows": sample_rows},
                    )
                    column.sample_values = [
                        _coerce_text(row[0]) for row in cursor if _coerce_text(row[0])
                    ]
                except Exception:
                    column.sample_values = []

    def execute_select(self, sql: str, max_rows: int) -> QueryResults:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(sql)
            columns = [description[0] for description in cursor.description or []]
            rows: list[dict[str, Any]] = []
            for row in cursor.fetchmany(max_rows):
                rows.append(
                    {columns[index]: _coerce_result_value(value) for index, value in enumerate(row)}
                )
        return QueryResults(columns=columns, rows=rows, total=len(rows))

    def list_db_admin_objects(self, object_type: str) -> list[dict[str, Any]]:
        """List user tables or views for the DB admin console."""
        normalized_type = "view" if object_type.lower() == "view" else "table"
        if normalized_type == "view":
            sql = """
                SELECT v.view_name, USER, NULL, NVL(c.comments, ' ')
                FROM user_views v
                LEFT JOIN user_tab_comments c ON c.table_name = v.view_name
                WHERE v.view_name NOT LIKE '%$%'
                ORDER BY v.view_name
            """
        else:
            sql = """
                SELECT t.table_name, USER, t.num_rows, NVL(c.comments, ' ')
                FROM user_tables t
                LEFT JOIN user_tab_comments c ON c.table_name = t.table_name
                WHERE t.table_name NOT LIKE '%$%'
                ORDER BY t.table_name
            """
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
        return [
            {
                "name": str(row[0] or ""),
                "owner": str(row[1] or ""),
                "object_type": normalized_type,
                "row_count": int(row[2]) if row[2] is not None else None,
                "comment": _coerce_text(row[3]) if len(row) > 3 else "",
            }
            for row in rows
        ]

    def get_db_admin_object_detail(self, *, object_name: str, object_type: str) -> dict[str, Any]:
        """Return columns and DBMS_METADATA DDL for a table/view."""
        safe_name = _strict_sql_name(object_name)
        normalized_type = "VIEW" if object_type.lower() == "view" else "TABLE"
        columns: list[SchemaColumn] = []
        warnings: list[str] = []
        comment = ""
        row_count: int | None = None
        ddl = ""
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.column_name,
                       c.data_type ||
                       CASE
                         WHEN c.data_type IN ('VARCHAR2','CHAR','NVARCHAR2','NCHAR')
                         THEN '(' || c.data_length || ')'
                         WHEN c.data_type = 'NUMBER' AND c.data_precision IS NOT NULL
                         THEN '(' || c.data_precision ||
                              CASE WHEN c.data_scale > 0 THEN ',' || c.data_scale ELSE '' END || ')'
                         ELSE ''
                       END AS data_type,
                       c.nullable,
                       NVL(cc.comments, ' ')
                FROM user_tab_columns c
                LEFT JOIN user_col_comments cc
                  ON cc.table_name = c.table_name AND cc.column_name = c.column_name
                WHERE c.table_name = :object_name
                ORDER BY c.column_id
                """,
                {"object_name": safe_name},
            )
            for column_name, data_type, nullable, column_comment in cursor:
                columns.append(
                    SchemaColumn(
                        column_name=str(column_name or ""),
                        logical_name=_coerce_text(column_comment) or str(column_name or ""),
                        data_type=str(data_type or ""),
                        nullable=str(nullable or "Y").upper() == "Y",
                        comment=_coerce_text(column_comment),
                    )
                )
            cursor.execute(
                "SELECT comments FROM user_tab_comments WHERE table_name = :object_name",
                {"object_name": safe_name},
            )
            row = cursor.fetchone()
            comment = _coerce_text(row[0]) if row and row[0] else ""
            if normalized_type == "TABLE":
                try:
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {_quote_identifier(safe_name)}"  # nosec B608
                    )
                    row = cursor.fetchone()
                    row_count = int(row[0] or 0) if row else None
                except Exception as exc:
                    warnings.append(f"row count の取得に失敗しました: {exc}")
            try:
                cursor.execute(
                    "SELECT DBMS_METADATA.GET_DDL(:object_type, :object_name) FROM DUAL",
                    {"object_type": normalized_type, "object_name": safe_name},
                )
                row = cursor.fetchone()
                ddl = _coerce_text(row[0]) if row and row[0] else ""
            except Exception as exc:
                warnings.append(f"DBMS_METADATA.GET_DDL に失敗しました: {exc}")
        if ddl:
            ddl = ddl.rstrip()
            if not ddl.endswith(";"):
                ddl += ";"
            if comment:
                escaped_comment = comment.replace("'", "''")
                ddl += (
                    f"\nCOMMENT ON {normalized_type} {_quote_identifier(safe_name)} "
                    f"IS '{escaped_comment}';"
                )
            for column in columns:
                if column.comment:
                    escaped_column_comment = column.comment.replace("'", "''")
                    ddl += (
                        f"\nCOMMENT ON COLUMN {_quote_identifier(safe_name)}."
                        f"{_quote_identifier(column.column_name)} "
                        f"IS '{escaped_column_comment}';"
                    )
        return {
            "name": safe_name,
            "owner": self.settings.oracle_user.strip().upper(),
            "object_type": normalized_type.lower(),
            "row_count": row_count,
            "comment": comment,
            "columns": [column.model_dump(mode="json") for column in columns],
            "ddl": ddl,
            "warnings": warnings,
        }

    def execute_admin_statements(self, statements: list[str]) -> list[dict[str, Any]]:
        """Execute non-SELECT admin SQL statements with all-or-nothing transaction."""
        results: list[dict[str, Any]] = []
        ok = True
        with self.connection() as conn, conn.cursor() as cursor:
            with suppress(Exception):
                cursor.callproc("dbms_output.enable")
            for index, statement in enumerate(statements, start=1):
                started = datetime.now(UTC)
                statement_type = self._admin_statement_type(statement)
                normalized = self._normalize_admin_statement(statement)
                try:
                    cursor.execute(normalized)
                    row_count = getattr(cursor, "rowcount", None)
                    message = self._fetch_dbms_output(cursor) or self._admin_success_message(
                        statement_type,
                        row_count,
                    )
                    elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
                    results.append(
                        {
                            "index": index,
                            "statement_type": statement_type,
                            "status": "success",
                            "sql": normalized,
                            "row_count": row_count if isinstance(row_count, int) else None,
                            "message": message,
                            "elapsed_ms": elapsed_ms,
                        }
                    )
                except Exception as exc:
                    ok = False
                    elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
                    results.append(
                        {
                            "index": index,
                            "statement_type": statement_type,
                            "status": "error",
                            "sql": normalized,
                            "row_count": None,
                            "message": "",
                            "elapsed_ms": elapsed_ms,
                            "error_message": str(exc),
                        }
                    )
            if ok:
                conn.commit()
            else:
                conn.rollback()
        return results

    def import_tabular_table(
        self,
        *,
        table_name: str,
        columns: list[CsvImportColumn],
        rows: list[dict[str, str | None]],
        mode: str,
    ) -> dict[str, Any]:
        """Import parsed tabular rows into Oracle using create/replace/append/truncate mode."""
        safe_table = _strict_sql_name(table_name)
        quoted_table = _quote_identifier(safe_table)
        column_defs = ", ".join(
            f"{_quote_identifier(column.column_name)} {column.data_type}" for column in columns
        )
        ddl = f"CREATE TABLE {quoted_table} ({column_defs})"
        bind_names = [f"c{index}" for index, _column in enumerate(columns)]
        insert_sql = (
            f"INSERT INTO {quoted_table} "  # nosec B608
            f"({', '.join(_quote_identifier(column.column_name) for column in columns)}) "
            f"VALUES ({', '.join(':' + name for name in bind_names)})"
        )
        bind_rows = [
            {
                bind_names[index]: self._coerce_csv_value(row.get(column.column_name), column)
                for index, column in enumerate(columns)
            }
            for row in rows
        ]
        normalized_mode = mode.strip().lower()
        with self.connection() as conn, conn.cursor() as cursor:
            if normalized_mode in {"replace", "create"}:
                if normalized_mode == "replace":
                    self._drop_best_effort(cursor, f"DROP TABLE {quoted_table} PURGE", {})
                self._execute_plsql_like(cursor, ddl, {})
            elif normalized_mode == "truncate":
                self._execute_plsql_like(cursor, f"TRUNCATE TABLE {quoted_table}", {})
            elif normalized_mode != "append":
                raise OracleAdapterError(f"未対応 import mode です: {mode}")
            if bind_rows:
                cursor.executemany(insert_sql, bind_rows)
            conn.commit()
        return {
            "runtime": "oracle",
            "table_name": safe_table,
            "row_count": len(bind_rows),
            "mode": normalized_mode,
            "ddl": ddl,
            "insert_sql": insert_sql,
        }

    def apply_comment_statements(self, statements: list[str]) -> dict[str, Any]:
        """Execute generated COMMENT ON statements.

        Callers must generate the statements from validated catalog metadata; this method
        intentionally does not accept arbitrary DDL from API clients.
        """
        with self.connection() as conn, conn.cursor() as cursor:
            for statement in statements:
                self._execute_plsql_like(cursor, statement.strip().rstrip(";"), {})
            conn.commit()
        return {
            "runtime": "oracle",
            "statement_count": len(statements),
        }

    def apply_safe_statements(self, statements: list[str]) -> dict[str, Any]:
        """Execute generated catalog-validated metadata statements."""
        with self.connection() as conn, conn.cursor() as cursor:
            for statement in statements:
                self._execute_plsql_like(cursor, statement.strip().rstrip(";"), {})
            conn.commit()
        return {
            "runtime": "oracle",
            "statement_count": len(statements),
        }

    def list_select_ai_profiles(self) -> list[dict[str, Any]]:
        """List DBMS_CLOUD_AI profiles from the Oracle data dictionary when available."""
        candidates = [
            """
            SELECT PROFILE_NAME, NVL(STATUS, 'UNKNOWN'), OWNER, CREATED
            FROM USER_CLOUD_AI_PROFILES
            ORDER BY PROFILE_NAME
            """,
            """
            SELECT PROFILE_NAME, 'UNKNOWN', USER, NULL
            FROM USER_CLOUD_AI_PROFILES
            ORDER BY PROFILE_NAME
            """,
        ]
        errors: list[str] = []
        with self.connection() as conn, conn.cursor() as cursor:
            for sql in candidates:
                try:
                    cursor.execute(sql)
                    rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
                    return [
                        {
                            "name": str(row[0] or ""),
                            "status": str(row[1] or "unknown").lower(),
                            "owner": str(row[2] or ""),
                            "created_at": _coerce_text(row[3]) if len(row) > 3 else "",
                        }
                        for row in rows
                    ]
                except Exception as exc:
                    errors.append(str(exc))
                    continue
        raise OracleAdapterError(
            "Oracle Select AI profile 一覧を取得できませんでした: " + "; ".join(errors)
        )

    def get_select_ai_profile_detail(self, *, profile_name: str) -> dict[str, Any]:
        """Fetch one DBMS_CLOUD_AI profile detail with best-effort attribute decoding."""
        safe_name = profile_name.strip()
        if not safe_name:
            raise OracleAdapterError("profile_name が空です。")
        candidates = [
            """
            SELECT PROFILE_NAME, NVL(STATUS, 'UNKNOWN'), OWNER, CREATED, ATTRIBUTES, DESCRIPTION
            FROM USER_CLOUD_AI_PROFILES
            WHERE UPPER(PROFILE_NAME) = UPPER(:profile_name)
            """,
            """
            SELECT PROFILE_NAME, 'UNKNOWN', USER, NULL, ATTRIBUTES, NULL
            FROM USER_CLOUD_AI_PROFILES
            WHERE UPPER(PROFILE_NAME) = UPPER(:profile_name)
            """,
            """
            SELECT PROFILE_NAME, 'UNKNOWN', USER, NULL, NULL, NULL
            FROM USER_CLOUD_AI_PROFILES
            WHERE UPPER(PROFILE_NAME) = UPPER(:profile_name)
            """,
        ]
        errors: list[str] = []
        with self.connection() as conn, conn.cursor() as cursor:
            for sql in candidates:
                try:
                    cursor.execute(sql, {"profile_name": safe_name})
                    row = cursor.fetchone()
                    if not row:
                        raise OracleAdapterError(f"{profile_name}: profile が見つかりません。")
                    attributes_text = _coerce_text(row[4]) if len(row) > 4 else ""
                    try:
                        attributes = json.loads(attributes_text) if attributes_text else {}
                    except json.JSONDecodeError:
                        attributes = {"raw": attributes_text}
                    object_list = attributes.get("object_list")
                    if not isinstance(object_list, list):
                        object_list = []
                    return {
                        "name": str(row[0] or safe_name),
                        "status": str(row[1] or "unknown").lower(),
                        "owner": str(row[2] or ""),
                        "created_at": _coerce_text(row[3]) if len(row) > 3 else "",
                        "attributes": attributes,
                        "description": _coerce_text(row[5]) if len(row) > 5 else "",
                        "object_list": object_list,
                    }
                except OracleAdapterError:
                    raise
                except Exception as exc:
                    errors.append(str(exc))
                    continue
        raise OracleAdapterError(
            "Oracle Select AI profile 詳細を取得できませんでした: " + "; ".join(errors)
        )

    def upsert_select_ai_profile_low_level(
        self,
        *,
        profile_name: str,
        attributes: dict[str, Any],
        description: str = "",
        original_name: str = "",
    ) -> dict[str, Any]:
        """Create or replace DBMS_CLOUD_AI profile from raw attributes JSON."""
        safe_name = profile_name.strip()
        if not safe_name:
            raise OracleAdapterError("profile_name が空です。")
        attrs = json.dumps(attributes or {}, ensure_ascii=False)
        desc = description or ""
        with self.connection() as conn, conn.cursor() as cursor:
            if original_name.strip() and original_name.strip().upper() != safe_name.upper():
                self._drop_cloud_ai_profile_best_effort(cursor, original_name.strip())
            self._drop_cloud_ai_profile_best_effort(cursor, safe_name)
            self._execute_first_supported_plsql(
                cursor,
                [
                    (
                        """
                        BEGIN
                            DBMS_CLOUD_AI.CREATE_PROFILE(
                                profile_name => :name,
                                attributes => :attrs,
                                description => :description
                            );
                        END;
                        """,
                        {"name": safe_name, "attrs": attrs, "description": desc},
                    ),
                    (
                        """
                        BEGIN
                            DBMS_CLOUD_AI.CREATE_PROFILE(
                                profile_name => :name,
                                attributes => :attrs
                            );
                        END;
                        """,
                        {"name": safe_name, "attrs": attrs},
                    ),
                ],
            )
            conn.commit()
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI",
            "profile_name": safe_name,
            "attributes": attributes,
            "description": desc,
        }

    def list_agent_conversations(
        self, *, team_name: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Fetch recent Select AI Agent conversation prompts when dictionary view exists."""
        filters = []
        binds: dict[str, Any] = {"limit": max(limit, 1)}
        if team_name:
            filters.append("UPPER(TEAM_NAME) = UPPER(:team_name)")
            binds["team_name"] = team_name
        where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""
        conversation_sql = (
            "SELECT CONVERSATION_ID, PROMPT, RESPONSE, CREATED, TEAM_NAME "  # nosec B608
            "FROM USER_CLOUD_AI_CONVERSATION_PROMPTS "
            f"{where_clause} "
            "ORDER BY CREATED DESC "
            "FETCH FIRST :limit ROWS ONLY"
        )
        prompt_sql = (
            "SELECT CONVERSATION_ID, PROMPT, NULL, CREATED, NULL "  # nosec B608
            "FROM USER_CLOUD_AI_CONVERSATION_PROMPTS "
            f"{where_clause} "
            "ORDER BY CREATED DESC "
            "FETCH FIRST :limit ROWS ONLY"
        )
        candidates = [
            (conversation_sql, binds),
            (prompt_sql, binds),
        ]
        errors: list[str] = []
        with self.connection() as conn, conn.cursor() as cursor:
            for sql, params in candidates:
                try:
                    cursor.execute(sql, params)
                    rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
                    return [
                        {
                            "conversation_id": str(row[0] or ""),
                            "prompt": _coerce_text(row[1]),
                            "response": _coerce_text(row[2]) if len(row) > 2 else "",
                            "created_at": _coerce_text(row[3]) if len(row) > 3 else "",
                            "team_name": str(row[4] or "") if len(row) > 4 else "",
                        }
                        for row in rows
                    ]
                except Exception as exc:
                    errors.append(str(exc))
                    continue
        raise OracleAdapterError(
            "Select AI Agent conversation 履歴を取得できませんでした: " + "; ".join(errors)
        )

    def check_select_ai_agent_privileges(self) -> list[dict[str, str]]:
        """Run side-effect-free Select AI Agent privilege checks."""

        def check_count(
            cursor: Any,
            *,
            name: str,
            sql: str,
            params: dict[str, Any] | None = None,
            ok_message: str,
            warning_message: str,
        ) -> dict[str, str]:
            try:
                cursor.execute(sql, params or {})
                row = cursor.fetchone()
                count = int(row[0] or 0) if row else 0
                return {
                    "name": name,
                    "status": "ok" if count > 0 else "warning",
                    "message": ok_message if count > 0 else warning_message,
                }
            except Exception as exc:
                return {
                    "name": name,
                    "status": "warning",
                    "message": f"{warning_message}: {exc}",
                }

        def check_access(
            cursor: Any,
            *,
            name: str,
            sql: str,
            ok_message: str,
            warning_message: str,
        ) -> dict[str, str]:
            try:
                cursor.execute(sql)
                cursor.fetchone()
                return {"name": name, "status": "ok", "message": ok_message}
            except Exception as exc:
                return {
                    "name": name,
                    "status": "warning",
                    "message": f"{warning_message}: {exc}",
                }

        with self.connection() as conn, conn.cursor() as cursor:
            checks: list[dict[str, str]] = []
            try:
                cursor.execute("SELECT 1 FROM DUAL")
                checks.append(
                    {
                        "name": "oracle_connection",
                        "status": "ok",
                        "message": "Oracle へ接続できます。",
                    }
                )
            except Exception as exc:
                raise OracleAdapterError(f"Oracle 接続確認に失敗しました: {exc}") from exc
            checks.append(
                check_count(
                    cursor,
                    name="dbms_cloud_ai_package",
                    sql="""
                    SELECT COUNT(*)
                    FROM ALL_PROCEDURES
                    WHERE OBJECT_NAME = 'DBMS_CLOUD_AI'
                    """,
                    ok_message="DBMS_CLOUD_AI package が参照可能です。",
                    warning_message="DBMS_CLOUD_AI package を参照できません。",
                )
            )
            checks.append(
                check_count(
                    cursor,
                    name="dbms_cloud_ai_agent_package",
                    sql="""
                    SELECT COUNT(*)
                    FROM ALL_PROCEDURES
                    WHERE OBJECT_NAME = 'DBMS_CLOUD_AI_AGENT'
                    """,
                    ok_message="DBMS_CLOUD_AI_AGENT package が参照可能です。",
                    warning_message="DBMS_CLOUD_AI_AGENT package を参照できません。",
                )
            )
            checks.append(
                check_access(
                    cursor,
                    name="user_cloud_ai_profiles",
                    sql="SELECT PROFILE_NAME FROM USER_CLOUD_AI_PROFILES WHERE 1 = 0",
                    ok_message="USER_CLOUD_AI_PROFILES を参照できます。",
                    warning_message="USER_CLOUD_AI_PROFILES を参照できません。",
                )
            )
            checks.append(
                check_access(
                    cursor,
                    name="user_cloud_ai_conversation_prompts",
                    sql=(
                        "SELECT CONVERSATION_ID "
                        "FROM USER_CLOUD_AI_CONVERSATION_PROMPTS WHERE 1 = 0"
                    ),
                    ok_message="USER_CLOUD_AI_CONVERSATION_PROMPTS を参照できます。",
                    warning_message="USER_CLOUD_AI_CONVERSATION_PROMPTS を参照できません。",
                )
            )
            return checks

    def generate_synthetic_data(
        self,
        *,
        table_name: str,
        row_count: int,
        profile_name: str = "",
        object_list: list[str] | None = None,
        user_prompt: str = "",
        sample_rows: int = 0,
        use_comments: bool = True,
    ) -> dict[str, Any]:
        """Call DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA for a validated table."""
        safe_table = _strict_sql_name(table_name) if table_name.strip() else ""
        safe_objects = [_strict_sql_name(item) for item in object_list or [] if item.strip()]
        if not safe_table and not safe_objects:
            raise OracleAdapterError("synthetic data 対象 table/object_list が空です。")
        params_json = json.dumps(
            {
                "comments": bool(use_comments),
                "sample_rows": max(int(sample_rows), 0),
            },
            ensure_ascii=False,
        )
        candidates = [
            (
                """
                SELECT DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA(
                    table_name => :table_name,
                    row_count => :row_count,
                    profile_name => :profile_name
                )
                FROM DUAL
                """,
                {
                    "table_name": safe_table or safe_objects[0],
                    "row_count": int(row_count),
                    "profile_name": profile_name or None,
                },
            ),
            (
                """
                SELECT DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA(
                    table_name => :table_name,
                    row_count => :row_count
                )
                FROM DUAL
                """,
                {"table_name": safe_table or safe_objects[0], "row_count": int(row_count)},
            ),
        ]
        procedure_candidates: list[tuple[str, dict[str, Any]]] = []
        if profile_name.strip():
            if safe_objects and not safe_table:
                procedure_candidates.append(
                    (
                        """
                        BEGIN
                            DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA(
                                profile_name => :profile_name,
                                object_list => :object_list,
                                params => :params
                            );
                        END;
                        """,
                        {
                            "profile_name": profile_name,
                            "object_list": json.dumps(
                                [
                                    {
                                        "owner": self.settings.oracle_user.strip().upper(),
                                        "name": object_name,
                                        "record_count": int(row_count),
                                    }
                                    for object_name in safe_objects
                                ],
                                ensure_ascii=False,
                            ),
                            "params": params_json,
                        },
                    )
                )
            else:
                procedure_candidates.append(
                    (
                        """
                        BEGIN
                            DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA(
                                profile_name => :profile_name,
                                object_name => :object_name,
                                owner_name => :owner_name,
                                record_count => :row_count,
                                user_prompt => :user_prompt,
                                params => :params
                            );
                        END;
                        """,
                        {
                            "profile_name": profile_name,
                            "object_name": safe_table or safe_objects[0],
                            "owner_name": self.settings.oracle_user.strip().upper(),
                            "row_count": int(row_count),
                            "user_prompt": user_prompt or None,
                            "params": params_json,
                        },
                    )
                )
        errors: list[str] = []
        with self.connection() as conn, conn.cursor() as cursor:
            for sql, params in candidates:
                try:
                    cursor.execute(sql, params)
                    row = cursor.fetchone()
                    conn.commit()
                    operation_id = _coerce_text(row[0] if row else "").strip()
                    return {
                        "runtime": "oracle",
                        "package": "DBMS_CLOUD_AI",
                        "operation_id": operation_id,
                        "table_name": safe_table or (safe_objects[0] if safe_objects else ""),
                        "object_list": safe_objects,
                        "row_count": int(row_count),
                    }
                except Exception as exc:
                    message = str(exc)
                    if self._looks_like_signature_error(message):
                        errors.append(message)
                        continue
                    raise OracleAdapterError(
                        f"DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA に失敗しました: {message}"
                    ) from exc
            for sql, params in procedure_candidates:
                try:
                    cursor.execute(sql, params)
                    conn.commit()
                    return {
                        "runtime": "oracle",
                        "package": "DBMS_CLOUD_AI",
                        "mode": "procedure",
                        "operation_id": "",
                        "table_name": safe_table or (safe_objects[0] if safe_objects else ""),
                        "object_list": safe_objects,
                        "row_count": int(row_count),
                    }
                except Exception as exc:
                    message = str(exc)
                    if self._looks_like_signature_error(message):
                        errors.append(message)
                        continue
                    raise OracleAdapterError(
                        f"DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA に失敗しました: {message}"
                    ) from exc
        raise OracleAdapterError(
            "DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA の対応 signature が見つかりません: "
            + "; ".join(errors)
        )

    def synthetic_data_operation_status(self, *, operation_id: str) -> dict[str, Any]:
        safe_operation_id = operation_id.strip()
        if not safe_operation_id:
            raise OracleAdapterError("operation_id が空です。")
        candidates = [
            """
            SELECT STATUS, ERROR_MESSAGE, RESULT
            FROM USER_CLOUD_AI_OPERATIONS
            WHERE OPERATION_ID = :operation_id
            """,
            """
            SELECT STATUS, NULL, NULL
            FROM USER_CLOUD_AI_OPERATIONS
            WHERE OPERATION_ID = :operation_id
            """,
        ]
        errors: list[str] = []
        with self.connection() as conn, conn.cursor() as cursor:
            for sql in candidates:
                try:
                    cursor.execute(sql, {"operation_id": safe_operation_id})
                    row = cursor.fetchone()
                    if not row:
                        return {"runtime": "oracle", "status": "not_found", "message": ""}
                    return {
                        "runtime": "oracle",
                        "status": str(row[0] or "unknown").lower(),
                        "message": _coerce_text(row[1]) if len(row) > 1 else "",
                        "result": {"raw": _coerce_text(row[2])} if len(row) > 2 and row[2] else {},
                    }
                except Exception as exc:
                    errors.append(str(exc))
                    continue
        raise OracleAdapterError(
            "synthetic data operation status を取得できませんでした: " + "; ".join(errors)
        )

    def rebuild_feedback_vector_index(
        self,
        *,
        table_name: str,
        index_name: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Recreate the NL2SQL feedback VECTOR table and vector index."""
        safe_table = _strict_sql_name(table_name)
        safe_index = _strict_sql_name(index_name)
        quoted_table = _quote_identifier(safe_table)
        quoted_index = _quote_identifier(safe_index)
        create_table = (
            f"CREATE TABLE {quoted_table} ("  # nosec B608
            "HISTORY_ID VARCHAR2(64) PRIMARY KEY, "
            "PROFILE_ID VARCHAR2(128), "
            "QUESTION CLOB, "
            "GENERATED_SQL CLOB, "
            "FEEDBACK_RATING VARCHAR2(32), "
            "EMBEDDING VECTOR(1536, FLOAT32), "
            "CREATED_AT TIMESTAMP WITH TIME ZONE)"
        )
        insert_sql = (
            f"INSERT INTO {quoted_table} "  # nosec B608
            "(HISTORY_ID, PROFILE_ID, QUESTION, GENERATED_SQL, "
            "FEEDBACK_RATING, EMBEDDING, CREATED_AT) "
            "VALUES (:history_id, :profile_id, :question, :generated_sql, :feedback_rating, "
            "TO_VECTOR(:embedding_json), SYSTIMESTAMP)"
        )
        create_index = (
            f"CREATE VECTOR INDEX {quoted_index} "  # nosec B608
            f"ON {quoted_table} (EMBEDDING) "
            "ORGANIZATION INMEMORY NEIGHBOR GRAPH DISTANCE COSINE"
        )
        bind_rows = [
            {
                "history_id": str(row["history_id"]),
                "profile_id": str(row.get("profile_id") or ""),
                "question": str(row.get("question") or ""),
                "generated_sql": str(row.get("generated_sql") or ""),
                "feedback_rating": str(row.get("feedback_rating") or ""),
                "embedding_json": json.dumps(row.get("embedding") or []),
            }
            for row in rows
        ]
        with self.connection() as conn, conn.cursor() as cursor:
            self._drop_best_effort(cursor, f"DROP INDEX {quoted_index}", {})
            self._drop_best_effort(cursor, f"DROP TABLE {quoted_table} PURGE", {})
            self._execute_plsql_like(cursor, create_table, {})
            if bind_rows:
                cursor.executemany(insert_sql, bind_rows)
            self._execute_plsql_like(cursor, create_index, {})
            conn.commit()
        return {
            "runtime": "oracle",
            "table_name": safe_table,
            "index_name": safe_index,
            "row_count": len(bind_rows),
        }

    def clear_feedback_vector_index(self, *, table_name: str, index_name: str) -> dict[str, Any]:
        """Drop the NL2SQL feedback VECTOR index/table if present."""
        safe_table = _strict_sql_name(table_name)
        safe_index = _strict_sql_name(index_name)
        quoted_table = _quote_identifier(safe_table)
        quoted_index = _quote_identifier(safe_index)
        with self.connection() as conn, conn.cursor() as cursor:
            self._drop_best_effort(cursor, f"DROP INDEX {quoted_index}", {})
            self._drop_best_effort(cursor, f"DROP TABLE {quoted_table} PURGE", {})
            conn.commit()
        return {
            "runtime": "oracle",
            "table_name": safe_table,
            "index_name": safe_index,
        }

    def search_feedback_vector_index(
        self,
        *,
        table_name: str,
        embedding: list[float],
        profile_id: str | None,
        include_bad: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Search feedback history with Oracle 26ai vector similarity."""
        safe_table = _strict_sql_name(table_name)
        quoted_table = _quote_identifier(safe_table)
        filters = ["1 = 1"]
        binds: dict[str, Any] = {
            "embedding_json": json.dumps(embedding),
            "limit": max(limit, 1),
        }
        if profile_id:
            filters.append("PROFILE_ID = :profile_id")
            binds["profile_id"] = profile_id
        if not include_bad:
            filters.append("(FEEDBACK_RATING IS NULL OR FEEDBACK_RATING <> 'bad')")
        where_clause = " AND ".join(filters)
        query = (
            "SELECT HISTORY_ID, PROFILE_ID, QUESTION, GENERATED_SQL, FEEDBACK_RATING, "
            "VECTOR_DISTANCE(EMBEDDING, TO_VECTOR(:embedding_json), COSINE) AS DISTANCE "
            f"FROM {quoted_table} "  # nosec B608
            f"WHERE {where_clause} "
            "ORDER BY DISTANCE FETCH FIRST :limit ROWS ONLY"
        )
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(query, binds)
            rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
        results: list[dict[str, Any]] = []
        for row in rows:
            distance = float(row[5] or 0)
            results.append(
                {
                    "history_id": str(row[0] or ""),
                    "profile_id": str(row[1] or ""),
                    "question": _coerce_text(row[2]),
                    "generated_sql": _coerce_text(row[3]),
                    "feedback_rating": str(row[4] or ""),
                    "distance": distance,
                    "score": round(max(0.0, 1.0 - distance), 3),
                }
            )
        return results

    def import_csv_table(
        self,
        *,
        table_name: str,
        columns: list[CsvImportColumn],
        rows: list[dict[str, str | None]],
        replace_existing: bool,
    ) -> dict[str, Any]:
        """Create a table and insert parsed CSV rows into Oracle."""
        quoted_table = _quote_identifier(table_name)
        column_defs = ", ".join(
            f"{_quote_identifier(column.column_name)} {column.data_type}" for column in columns
        )
        ddl = f"CREATE TABLE {quoted_table} ({column_defs})"
        bind_names = [f"c{index}" for index, _column in enumerate(columns)]
        # Safe: table and columns are sanitized and quoted; values use binds.
        insert_sql = (
            f"INSERT INTO {quoted_table} "  # nosec B608
            f"({', '.join(_quote_identifier(column.column_name) for column in columns)}) "
            f"VALUES ({', '.join(':' + name for name in bind_names)})"
        )
        bind_rows = [
            {
                bind_names[index]: self._coerce_csv_value(row.get(column.column_name), column)
                for index, column in enumerate(columns)
            }
            for row in rows
        ]
        with self.connection() as conn, conn.cursor() as cursor:
            if replace_existing:
                self._drop_best_effort(cursor, f"DROP TABLE {quoted_table} PURGE", {})
            self._execute_plsql_like(cursor, ddl, {})
            if bind_rows:
                cursor.executemany(insert_sql, bind_rows)
            conn.commit()
        return {
            "runtime": "oracle",
            "table_name": table_name,
            "row_count": len(bind_rows),
            "ddl": ddl,
            "insert_sql": insert_sql,
        }

    def generate_select_ai_sql(
        self, *, profile_name: str, question: str, action: str = "showsql"
    ) -> str:
        """Oracle Select AI profile で SQL を生成する。

        DBMS_CLOUD_AI.GENERATE の属性は環境差があるため、呼び出しは adapter 内に限定する。
        """
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT DBMS_CLOUD_AI.GENERATE(
                    prompt => :prompt,
                    profile_name => :profile_name,
                    action => :action
                )
                FROM DUAL
                """,
                {"prompt": question, "profile_name": profile_name, "action": action},
            )
            row = cursor.fetchone()
            text = _coerce_text(row[0] if row else "")
        return _extract_select_statement(text)

    def refresh_select_ai_profile(
        self,
        *,
        profile_name: str,
        allowed_tables: list[str],
        row_limit: int,
        description: str = "",
    ) -> dict[str, Any]:
        """Create or replace an Oracle Select AI profile."""
        attributes = self._select_ai_profile_attributes(
            allowed_tables=allowed_tables,
            row_limit=row_limit,
            description=description,
        )
        with self.connection() as conn, conn.cursor() as cursor:
            self._drop_cloud_ai_profile_best_effort(cursor, profile_name)
            self._execute_plsql(
                cursor,
                """
                BEGIN
                    DBMS_CLOUD_AI.CREATE_PROFILE(
                        profile_name => :profile_name,
                        attributes => :attributes
                    );
                END;
                """,
                {"profile_name": profile_name, "attributes": json.dumps(attributes)},
            )
            conn.commit()
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI",
            "profile_name": profile_name,
            "profile_attributes": attributes,
        }

    def drop_select_ai_profile(self, *, profile_name: str) -> dict[str, Any]:
        """Drop an Oracle Select AI profile if it exists."""
        with self.connection() as conn, conn.cursor() as cursor:
            self._drop_cloud_ai_profile_best_effort(cursor, profile_name)
            conn.commit()
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI",
            "profile_name": profile_name,
        }

    def refresh_select_ai_agent_assets(
        self,
        *,
        profile_name: str,
        tool_name: str,
        agent_name: str,
        task_name: str,
        team_name: str,
        allowed_tables: list[str],
        row_limit: int,
        description: str = "",
    ) -> dict[str, Any]:
        """Create or replace Oracle Select AI Agent profile/tool/agent/task/team assets."""
        profile_meta = self.refresh_select_ai_profile(
            profile_name=profile_name,
            allowed_tables=allowed_tables,
            row_limit=row_limit,
            description=description,
        )
        profile_attributes = self._select_ai_profile_attributes(
            allowed_tables=allowed_tables,
            row_limit=row_limit,
            description=description,
        )
        tool_attributes = {
            "tool_type": "SQL",
            "tool_params": {"profile_name": profile_name},
            "instruction": (
                "Use this tool to generate Oracle SELECT/WITH SQL from natural language. "
                "Use SHOWSQL behavior and do not execute DML, DDL, PL/SQL, or multi-statement SQL."
            ),
        }
        agent_attributes = {
            "tools": [tool_name],
        }
        task_attributes = {
            "instruction": (
                "Use the SQL tool to create exactly one Oracle SELECT statement for the "
                f"user's request. Invoke tool {tool_name} with JSON keys TOOL_NAME, QUERY, "
                "and ACTION. TOOL_NAME must be the SQL tool name, QUERY must be the user's "
                "natural language request, and ACTION must be SHOWSQL. "
                "Return strict JSON only with keys sql and explanation. "
                "sql must be a single SELECT/WITH statement without markdown, comments, "
                "or trailing narration. explanation must be concise and written in Japanese."
            ),
            "tools": [tool_name],
            "enable_human_tool": False,
        }
        team_attributes = {
            "agents": [{"name": agent_name, "task": task_name}],
            "process": "sequential",
        }
        with self.connection() as conn, conn.cursor() as cursor:
            for procedure, name_param, name in [
                ("DROP_TEAM", "team_name", team_name),
                ("DROP_TASK", "task_name", task_name),
                ("DROP_AGENT", "agent_name", agent_name),
                ("DROP_TOOL", "tool_name", tool_name),
            ]:
                self._drop_best_effort(
                    cursor,
                    f"""
                    BEGIN
                        DBMS_CLOUD_AI_AGENT.{procedure}({name_param} => :name, force => TRUE);
                    END;
                    """,
                    {"name": name},
                )
            self._drop_cloud_ai_profile_best_effort(cursor, f"AGENT${team_name}")
            self._drop_sql_translator_profile_best_effort(cursor, f"AGENT${team_name}")
            conn.commit()
            self._execute_agent_create(
                cursor,
                procedure="CREATE_TOOL",
                name_param="tool_name",
                name=tool_name,
                attributes=tool_attributes,
            )
            self._execute_agent_create(
                cursor,
                procedure="CREATE_AGENT",
                name_param="agent_name",
                name=agent_name,
                attributes=agent_attributes,
            )
            self._execute_agent_create(
                cursor,
                procedure="CREATE_TASK",
                name_param="task_name",
                name=task_name,
                attributes=task_attributes,
            )
            try:
                self._execute_agent_create(
                    cursor,
                    procedure="CREATE_TEAM",
                    name_param="team_name",
                    name=team_name,
                    attributes=team_attributes,
                )
            except OracleAdapterError as exc:
                if not self._looks_like_profile_already_exists(str(exc)):
                    raise
                self._drop_cloud_ai_profile_best_effort(cursor, f"AGENT${team_name}")
                self._drop_sql_translator_profile_best_effort(cursor, f"AGENT${team_name}")
                conn.commit()
                self._execute_agent_create(
                    cursor,
                    procedure="CREATE_TEAM",
                    name_param="team_name",
                    name=team_name,
                    attributes=team_attributes,
                )
            conn.commit()
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI_AGENT",
            "select_ai_profile_meta": profile_meta,
            "profile_attributes": profile_attributes,
            "tool_attributes": tool_attributes,
            "agent_attributes": agent_attributes,
            "task_attributes": task_attributes,
            "team_attributes": team_attributes,
        }

    def drop_select_ai_agent_assets(
        self,
        *,
        profile_name: str,
        tool_name: str,
        agent_name: str,
        task_name: str,
        team_name: str,
    ) -> dict[str, Any]:
        """Drop Oracle Select AI Agent assets if they exist."""
        with self.connection() as conn, conn.cursor() as cursor:
            for procedure, name_param, name in [
                ("DROP_TEAM", "team_name", team_name),
                ("DROP_TASK", "task_name", task_name),
                ("DROP_AGENT", "agent_name", agent_name),
                ("DROP_TOOL", "tool_name", tool_name),
            ]:
                self._drop_best_effort(
                    cursor,
                    f"""
                    BEGIN
                        DBMS_CLOUD_AI_AGENT.{procedure}({name_param} => :name, force => TRUE);
                    END;
                    """,
                    {"name": name},
                )
            for name in [f"AGENT${team_name}", profile_name]:
                self._drop_cloud_ai_profile_best_effort(cursor, name)
            self._drop_sql_translator_profile_best_effort(cursor, f"AGENT${team_name}")
            conn.commit()
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI_AGENT",
            "profile_name": profile_name,
            "tool_name": tool_name,
            "agent_name": agent_name,
            "task_name": task_name,
            "team_name": team_name,
        }

    def run_select_ai_agent_team(
        self, *, team_name: str, question: str, tool_name: str | None = None
    ) -> tuple[str, str]:
        conversation_id = ""
        try:
            conversation_id = self.create_agent_conversation()
        except OracleAdapterError:
            conversation_id = ""
        params = json.dumps(
            {"conversation_id": conversation_id} if conversation_id else {},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        candidates = [
            (
                """
                SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM(
                    team_name => :team_name,
                    user_prompt => :user_prompt,
                    conversation_id => :conversation_id,
                    params => :params
                )
                FROM DUAL
                """,
                {
                    "team_name": team_name,
                    "user_prompt": question,
                    "conversation_id": conversation_id,
                    "params": params,
                },
            )
        ]
        candidates.append(
            (
                """
                SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM(
                    team_name => :team_name,
                    user_prompt => :user_prompt,
                    params => :params
                )
                FROM DUAL
                """,
                {"team_name": team_name, "user_prompt": question, "params": params},
            )
        )
        candidates.extend(
            [
                (
                    """
                    SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM(:team_name, :user_prompt, :params)
                    FROM DUAL
                    """,
                    {"team_name": team_name, "user_prompt": question, "params": params},
                ),
                (
                    """
                    SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM(
                        :team_name,
                        :user_prompt,
                        :conversation_id,
                        :params
                    )
                    FROM DUAL
                    """,
                    {
                        "team_name": team_name,
                        "user_prompt": question,
                        "conversation_id": conversation_id,
                        "params": params,
                    },
                ),
                (
                    """
                    SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM(:team_name, :user_prompt)
                    FROM DUAL
                    """,
                    {"team_name": team_name, "user_prompt": question},
                ),
            ]
        )
        errors: list[str] = []
        with self.connection() as conn, conn.cursor() as cursor:
            for sql, bindings in candidates:
                try:
                    cursor.execute(sql, bindings)
                    row = cursor.fetchone()
                    text = _coerce_text(row[0] if row else "")
                    return _extract_select_statement(text), conversation_id
                except Exception as exc:
                    message = str(exc)
                    if self._looks_like_agent_profile_loss(message):
                        return self.run_select_ai_agent_tool(
                            tool_name=tool_name or self._tool_name_from_team_name(team_name),
                            question=question,
                        )
                    if self._looks_like_signature_error(message):
                        errors.append(message)
                        continue
                    raise self._agent_runtime_error(exc) from exc
        if errors:
            raise self._agent_runtime_error(RuntimeError("; ".join(errors)))
        raise OracleAdapterError("Select AI Agent team の実行結果を取得できませんでした。")

    def run_select_ai_agent_tool(self, *, tool_name: str, question: str) -> tuple[str, str]:
        payload = json.dumps(
            {"TOOL_NAME": tool_name, "QUERY": question, "ACTION": "SHOWSQL"},
            ensure_ascii=False,
        )
        with self.connection() as conn, conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    SELECT DBMS_CLOUD_AI_AGENT.RUN_TOOL(
                        tool_name => :tool_name,
                        input => :input
                    )
                    FROM DUAL
                    """,
                    {"tool_name": tool_name, "input": payload},
                )
            except Exception as exc:
                raise self._agent_runtime_error(exc) from exc
            row = cursor.fetchone()
            text = _coerce_text(row[0] if row else "")
        return _extract_select_statement(text), f"run_tool:{tool_name}"

    def create_agent_conversation(self) -> str:
        with self.connection() as conn, conn.cursor() as cursor:
            try:
                cursor.execute("SELECT DBMS_CLOUD_AI_AGENT.CREATE_CONVERSATION() FROM DUAL")
            except Exception as exc:
                raise self._agent_runtime_error(exc) from exc
            row = cursor.fetchone()
        conversation_id = _coerce_text(row[0] if row else "").strip()
        if not conversation_id:
            raise OracleAdapterError("Select AI Agent conversation_id を作成できませんでした。")
        return conversation_id

    def _select_ai_profile_attributes(
        self, *, allowed_tables: list[str], row_limit: int, description: str
    ) -> dict[str, Any]:
        del row_limit, description
        attributes: dict[str, Any] = {
            "provider": self.settings.nl2sql_select_ai_provider,
            "enforce_object_list": True,
            "annotations": True,
            "comments": True,
            "constraints": True,
            "object_list": self._object_list(allowed_tables),
        }
        if self.settings.nl2sql_select_ai_credential_name:
            attributes["credential_name"] = self.settings.nl2sql_select_ai_credential_name
        if self.settings.nl2sql_select_ai_model:
            attributes["model"] = self.settings.nl2sql_select_ai_model
        if self.settings.oci_region:
            attributes["region"] = self.settings.oci_region
        if self.settings.oci_compartment_id:
            attributes["oci_compartment_id"] = self.settings.oci_compartment_id
        return attributes

    def _object_list(self, allowed_tables: list[str]) -> list[dict[str, str]]:
        owner = self.settings.oracle_user.upper() if self.settings.oracle_user else ""
        objects: list[dict[str, str]] = []
        for table_name in allowed_tables:
            normalized = table_name.strip().upper()
            if not normalized:
                continue
            if "." in normalized:
                object_owner, object_name = normalized.split(".", 1)
                objects.append({"owner": object_owner, "name": object_name})
            elif owner:
                objects.append({"owner": owner, "name": normalized})
            else:
                objects.append({"name": normalized})
        return objects

    def _execute_agent_create(
        self,
        cursor: Any,
        *,
        procedure: str,
        name_param: str,
        name: str,
        attributes: dict[str, Any],
    ) -> None:
        attributes_json = json.dumps(attributes, ensure_ascii=False)
        self._execute_first_supported_plsql(
            cursor,
            [
                (
                    f"""
                    BEGIN
                        DBMS_CLOUD_AI_AGENT.{procedure}(
                            {name_param} => :name,
                            attributes => :attributes
                        );
                    END;
                    """,
                    {"name": name, "attributes": attributes_json},
                ),
                (
                    f"""
                    BEGIN
                        DBMS_CLOUD_AI_AGENT.{procedure}(
                            name => :name,
                            attributes => :attributes
                        );
                    END;
                    """,
                    {"name": name, "attributes": attributes_json},
                ),
            ],
        )

    def _execute_first_supported_plsql(
        self, cursor: Any, candidates: list[tuple[str, dict[str, Any]]]
    ) -> None:
        errors: list[str] = []
        for sql, params in candidates:
            try:
                self._execute_plsql(cursor, sql, params)
                return
            except OracleAdapterError as exc:
                if not self._looks_like_signature_error(str(exc)):
                    raise
                errors.append(str(exc))
        raise OracleAdapterError("; ".join(errors) or "Oracle PL/SQL 呼び出しに失敗しました。")

    def _execute_plsql(self, cursor: Any, sql: str, params: dict[str, Any]) -> None:
        try:
            cursor.execute(sql, params)
        except Exception as exc:
            raise OracleAdapterError(f"Oracle PL/SQL 実行に失敗しました: {exc}") from exc

    def _execute_plsql_like(self, cursor: Any, sql: str, params: dict[str, Any]) -> None:
        try:
            cursor.execute(sql, params)
        except Exception as exc:
            raise OracleAdapterError(f"Oracle SQL 実行に失敗しました: {exc}") from exc

    def _drop_best_effort(self, cursor: Any, sql: str, params: dict[str, Any]) -> None:
        try:
            cursor.execute(sql, params)
        except Exception:
            return

    def _drop_cloud_ai_profile_best_effort(self, cursor: Any, profile_name: str) -> None:
        for sql, params in [
            (
                """
                BEGIN
                    DBMS_CLOUD_AI.DROP_PROFILE(
                        profile_name => :name,
                        force => TRUE
                    );
                END;
                """,
                {"name": profile_name},
            ),
            (
                """
                BEGIN
                    DBMS_CLOUD_AI.DROP_PROFILE(profile_name => :name);
                END;
                """,
                {"name": profile_name},
            ),
            (
                """
                BEGIN
                    DBMS_CLOUD_AI.DROP_PROFILE(:name, TRUE);
                END;
                """,
                {"name": profile_name},
            ),
            (
                """
                BEGIN
                    DBMS_CLOUD_AI.DROP_PROFILE(:name);
                END;
                """,
                {"name": profile_name},
            ),
        ]:
            self._drop_best_effort(cursor, sql, params)

    def _drop_sql_translator_profile_best_effort(self, cursor: Any, profile_name: str) -> None:
        for sql, params in [
            (
                """
                BEGIN
                    DBMS_SQL_TRANSLATOR.DROP_PROFILE(profile_name => :name);
                END;
                """,
                {"name": profile_name},
            ),
            (
                """
                BEGIN
                    DBMS_SQL_TRANSLATOR.DROP_PROFILE(:name);
                END;
                """,
                {"name": profile_name},
            ),
        ]:
            self._drop_best_effort(cursor, sql, params)

    def _looks_like_signature_error(self, message: str) -> bool:
        normalized = message.upper()
        return (
            "PLS-00306" in normalized
            or "PLS-306" in normalized
            or "PLS-00302" in normalized
            or "ORA-00904" in normalized
            or "ORA-06550" in normalized
        )

    def _looks_like_profile_already_exists(self, message: str) -> bool:
        normalized = message.upper()
        return "PROFILE" in normalized and "ALREADY EXISTS" in normalized

    def _agent_runtime_error(self, exc: Exception) -> OracleAdapterError:
        message = str(exc)
        if self._looks_like_signature_error(message):
            return OracleAdapterError(
                "Oracle Select AI Agent runtime API がこの database では利用できません。"
                f"DBMS_CLOUD_AI_AGENT の version / 権限を確認してください: {message}"
            )
        return OracleAdapterError(f"Oracle Select AI Agent 実行に失敗しました: {message}")

    def _looks_like_agent_profile_loss(self, message: str) -> bool:
        normalized = message.upper()
        return "INVALID PROFILE" in normalized or "ORA-20046" in normalized

    def _tool_name_from_team_name(self, team_name: str) -> str:
        if team_name.endswith("_TEAM"):
            return f"{team_name[: -len('_TEAM')]}_TOOL"
        return f"{team_name}_TOOL"

    def _admin_statement_type(self, statement: str) -> str:
        stripped = str(statement or "").strip()
        while stripped.startswith("--") or stripped.startswith("/*"):
            if stripped.startswith("--"):
                newline = stripped.find("\n")
                stripped = "" if newline < 0 else stripped[newline + 1 :].lstrip()
            else:
                end = stripped.find("*/")
                stripped = "" if end < 0 else stripped[end + 2 :].lstrip()
        if re.match(r"^comment\s+on\b", stripped, flags=re.IGNORECASE):
            return "COMMENT"
        if re.match(r"^(select|with)\b", stripped, flags=re.IGNORECASE):
            return "SELECT"
        if re.match(r"^(begin|declare|exec|execute)\b", stripped, flags=re.IGNORECASE):
            return "PLSQL"
        for keyword in (
            "insert",
            "update",
            "delete",
            "merge",
            "create",
            "drop",
            "alter",
            "truncate",
            "grant",
            "revoke",
        ):
            if re.match(rf"^{keyword}\b", stripped, flags=re.IGNORECASE):
                return keyword.upper()
        return "UNKNOWN"

    def _normalize_admin_statement(self, statement: str) -> str:
        stripped = str(statement or "").strip()
        if re.match(r"^(exec|execute)\b", stripped, flags=re.IGNORECASE):
            body = re.sub(r"^(exec|execute)\s+", "", stripped, flags=re.IGNORECASE).strip()
            return f"BEGIN {body.rstrip(';')}; END;"
        return stripped

    def _fetch_dbms_output(self, cursor: Any, batch: int = 1000) -> str:
        lines: list[str] = []
        try:
            line_var = cursor.var(str)
            status_var = cursor.var(int)
            for _ in range(batch):
                cursor.callproc("dbms_output.get_line", (line_var, status_var))
                if int(status_var.getvalue() or 0) != 0:
                    break
                lines.append(str(line_var.getvalue() or ""))
        except Exception:
            return ""
        return "\n".join(line for line in lines if line)

    def _admin_success_message(self, statement_type: str, row_count: int | None) -> str:
        if statement_type in {"INSERT", "UPDATE", "DELETE", "MERGE"}:
            return f"RowsAffected={row_count if row_count is not None else 0}"
        if statement_type == "PLSQL":
            return "PL/SQL executed"
        if statement_type == "COMMENT":
            return "Comment applied"
        return "OK"

    def _load_oracledb(self) -> Any:
        if self._oracledb is not None:
            return self._oracledb
        try:
            self._oracledb = importlib.import_module("oracledb")
        except ModuleNotFoundError as exc:
            raise OracleAdapterError("python-oracledb がインストールされていません。") from exc
        return self._oracledb

    def _init_client(self, oracledb: Any) -> None:
        if self._client_initialized or not self.settings.oracle_client_lib_dir:
            return
        init_oracle_client = getattr(oracledb, "init_oracle_client", None)
        if callable(init_oracle_client):
            init_oracle_client(lib_dir=self.settings.oracle_client_lib_dir)
        self._client_initialized = True

    def _coerce_csv_value(self, value: str | None, column: CsvImportColumn) -> Any:
        if value is None or value == "":
            return None
        if column.data_type == "NUMBER":
            try:
                return int(value)
            except ValueError:
                return float(value)
        return value
