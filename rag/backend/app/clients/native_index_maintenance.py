"""原生hybrid索引の削除残骸を、専用接続で実数を確認して整理する。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from rag_engine.adapters.oracle.native_hybrid import NATIVE_HYBRID_INDEX


class MaintenanceCursor(Protocol):
    def execute(self, statement: str, binds: Mapping[str, object] | None = None) -> object: ...

    def fetchone(self) -> Sequence[object] | None: ...

    def close(self) -> object: ...


class MaintenanceConnection(Protocol):
    def cursor(self) -> MaintenanceCursor: ...


@dataclass(frozen=True, slots=True)
class NativeVectorCounts:
    stored: int
    visible: int

    @property
    def has_deleted_vectors(self) -> bool:
        return self.stored > self.visible


@dataclass(frozen=True, slots=True)
class NativeMaintenanceResult:
    before: NativeVectorCounts
    after: NativeVectorCounts
    strategy: str


class NativeIndexMaintenanceIncomplete(RuntimeError):
    """時間制限等で整理が残る。完了扱いせず、後続の回で再開する。"""


def _counts(
    cursor: MaintenanceCursor, index_name: str, before_call: Callable[[], None]
) -> NativeVectorCounts:
    before_call()
    cursor.execute(
        f"""SELECT
            (SELECT COUNT(*) FROM DR${index_name}$VR),
            (SELECT COUNT(*) FROM {index_name}$VECTORS)
        FROM dual"""
    )
    before_call()
    row = cursor.fetchone()
    if row is None or len(row) != 2:
        raise RuntimeError("原生索引の整理対象件数を取得できませんでした。")
    return NativeVectorCounts(int(str(row[0])), int(str(row[1])))


def maintain_native_vectors(
    connection: MaintenanceConnection,
    *,
    index_name: str = NATIVE_HYBRID_INDEX,
    before_call: Callable[[], None] = lambda: None,
) -> NativeMaintenanceResult:
    """commit済みの変更だけを、業務接続とは別の接続で整理する。

    OPTIMIZE_INDEX は暗黙commitを行うため、業務transactionや行lockを持つ
    接続を渡さない。検索要求からは呼ばず、永続化した整理要求を消費する
    workerだけが呼ぶ。件数検査もそのworkerに限定する。
    """
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,29}", index_name):
        raise ValueError("原生索引名が不正です。")
    cursor = connection.cursor()
    try:
        before = _counts(cursor, index_name, before_call)
        # semanticだけでは$VR件数が正常でもSEARCHが欠落する実例がある。
        # serial FULLを最後まで実行し、専用接続のcall_timeoutで時間を制限する。
        # maxtimeで部分完了を正常終了させると、要求を誤って消すため指定しない。
        before_call()
        cursor.execute(
            "BEGIN CTX_DDL.OPTIMIZE_INDEX(:index_name, CTX_DDL.OPTLEVEL_FULL); END;",
            {"index_name": index_name},
        )
        after = _counts(cursor, index_name, before_call)
        strategy = "full"
        if after.has_deleted_vectors:
            raise NativeIndexMaintenanceIncomplete("原生索引の削除残骸の整理が残っています。")
        return NativeMaintenanceResult(before, after, strategy)
    finally:
        cursor.close()
