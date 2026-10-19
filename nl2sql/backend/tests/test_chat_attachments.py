"""チャットの添付（会話の期間だけ使う作業ファイル。#2517）。

- 添付の Excel・CSV のシートを WITH 句の行の表 `ATTACHMENT_<n>` にし、SQL の生成（Enterprise AI
  Direct）に表の名前・列・型・先頭の行を渡す。検証は列の形だけの CTE で、実行は JSON_TABLE と
  CLOB の bind で行い、動的な CREATE TABLE はしない。
- チャットの送信: 添付を会話に結び付け、ターンに添付の名前を残す。Select AI の方式では止める。
- 業務の表との JOIN は検証を通り、業務プロファイルの外の表は今までどおり止める。

保存は platform のメモリの store、モデルは決定論の fake（合成のデータ）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from pr_system_settings.chat_attachments import ChatAttachmentService, InMemoryChatAttachmentStore
from test_nl2sql_job_runtime import _FakeEnterpriseAiClient, _repository, _request, _worker

from app.features.nl2sql import chat_attachments
from app.features.nl2sql.chat_attachments import (
    AttachmentContext,
    attachment_context,
    executable_with_attachments,
    prompt_block,
    referenced_tables,
    use_attachment_context,
    validation_sql,
)
from app.features.nl2sql.models import AllowedObjects, JobCreateRequest, Nl2SqlEngine
from app.features.nl2sql.service import Nl2SqlService
from app.security.permissions import ROUTE_PERMISSIONS
from app.settings import get_settings

CSV = "注文 ID,金額,受付日\n1,1200,2026-01-05\n2,3400.5,2026-02-10\n".encode()
JOIN_SQL = 'SELECT o.ID, a."金額" FROM APP.ORDERS o JOIN ATTACHMENT_1 a ON a."注文 ID" = o.ID'


@dataclass(frozen=True)
class _Principal:
    user_uuid: str
    is_system_admin: bool = False


class _Client(_FakeEnterpriseAiClient):
    def __init__(self) -> None:
        super().__init__(
            f'{{"sql":"{JOIN_SQL.replace(chr(34), chr(92) + chr(34))}","explanation":"照合"}}'
        )
        self.contexts: list[str] = []

    def generate(self, **kwargs: object) -> str:
        self.contexts.append(str(kwargs["context"]))
        return self.text


@pytest.fixture
def service() -> Iterator[ChatAttachmentService]:
    created = ChatAttachmentService(
        InMemoryChatAttachmentStore(), product="nl2sql", settings=lambda: SimpleNamespace()
    )
    chat_attachments.set_chat_attachment_service(created)
    yield created
    chat_attachments.set_chat_attachment_service(None)


def _upload(service: ChatAttachmentService, user: str = "user-1", name: str = "a.csv") -> str:
    item = asyncio.run(service.upload(_Principal(user, True), file_name=name, data=CSV))
    return item.attachment_id


def _bound(service: ChatAttachmentService) -> AttachmentContext:
    attachment_id = _upload(service)
    service.attach_to_conversation(
        _Principal("user-1", True), [attachment_id], conversation_id="c1", owner_user_uuid="user-1"
    )
    return attachment_context("c1", "金額")


# ---- 添付の表 ----


def test_sheet_becomes_typed_attachment_table(service: ChatAttachmentService) -> None:
    context = _bound(service)
    [table] = context.tables
    assert table.name == "ATTACHMENT_1"
    assert [(column.name, column.sql_type) for column in table.columns] == [
        ("注文 ID", "NUMBER"),
        ("金額", "NUMBER"),
        ("受付日", "DATE"),
    ]
    block = prompt_block(context)
    assert "table ATTACHMENT_1（a.csv のシート「a」、2 行）" in block
    assert 'column "受付日" type=DATE' in block
    assert "OWNER を付けずに" in block


def test_validation_and_execution_prepend_ctes(service: ChatAttachmentService) -> None:
    context = _bound(service)
    assert [table.name for table in referenced_tables(JOIN_SQL, context)] == ["ATTACHMENT_1"]
    assert referenced_tables("SELECT 1 FROM APP.ATTACHMENT_10", context) == []
    checked = validation_sql(JOIN_SQL, context)
    assert checked.startswith(
        'WITH ATTACHMENT_1 ("注文 ID", "金額", "受付日") AS (SELECT CAST(NULL'
    )
    assert ":attachment_1" not in checked
    executable, binds = executable_with_attachments(JOIN_SQL, context)
    assert "JSON_TABLE(:attachment_1, '$[*]' COLUMNS (\"注文 ID\" NUMBER PATH '$[0]'" in executable
    assert executable.endswith(JOIN_SQL)
    assert binds == {"attachment_1": '[[1, 1200, "2026-01-05"], [2, 3400.5, "2026-02-10"]]'}
    # SQL が WITH で始まるときは、その WITH の最初に足す。
    merged, _ = executable_with_attachments(
        "WITH t AS (SELECT * FROM ATTACHMENT_1) SELECT * FROM t", context
    )
    assert (
        merged.startswith("WITH ATTACHMENT_1") and ",\nt AS (SELECT * FROM ATTACHMENT_1)" in merged
    )
    assert executable_with_attachments("SELECT 1 FROM DUAL", context) == ("SELECT 1 FROM DUAL", {})


def test_analyze_allows_join_with_attachment_but_not_other_tables(
    service: ChatAttachmentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _worker(_repository())
    allowed = AllowedObjects(table_names=["APP.ORDERS"], enforce_table_scope=True)
    with use_attachment_context(_bound(service)):
        ok = worker.analyze_sql(JOIN_SQL, allowed, None)
        assert ok.safety.is_safe, ok.safety.blocked_reason
        blocked = worker.analyze_sql(
            "SELECT * FROM APP.SECRET s JOIN ATTACHMENT_1 a ON 1 = 1", allowed, None
        )
        assert not blocked.safety.is_safe
    # 添付の無い会話では、添付の表の名前は未知の表として止める。
    assert not worker.analyze_sql(JOIN_SQL, allowed, None).safety.is_safe


# ---- チャットの送信 ----


def _chat_request(attachment_ids: list[str], engine: Nl2SqlEngine) -> JobCreateRequest:
    return _request().model_copy(
        update={
            "question": "添付の金額と注文を照合して",
            "generation_only": True,
            "chat": True,
            "engine": engine,
            "attachment_ids": attachment_ids,
        }
    )


def test_chat_turn_binds_attachments_and_passes_tables_to_generation(
    service: ChatAttachmentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_job_worker_mode", "external")
    client = _Client()
    worker: Nl2SqlService = _worker(_repository(), client)
    attachment_id = _upload(service)
    created = worker.start_job(
        _chat_request([attachment_id], Nl2SqlEngine.ENTERPRISE_AI_DIRECT),
        actor_user_uuid="user-1",
        actor_is_system_admin=True,
    )
    assert worker.run_next_nl2sql_job(worker_id="attachments", job_id=created.job_id)
    job = worker.get_job(created.job_id, actor_user_uuid="user-1")
    assert job is not None
    assert [(item.attachment_id, item.file_name) for item in job.attachments] == [
        (attachment_id, "a.csv")
    ]
    assert [record.attachment_id for record in service.conversation_records(created.job_id)] == [
        attachment_id
    ]
    assert any("attachment_tables:" in context for context in client.contexts)
    assert job.result is not None and job.result.generated_sql.endswith("o.ID")
    assert job.result.safety.is_safe, job.result.safety.blocked_reason


def test_select_ai_engine_refuses_attachments(service: ChatAttachmentService) -> None:
    worker = _worker(_repository())
    attachment_id = _upload(service)
    with pytest.raises(ValueError, match="Enterprise AI"):
        worker.start_job(
            _chat_request([attachment_id], Nl2SqlEngine.SELECT_AI),
            actor_user_uuid="user-1",
            actor_is_system_admin=True,
        )


def test_attachments_of_others_are_not_found(service: ChatAttachmentService) -> None:
    worker = _worker(_repository())
    attachment_id = _upload(service, user="user-2")
    from pr_system_settings.chat_attachments import ChatAttachmentError

    with pytest.raises(ChatAttachmentError) as caught:
        worker.start_job(
            _chat_request([attachment_id], Nl2SqlEngine.ENTERPRISE_AI_DIRECT),
            actor_user_uuid="user-1",
            actor_is_system_admin=False,
        )
    assert caught.value.status_code == 404


def test_execute_select_binds_rows_as_clob() -> None:
    from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter

    calls: list[Any] = []

    class _Cursor:
        description = [("N", None, None, None, None, None, None)]

        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def setinputsizes(self, **sizes: object) -> None:
            calls.append(("sizes", sorted(sizes)))

        def execute(self, sql: str, binds: object = None) -> None:
            calls.append(("execute", binds))

        def fetchmany(self, size: int) -> list[Any]:
            return []

        def fetchall(self) -> list[Any]:
            return []

    class _Connection:
        def __enter__(self) -> _Connection:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def cursor(self) -> _Cursor:
            return _Cursor()

    adapter = OracleNl2SqlAdapter.__new__(OracleNl2SqlAdapter)
    adapter.settings = get_settings()
    adapter.user_data_connection = lambda read_only=True: _Connection()  # type: ignore[method-assign,assignment,misc]
    adapter._load_oracledb = lambda: SimpleNamespace(DB_TYPE_CLOB="CLOB")  # type: ignore[method-assign]
    adapter.execute_select("WITH ATTACHMENT_1 ... SELECT 1", 10, binds={"attachment_1": "[]"})
    assert calls[0] == ("sizes", ["attachment_1"])
    assert calls[1] == ("execute", {"attachment_1": "[]"})


def test_routes_are_in_the_permission_manifest() -> None:
    assert ("POST", "/chat-attachments") in ROUTE_PERMISSIONS
    assert ("DELETE", "/chat-attachments/{attachment_id}") in ROUTE_PERMISSIONS
    assert ROUTE_PERMISSIONS[("PUT", "/settings/chat-attachments")]
