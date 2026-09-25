"""Oracle statement 共用执行器。Direct SQL 与 DeepSec 共用此事务/结果契约。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any


class OracleStatementExecutionError(RuntimeError):
    pass


class OracleStatementExecutor:
    """已取得 connection 上で statement 群を一貫して実行する。"""

    def execute(
        self,
        connection: Any,
        statements: Sequence[str],
        *,
        atomic: bool = True,
        include_sql: bool = True,
        normalize: Callable[[str], str] | None = None,
        statement_type: Callable[[str], str] | None = None,
        output_reader: Callable[[Any], str] | None = None,
        success_message: Callable[[str, int | None], str] | None = None,
        ignored_error_codes: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        all_ok = True
        success_count = 0
        normalizer = normalize or (lambda value: value.strip())
        type_resolver = statement_type or self._statement_type
        message_resolver = success_message or self._success_message
        with connection.cursor() as cursor:
            with suppress(Exception):
                cursor.callproc("dbms_output.enable")
            for index, statement in enumerate(statements, start=1):
                started = datetime.now(UTC)
                normalized = normalizer(statement)
                resolved_type = type_resolver(normalized)
                result: dict[str, Any] = {
                    "index": index,
                    "statement_type": resolved_type,
                }
                if include_sql:
                    result["sql"] = normalized
                try:
                    cursor.execute(normalized)
                    row_count = getattr(cursor, "rowcount", None)
                    message = output_reader(cursor) if output_reader else ""
                    result.update(
                        status="success",
                        row_count=row_count if isinstance(row_count, int) else None,
                        message=message or message_resolver(resolved_type, row_count),
                    )
                    success_count += 1
                except Exception as exc:
                    error_code = self._oracle_error_code(exc)
                    if error_code and error_code in ignored_error_codes:
                        result.update(
                            status="skipped",
                            row_count=None,
                            message=f"既存オブジェクトを確認しました ({error_code})。",
                        )
                        success_count += 1
                    else:
                        all_ok = False
                        result.update(
                            status="error",
                            row_count=None,
                            message="",
                            error_message=self._safe_error(exc),
                        )
                result["elapsed_ms"] = int((datetime.now(UTC) - started).total_seconds() * 1000)
                results.append(result)
            should_commit = all_ok if atomic else success_count > 0
            if should_commit:
                connection.commit()
            else:
                connection.rollback()
        return results

    @staticmethod
    def _statement_type(statement: str) -> str:
        match = re.match(r"\s*([A-Za-z]+)", statement)
        return match.group(1).upper() if match else "UNKNOWN"

    @staticmethod
    def _success_message(statement_type: str, row_count: int | None) -> str:
        if statement_type in {"INSERT", "UPDATE", "DELETE", "MERGE"}:
            return f"RowsAffected={row_count if row_count is not None else 0}"
        if statement_type in {"BEGIN", "DECLARE"}:
            return "PL/SQL executed"
        return "OK"

    @staticmethod
    def _oracle_error_code(exc: Exception) -> str | None:
        match = re.search(r"ORA-\d{5}", str(exc), flags=re.IGNORECASE)
        return match.group(0).upper() if match else None

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        # 接続文字列や secret を結果へ含めない。Oracle code と先頭 message のみ返す。
        text = str(exc).replace("\n", " ")[:1000]
        return text


oracle_statement_executor = OracleStatementExecutor()
