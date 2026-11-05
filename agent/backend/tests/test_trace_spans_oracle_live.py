"""Agent の Run の span を、共通の span の表（`PLATFORM_TRACE_SPANS`。#3226 の案 A）に
実の Oracle で書いて読む結合テスト。

CI の実 Oracle の結合テスト（`.github/workflows/oracle-integration.yml`。システムテーブルを
作った後の Oracle AI Database Free）だけで実行する（`AGENT_RUN_ORACLE_INTEGRATION=1`。接続は
`platform/.env` の `PLATFORM_ORACLE_*`）。ほかでは skip する。テストごとに別の trace に書き、
終わったら消す（並列でも互いの行を読まない）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pr_backend_core.chat_progress import chat_progress_details
from pr_backend_core.observability.trace_store import TraceSpanOwner, TraceSpanWriter
from pr_backend_core.observability.tracing import SpanDisplay, SpanUpdate

from app.features.agent.trace_spans import AgentTraceSpans
from app.oracle_connection import connect_platform_oracle, platform_oracle_connection

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_RUN_ORACLE_INTEGRATION") != "1",
    reason="実 Oracle の結合テスト（AGENT_RUN_ORACLE_INTEGRATION=1）だけで実行する",
)

RUN_USER = "aaaaaaaa-0000-0000-0000-000000000001"
OTHER_USER = "aaaaaaaa-0000-0000-0000-000000000002"


@pytest.fixture
def trace_id() -> Iterator[str]:
    value = uuid4().hex
    yield value
    with connect_platform_oracle() as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM PLATFORM_TRACE_SPANS WHERE TRACE_ID = :trace_id", {"trace_id": value}
        )
        connection.commit()


def _update(trace_id: str, span_id: str, status: str, **fields: object) -> SpanUpdate:
    started = datetime(2026, 10, 1, tzinfo=UTC)
    return SpanUpdate.model_validate(
        {
            "trace_id": trace_id,
            "span_id": span_id,
            "operation_name": "gen_ai.execute_tool",
            "status": status,
            "started_at": started,
            "finished_at": None if status == "running" else started + timedelta(seconds=1),
            **fields,
        }
    )


def test_run_spans_are_written_and_read_with_the_owner_filter(trace_id: str) -> None:
    spans = AgentTraceSpans()
    spans.use_writer(
        TraceSpanWriter(connection_factory=platform_oracle_connection, flush_seconds=60)
    )
    owner = TraceSpanOwner(
        product="agent", operation_id=f"run_{trace_id}", owner_user_uuid=RUN_USER
    )
    other = TraceSpanOwner(product="rag", operation_id="live-call", owner_user_uuid=OTHER_USER)
    details = chat_progress_details(code=[("引数", '{"query": "合成の語"}', "json")])
    root = "a000000000000001"

    spans.submit(
        _update(trace_id, root, "running", operation_name="agent.execute", root=True), owner
    )
    tool = _update(
        trace_id,
        "a000000000000002",
        "done",
        parent_span_id=root,
        display=SpanDisplay(kind="tool", params={"tool": "synthetic_tool"}, details=details),
        attributes={"gen_ai.tool.name": "synthetic_tool"},
    )
    spans.submit(tool, owner)
    spans.submit(_update(trace_id, "a000000000000003", "done", parent_span_id=root), other)
    spans.submit(_update(trace_id, root, "done", operation_name="agent.execute", root=True), owner)
    assert spans._writer is not None  # noqa: SLF001 - テストで待たずに書く
    spans._writer.flush()  # noqa: SLF001
    assert spans.dropped(trace_id) == 0

    mine = spans.fetch(trace_id, viewer_user_uuid=RUN_USER)
    assert [(span.span_id, span.status, span.root) for span in mine] == [
        (root, "done", True),
        ("a000000000000002", "done", False),
    ]
    [written_tool] = [span for span in mine if span.span_id == "a000000000000002"]
    assert (written_tool.display_kind, written_tool.display_params) == (
        "tool",
        {"tool": "synthetic_tool"},
    )
    assert written_tool.parent_span_id == root and written_tool.finished_at is not None
    # 全員の span（system_admin）は、ほかの利用者が同じ trace に書いた span も読む。
    assert len(spans.fetch(trace_id, viewer_user_uuid=None)) == 3
    # 工程の詳細（引数）は表に入れない。
    with connect_platform_oracle() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM PLATFORM_TRACE_SPANS WHERE TRACE_ID = :trace_id "
            "AND (DISPLAY_PARAMS_JSON LIKE '%合成の語%' OR ATTRIBUTES_JSON LIKE '%合成の語%')",
            {"trace_id": trace_id},
        )
        assert cursor.fetchone()[0] == 0


def test_span_times_keep_sub_seconds_and_order(trace_id: str) -> None:
    """ミリ秒の差の 2 つの span は、書いて読んでも時刻の差と開始の順を保つ（#3271）。

    python-oracledb は `datetime` を既定で DATE（秒まで）で bind する。秒より細かい部分が落ちると、
    同じ秒の span は SPAN_ID の順になり、所要時間も整数の秒になる。後に始まる span に小さい
    SPAN_ID を付けて、開始の時刻で並ぶことを確かめる。
    """
    spans = AgentTraceSpans()
    spans.use_writer(
        TraceSpanWriter(connection_factory=platform_oracle_connection, flush_seconds=60)
    )
    owner = TraceSpanOwner(
        product="agent", operation_id=f"run_{trace_id}", owner_user_uuid=RUN_USER
    )
    base = datetime(2026, 10, 1, 0, 0, 1, 123456, tzinfo=UTC)
    first, second = "b000000000000002", "b000000000000001"
    times = {
        first: (base, base + timedelta(milliseconds=3)),
        second: (base + timedelta(milliseconds=3), base + timedelta(milliseconds=10)),
    }
    for span_id, (started, finished) in times.items():
        spans.submit(
            SpanUpdate.model_validate(
                {
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "operation_name": "gen_ai.execute_tool",
                    "status": "done",
                    "started_at": started,
                    "finished_at": finished,
                }
            ),
            owner,
        )
    assert spans._writer is not None  # noqa: SLF001 - テストで待たずに書く
    spans._writer.flush()  # noqa: SLF001
    assert spans.dropped(trace_id) == 0

    read = spans.fetch(trace_id, viewer_user_uuid=RUN_USER)
    assert [span.span_id for span in read] == [first, second]
    assert [(span.started_at, span.finished_at) for span in read] == [
        times[first],
        times[second],
    ]


@pytest.mark.parametrize("text", ["", "先頭。" + "合成の本文。" * 16000 + "最後の条件。"])
def test_shared_trace_body_roundtrip_ownership_and_final_deletion(text: str) -> None:
    """共通本文DAOを実Oracleで検証する。製品の正本の認可は別の結合層が担う。"""
    import oracledb
    from pr_backend_core.observability.trace_bodies import (
        BodyDeleted,
        BodyOwner,
        attach_body_owner,
        prepare_body,
        read_body_page,
        release_body_owner,
        write_body,
    )

    prepared = prepare_body(text, part_characters=4097, visibility="system_admin")
    reference = prepared.reference
    message = BodyOwner("agent", "message", uuid4().hex)
    history = BodyOwner("agent", "history", uuid4().hex)
    unrelated = BodyOwner("agent", "message", uuid4().hex)
    try:
        with connect_platform_oracle() as connection, connection.cursor() as cursor:
            attach_body_owner(cursor, message, [reference])
            attach_body_owner(cursor, history, [reference])
            write_body(cursor, prepared, clob_type=oracledb.DB_TYPE_CLOB, batch_size=3)
            connection.commit()
        with connect_platform_oracle() as connection, connection.cursor() as cursor:
            assert read_body_page(cursor, message, reference) is None
            assert read_body_page(cursor, unrelated, reference, allow_restricted=True) is None
            collected: list[str] = []
            start = 0
            while True:
                page = read_body_page(
                    cursor, message, reference, first_part=start, max_parts=3, allow_restricted=True
                )
                assert page is not None and page.status == "complete" and page.failure_code is None
                collected.extend(part.text for part in page.parts)
                if page.next_part is None:
                    break
                start = page.next_part
            assert "".join(collected) == text
            release_body_owner(cursor, message)
            connection.rollback()
            assert read_body_page(cursor, message, reference, allow_restricted=True) is not None
            release_body_owner(cursor, message)
            connection.commit()
        with connect_platform_oracle() as connection, connection.cursor() as cursor:
            assert read_body_page(cursor, message, reference, allow_restricted=True) is None
            assert read_body_page(cursor, history, reference, allow_restricted=True) is not None
            release_body_owner(cursor, history)
            connection.commit()
        with connect_platform_oracle() as connection, connection.cursor() as cursor:
            assert read_body_page(cursor, history, reference, allow_restricted=True) is None
            cursor.execute(
                "SELECT COUNT(*) FROM PLATFORM_TRACE_BODY_PARTS WHERE BODY_ID = :body_id",
                {"body_id": reference.body_id},
            )
            assert cursor.fetchone()[0] == 0
            with pytest.raises(BodyDeleted):
                write_body(cursor, prepared, clob_type=oracledb.DB_TYPE_CLOB)
            connection.rollback()
    finally:
        with connect_platform_oracle() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM PLATFORM_TRACE_BODIES WHERE BODY_ID = :body_id",
                {"body_id": reference.body_id},
            )
            connection.commit()


def test_partial_trace_body_write_rolls_back_and_failure_does_not_downgrade_complete() -> None:
    """途中のINSERT後に整合性検査が失敗しても、部分本文をcompleteとしない。"""
    from dataclasses import replace

    import oracledb
    from pr_backend_core.observability.trace_bodies import (
        BodyOwner,
        attach_body_owner,
        mark_body_incomplete,
        prepare_body,
        read_body_page,
        write_body,
    )

    body = prepare_body("合成本文" * 1000, part_characters=101)
    owner = BodyOwner("agent", "history", uuid4().hex)
    try:
        with connect_platform_oracle() as connection, connection.cursor() as cursor:
            attach_body_owner(cursor, owner, [body.reference])
            connection.commit()
            corrupted = replace(body, text="別" + body.text[1:])
            with pytest.raises(ValueError, match="do not cover"):
                write_body(cursor, corrupted, clob_type=oracledb.DB_TYPE_CLOB, batch_size=2)
            connection.rollback()
        with connect_platform_oracle() as connection, connection.cursor() as cursor:
            page = read_body_page(cursor, owner, body.reference)
            assert page is not None and page.status == "pending" and not page.parts
            mark_body_incomplete(cursor, body.reference, "write_failed")
            connection.commit()
            page = read_body_page(cursor, owner, body.reference)
            assert page is not None and page.status == "incomplete"
            assert page.failure_code == "write_failed"
            write_body(cursor, body, clob_type=oracledb.DB_TYPE_CLOB)
            connection.commit()
            mark_body_incomplete(cursor, body.reference, "write_failed")
            connection.commit()
            page = read_body_page(cursor, owner, body.reference)
            assert page is not None and page.status == "complete" and page.failure_code is None
    finally:
        with connect_platform_oracle() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM PLATFORM_TRACE_BODIES WHERE BODY_ID = :body_id",
                {"body_id": body.reference.body_id},
            )
            connection.commit()


def test_page_read_scopes_latest_attempt_before_limit(trace_id: str) -> None:
    """新しい page SQL を実 Oracle で通し、2件と追加1件の境界を確認する。"""
    from pr_backend_core.observability.trace_store import fetch_trace_span_page

    spans = AgentTraceSpans()
    spans.use_writer(
        TraceSpanWriter(connection_factory=platform_oracle_connection, flush_seconds=60)
    )
    owner = TraceSpanOwner(
        product="agent", operation_id="synthetic-run", owner_user_uuid=RUN_USER, attempt=2
    )
    old = TraceSpanOwner(
        product="agent", operation_id="synthetic-run", owner_user_uuid=RUN_USER, attempt=1
    )
    hidden = TraceSpanOwner(
        product="agent", operation_id="synthetic-run", owner_user_uuid=OTHER_USER, attempt=3
    )
    other_product = TraceSpanOwner(
        product="rag", operation_id="synthetic-run", owner_user_uuid=RUN_USER
    )
    other_operation = TraceSpanOwner(
        product="agent", operation_id="other-run", owner_user_uuid=RUN_USER
    )
    for index, current in enumerate(
        (old, old, old, hidden, other_product, other_operation), start=1
    ):
        spans.submit(_update(trace_id, f"{index:016x}", "done"), current)
    for index in (20, 21):
        spans.submit(_update(trace_id, f"{index:016x}", "done"), owner)
    assert spans._writer is not None  # noqa: SLF001 - 試験では同期 flush する。
    spans._writer.flush()  # noqa: SLF001
    with platform_oracle_connection() as connection:
        page = fetch_trace_span_page(
            connection,
            trace_id,
            viewer_user_uuid=RUN_USER,
            limit=2,
            product="agent",
            operation_id="synthetic-run",
        )
    assert [span.span_id for span in page.spans] == [f"{index:016x}" for index in (20, 21)]
    assert page.truncated is False
    # Agent の全製品の木では、自分が実行した呼び先を含めて2件を超える。
    assert spans.fetch_page(trace_id, viewer_user_uuid=RUN_USER, limit=2).truncated is True
    spans.submit(_update(trace_id, f"{22:016x}", "done"), owner)
    spans._writer.flush()  # noqa: SLF001
    with platform_oracle_connection() as connection:
        page = fetch_trace_span_page(
            connection,
            trace_id,
            viewer_user_uuid=RUN_USER,
            limit=2,
            product="agent",
            operation_id="synthetic-run",
        )
    assert len(page.spans) == 2 and page.truncated is True
