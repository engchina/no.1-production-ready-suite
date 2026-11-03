"""全文の分割契約。保存の64k制限やUTF-8境界で原文を失わない。"""

import hashlib
from dataclasses import replace
from typing import Any

import pytest

from pr_backend_core.observability.trace_bodies import (
    BodyDeleted,
    BodyOwner,
    attach_body_owner,
    lock_body_manifest,
    mark_body_incomplete,
    prepare_body,
    read_body_page,
    release_body_owner,
    write_body,
)


def test_unicode_body_larger_than_old_capture_limit_keeps_tail_and_offsets() -> None:
    text = "先頭。" + "合成の本文🗂。" * 15000 + "最終条件。"
    prepared = prepare_body(text, part_characters=997)
    parts = list(prepared.parts())
    assert "".join(part.text for part in parts) == text
    assert parts[-1].text.endswith("最終条件。")
    assert all(left.end == right.start for left, right in zip(parts, parts[1:], strict=False))
    assert all(part.text == text[part.start : part.end] for part in parts)
    assert prepared.reference.byte_count == len(text.encode("utf-8"))
    assert prepared.reference.sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_recorded_empty_body_has_one_empty_part() -> None:
    prepared = prepare_body("", part_characters=500)
    assert prepared.reference.character_count == 0
    assert prepared.reference.part_count == 1
    (part,) = prepared.parts()
    assert part.text == "" and part.start == part.end == 0


def test_record_owners_are_distinct_even_for_same_product() -> None:
    assert BodyOwner("rag", "message", "record-a") != BodyOwner("rag", "history", "record-a")
    assert BodyOwner("rag", "history", "record-a") != BodyOwner("agent", "history", "record-a")


@pytest.mark.parametrize("part_size", [0, -1])
def test_invalid_partition_size_is_not_silent_data_loss(part_size: int) -> None:
    with pytest.raises(ValueError):
        prepare_body("合成本文", part_characters=part_size)


