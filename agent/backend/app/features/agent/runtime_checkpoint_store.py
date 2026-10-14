"""Run・業務 Agent の checkpoint（`AGENT_RUNTIME_CHECKPOINTS`）の版つきの読み書き（#1916）。

checkpoint は全 Run・業務 Agent を 1 行の JSON（snapshot）に持ち、書くたびに全体を書き戻す。
同じ保存先に 2 つのプロセス（worktree で主の `.env` のまま起動した 2 つ目の backend・
runtime-dispatcher・CLI）が書くと、版を確かめないかぎり、後から書いた方が先に書いた方の更新を
消す（#1863 の確認中に Run の完了の記録が消えた）。

- 書くたびに `VERSION_NO` を 1 つ増やし、`WRITER_ID` に書いたプロセスを残す。
- 書き込みは、このプロセスが最後に読んだ・書いた版と同じときだけ行う（`UPDATE ... WHERE
  VERSION_NO = :expected`。行が無かったときは `INSERT` で、先に作られていたら ORA-00001）。
- 版が違えば（別のプロセスが先に書いた）、行を lock して読み直し、Run・業務 Agent の 1 件ずつ
  まとめ直してから書く（`plan_entity_merge`）。このプロセスが前回の同期から変えたものは
  このプロセスの値、変えていないものは保存先の値にする。両方が同じ 1 件を変えていたときだけ、
  このプロセスの値が勝つ（ログに残す）。
- `VERSION_NO` の列が無い（migration 015 を適用する前の）DB では、今までどおり確かめずに書き、
  警告を残す（起動・保存を止めない）。
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any
from uuid import uuid4

# このプロセスを表す ID（`WRITER_ID`。ホスト・pid・起動ごとの乱数）。
WRITER_ID = f"{socket.gethostname()[:120]}:{os.getpid()}:{uuid4().hex[:8]}"


class CheckpointVersionUnsupported(Exception):  # noqa: N818 - 例外ではなく「列が無い」という状態
    """`VERSION_NO` の列が無い（migration 015 を適用する前の DB）。"""


@dataclass(frozen=True, slots=True)
class CheckpointRow:
    snapshot_json: str
    # 版（列が無い DB では None）。
    version: int | None
    writer_id: str | None = None


def is_missing_column_error(exc: Exception) -> bool:
    text = str(exc)
    return "ORA-00904" in text or "invalid identifier" in text


def is_unique_violation(exc: Exception) -> bool:
    return "ORA-00001" in str(exc)


def read_checkpoint(
    cursor: Any, table: str, checkpoint_key: str, *, for_update: bool = False
) -> CheckpointRow | None:
    """checkpoint の行（無ければ None）。版の列が無い DB では `CheckpointVersionUnsupported`。"""
    lock = " FOR UPDATE" if for_update else ""
    query = (
        f"SELECT snapshot_json, version_no, writer_id FROM {table} "  # nosec B608 - 固定の表名
        f"WHERE checkpoint_key = :checkpoint_key{lock}"
    )
    try:
        cursor.execute(query, checkpoint_key=checkpoint_key)
    except Exception as exc:
        if is_missing_column_error(exc):
            raise CheckpointVersionUnsupported(str(exc)) from exc
        raise
    row = cursor.fetchone()
    if row is None:
        return None
    # CLOB は接続を閉じる前に読む（閉じた後に読むと DPY-1001。#765）。
    return CheckpointRow(
        snapshot_json=_lob_to_text(row[0]),
        version=int(row[1]) if row[1] is not None else 0,
        writer_id=str(row[2]) if row[2] is not None else None,
    )


def read_legacy_checkpoint(
    cursor: Any, table: str, checkpoint_key: str, *, for_update: bool = False
) -> CheckpointRow | None:
    """版の列が無い DB の checkpoint の行（無ければ None）。"""
    lock = " FOR UPDATE" if for_update else ""
    cursor.execute(
        f"SELECT snapshot_json FROM {table} "  # nosec B608 - 固定の表名
        f"WHERE checkpoint_key = :checkpoint_key{lock}",
        checkpoint_key=checkpoint_key,
    )
    row = cursor.fetchone()
    return CheckpointRow(_lob_to_text(row[0]), None) if row is not None else None


def write_checkpoint(
    cursor: Any,
    table: str,
    checkpoint_key: str,
    snapshot_json: str,
    *,
    expected_version: int | None,
) -> int | None:
    """読んだ版（`expected_version`。行が無かったときは None）のときだけ書き、新しい版を返す。

    別のプロセスが先に書いていた（版が違う・行が先に作られた・行が消えた）ときは None。
    """
    _bind_clob(cursor)
    if expected_version is None:
        try:
            cursor.execute(
                f"""
                INSERT INTO {table} (
                    checkpoint_key, snapshot_json, version_no, writer_id, updated_at
                ) VALUES (
                    :checkpoint_key, :snapshot_json, 1, :writer_id, SYSTIMESTAMP
                )
                """,  # nosec B608 - 固定の表名
                checkpoint_key=checkpoint_key,
                snapshot_json=snapshot_json,
                writer_id=WRITER_ID,
            )
        except Exception as exc:
            if is_unique_violation(exc):
                return None
            raise
        return 1
    cursor.execute(
        f"""
        UPDATE {table}
        SET snapshot_json = :snapshot_json,
            version_no = version_no + 1,
            writer_id = :writer_id,
            updated_at = SYSTIMESTAMP
        WHERE checkpoint_key = :checkpoint_key
          AND version_no = :expected_version
        """,  # nosec B608 - 固定の表名
        checkpoint_key=checkpoint_key,
        snapshot_json=snapshot_json,
        writer_id=WRITER_ID,
        expected_version=expected_version,
    )
    if int(cursor.rowcount or 0) != 1:
        return None
    return expected_version + 1


def write_legacy_checkpoint(
    cursor: Any, table: str, checkpoint_key: str, snapshot_json: str
) -> None:
    """版の列が無い DB に、確かめずに書く（migration 015 を適用する前の形）。"""
    _bind_clob(cursor)
    cursor.execute(
        f"""
        MERGE INTO {table} target
        USING (
            SELECT
                :checkpoint_key AS checkpoint_key,
                :snapshot_json AS snapshot_json
            FROM dual
        ) source
        ON (target.checkpoint_key = source.checkpoint_key)
        WHEN MATCHED THEN UPDATE SET
            target.snapshot_json = source.snapshot_json,
            target.updated_at = SYSTIMESTAMP
        WHEN NOT MATCHED THEN INSERT (
            checkpoint_key,
            snapshot_json,
            updated_at
        ) VALUES (
            source.checkpoint_key,
            source.snapshot_json,
            SYSTIMESTAMP
        )
        """,  # nosec B608 - 固定の表名
        checkpoint_key=checkpoint_key,
        snapshot_json=snapshot_json,
    )


def _bind_clob(cursor: Any) -> None:
    # snapshot は全 Run を含み 4,000 / 32,767 byte を超える。型を指定しないと bind が VARCHAR2 に
    # なり ORA-01461（#841）。
    cursor.setinputsizes(snapshot_json=import_module("oracledb").DB_TYPE_CLOB)


def _lob_to_text(value: object) -> str:
    read = getattr(value, "read", None)
    if callable(read):
        return str(read())
    return str(value)


# ---- Run・業務 Agent の単位のまとめ直し ------------------------------------------------


@dataclass(slots=True)
class EntityMergePlan:
    """保存先（theirs）の値で、このプロセス（ours）のメモリをどう変えるか。

    - `take`: 保存先の値にする（このプロセスは前回の同期から変えていない。id → 保存先の JSON）
    - `drop`: メモリから消す（保存先で消え、このプロセスは変えていない）
    - `overlapping`: 両方が変えた（このプロセスの値を残す）
    - `unidentified`: 保存先の id の無い要素（読み込みで退避させる）
    """

    take: dict[str, Any] = field(default_factory=dict)
    drop: set[str] = field(default_factory=set)
    overlapping: list[str] = field(default_factory=list)
    unidentified: list[Any] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.take or self.drop or self.unidentified)


def plan_entity_merge(
    base: list[Any] | None, ours: list[Any], theirs: list[Any]
) -> EntityMergePlan:
    """Run・業務 Agent の配列（JSON）を id の単位でまとめ直す。

    `base` は前回の同期（読み込み・書き込み）の時点のこのプロセスの値（None なら、このプロセスは
    何も変えていないとみなし、保存先の値にそろえる）。
    """
    ours_by_id = _by_id(ours)
    base_by_id = ours_by_id if base is None else _by_id(base)
    theirs_by_id = _by_id(theirs)
    plan = EntityMergePlan(
        unidentified=[item for item in theirs if _entity_id(item) is None],
    )
    for entity_id in sorted(set(ours_by_id) | set(base_by_id) | set(theirs_by_id)):
        mine = ours_by_id.get(entity_id)
        before = base_by_id.get(entity_id)
        other = theirs_by_id.get(entity_id)
        if mine != before:
            # このプロセスが変えた（足した・消した）。このプロセスの値を残す。
            if other != before and other != mine:
                plan.overlapping.append(entity_id)
            continue
        if other is None:
            if mine is not None:
                plan.drop.add(entity_id)
        elif other != mine:
            plan.take[entity_id] = other
    return plan


def _entity_id(item: object) -> str | None:
    if isinstance(item, dict):
        value = item.get("id")
        if isinstance(value, str) and value:
            return value
    return None


def _by_id(items: list[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        entity_id = _entity_id(item)
        if entity_id is not None:
            result[entity_id] = item
    return result


__all__ = [
    "WRITER_ID",
    "CheckpointRow",
    "CheckpointVersionUnsupported",
    "EntityMergePlan",
    "plan_entity_merge",
    "read_checkpoint",
    "read_legacy_checkpoint",
    "write_checkpoint",
    "write_legacy_checkpoint",
]
