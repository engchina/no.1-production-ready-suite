"""原生hybrid索引の削除残骸を、専用接続で実数を確認して整理する。"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

from rag_engine.adapters.oracle.native_hybrid import NATIVE_HYBRID_INDEX

logger = logging.getLogger(__name__)


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


class NativeIndexVerificationFailed(RuntimeError):
    """整理は返ったが、件数を確認できなかった。整理そのものの失敗とは分けて記録する。"""


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
    before_verify: Callable[[], None] = lambda: None,
) -> NativeMaintenanceResult:
    """commit済みの変更だけを、業務接続とは別の接続で整理する。

    OPTIMIZE_INDEX は暗黙commitを行うため、業務transactionや行lockを持つ
    接続を渡さない。検索要求からは呼ばず、永続化した整理要求を消費する
    workerだけが呼ぶ。件数検査もそのworkerに限定する。

    ``before_call`` は整理を始めるまでの呼出しの前に、``before_verify`` は整理が返った後の
    件数検査の呼出しの前に呼ぶ。整理の時間で後の検査を失敗させない（#4097）。
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
        started = monotonic()
        cursor.execute(
            "BEGIN CTX_DDL.OPTIMIZE_INDEX(:index_name, CTX_DDL.OPTLEVEL_FULL); END;",
            {"index_name": index_name},
        )
        logger.info(
            "native_index_maintenance_optimize_returned",
            extra={"optimize_seconds": round(monotonic() - started, 1)},
        )
        try:
            after = _counts(cursor, index_name, before_verify)
        except Exception as exc:
            logger.warning(
                "native_index_maintenance_verification_failed",
                extra={"error_type": type(exc).__name__},
            )
            raise NativeIndexVerificationFailed("原生索引の整理後の件数を確認できません。") from exc
        strategy = "full"
        if after.has_deleted_vectors:
            raise NativeIndexMaintenanceIncomplete("原生索引の削除残骸の整理が残っています。")
        return NativeMaintenanceResult(before, after, strategy)
    finally:
        cursor.close()
