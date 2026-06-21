"""Optional Oracle runtime adapter for NL2SQL.

この module は `oracledb` を import-time dependency にしない。local / CI は deterministic
adapter のまま動き、`NL2SQL_RUNTIME_MODE=oracle` のときだけ runtime import する。
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from app.settings import Settings

from .models import CsvImportColumn, QueryResults, SchemaCatalog, SchemaColumn, SchemaTable


class OracleAdapterError(RuntimeError):
    """Oracle adapter の実行時エラー。"""


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
        return all(
            [
                self.settings.oracle_user,
                self.settings.oracle_password,
                self.settings.oracle_dsn,
            ]
        )

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
        conn = oracledb.connect(
            user=self.settings.oracle_user,
            password=self.settings.oracle_password,
            dsn=self.settings.oracle_dsn,
            tcp_connect_timeout=self.settings.nl2sql_oracle_connect_timeout_seconds,
        )
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