class OwnerCursor:
    """取引は呼出側が持つDB-API代役。真のOracleでのSQL確認は別の統合試験で行う。"""

    def __init__(self) -> None:
        self.manifests: dict[str, list[Any]] = {}
        self.owners: set[tuple[str, str, str, str]] = set()
        self.parts: dict[str, list[str]] = {}
        self.part_rows: dict[str, list[tuple[Any, ...]]] = {}
        self.failures: dict[str, str] = {}
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, binds: dict[str, Any]) -> None:
        sql = " ".join(sql.split())
        body_id = binds.get("body_id", "")
        if sql.startswith("MERGE INTO PLATFORM_TRACE_BODIES "):
            self.manifests.setdefault(
                body_id,
                [binds[key] for key in ("sha256", "characters", "bytes", "parts", "visibility")]
                + ["pending"],
            )
        elif sql.startswith("SELECT CONTENT_SHA256"):
            value = self.manifests.get(body_id)
            self.rows = [tuple(value)] if value else []
        elif sql.startswith("MERGE INTO PLATFORM_TRACE_BODY_OWNERS "):
            self.owners.add((binds["product"], binds["kind"], binds["record_id"], body_id))
        elif sql.startswith("SELECT BODY_ID FROM PLATFORM_TRACE_BODY_OWNERS"):
            self.rows = sorted(
                (key[3],)
                for key in self.owners
                if key[:3] == (binds["product"], binds["kind"], binds["record_id"])
            )
        elif sql.startswith("SELECT BODY_ID FROM PLATFORM_TRACE_BODIES"):
            self.rows = [(body_id,)] if body_id in self.manifests else []
        elif sql.startswith("DELETE FROM PLATFORM_TRACE_BODY_OWNERS"):
            self.owners.discard((binds["product"], binds["kind"], binds["record_id"], body_id))
        elif sql.startswith("SELECT COUNT(*) FROM PLATFORM_TRACE_BODY_OWNERS"):
            self.rows = [(sum(key[3] == body_id for key in self.owners),)]
        elif sql.startswith("DELETE FROM PLATFORM_TRACE_BODY_PARTS"):
            self.parts.pop(body_id, None)
            self.part_rows.pop(body_id, None)
        elif sql.startswith("UPDATE PLATFORM_TRACE_BODIES SET STATUS = 'deleted'"):
            self.manifests[body_id][5] = "deleted"
        elif sql.startswith("UPDATE PLATFORM_TRACE_BODIES SET STATUS = 'complete'"):
            self.manifests[body_id][5] = "complete"
            self.failures.pop(body_id, None)
        elif sql.startswith("UPDATE PLATFORM_TRACE_BODIES SET STATUS = 'incomplete'"):
            self.manifests[body_id][5] = "incomplete"
            self.failures[body_id] = binds["reason"]
        elif sql.startswith("SELECT b.CONTENT_SHA256"):
            owner = (binds["product"], binds["kind"], binds["record_id"], body_id)
            value = self.manifests.get(body_id)
            self.rows = (
                [(*value, self.failures.get(body_id))]
                if value
                and value[5] != "deleted"
                and owner in self.owners
                and (value[4] == "owner" or binds["allow_restricted"] == 1)
                else []
            )
        elif sql.startswith("SELECT p.PART_INDEX"):
            owner = (binds["product"], binds["kind"], binds["record_id"], body_id)
            self.rows = (
                [row for row in self.part_rows.get(body_id, []) if row[0] >= binds["first_part"]][
                    : binds["take"]
                ]
                if owner in self.owners
                else []
            )
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def setinputsizes(self, **sizes: Any) -> None:
        assert sizes["content_text"] is not None

    def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None:
        assert "INSERT INTO PLATFORM_TRACE_BODY_PARTS" in sql
        for row in rows:
            self.parts.setdefault(row["body_id"], []).append(row["content_text"])
            self.part_rows.setdefault(row["body_id"], []).append(
                tuple(
                    row[key]
                    for key in (
                        "part_index",
                        "start_offset",
                        "end_offset",
                        "part_sha256",
                        "content_text",
                    )
                )
            )

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)


def test_delete_chat_keeps_shared_history_and_last_owner_deletes_body() -> None:
    body = prepare_body("合成の長い全文" * 10000, part_characters=1000)
    cursor = OwnerCursor()
    chat = BodyOwner("rag", "message", "message-a")
    history = BodyOwner("rag", "history", "history-a")
    attach_body_owner(cursor, chat, [body.reference])
    attach_body_owner(cursor, history, [body.reference])
    cursor.parts[body.reference.body_id] = [part.text for part in body.parts()]
    release_body_owner(cursor, chat)
    assert "".join(cursor.parts[body.reference.body_id]) == body.text
    assert lock_body_manifest(cursor, body.reference) == "pending"
    release_body_owner(cursor, history)
    assert body.reference.body_id not in cursor.parts
    with pytest.raises(BodyDeleted):
        lock_body_manifest(cursor, body.reference)
    with pytest.raises(BodyDeleted):
        attach_body_owner(cursor, chat, [body.reference])
    assert not cursor.owners


def test_deleting_another_record_does_not_release_same_product_body() -> None:
    body = prepare_body("合成本文", part_characters=1000)
    cursor = OwnerCursor()
    owner = BodyOwner("agent", "run", "run-a")
    attach_body_owner(cursor, owner, [body.reference, body.reference])
    release_body_owner(cursor, BodyOwner("agent", "run", "run-b"))
    assert len(cursor.owners) == 1
    assert lock_body_manifest(cursor, body.reference) == "pending"


