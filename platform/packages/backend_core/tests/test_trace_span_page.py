"""spanの有界pageと既存list読取の契約。実Oracleは呼ばない。"""

from datetime import UTC, datetime
from typing import Any

import pytest

from pr_backend_core.observability.trace_store import fetch_trace_span_page, fetch_trace_spans


class Cursor:
    def __init__(self, count: int) -> None:
        self.rows = [
            (
                "a" * 32,
                f"{i:016x}",
                None,
                "rag",
                "synthetic-op",
                0,
                1,
                "owner",
                "synthetic",
                "internal",
                "done",
                datetime(2026, 1, 1, tzinfo=UTC),
                None,
                None,
                None,
                None,
            )
            for i in range(count)
        ]
        self.sql = ""
        self.binds: dict[str, Any] = {}

    def execute(self, sql: str, binds: dict[str, Any]) -> None:
        self.sql, self.binds = sql, binds

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows[: self.binds["limit"]]

    def cursor(self) -> "Cursor":
        return self


@pytest.mark.parametrize("count", [999, 1000, 1001])
def test_page_probes_one_extra_row_without_changing_writer_facts(count: int) -> None:
    connection = Cursor(count)
    page = fetch_trace_span_page(
        connection, "a" * 32, viewer_user_uuid="owner", product="rag", operation_id="synthetic-op"
    )
    assert len(page.spans) == min(count, 1000)
    assert page.truncated is (count > 1000)
    assert connection.binds == {
        "trace_id": "a" * 32,
        "viewer": "owner",
        "product": "rag",
        "operation_id": "synthetic-op",
        "limit": 1001,
    }
    assert connection.sql.count("OWNER_USER_UUID = :viewer") == 2
    assert connection.sql.count("PRODUCT = :product") == 2
    assert connection.sql.count("OPERATION_ID = :operation_id") == 2
    assert "MAX(ATTEMPT)" in connection.sql


def test_legacy_list_reader_keeps_its_original_selection() -> None:
    connection = Cursor(1001)
    assert len(fetch_trace_spans(connection, "a" * 32, viewer_user_uuid=None)) == 1000
    assert "MAX(ATTEMPT)" not in connection.sql
    assert connection.binds == {"trace_id": "a" * 32, "limit": 1000}


@pytest.mark.parametrize("limit", [0, -1, 1001])
def test_page_rejects_unbounded_or_invalid_limits(limit: int) -> None:
    with pytest.raises(ValueError):
        fetch_trace_span_page(Cursor(1), "a" * 32, viewer_user_uuid=None, limit=limit)
