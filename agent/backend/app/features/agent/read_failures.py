"""DB の読み取りの失敗を、画面が見分けられる API の失敗にする（#4222。messaging.md §3.6.1）。

読み取りの失敗を 200 + メモリの Run の集計で返さない。Oracle の構成では、同じ保存先に書く別の
プロセス（runtime-dispatcher・2 つ目の backend・CLI）の Run は、このプロセスが次に保存先へ書くまで
メモリに無い。メモリの集計は保存先の全件と同じとは限らないため、失敗は失敗として返し、画面は
status と `error_code` で原因を分ける（文言は照合しない）。code は 3 製品で同じ値
（`@production-ready/ui` の `dataLoadFailureKind`）。
"""

from __future__ import annotations

from fastapi import HTTPException
from pr_backend_core.oracle_errors import (
    is_oracle_connection_error,
    is_oracle_pool_wait_timeout,
    oracle_error_codes,
)

from app.features.agent.runtime import AgentRuntimeStorageUnavailableError

DATABASE_READ_TIMEOUT_CODE = "database_read_timeout"
DATABASE_READ_FAILED_CODE = "database_read_failed"
# 同じ読み取りをすぐ繰り返しても混雑を増やすだけなので、次に試すまでの目安を返す（秒）。
DATABASE_READ_RETRY_AFTER_SECONDS = 5


def database_read_failure(exc: BaseException) -> Exception:
    """読み取りの例外を、原因ごとの API の失敗にして返す（呼び出し側が `raise ... from exc`）。

    - pool の待ちの timeout（`DPY-4005`）: 503 `database_read_timeout`（混雑の可能性。DB の不通と
      言わない）
    - 接続の失敗: `AgentRuntimeStorageUnavailableError`（503 `agent_storage_unavailable`）
    - それ以外: 500 `database_read_failed`（接続できないと言い切らない）
    """
    if is_oracle_pool_wait_timeout(exc):
        return HTTPException(
            status_code=503,
            detail={
                "code": DATABASE_READ_TIMEOUT_CODE,
                "message": "データの読み取りがタイムアウトしました。データは失われていません。",
            },
            headers={"Retry-After": str(DATABASE_READ_RETRY_AFTER_SECONDS)},
        )
    if is_oracle_connection_error(exc):
        # ORA / DPY のコードだけを添える（接続先・資格情報は出さない）。
        codes = ", ".join(oracle_error_codes(exc))
        return AgentRuntimeStorageUnavailableError(
            "Agent の保存先のデータベースに接続できません"
            + (f"（{codes}）" if codes else "")
            + "。データは失われていません。"
        )
    return HTTPException(
        status_code=500,
        detail={
            "code": DATABASE_READ_FAILED_CODE,
            "message": "データを読み取れませんでした。データは失われていません。",
        },
    )