def test_full_body_is_read_in_pages_for_the_actual_owner_only() -> None:
    body = prepare_body("全文。" * 40000 + "末尾。", part_characters=1023)
    cursor = OwnerCursor()
    owner = BodyOwner("rag", "history", "history-a")
    attach_body_owner(cursor, owner, [body.reference])
    write_body(cursor, body, clob_type=object(), batch_size=3)
    collected: list[str] = []
    first = 0
    while True:
        page = read_body_page(cursor, owner, body.reference, first_part=first, max_parts=4)
        assert page is not None and page.status == "complete"
        collected.extend(part.text for part in page.parts)
        if page.next_part is None:
            break
        assert page.next_part > first
        first = page.next_part
    assert "".join(collected) == body.text
    assert read_body_page(cursor, BodyOwner("rag", "history", "history-b"), body.reference) is None


def test_restricted_body_does_not_follow_a_forged_owner_visibility() -> None:
    body = prepare_body("管理者限定の合成本文", part_characters=1023, visibility="system_admin")
    cursor = OwnerCursor()
    owner = BodyOwner("nl2sql", "job", "job-a")
    attach_body_owner(cursor, owner, [body.reference])
    write_body(cursor, body, clob_type=object())
    assert read_body_page(cursor, owner, body.reference) is None
    assert read_body_page(cursor, owner, replace(body.reference, visibility="owner")) is None
    page = read_body_page(cursor, owner, body.reference, allow_restricted=True)
    assert page is not None and "".join(part.text for part in page.parts) == body.text


def test_missing_parts_are_never_marked_complete_on_read() -> None:
    body = prepare_body("前半。後半。", part_characters=3)
    cursor = OwnerCursor()
    owner = BodyOwner("rag", "message", "message-a")
    attach_body_owner(cursor, owner, [body.reference])
    write_body(cursor, body, clob_type=object())
    cursor.part_rows[body.reference.body_id].pop()
    page = read_body_page(cursor, owner, body.reference)
    assert page is not None and page.status == "incomplete"
    assert page.failure_code == "body_parts_missing"


def test_write_failure_and_later_failure_cannot_claim_or_destroy_complete_body() -> None:
    class FailingCursor(OwnerCursor):
        written = 0

        def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None:
            if self.written:
                raise RuntimeError("synthetic storage failure")
            super().executemany(sql, rows)
            self.written += 1

    body = prepare_body("合成本文" * 100, part_characters=10)
    cursor = FailingCursor()
    owner = BodyOwner("rag", "history", "history-a")
    attach_body_owner(cursor, owner, [body.reference])
    with pytest.raises(RuntimeError):
        write_body(cursor, body, clob_type=object(), batch_size=1)
    assert lock_body_manifest(cursor, body.reference) == "pending"
    mark_body_incomplete(cursor, body.reference, "write_failed")
    page = read_body_page(cursor, owner, body.reference)
    assert page is not None and page.status == "incomplete" and page.failure_code == "write_failed"
    # 接続のrollbackを経た再試行を別cursorで模擬し、成功後の遅れた障害通知は無視する。
    retry = OwnerCursor()
    attach_body_owner(retry, owner, [body.reference])
    write_body(retry, body, clob_type=object())
    mark_body_incomplete(retry, body.reference, "write_failed")
    assert lock_body_manifest(retry, body.reference) == "complete"


def test_conflicting_references_are_rejected_before_changing_owners() -> None:
    reference = prepare_body("body", part_characters=3).reference
    cursor = OwnerCursor()
    with pytest.raises(ValueError, match="conflicting"):
        attach_body_owner(
            cursor,
            BodyOwner("rag", "message", "a"),
            [reference, replace(reference, visibility="system_admin")],
        )
    assert not cursor.manifests and not cursor.owners


@pytest.mark.parametrize(
    "values", [{"body_id": "bad"}, {"sha256": "bad"}, {"character_count": -1}, {"part_count": 0}]
)
def test_invalid_reference_is_not_passed_to_storage(values: dict[str, Any]) -> None:
    reference = prepare_body("body", part_characters=3).reference
    with pytest.raises(ValueError):
        replace(reference, **values)
