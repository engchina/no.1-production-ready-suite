"""索引整理のcommit済み要求とworkerのlease。利用者の本文は保存しない。"""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import suppress
from time import monotonic
from typing import Protocol, cast
from uuid import uuid4

from app.clients.native_index_maintenance import (
    MaintenanceConnection,
    MaintenancePlan,
    NativeMaintenanceResult,
    maintain_native_vectors,
)
from app.clients.native_maintenance_schema import (
    EVENTS_TABLE,
    FULL_STATE_KEY,
    STATE_KEY,
    STATE_TABLE,
)
from app.clients.oracle import OracleClient, OracleConnectionProtocol, OracleCursorProtocol
from app.config import get_settings
from app.rag.native_index_maintenance import MaintenanceLease

logger = logging.getLogger(__name__)

EVENT_BATCH_LIMIT = 1000
LEASE_SECONDS = 600
OPTIMIZE_CALL_TIMEOUT_MS = 180_000
# 整理が返った後の件数検査の呼出しごとの上限。整理の残り時間とは別に持つ（#4097）。
VERIFY_CALL_TIMEOUT_MS = 60_000


class TimedMaintenanceConnection(OracleConnectionProtocol, Protocol):
    call_timeout: int


class NativeMaintenanceStore:
    def __init__(
        self, oracle: OracleClient | None = None, *, full_interval_hours: float | None = None
    ) -> None:
        self.oracle = oracle or OracleClient()
        self.full_interval_hours = (
            get_settings().rag_native_index_full_optimize_interval_hours
            if full_interval_hours is None
            else full_interval_hours
        )

    async def claim(self, owner: str) -> MaintenanceLease | None:
        token = uuid4().hex

        def operation(connection: OracleConnectionProtocol) -> MaintenanceLease | None:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"""SELECT attempt_count FROM {STATE_TABLE}
                    WHERE state_key=:state_key AND next_attempt_at<=SYSTIMESTAMP
                    AND (lease_until IS NULL OR lease_until<SYSTIMESTAMP)
                    FOR UPDATE SKIP LOCKED""",
                    {"state_key": STATE_KEY},
                )
                state = cursor.fetchone()
                if state is None:
                    return None
                cursor.execute(
                    f"""SELECT event_id FROM {EVENTS_TABLE}
                    ORDER BY created_at, event_id FETCH FIRST :event_limit ROWS ONLY""",
                    {"event_limit": EVENT_BATCH_LIMIT},
                )
                ids = tuple(str(row[0]) for row in cursor.fetchall())
                if not ids:
                    return None
                cursor.execute(
                    f"""UPDATE {STATE_TABLE} SET lease_owner=:owner, lease_token=:token,
                    lease_until=SYSTIMESTAMP+NUMTODSINTERVAL(:lease_seconds, 'SECOND'),
                    attempt_count=attempt_count+1, updated_at=SYSTIMESTAMP
                    WHERE state_key=:state_key""",
                    {
                        "owner": owner,
                        "token": token,
                        "lease_seconds": LEASE_SECONDS,
                        "state_key": STATE_KEY,
                    },
                )
                return MaintenanceLease(owner, token, ids, attempt=int(str(state[0])) + 1)
            finally:
                cursor.close()

        return await self.oracle._run_transaction(operation)

    async def heartbeat(self, lease: MaintenanceLease) -> bool:
        def operation(connection: OracleConnectionProtocol) -> bool:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"""UPDATE {STATE_TABLE} SET
                    lease_until=SYSTIMESTAMP+NUMTODSINTERVAL(:lease_seconds, 'SECOND'),
                    updated_at=SYSTIMESTAMP
                    WHERE state_key=:state_key AND lease_owner=:owner AND lease_token=:token
                    AND lease_until>SYSTIMESTAMP""",
                    {
                        "state_key": STATE_KEY,
                        "owner": lease.owner,
                        "token": lease.token,
                        "lease_seconds": LEASE_SECONDS,
                    },
                )
                row_count: object = getattr(cursor, "rowcount", None)
                return isinstance(row_count, int) and row_count == 1
            finally:
                cursor.close()

        return await self.oracle._run_transaction(operation)

    async def complete(self, lease: MaintenanceLease) -> bool:
        def operation(connection: OracleConnectionProtocol) -> bool:
            cursor = connection.cursor()
            try:
                binds = {"state_key": STATE_KEY, "owner": lease.owner, "token": lease.token}
                cursor.execute(
                    f"""SELECT 1 FROM {STATE_TABLE}
                    WHERE state_key=:state_key AND lease_owner=:owner AND lease_token=:token
                    AND lease_until>SYSTIMESTAMP FOR UPDATE""",
                    binds,
                )
                if cursor.fetchone() is None:
                    return False
                # 後からcommitされた要求を、IDの大小で巻き込まない。
                if lease.event_ids:
                    event_binds = {f"e{i}": value for i, value in enumerate(lease.event_ids)}
                    placeholders = ", ".join(f":{key}" for key in event_binds)
                    cursor.execute(
                        f"DELETE FROM {EVENTS_TABLE} WHERE event_id IN ({placeholders})",
                        event_binds,
                    )
                cursor.execute(
                    f"""UPDATE {STATE_TABLE} SET lease_owner=NULL, lease_token=NULL,
                    lease_until=NULL, last_error_type=NULL, attempt_count=0,
                    last_completed_at=SYSTIMESTAMP,
                    next_attempt_at=SYSTIMESTAMP, updated_at=SYSTIMESTAMP
                    WHERE state_key=:state_key AND lease_owner=:owner AND lease_token=:token""",
                    binds,
                )
                return True
            finally:
                cursor.close()

        return await self.oracle._run_transaction(operation)

    async def fail(self, lease: MaintenanceLease, error_type: str) -> None:
        def operation(connection: OracleConnectionProtocol) -> None:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"""UPDATE {STATE_TABLE} SET lease_owner=NULL, lease_token=NULL,
                    lease_until=NULL, last_error_type=:error_type,
                    next_attempt_at=SYSTIMESTAMP+NUMTODSINTERVAL(
                        LEAST(900, 5*POWER(2, LEAST(attempt_count, 8))), 'SECOND'),
                    updated_at=SYSTIMESTAMP
                    WHERE state_key=:state_key AND lease_owner=:owner AND lease_token=:token""",
                    {
                        "state_key": STATE_KEY,
                        "owner": lease.owner,
                        "token": lease.token,
                        "error_type": error_type[:128],
                    },
                )
            finally:
                cursor.close()

        await self.oracle._run_transaction(operation)

    def _plan(self, cursor: OracleCursorProtocol, lease: MaintenanceLease) -> MaintenancePlan:
        """失敗の次は FULL に戻す。FULL の間隔が過ぎていれば semantic の後に FULL も行う。"""
        if lease.attempt > 1:
            return "full"
        cursor.execute(
            f"""SELECT CASE WHEN last_completed_at>SYSTIMESTAMP
                    -NUMTODSINTERVAL(:interval_hours, 'HOUR') THEN 0 ELSE 1 END
            FROM {STATE_TABLE} WHERE state_key=:state_key""",
            {"interval_hours": self.full_interval_hours, "state_key": FULL_STATE_KEY},
        )
        row = cursor.fetchone()
        return "semantic_then_full" if row is None or int(str(row[0])) == 1 else "semantic"

    async def _record_full_completed(self) -> None:
        def operation(connection: OracleConnectionProtocol) -> None:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"""MERGE INTO {STATE_TABLE} s
                    USING (SELECT :state_key state_key FROM dual) v ON (s.state_key=v.state_key)
                    WHEN MATCHED THEN UPDATE SET
                        last_completed_at=SYSTIMESTAMP, updated_at=SYSTIMESTAMP
                    WHEN NOT MATCHED THEN INSERT (state_key, last_completed_at)
                        VALUES (v.state_key, SYSTIMESTAMP)""",
                    {"state_key": FULL_STATE_KEY},
                )
            finally:
                cursor.close()

        await self.oracle._run_transaction(operation)

    async def optimize(self, lease: MaintenanceLease) -> None:
        results: list[NativeMaintenanceResult] = []

        def operation() -> None:
            connection = cast(TimedMaintenanceConnection, self.oracle.acquire_connection())
            previous_timeout = getattr(connection, "call_timeout", None)
            try:
                if not isinstance(previous_timeout, int):
                    raise RuntimeError("索引整理接続の呼出時間上限を設定できません。")
                deadline = monotonic() + OPTIMIZE_CALL_TIMEOUT_MS / 1000

                def before_call() -> None:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise TimeoutError("索引整理の実行時間上限を超えました。")
                    connection.call_timeout = max(1, math.ceil(remaining * 1000))

                def before_verify() -> None:
                    connection.call_timeout = VERIFY_CALL_TIMEOUT_MS

                # 接続待ちの間に失効した古いworkerは、索引操作を始めない。
                cursor = connection.cursor()
                try:
                    before_call()
                    cursor.execute(
                        f"""SELECT 1 FROM {STATE_TABLE}
                        WHERE state_key=:state_key AND lease_owner=:owner AND lease_token=:token
                        AND lease_until>SYSTIMESTAMP
                            +NUMTODSINTERVAL(:operation_seconds, 'SECOND')""",
                        {
                            "state_key": STATE_KEY,
                            "owner": lease.owner,
                            "token": lease.token,
                            "operation_seconds": OPTIMIZE_CALL_TIMEOUT_MS / 1000,
                        },
                    )
                    before_call()
                    if cursor.fetchone() is None:
                        raise RuntimeError("索引整理のleaseが失効しています。")
                    before_call()
                    plan = self._plan(cursor, lease)
                finally:
                    cursor.close()
                result = maintain_native_vectors(
                    cast(MaintenanceConnection, connection),
                    before_call=before_call,
                    before_verify=before_verify,
                    plan=plan,
                )
                results.append(result)
                logger.info(
                    "native_index_maintenance_optimized",
                    extra={
                        "strategy": result.strategy,
                        "stored_before": result.before.stored,
                        "visible_before": result.before.visible,
                        "stored_after": result.after.stored,
                        "visible_after": result.after.visible,
                    },
                )
            finally:
                if isinstance(previous_timeout, int):
                    with suppress(Exception):
                        connection.call_timeout = previous_timeout
                connection.close()

        # OPTIMIZE_INDEXの暗黙commitを業務transactionやlease行lockへ混ぜない。
        async def run() -> None:
            await self.oracle._db_call_runner(operation)

        task = asyncio.create_task(run())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # coroutineの取消だけでは実SQLは止まらない。接続が閉じるまで待ち、
            # 呼出元のheartbeatを維持する。要求は完了せず、次回へ残す。
            with suppress(Exception):
                await asyncio.shield(task)
            raise
        if results and results[0].full_completed:
            # 記録に失敗しても整理は終わっている。次の要求で FULL がもう一度走るだけ。
            try:
                await self._record_full_completed()
            except Exception as exc:
                logger.warning(
                    "native_index_maintenance_full_record_failed",
                    extra={"error_type": type(exc).__name__},
                )
