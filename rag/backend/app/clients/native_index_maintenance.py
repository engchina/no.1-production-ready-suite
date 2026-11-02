"""原生hybrid索引の削除残骸を、専用接続で実数を確認して整理する。"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Literal, Protocol

from rag_engine.adapters.oracle.native_hybrid import NATIVE_HYBRID_INDEX

logger = logging.getLogger(__name__)

# semantic: $VR だけを整理する。残れば FULL へ進む。
# semantic_then_full: 全文の分($I・$N)も今回まとめて整理する。FULL の失敗で要求を残さない。
# full: 前回の失敗の後。semantic を飛ばして、続きから再開できる FULL だけを実行する。
MaintenancePlan = Literal["semantic", "semantic_then_full", "full"]
_SEMANTIC_OPTIMIZE_SQL = """BEGIN CTX_DDL.OPTIMIZE_INDEX(:index_name, CTX_DDL.OPTLEVEL_TOKEN_TYPE,
    section_type => CTX_DDL.SECTION_SEMANTIC_INDEX); END;"""
_FULL_OPTIMIZE_SQL = "BEGIN CTX_DDL.OPTIMIZE_INDEX(:index_name, CTX_DDL.OPTLEVEL_FULL); END;"


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

    @property
    def full_completed(self) -> bool:
        return self.strategy != "semantic"


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


def _verified_counts(
    cursor: MaintenanceCursor, index_name: str, before_verify: Callable[[], None]
) -> NativeVectorCounts:
    try:
        return _counts(cursor, index_name, before_verify)
    except Exception as exc:
        logger.warning(
            "native_index_maintenance_verification_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise NativeIndexVerificationFailed("原生索引の整理後の件数を確認できません。") from exc


def _optimize(
    cursor: MaintenanceCursor,
    statement: str,
    step: str,
    index_name: str,
    before_call: Callable[[], None],
) -> None:
    before_call()
    started = monotonic()
    cursor.execute(statement, {"index_name": index_name})
    logger.info(
        "native_index_maintenance_optimize_returned",
        extra={"step": step, "optimize_seconds": round(monotonic() - started, 1)},
    )


def maintain_native_vectors(
    connection: MaintenanceConnection,
    *,
    index_name: str = NATIVE_HYBRID_INDEX,
    before_call: Callable[[], None] = lambda: None,
    before_verify: Callable[[], None] = lambda: None,
    plan: MaintenancePlan = "semantic",
) -> NativeMaintenanceResult:
    """commit済みの変更だけを、業務接続とは別の接続で整理する。

    OPTIMIZE_INDEX は暗黙commitを行うため、業務transactionや行lockを持つ
    接続を渡さない。検索要求からは呼ばず、永続化した整理要求を消費する
    workerだけが呼ぶ。件数検査もそのworkerに限定する。

    検索の候補の不足を起こす $VR の残骸を先に片付ける(#4097)。呼出しの成功では完了とせず、
    件数が残れば FULL へ進む(Oracle Free では semantic が残骸を減らさない。#4021)。

    ``before_call`` は整理を始めるまでの呼出しの前に、``before_verify`` は整理が返った後の
    件数検査の呼出しの前に呼ぶ。整理の時間で後の検査を失敗させない（#4097）。
    """
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,29}", index_name):
        raise ValueError("原生索引名が不正です。")
    cursor = connection.cursor()
    try:
        before = _counts(cursor, index_name, before_call)
        after = before
        strategy = "full"
        if plan != "full":
            _optimize(cursor, _SEMANTIC_OPTIMIZE_SQL, "semantic", index_name, before_call)
            after = _verified_counts(cursor, index_name, before_verify)
            strategy = "semantic"
        if plan != "semantic" or after.has_deleted_vectors:
            # maxtimeで部分完了を正常終了させると、要求を誤って消すため指定しない。
            # 専用接続のcall_timeoutで時間を制限する。
            try:
                _optimize(cursor, _FULL_OPTIMIZE_SQL, "full", index_name, before_call)
            except Exception as exc:
                if plan != "semantic_then_full" or after.has_deleted_vectors:
                    raise
                # $VR は片付いているので検索は不足しない。全文の分は次の要求で続きから進める。
                logger.warning(
                    "native_index_maintenance_full_deferred",
                    extra={"error_type": type(exc).__name__},
                )
                return NativeMaintenanceResult(before, after, "semantic")
            after = _verified_counts(cursor, index_name, before_verify)
            strategy = "full" if plan == "full" else "semantic_then_full"
        if after.has_deleted_vectors:
            raise NativeIndexMaintenanceIncomplete("原生索引の削除残骸の整理が残っています。")
        return NativeMaintenanceResult(before, after, strategy)
    finally:
        cursor.close()
