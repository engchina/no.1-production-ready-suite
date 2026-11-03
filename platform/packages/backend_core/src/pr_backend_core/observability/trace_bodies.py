"""製品の所属記録に結び付く、全文snapshotの分割保存契約。

本文は呼出側で秘密値・媒体を除去した後の値。原文を切り捨てず、分割のサイズだけを
制限する。権限は製品の所属記録で確認し、保存層でも所属と管理者限定の条件を再確認する。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

BodyVisibility = Literal["owner", "system_admin"]
BodyState = Literal["pending", "complete", "incomplete"]
BodyProduct = Literal["rag", "nl2sql", "agent"]
BodyFailure = Literal[
    "storage_unavailable",
    "queue_full",
    "write_failed",
    "capture_failed",
    "writer_stopped",
    "invalid_encoding",
]

BODY_TABLES = (
    "PLATFORM_TRACE_BODIES",
    "PLATFORM_TRACE_BODY_PARTS",
    "PLATFORM_TRACE_BODY_OWNERS",
)

BODY_DDL = (
    """
    CREATE TABLE PLATFORM_TRACE_BODIES (
        BODY_ID VARCHAR2(32) NOT NULL,
        CONTENT_SHA256 VARCHAR2(64) NOT NULL,
        CHARACTER_COUNT NUMBER(19) NOT NULL,
        BYTE_COUNT NUMBER(19) NOT NULL,
        PART_COUNT NUMBER(10) NOT NULL,
        VISIBILITY VARCHAR2(16) NOT NULL,
        STATUS VARCHAR2(16) DEFAULT 'pending' NOT NULL,
        FAILURE_CODE VARCHAR2(64),
        CREATED_AT TIMESTAMP(6) WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        UPDATED_AT TIMESTAMP(6) WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        CONSTRAINT PK_PLATFORM_TRACE_BODIES PRIMARY KEY (BODY_ID),
        CONSTRAINT CK_PLATFORM_TRACE_BODY_STATUS CHECK (
            STATUS IN ('pending', 'complete', 'incomplete', 'deleted')),
        CONSTRAINT CK_PLATFORM_TRACE_BODY_VIS CHECK (
            VISIBILITY IN ('owner', 'system_admin')),
        CONSTRAINT CK_PLATFORM_TRACE_BODY_SIZE CHECK (
            CHARACTER_COUNT >= 0 AND BYTE_COUNT >= 0 AND PART_COUNT > 0)
    )
    """,
    """
    CREATE TABLE PLATFORM_TRACE_BODY_PARTS (
        BODY_ID VARCHAR2(32) NOT NULL,
        PART_INDEX NUMBER(10) NOT NULL,
        START_OFFSET NUMBER(19) NOT NULL,
        END_OFFSET NUMBER(19) NOT NULL,
        CONTENT_SHA256 VARCHAR2(64) NOT NULL,
        CONTENT_TEXT CLOB,
        CONSTRAINT PK_PLATFORM_TRACE_BODY_PARTS PRIMARY KEY (BODY_ID, PART_INDEX),
        CONSTRAINT FK_PLATFORM_TRACE_BODY_PARTS FOREIGN KEY (BODY_ID)
            REFERENCES PLATFORM_TRACE_BODIES (BODY_ID) ON DELETE CASCADE,
        CONSTRAINT CK_PLATFORM_TRACE_BODY_PART_RANGE CHECK (
            PART_INDEX >= 0 AND START_OFFSET >= 0 AND END_OFFSET >= START_OFFSET)
    )
    """,
    """
    CREATE TABLE PLATFORM_TRACE_BODY_OWNERS (
        PRODUCT VARCHAR2(16) NOT NULL,
        OWNER_KIND VARCHAR2(64) NOT NULL,
        OWNER_ID VARCHAR2(256) NOT NULL,
        BODY_ID VARCHAR2(32) NOT NULL,
        CONSTRAINT PK_PLATFORM_TRACE_BODY_OWNERS
            PRIMARY KEY (PRODUCT, OWNER_KIND, OWNER_ID, BODY_ID),
        CONSTRAINT FK_PLATFORM_TRACE_BODY_OWNER FOREIGN KEY (BODY_ID)
            REFERENCES PLATFORM_TRACE_BODIES (BODY_ID) ON DELETE CASCADE,
        CONSTRAINT CK_PLATFORM_TRACE_BODY_PRODUCT CHECK (
            PRODUCT IN ('rag', 'nl2sql', 'agent'))
    )
    """,
    "CREATE INDEX IX_PLATFORM_TRACE_BODY_OWNER ON PLATFORM_TRACE_BODY_OWNERS (BODY_ID)",
)


@dataclass(frozen=True, slots=True)
class BodyOwner:
    """利用者ではなく、本文の保持を必要とする実際の保存記録を表す。"""

    product: BodyProduct
    kind: str
    record_id: str

    def __post_init__(self) -> None:
        if self.product not in {"rag", "nl2sql", "agent"}:
            raise ValueError("unsupported trace body product")
        if not self.kind.strip() or len(self.kind) > 64:
            raise ValueError("trace body owner kind is required")
        if not self.record_id.strip() or len(self.record_id) > 256:
            raise ValueError("trace body owner record is required")


@dataclass(frozen=True, slots=True)
class BodyReference:
    body_id: str
    sha256: str
    character_count: int
    byte_count: int
    part_count: int
    visibility: BodyVisibility

    def __post_init__(self) -> None:
        if len(self.body_id) != 32 or any(
            value not in "0123456789abcdef" for value in self.body_id
        ):
            raise ValueError("invalid trace body identifier")
        if len(self.sha256) != 64 or any(value not in "0123456789abcdef" for value in self.sha256):
            raise ValueError("invalid trace body digest")
        if (
            self.character_count < 0
            or self.byte_count < self.character_count
            or self.part_count < 1
        ):
            raise ValueError("invalid trace body size")
        if self.visibility not in {"owner", "system_admin"}:
            raise ValueError("unsupported trace body visibility")


@dataclass(frozen=True, slots=True)
class BodyPart:
    index: int
    start: int
    end: int
    text: str
    sha256: str


@dataclass(frozen=True, slots=True)
class PreparedBody:
    reference: BodyReference
    text: str
    part_characters: int

    def parts(self) -> Iterator[BodyPart]:
        """Unicodeの文字境界で順序どおりに分け、空本文も記録済みとして1片持つ。"""
        for index in range(self.reference.part_count):
            start = index * self.part_characters
            end = min(start + self.part_characters, len(self.text))
            text = self.text[start:end]
            yield BodyPart(index, start, end, text, hashlib.sha256(text.encode()).hexdigest())


def prepare_body(
    text: str, *, part_characters: int, visibility: BodyVisibility = "owner"
) -> PreparedBody:
    """全文の長さ・hashを固定する。分割数の上限で末尾を捨てない。"""
    if part_characters < 1:
        raise ValueError("trace body part size must be positive")
    if visibility not in {"owner", "system_admin"}:
        raise ValueError("unsupported trace body visibility")
    encoded = text.encode("utf-8")
    return PreparedBody(
        BodyReference(
            uuid4().hex,
            hashlib.sha256(encoded).hexdigest(),
            len(text),
            len(encoded),
            max(1, (len(text) + part_characters - 1) // part_characters),
            visibility,
        ),
        text,
        part_characters,
    )


@dataclass(frozen=True, slots=True)
class BodyPage:
    reference: BodyReference
    status: BodyState
    failure_code: str | None
    parts: tuple[BodyPart, ...]
    next_part: int | None


class BodyDeleted(ValueError):
    """最後の所属記録の削除後に遅れた書込が本文を復活させないための状態。"""


def _reference_binds(reference: BodyReference) -> dict[str, str | int]:
    return {
        "body_id": reference.body_id,
        "sha256": reference.sha256,
        "characters": reference.character_count,
        "bytes": reference.byte_count,
        "parts": reference.part_count,
        "visibility": reference.visibility,
    }


def _owner_binds(owner: BodyOwner) -> dict[str, str]:
    return {"product": owner.product, "kind": owner.kind, "record_id": owner.record_id}


def lock_body_manifest(cursor: Any, reference: BodyReference) -> str:
    """呼出側の取引内でpendingの箱を作りロックする。既存本文の同一性は書き換えない。"""
    try:
        cursor.execute(
            """
            MERGE INTO PLATFORM_TRACE_BODIES t
            USING (SELECT :body_id AS BODY_ID FROM DUAL) s ON (t.BODY_ID = s.BODY_ID)
            WHEN NOT MATCHED THEN INSERT (
                BODY_ID, CONTENT_SHA256, CHARACTER_COUNT, BYTE_COUNT, PART_COUNT, VISIBILITY
            ) VALUES (:body_id, :sha256, :characters, :bytes, :parts, :visibility)
            """,
            _reference_binds(reference),
        )
    except Exception as exc:
        # 同じ箱の最初の登録が競合した場合だけ、先に作られた行をロックして照合する。
        if not exc.args or getattr(exc.args[0], "code", None) != 1:
            raise
    cursor.execute(
        """SELECT CONTENT_SHA256, CHARACTER_COUNT, BYTE_COUNT, PART_COUNT, VISIBILITY, STATUS
        FROM PLATFORM_TRACE_BODIES WHERE BODY_ID = :body_id FOR UPDATE""",
        {"body_id": reference.body_id},
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("trace body manifest is missing")
    if tuple(row[:5]) != (
        reference.sha256,
        reference.character_count,
        reference.byte_count,
        reference.part_count,
        reference.visibility,
    ):
        raise ValueError("trace body reference does not match its manifest")
    if row[5] == "deleted":
        raise BodyDeleted("trace body no longer has an owner")
    return str(row[5])


def attach_body_owner(cursor: Any, owner: BodyOwner, references: Iterable[BodyReference]) -> None:
    """所属記録の保存と同じ取引で呼ぶ。本文の書込前でも参照と所属を確定できる。"""
    # 同じ順でmanifestをロックし、複数本文を共有する保存・削除のロック順をそろえる。
    unique: dict[str, BodyReference] = {}
    for reference in references:
        if reference.body_id in unique and unique[reference.body_id] != reference:
            raise ValueError("conflicting trace body references")
        unique[reference.body_id] = reference
    for reference in sorted(unique.values(), key=lambda value: value.body_id):
        lock_body_manifest(cursor, reference)
        cursor.execute(
            """
            MERGE INTO PLATFORM_TRACE_BODY_OWNERS t
            USING (SELECT :product AS PRODUCT, :kind AS OWNER_KIND, :record_id AS OWNER_ID,
                          :body_id AS BODY_ID FROM DUAL) s
            ON (t.PRODUCT = s.PRODUCT AND t.OWNER_KIND = s.OWNER_KIND
                AND t.OWNER_ID = s.OWNER_ID AND t.BODY_ID = s.BODY_ID)
            WHEN NOT MATCHED THEN INSERT (PRODUCT, OWNER_KIND, OWNER_ID, BODY_ID)
            VALUES (:product, :kind, :record_id, :body_id)
            """,
            {**_owner_binds(owner), "body_id": reference.body_id},
        )


def release_body_owner(cursor: Any, owner: BodyOwner) -> None:
    """実際の所属記録を消す取引内で呼ぶ。投影の期限切れには使わない。"""
    binds = _owner_binds(owner)
    cursor.execute(
        """SELECT BODY_ID FROM PLATFORM_TRACE_BODY_OWNERS
        WHERE PRODUCT = :product AND OWNER_KIND = :kind AND OWNER_ID = :record_id
        ORDER BY BODY_ID""",
        binds,
    )
    ids = [str(row[0]) for row in cursor.fetchall()]
    for body_id in ids:
        cursor.execute(
            "SELECT BODY_ID FROM PLATFORM_TRACE_BODIES WHERE BODY_ID = :body_id FOR UPDATE",
            {"body_id": body_id},
        )
        if cursor.fetchone() is None:
            continue
        cursor.execute(
            """DELETE FROM PLATFORM_TRACE_BODY_OWNERS
            WHERE PRODUCT = :product AND OWNER_KIND = :kind AND OWNER_ID = :record_id
            AND BODY_ID = :body_id""",
            {**binds, "body_id": body_id},
        )
        cursor.execute(
            "SELECT COUNT(*) FROM PLATFORM_TRACE_BODY_OWNERS WHERE BODY_ID = :body_id",
            {"body_id": body_id},
        )
        if cursor.fetchone()[0]:
            continue
        cursor.execute(
            "DELETE FROM PLATFORM_TRACE_BODY_PARTS WHERE BODY_ID = :body_id",
            {"body_id": body_id},
        )
        # 本文は即時に消す。非同期writerの遅い到着で復活させないため、本文の無い墓標を残す。
        cursor.execute(
            """UPDATE PLATFORM_TRACE_BODIES SET STATUS = 'deleted', FAILURE_CODE = NULL,
            UPDATED_AT = SYSTIMESTAMP WHERE BODY_ID = :body_id""",
            {"body_id": body_id},
        )


def write_body(cursor: Any, body: PreparedBody, *, clob_type: Any, batch_size: int = 25) -> None:
    """全片とcompleteを一つの取引で保存する。例外時のrollbackは接続を持つ呼出側が行う。"""
    if batch_size < 1:
        raise ValueError("trace body write batch must be positive")
    if lock_body_manifest(cursor, body.reference) == "complete":
        return
    cursor.execute(
        "DELETE FROM PLATFORM_TRACE_BODY_PARTS WHERE BODY_ID = :body_id",
        {"body_id": body.reference.body_id},
    )

    cursor.setinputsizes(content_text=clob_type)
    sql = """INSERT INTO PLATFORM_TRACE_BODY_PARTS (
        BODY_ID, PART_INDEX, START_OFFSET, END_OFFSET, CONTENT_SHA256, CONTENT_TEXT
    ) VALUES (:body_id, :part_index, :start_offset, :end_offset, :part_sha256, :content_text)"""
    batch: list[dict[str, object]] = []
    digest = hashlib.sha256()
    characters = byte_count = part_count = 0
    for part in body.parts():
        encoded = part.text.encode("utf-8")
        digest.update(encoded)
        characters += len(part.text)
        byte_count += len(encoded)
        part_count += 1
        batch.append(
            {
                "body_id": body.reference.body_id,
                "part_index": part.index,
                "start_offset": part.start,
                "end_offset": part.end,
                "part_sha256": part.sha256,
                "content_text": part.text,
            }
        )
        if len(batch) == batch_size:
            cursor.executemany(sql, batch)
            batch = []
    if batch:
        cursor.executemany(sql, batch)
    if (digest.hexdigest(), characters, byte_count, part_count) != (
        body.reference.sha256,
        body.reference.character_count,
        body.reference.byte_count,
        body.reference.part_count,
    ):
        raise ValueError("trace body parts do not cover the prepared snapshot")
    cursor.execute(
        """UPDATE PLATFORM_TRACE_BODIES SET STATUS = 'complete', FAILURE_CODE = NULL,
        UPDATED_AT = SYSTIMESTAMP WHERE BODY_ID = :body_id""",
        {"body_id": body.reference.body_id},
    )


def mark_body_incomplete(cursor: Any, reference: BodyReference, reason: BodyFailure) -> None:
    """本文の書込失敗とは別の取引で記録する。完了済み・削除済みを遅い失敗で変えない。"""
    if reason not in {
        "storage_unavailable",
        "queue_full",
        "write_failed",
        "capture_failed",
        "writer_stopped",
        "invalid_encoding",
    }:
        raise ValueError("unsupported trace body failure code")
    try:
        status = lock_body_manifest(cursor, reference)
    except BodyDeleted:
        return
    if status == "complete":
        return
    cursor.execute(
        """UPDATE PLATFORM_TRACE_BODIES SET STATUS = 'incomplete', FAILURE_CODE = :reason,
        UPDATED_AT = SYSTIMESTAMP WHERE BODY_ID = :body_id""",
        {"body_id": reference.body_id, "reason": reason},
    )


def read_body_page(
    cursor: Any,
    owner: BodyOwner,
    reference: BodyReference,
    *,
    first_part: int = 0,
    max_parts: int = 8,
    allow_restricted: bool = False,
) -> BodyPage | None:
    """製品が所属記録を認可した後に読む。別の所属・管理者限定本文は再度ここで除外する。"""
    if first_part < 0 or max_parts < 1:
        raise ValueError("trace body page range is invalid")
    scope_binds = {**_owner_binds(owner), "body_id": reference.body_id}
    cursor.execute(
        """SELECT b.CONTENT_SHA256, b.CHARACTER_COUNT, b.BYTE_COUNT, b.PART_COUNT,
        b.VISIBILITY, b.STATUS, b.FAILURE_CODE
        FROM PLATFORM_TRACE_BODIES b JOIN PLATFORM_TRACE_BODY_OWNERS o ON o.BODY_ID = b.BODY_ID
        WHERE b.BODY_ID = :body_id AND o.PRODUCT = :product
          AND o.OWNER_KIND = :kind AND o.OWNER_ID = :record_id AND b.STATUS <> 'deleted'
          AND (b.VISIBILITY = 'owner' OR :allow_restricted = 1)""",
        {**scope_binds, "allow_restricted": int(allow_restricted)},
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if tuple(row[:5]) != (
        reference.sha256,
        reference.character_count,
        reference.byte_count,
        reference.part_count,
        reference.visibility,
    ):
        return BodyPage(reference, "incomplete", "reference_mismatch", (), None)
    status: BodyState = row[5]
    if status not in {"pending", "complete", "incomplete"}:
        return None
    failure = str(row[6]) if row[6] else None
    cursor.execute(
        """SELECT p.PART_INDEX, p.START_OFFSET, p.END_OFFSET, p.CONTENT_SHA256, p.CONTENT_TEXT
        FROM PLATFORM_TRACE_BODY_PARTS p JOIN PLATFORM_TRACE_BODY_OWNERS o ON o.BODY_ID = p.BODY_ID
        WHERE p.BODY_ID = :body_id AND o.PRODUCT = :product
          AND o.OWNER_KIND = :kind AND o.OWNER_ID = :record_id
          AND p.PART_INDEX >= :first_part ORDER BY p.PART_INDEX FETCH FIRST :take ROWS ONLY""",
        {**scope_binds, "first_part": first_part, "take": max_parts + 1},
    )
    rows = cursor.fetchall()
    more = len(rows) > max_parts
    parts: list[BodyPart] = []
    for value in rows[:max_parts]:
        content = value[4].read() if callable(getattr(value[4], "read", None)) else value[4]
        part = BodyPart(
            int(value[0]), int(value[1]), int(value[2]), str(content or ""), str(value[3])
        )
        if (
            part.index != first_part + len(parts)
            or part.end - part.start != len(part.text)
            or (not parts and first_part == 0 and part.start != 0)
            or (bool(parts) and part.start != parts[-1].end)
            or (part.index == reference.part_count - 1 and part.end != reference.character_count)
            or hashlib.sha256(part.text.encode("utf-8")).hexdigest() != part.sha256
        ):
            return BodyPage(reference, "incomplete", "body_part_invalid", tuple(parts), None)
        parts.append(part)
    expected = min(max_parts, max(0, reference.part_count - first_part))
    if status == "complete" and len(parts) != expected:
        status, failure = "incomplete", "body_parts_missing"
    return BodyPage(
        reference, status, failure, tuple(parts), parts[-1].index + 1 if more and parts else None
    )
